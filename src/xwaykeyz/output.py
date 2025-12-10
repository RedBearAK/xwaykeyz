import time
from evdev import ecodes
from evdev.uinput import UInput

from .lib.logger import debug
from .models.action import PRESS, RELEASE, Action
from .models.combo import Combo
from .models.key import Key
from .models.modifier import Modifier
from .config_api import _THROTTLES


VIRT_DEVICE_PREFIX = "XWayKeyz (virtual)"

# Remove all buttons so udev doesn't think virtual keyboard is a joystick
_KEYBOARD_KEYS: set = ecodes.keys.keys() - ecodes.BTN

# But we want mouse buttons, so let's enumerate those and add them
# back into the set of buttons we'll watch and use
_MOUSE_BUTTONS = {
    256: ["BTN_0", "BTN_MISC"],
    257: "BTN_1",
    258: "BTN_2",
    259: "BTN_3",
    260: "BTN_4",
    261: "BTN_5",
    262: "BTN_6",
    263: "BTN_7",
    264: "BTN_8",
    265: "BTN_9",
    272: ["BTN_LEFT", "BTN_MOUSE"],
    273: "BTN_RIGHT",
    274: "BTN_MIDDLE",
    275: "BTN_SIDE",
    276: "BTN_EXTRA",
}
_KEYBOARD_KEYS.update(_MOUSE_BUTTONS)

# We also want trackpad/touchpad buttons, so let's enumerate those and add them
# back into the set of buttons we'll watch and use
_TOUCHPAD_BUTTONS = {
    325: "BTN_TOOL_FINGER",                                     # event seen as finger touch
    330: ["BTN_TOUCH", "BTN_TOUCHPAD"],                         # touchpad being touched
    333: "BTN_TOOL_DOUBLETAP",                                  # touchpad double tap
    334: "BTN_TOOL_TRIPLETAP",                                  # touchpad triple tap
    335: "BTN_TOOL_QUADTAP",                                    # touchpad quadruple tap
    336: "BTN_TOOL_QUINTTAP",                                   # touchpad quintuple tap
    337: "BTN_TOOL_TRIPLETAP2",                                 # second triple tap on a touchpad
    338: "BTN_TOOL_QUADTAP2",                                   # second quadruple tap on a touchpad
    339: "BTN_TOOL_QUINTTAP2",                                  # second quintuple tap on a touchpad
}
_KEYBOARD_KEYS.update(_TOUCHPAD_BUTTONS)

_uinput = None


# for use with throttle delays
def sleep_ms(msec):
    if msec == 0:
        return
    return time.sleep(msec / 1000)


def real_uinput():
    return UInput(
        name=f"{VIRT_DEVICE_PREFIX} Keyboard",
        events={
            ecodes.EV_KEY: _KEYBOARD_KEYS,
            ecodes.EV_REL: set([0, 1, 6, 8, 9]),
        },
    )


# TODO: improve injection?
def setup_uinput(uinput=None):
    global _uinput
    _uinput = uinput or real_uinput()


# Throttle delay minimums defined here are calculated to hopefully
# avoid interfering with most human-achievable normal typing speeds,
# minimizing the risk of causing input buffering while maintaining
# some minimal temporal separation between all keystroke events,
# even in the case of the user having their main throttle delay
# values set to zero in their config. Or missing, defaulting to
# zero. Having actual zero delays between key action events causes
# too many problems, may be partly responsible for some of the
# "stuck key" issues some users run into.
_THROTTLE_MIN_PRE_MS = 1
_THROTTLE_MIN_POST_MS = 2


