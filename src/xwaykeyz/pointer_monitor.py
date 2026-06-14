"""
src/xwaykeyz/pointer_monitor.py

Read-only monitor for pointer devices (touchpads, mice), used to resume
suspended modifier keys when the user interacts with a pointing device.

Tap-to-click does not exist at the evdev level (libinput synthesizes it
in userspace from raw touch sequences), so this module gates on events
that DO exist there: BTN_TOUCH (finger contact on a touchpad) and the
mouse button range (physical clicks). Finger contact precedes both a
tap-click and a physical clickpad press, so resuming suspended modifiers
on contact ensures the modifier reaches the compositor before libinput
emits the synthesized or physical click.

Scroll wheel events (EV_REL) also trigger a resume, covering modifier+
wheel actions (Ctrl+wheel zoom, Alt+wheel history) on plain mice, which
emit no contact or button event while scrolling. Touchpad scrolling is
covered incidentally by BTN_TOUCH, since scrolling requires contact.

Devices are opened without grabbing and isolating; libinput and the
compositor continue to own all pointer behavior. To keep CPU cost at
zero during normal typing and pointer use, readers are registered with
the event loop only while the keymapper's suspend timer is active
(listen()/unlisten() are called from suspend_keys()/resume_keys() in
transform.py). Device fds are drained when listening starts, so contact
or clicks from before the suspend window cannot trigger a spurious
resume. A finger already resting on the pad emits no new BTN_TOUCH
press, so pre-existing contact naturally never triggers either.

Hotplug pickup is lazy and rate-limited: /dev/input is re-listed at
most once per _RESCAN_MIN_INTERVAL seconds, at listen() time, and a
rescan only runs when the set of device paths actually changed.
"""

__version__ = '20260614'

import time

from evdev import ecodes, InputDevice, list_devices

from .output import VIRT_DEVICE_PREFIX
from .lib.logger import debug, error


PTR_LOG_PFX = '--> POINTER'
PTR_DBG_CTX = 'PT'

# Button-range and touch-contact key events that signal pointer intent.
# A press event (value == 1) of any of these resumes suspended modifiers.
_TRIGGER_KEY_CODES = frozenset([
    ecodes.BTN_BACK,
    ecodes.BTN_EXTRA,
    ecodes.BTN_FORWARD,
    ecodes.BTN_LEFT,
    ecodes.BTN_MIDDLE,
    ecodes.BTN_RIGHT,
    ecodes.BTN_SIDE,
    ecodes.BTN_TASK,
    ecodes.BTN_TOUCH,
])

# Relative-axis events that signal pointer intent: scroll wheels only.
# Any value triggers (EV_REL events are nonzero by nature). Pointer
# motion (REL_X/REL_Y) is deliberately excluded — merely moving a mouse
# is not click intent. The HI_RES codes may be missing from older
# evdev/kernel-header builds; their kernel ABI values are fixed, so a
# numeric fallback is safe. Public: transform.py imports this set for
# the grabbed-device wheel-resume check in on_event().
TRIGGER_REL_CODES = frozenset([
    ecodes.REL_WHEEL,
    ecodes.REL_HWHEEL,
    getattr(ecodes, 'REL_WHEEL_HI_RES', 0x0b),
    getattr(ecodes, 'REL_HWHEEL_HI_RES', 0x0c),
])

# Minimum seconds between /dev/input rescans (hotplug pickup is lazy).
_RESCAN_MIN_INTERVAL = 20.0

_devices_dct: 'dict[str, InputDevice]'  = {}
_known_paths: 'set[str]'                = set()
_last_scan_time                         = 0.0
_listening                              = False
_loop                                   = None
_resume_fn                              = None


