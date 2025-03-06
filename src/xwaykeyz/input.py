import signal
import asyncio

from asyncio import Task, TimerHandle
from copy import copy
from inotify_simple import INotify, flags
from inotify_simple import Event as inotify_Event
from sys import exit
from typing import List, Optional

from evdev import InputDevice, InputEvent, ecodes
from evdev.eventio import EventIO

from . import config_api, transform
from .devices import DeviceFilter, DeviceGrabError, DeviceRegistry
from .lib.dummy_device import DummyDevice
from .lib import logger
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
    # down = InputEvent(0, 0, ecodes.EV_KEY, Key.LEFT_SHIFT, Action.PRESS)
    # up = InputEvent(0, 0, ecodes.EV_KEY, Key.LEFT_SHIFT, Action.RELEASE)
    # for ev in [down, up]:
    #     on_event(ev, None)

    dummy_device = DummyDevice()

    # List all modifier keys that should be reset at startup
    modifier_keys = [
        Key.LEFT_SHIFT, Key.RIGHT_SHIFT,
        Key.LEFT_CTRL, Key.RIGHT_CTRL,
        Key.LEFT_ALT, Key.RIGHT_ALT,
        Key.LEFT_META, Key.RIGHT_META,
        # Key.CAPSLOCK, Key.NUMLOCK
    ]

    # List of all typical keyboard keys to reset
    keys_to_reset = [
        # Modifier keys
        Key.LEFT_SHIFT, Key.RIGHT_SHIFT,
        Key.LEFT_CTRL, Key.RIGHT_CTRL,
        Key.LEFT_ALT, Key.RIGHT_ALT, 
        Key.LEFT_META, Key.RIGHT_META,
        Key.CAPSLOCK, Key.NUMLOCK, Key.SCROLLLOCK,
        
        # Function keys
        Key.F1, Key.F2, Key.F3, Key.F4, Key.F5, Key.F6,
        Key.F7, Key.F8, Key.F9, Key.F10, Key.F11, Key.F12,
        
        # Number row
        Key.GRAVE, Key.KEY_1, Key.KEY_2, Key.KEY_3, Key.KEY_4, Key.KEY_5,
        Key.KEY_6, Key.KEY_7, Key.KEY_8, Key.KEY_9, Key.KEY_0,
        Key.MINUS, Key.EQUAL, Key.BACKSPACE,
        
        # Upper row
        Key.TAB, Key.Q, Key.W, Key.E, Key.R, Key.T, Key.Y, Key.U, Key.I, Key.O, Key.P,
        Key.LEFT_BRACE, Key.RIGHT_BRACE, Key.BACKSLASH,
        
        # Home row
        Key.A, Key.S, Key.D, Key.F, Key.G, Key.H, Key.J, Key.K, Key.L,
        Key.SEMICOLON, Key.APOSTROPHE, Key.ENTER,
        
        # Lower row
        Key.Z, Key.X, Key.C, Key.V, Key.B, Key.N, Key.M,
        Key.COMMA, Key.DOT, Key.SLASH,
        
        # Bottom row and space
        Key.SPACE,
        
        # Navigation
        Key.ESC, Key.INSERT, Key.DELETE,
        Key.HOME, Key.END, Key.PAGE_UP, Key.PAGE_DOWN,
        Key.UP, Key.DOWN, Key.LEFT, Key.RIGHT,
        
        # Numpad
        Key.KP0, Key.KP1, Key.KP2, Key.KP3, Key.KP4,
        Key.KP5, Key.KP6, Key.KP7, Key.KP8, Key.KP9,
        Key.KPASTERISK, Key.KPMINUS, Key.KPPLUS, Key.KPDOT,
        Key.KPSLASH, Key.KPENTER
    ]

    all_keys_to_reset = keys_to_reset + modifier_keys

    _verbose_state = copy(logger.VERBOSE)                  # Store verbosity chosen by user
    logger.VERBOSE = False                                 # Hide all the startup release events

    # Progress bar configuration
    bar_width = 20                                  # Total width of progress bar (inside brackets)
    fill_char = '='                                 # Character used to show progress
    empty_char = '.'                                # Character used to show remaining space

    # Initialize progress bar for verbose mode
    if _verbose_state:
        print(f"(--) Clearing key states: [{empty_char * bar_width}]", end="", flush=True)
        # Move cursor back to beginning of progress bar (after '[')
        print("\b" * (bar_width + 1), end="", flush=True)

    total_keys = len(all_keys_to_reset)
    chars_filled = 0

    # Generate release events for each key
    for i, key in enumerate(all_keys_to_reset):
        # Calculate current progress percentage
        progress_pct = (i + 1) / total_keys

        # Calculate how many fill characters should be shown by now
        fill_needed = int(progress_pct * bar_width)

        # Release event
        up = InputEvent(0, 0, ecodes.EV_KEY, key, Action.RELEASE)
        on_event(up, dummy_device)

        # Update the progress bar
        if _verbose_state and fill_needed > chars_filled:
            fill_to_print = fill_needed - chars_filled
            print(fill_char * fill_to_print, end="", flush=True)
            chars_filled = fill_needed

    # Send a sync event
    sync_event = InputEvent(0, 0, ecodes.EV_SYN, 0, 0)
    on_event(sync_event, dummy_device)

    logger.VERBOSE = _verbose_state                        # Reset verbosity to what user wanted

    # Complete the progress bar and show "Complete" message
    if _verbose_state:
        # Move cursor past the right bracket
        print("\b" * -1 + "] Complete", flush=True)


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
