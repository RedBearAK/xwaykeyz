import asyncio
import signal
from asyncio import Task, TimerHandle
from inotify_simple import INotify, flags
from inotify_simple import Event as inotify_Event
from sys import exit
from typing import List, Optional

from evdev import InputDevice, InputEvent, ecodes
from evdev.eventio import EventIO

from . import config_api, transform
from .devices import DeviceFilter, DeviceGrabError, DeviceRegistry
from .lib.logger import debug, error, info
from .models.action import Action
from .models.key import Key
from .transform import boot_config, dump_diagnostics, on_event

CONFIG = config_api


def shutdown():
    loop = asyncio.get_event_loop()
    loop.stop()
    transform.shutdown()


def sig_term():
    print("signal TERM received", flush=True)
    shutdown()
    exit(0)


def sig_int():
    print("signal INT received", flush=True)
    shutdown()
    exit(0)


def watch_dev_input():
    inotify = INotify()
    inotify.add_watch("/dev/input", flags.CREATE | flags.ATTRIB | flags.DELETE)
    return inotify


# Why? xmodmap won't persist mapping changes until it's seen at least
# one keystroke on a new device, so we need to give it something that
# won't do any harm, but is still an actual keypress, hence shift.
def wakeup_output():
    down = InputEvent(0, 0, ecodes.EV_KEY, Key.LEFT_SHIFT, Action.PRESS)
    up = InputEvent(0, 0, ecodes.EV_KEY, Key.LEFT_SHIFT, Action.RELEASE)
    for ev in [down, up]:
        on_event(ev, None)


def main_loop(arg_devices, device_watch):
    inotify = None

    boot_config()
    wakeup_output()

    if device_watch:
        inotify = watch_dev_input()

    try:
        loop = asyncio.get_event_loop()
        registry = DeviceRegistry(
            loop, input_cb=receive_input, filterer=DeviceFilter(arg_devices)
        )
        registry.autodetect()

        if device_watch:
            loop.add_reader(inotify.fd, _inotify_handler, registry, inotify)

        _sup = loop.create_task(supervisor())  # noqa: F841
        loop.add_signal_handler(signal.SIGINT, sig_int)
        loop.add_signal_handler(signal.SIGTERM, sig_term)
        info("Ready to process input.")
        loop.run_forever()
    except DeviceGrabError:
        loop.stop()
    finally:
        shutdown()
        registry.ungrab_all()
        if device_watch:
            inotify.close()


_tasks: List[Task] = []
_sup = None


async def supervisor():
    while True:
        await asyncio.sleep(5)
        for task in _tasks:
            if task.done():
                if task.exception():
                    import traceback

                    traceback.print_exception(task.exception())
                _tasks.remove(task)


def receive_input(device: EventIO):
    try:
        for event in device.read():
            if event.type == ecodes.EV_KEY:
                if event.code == CONFIG.EMERGENCY_EJECT_KEY:
                    error("BAIL OUT: Emergency eject - shutting down.")
                    shutdown()
                    exit(0)
                if event.code == CONFIG.DUMP_DIAGNOSTICS_KEY:
                    action = Action(event.value)
                    if action.just_pressed():
                        debug("DIAG: Diagnostics requested.")
                        dump_diagnostics()
                    continue

            on_event(event, device)
    # swallow "no such device errors" when unplugging a USB
    # device and we still have a few events in the inotify queue
    except OSError as e:
        if not e.errno == 19: # no such device
            raise


_add_timer: Optional[TimerHandle] = None
_notify_events = []


def _inotify_handler(registry, inotify: INotify):
    global _add_timer
    global _notify_events

    events = inotify.read(0)
    _notify_events.extend(events)

    if _add_timer:
        _add_timer.cancel()

    def device_change_task():
        task = loop.create_task(device_change(registry, _notify_events))
        _tasks.append(task)

    loop = asyncio.get_running_loop()
    # slow the roll a bit to allow for udev to change permissions, etc...
    _add_timer = loop.call_later(0.5, device_change_task)


async def device_change(registry: DeviceRegistry, events: List[inotify_Event]):
    while events:
        event: inotify_Event = events.pop(0)

        # type hint for `event.name` helps linter highlight `startswith()` correctly
        event_name: str = event.name
        # ignore mouse, mice, etc, non-event devices
        if not event_name.startswith("event"):
            continue

        filename = f"/dev/input/{event.name}"

        # deal with a permission problem of unknown origin
        tries                   = 9
        loop_cnt                = 1
        delay                   = 0.2
        delay_max               = delay * (2 ** (tries - 1))

        device = None
        while loop_cnt <= tries:
            try:
                device = InputDevice(filename)
                break  # Successful device initialization, exit retry loop
            except FileNotFoundError as fnf_err:
                # error(f"File not found '{filename}':\n\t{fnf_err}")
                registry.ungrab_by_filename(filename)
                break  # Exit retry loop if the device is not found
            except PermissionError as perm_err:
                if loop_cnt == tries:
                    error(f"PermissionError after {tries} attempts for '{filename}':\n\t{perm_err}")
                    break  # Final attempt due to PermissionError, exit retry loop
                else:
                    error(  f"Retrying to initialize '{filename}' due to PermissionError. "
                            f"Attempt {loop_cnt} of {tries}.\n\t{perm_err}")
            await asyncio.sleep(delay)
            delay = min(delay * 2, delay_max)
            loop_cnt += 1

        if device is None:
            continue

        # unplugging
        if event.mask == flags.DELETE:
            if device in registry:
                registry.ungrab(device)
            continue

        # potential new device
        try:
            if device not in registry:
                if registry.cares_about(device):
                    registry.grab(device)
        except FileNotFoundError:
            # likely received ATTR right before a DELETE, so we ignore
            continue