def _device_is_monitorable(device: InputDevice):
    """True for pointer devices worth monitoring: touchpads (BTN_TOUCH plus
    absolute axes) and mice (BTN_LEFT plus relative axes). Touchscreens
    (INPUT_PROP_DIRECT) and our own virtual output device are excluded.
    Grabbed devices may qualify but are silent on a read-only fd, which is
    harmless; non-grabbed combo keyboard/mouse devices qualify usefully.
    """
    if VIRT_DEVICE_PREFIX in device.name:
        return False

    try:
        caps_dct    = device.capabilities(verbose=False)
        props_lst   = device.input_props()
    except (OSError, AttributeError):
        return False

    if ecodes.INPUT_PROP_DIRECT in props_lst:
        return False

    keys_lst = caps_dct.get(ecodes.EV_KEY, [])

    if ecodes.BTN_TOUCH in keys_lst and ecodes.EV_ABS in caps_dct:
        return True
    if ecodes.BTN_LEFT in keys_lst and ecodes.EV_REL in caps_dct:
        return True

    return False


def _close_device(path):
    device = _devices_dct.pop(path, None)
    # Forget the path so the next rate-limited check sees a set
    # difference and triggers a rescan (covers transient errors).
    _known_paths.discard(path)
    if device is None:
        return
    if _listening and _loop is not None:
        try:
            _loop.remove_reader(device.fileno())
        except (OSError, ValueError):
            pass
    try:
        device.close()
    except OSError:
        pass


def _scan_devices():
    global _last_scan_time
    paths_lst       = list_devices()
    _last_scan_time = time.monotonic()

    # Drop devices whose nodes disappeared
    for path in [p for p in _devices_dct if p not in paths_lst]:
        _close_device(path)

    _known_paths.clear()
    _known_paths.update(paths_lst)

    # Probe paths we are not already holding open
    for path in paths_lst:
        if path in _devices_dct:
            continue
        try:
            device = InputDevice(path)
        except OSError:
            continue
        if not _device_is_monitorable(device):
            device.close()
            continue
        _devices_dct[path] = device
        debug(f"{PTR_LOG_PFX}: watching '{device.name}' ({path})", ctx=PTR_DBG_CTX)


def _drain_device(device: InputDevice):
    """Discard buffered events so activity from before the suspend
    window cannot trigger a resume."""
    try:
        while device.read_one() is not None:
            pass
    except BlockingIOError:
        pass


def _event_is_trigger(event):
    """Pointer-intent test: button/touch press, or any wheel motion."""
    if event.type == ecodes.EV_KEY:
        return event.value == 1 and event.code in _TRIGGER_KEY_CODES
    if event.type == ecodes.EV_REL:
        return event.code in TRIGGER_REL_CODES
    return False


def _on_readable(device: InputDevice):
    try:
        while True:
            event = device.read_one()
            if event is None:
                break
            if _event_is_trigger(event):
                debug(f"{PTR_LOG_PFX}: trigger event (type {event.type}, "
                        f"code {event.code}) on '{device.name}', resuming suspended keys",
                        ctx=PTR_DBG_CTX)
                # The resume path calls unlisten(), tearing down all readers.
                _resume_fn()
                return
    except BlockingIOError:
        pass
    except OSError:
        error(f"{PTR_LOG_PFX}: lost device '{device.name}', dropping it")
        _close_device(device.path)


def listen(loop, resume_callback):
    """Begin watching pointer devices for activity. Called from
    suspend_keys() in transform.py. Idempotent while already
    listening (the resuspend path calls suspend_keys() again)."""
    global _last_scan_time, _listening, _loop, _resume_fn
    if _listening:
        return

    _loop       = loop
    _resume_fn  = resume_callback

    now = time.monotonic()
    if now - _last_scan_time > _RESCAN_MIN_INTERVAL:
        if set(list_devices()) != _known_paths:
            _scan_devices()
        else:
            _last_scan_time = now

    for device in list(_devices_dct.values()):
        try:
            _drain_device(device)
            loop.add_reader(device.fileno(), _on_readable, device)
        except OSError:
            _close_device(device.path)

    _listening = True


def unlisten():
    """Stop watching pointer devices. Called from resume_keys() in
    transform.py, which is the single teardown path for all resume
    causes: timer expiry, key events, and pointer activity itself."""
    global _listening
    if not _listening:
        return
    _listening = False

    if _loop is None:
        return
    for device in list(_devices_dct.values()):
        try:
            _loop.remove_reader(device.fileno())
        except (OSError, ValueError):
            pass

# End of file #
