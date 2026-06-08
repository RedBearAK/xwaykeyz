"""
Runtime keyboard-layout correction map storage for the keymapper.

src/xwaykeyz/layout_correction.py

Holds the flat keycode correction map that the detection/analysis subsystem
(Toshy's toshy_common.kblayout_*) injects at runtime via set_correction_map(),
plus the inverse map derived from it. The keymapper reads the forward map to
pre-correct incoming keycodes for combo matching on non-US layouts, and the
inverse map to de-correct matched output back to the active layout.

All keycodes here are kernel/evdev codes, matching the Key enum (which mirrors
the kernel input header). XKB keycodes are offset by +8 from these; that offset
is the analyzer's concern and is resolved before the map ever reaches this
module, so nothing here applies or reasons about it.

The maps are swapped wholesale on each layout change. Swaps arrive on the
detector's watcher thread while the keymapper loop reads on its own thread, so
installation is an atomic reference rebind (never an in-place mutation), which
is safe under CPython without a lock.
"""

__version__ = '20260607'

from .lib.logger import debug, warn, error
from .models.key import Key


_NO_LABEL = 'Layout name not provided'

_correction_map: 'dict[Key, Key]' = {}
_inverse_map: 'dict[Key, Key]' = {}
_correction_label: str = _NO_LABEL


def set_correction_map(correction_map: 'dict[int, int] | None', label: str = _NO_LABEL):
    """
    Install a new keycode correction map (atomic reference rebind).

    correction_map  - flat {physical_keycode: us_keycode} map of raw kernel
                      (evdev) keycodes, or empty/None for "nothing to correct"
                      (the common case on US-like layouts). The analyzer has
                      already resolved XKB's +8 keycode offset, and Key is
                      evdev-based, so the codes map straight to Key objects with
                      no adjustment here. The inverse {us: physical} is derived
                      from the forward map for output de-correction.
    label           - human-readable layout name, supplied by the detection side
                      purely so the keymapper can name the active layout in its
                      own logs. Never parsed or acted on; the keymapper does no
                      layout reasoning.

    Called from the layout-detection coordinator's callback on the detector's
    watcher thread. A malformed map (a keycode not in the Key enum) is rejected
    whole and falls back to no correction, so a bad map never raises out onto
    the watcher thread.
    """
    global _correction_map
    global _inverse_map
    global _correction_label
    if not label:
        label = _NO_LABEL
    raw = correction_map or {}
    try:
        # Codes are kernel/evdev (the analyzer already removed XKB's +8 offset)
        # and Key is evdev-based, so they map straight across — do NOT offset here.
        forward = {Key(in_code): Key(out_code) for in_code, out_code in raw.items()}
    except ValueError as key_err:
        error(f"Correction map for '{label}' has an undefined keycode "
              f"({key_err}); disabling correction for this layout. raw={raw}")
        forward = {}
    inverse = {out_key: in_key for in_key, out_key in forward.items()}
    if len(inverse) != len(forward):
        warn(f"Correction map for '{label}' is not one-to-one; output "
             f"de-correction may be wrong. raw={raw}")
    _correction_map = forward
    _inverse_map = inverse
    _correction_label = label
    debug(f"Correction map installed for '{label}': "
          f"{len(forward)} entries: {forward}")


def correct_key_for_match(key: Key) -> Key:
    """Forward: map a physical key to its US-positional Key for combo matching.
    Returns the key unchanged when it has no correction entry."""
    return _correction_map.get(key, key)


def decorrect_key_for_output(key: Key) -> Key:
    """Inverse: map a US-positional output key to its active-layout Key so a
    matched remap renders the intended symbol. Unchanged when not in the map."""
    return _inverse_map.get(key, key)


def get_correction_map() -> 'dict[Key, Key]':
    """Return the currently installed forward correction map (live reference)."""
    return _correction_map


# End of file #