class Output:
    def __init__(self):
        self._pressed_modifier_keys     = set()
        self._pressed_keys              = set()
        self._suspended_mod_keys        = []
        self._suspend_depth             = 0
        self._last_output_for_cache     = None

    def __update_pressed_modifier_keys(self, key, action):
        if not isinstance(action, Action):
            raise TypeError(f'Expected type Action, received {type(action)}.')

        if not Modifier.is_key_modifier(key):
            return

        # Changing is_pressed to use property decorator, for consistency
        if action.is_pressed:
            self._pressed_modifier_keys.add(key)
        else:
            self._pressed_modifier_keys.discard(key)

    def __update_pressed_keys(self, key, action):
        if not isinstance(action, Action):
            raise TypeError(f'Expected type Action, received {type(action)}.')

        # Changing is_pressed to use property decorator, for consistency
        if action.is_pressed:
            self._pressed_keys.add(key)
        else:
            self._pressed_keys.discard(key)

    def diag(self):
        print("*** OUTPUT ***")
        print("_pressed_modifier_keys:")
        print(self._pressed_modifier_keys)
        print("_pressed_keys")
        print(self._pressed_keys)
        print("_suspended_mod_keys")
        print(self._suspended_mod_keys)
        print("_suspend_depth", self._suspend_depth)

    def __send_sync(self):
        _uinput.syn()

    def is_mod_pressed(self, key):
        return key in self._pressed_modifier_keys

    # Renamed from "is_pressed" to reduce naming 
    # redundancy with Action, Keystate properties.
    def is_key_pressed(self, key):
        return key in self._pressed_keys

    def send_event(self, event):
        _uinput.write_event(event)
        # TODO: do we need this? I think not.
        # self.__send_sync()

    def _cache_output(self, output_type: str, data):
        """
        Record output for repeat caching. Called by send methods.
        Only records the FIRST output (first-write-wins) to prevent
        send_combo() internal calls from overwriting the combo tracking.
        """
        if self._last_output_for_cache is None:
            self._last_output_for_cache = (output_type, data)

    def clear_cache_tracking(self):
        """Clear the output cache tracking. Call at start of each event."""
        self._last_output_for_cache = None

    def send_key_action(self, key, action):
        if not isinstance(action, Action):
            raise TypeError(f'Expected type Action, received {type(action)}.')

        # Track for cache - only track PRESS actions for passthrough
        if action.just_pressed:
            self._cache_output('passthrough', (key, action))

        sleep_ms(_THROTTLES['key_pre_delay_ms'] + _THROTTLE_MIN_PRE_MS)

        self.__update_pressed_modifier_keys(key, action)
        self.__update_pressed_keys(key, action)
        _uinput.write(ecodes.EV_KEY, key, action)

        mod_name = Modifier.get_modifier_name(key)
        mod_suffix = f" ({mod_name} mod)" if mod_name else ""
        debug(action, f"{key}{mod_suffix}", time.time(), ctx="OO")

        self.__send_sync()

        sleep_ms(_THROTTLES['key_post_delay_ms'] + _THROTTLE_MIN_POST_MS)

        # Visual terminator when all output keys are released
        if action.is_released and len(self._pressed_keys) == 0:
            debug("──────────", ctx="==")

    def send_key_action_fast(self, key, action):
        if not isinstance(action, Action):
            raise TypeError(f'Expected type Action, received {type(action)}.')

        # Track for cache - only track PRESS actions for passthrough
        if action.just_pressed:
            self._cache_output('passthrough', (key, action))

        sleep_ms(_THROTTLE_MIN_PRE_MS)

        self.__update_pressed_modifier_keys(key, action)
        self.__update_pressed_keys(key, action)
        _uinput.write(ecodes.EV_KEY, key, action)

        mod_name = Modifier.get_modifier_name(key)
        mod_suffix = f" ({mod_name} mod)" if mod_name else ""
        debug(action, f"{key}{mod_suffix}", time.time(), ctx="OO")

        self.__send_sync()

        sleep_ms(_THROTTLE_MIN_POST_MS)

        # Visual terminator when all output keys are released
        if action.is_released and len(self._pressed_keys) == 0:
            debug("──────────", ctx="==")

    def send_combo(self, combo: Combo):

        # Track for cache
        self._cache_output('combo', combo)

        released_mod_keys       = []
        pressed_mod_keys        = []

        mod_keys_we_need_to_lift = self._pressed_modifier_keys.copy()
        mods_we_need_to_press = combo.modifiers.copy()
        for pressed_key in self._pressed_modifier_keys:
            for modifier in combo.modifiers:
                if pressed_key in modifier.get_keys():
                    # already held down, we don't need to press or lift
                    mod_keys_we_need_to_lift.remove(pressed_key)

                    # Fix to prevent KeyError exception when both left/right of same mod key used in
                    # the input combo, and generic form of that mod is/was in mods_we_need_to_press:
                    if modifier in mods_we_need_to_press:
                        mods_we_need_to_press.remove(modifier)
                    else:
                        debug(f"Skipping redundant removal of modifier: {modifier}")
                    # TODO: The above "fix" needs to be deeply examined for possible side effects. 

        # Moved throttle delays into send_key_action() above.

        for key in reversed(list(mod_keys_we_need_to_lift)):
            self.send_key_action(key, RELEASE)
            released_mod_keys.append(key)

        for key in [mod.get_key() for mod in mods_we_need_to_press]:
            self.send_key_action(key, PRESS)
            pressed_mod_keys.append(key)

        # normal key portion of the combo
        self.send_key_action(combo.key, PRESS)
        self.send_key_action(combo.key, RELEASE)

        for modifier in reversed(pressed_mod_keys):
            self.send_key_action(modifier, RELEASE)

        if self.__is_suspending():
            self._suspended_mod_keys.extend(released_mod_keys)
        else:
            for modifier in reversed(released_mod_keys):
                self.send_key_action(modifier, PRESS)

    def send_key(self, key):
        # Track for cache
        self._cache_output('key', key)

        self.send_combo(Combo(None, key))

    def shutdown(self):
        # raise all keys for shutdown so that we have a clean state
        # on uninput with any watching apps as we're exiting
        for key in self._pressed_keys.copy():
            self.send_key_action(key, RELEASE)
        for key in self._pressed_modifier_keys.copy():
            self.send_key_action(key, RELEASE)
        _uinput.close()

    # ─── SUSPEND ──────────────────────────────────────────────────────────────────

    # self._suspended_mod_keys : list
    # self._suspend_depth : int

    def suspend_when_lifting(self):
        return SuspendWhenLifting(self)

    def __is_suspending(self):
        return self._suspend_depth > 0

    def __reexert(self, key):
        self.send_key_action(key, PRESS)

    # the function that calls this is re-entrant so we need to make sure
    # we can suspend/resume to multiple depths without losing track of
    # where we are, though this SHOULD be more of a theoretical concern
    def allow_suspend(self):
        self._suspend_depth += 1

    def disallow_suspend(self):
        self._suspend_depth -= 1

        if not self.__is_suspending():
            for mod in self._suspended_mod_keys:
                self.__reexert(mod)
            self._suspended_mod_keys.clear()


class SuspendWhenLifting:
    """
    wraps the suspending pattern for output

    When output release keys it doesn't need for the current combo instead
    of re-exerting them immediately after it will hold them until it is
    unsuspended (which is currently immediately when a sequence ends)
    """

    def __init__(self, output):
        if not isinstance(output, Output):
            raise TypeError(f'Expected type Output, received {type(output)}.')
        self._output = output

    def __enter__(self):
        self._output.allow_suspend()
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self._output.disallow_suspend()
        return False
