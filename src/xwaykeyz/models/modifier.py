import keyword
import re

from enum import EnumMeta
from ordered_set import OrderedSet
from typing import List

from .key import Key


def validate_new_key_name(name):
    """Validate the user-provided name for a new Key enum entry, for safety and usability."""

    # Check if name argument is a string
    if not isinstance(name, str):
        raise TypeError(f"'{name}' is not a valid string.")

    # Ensure it's not a Python keyword
    if keyword.iskeyword(name):
        raise ValueError(f"'{name}' is a reserved Python keyword and cannot be used.")

    # Ensure it follows identifier rules
    if not name.isidentifier():
        raise ValueError(f"'{name}' is not a valid Python identifier.")

    # Restrict to uppercase alphanumeric and underscore
    if not re.match(r'^[A-Z_][A-Z0-9_]*$', name):
        raise ValueError(f"'{name}' contains invalid characters or is not all-uppercase.")

    return name  # Return the validated name for further use


class Modifier:
    """represents a keyboard combo modifier, such as Shift or Cmd"""

    _BY_KEY = {}
    _MODIFIERS = {}
    _IDS = iter(range(100))

    def __init__(self, name, aliases, key=None, keys=None):
        cls = type(self)
        self._id = next(cls._IDS)
        self.name = name
        self.aliases = aliases
        keys = key or keys
        if isinstance(keys, Key):
            keys = [keys]
        self.keys = keys
        if len(self.keys) == 1:
            key = self.keys[0]
            if key in cls._BY_KEY:
                raise ValueError(
                    f"modifier {name} may not be assigned {key},"
                    " already assigned to another modifier"
                )
            cls._BY_KEY[key] = self
        if name in cls._MODIFIERS:
            raise ValueError(f"existing modifier named {name} already exists")
        cls._MODIFIERS[name] = self
        setattr(Modifier, name, self)

    def __str__(self):
        return self.aliases[0]

    def __repr__(self):
        return self.aliases[0] + f"<Key.{self.keys[0]}>"

    def __eq__(self, other):
        return self._id == other._id

    def __hash__(self):
        return self._id

    def is_specific(self):
        return len(self.keys) == 1

    def get_keys(self):
        return self.keys

    def get_key(self):
        return self.keys[0]

    def to_left(self):
        try:
            return getattr(Modifier, "L_" + self.name)
        except AttributeError:
            return None

    def to_right(self):
        try:
            return getattr(Modifier, "R_" + self.name)
        except AttributeError:
            return None

    @classmethod
    def from_key(cls, key):
        return cls._BY_KEY[key]

    @classmethod
    def all_aliases(cls):
        mods = cls._MODIFIERS.values()
        return [alias for mod in mods for alias in mod.aliases]

    @classmethod
    def is_key_modifier(cls, key):
        return key in cls._BY_KEY

    @classmethod
    def from_alias(cls, alias):
        for mod in cls._MODIFIERS.values():
            if alias in mod.aliases:
                return mod
        return None


# create all the default modifiers we ship with
Modifier("R_CONTROL", aliases=["RCtrl", "RC"], key=Key.RIGHT_CTRL)
Modifier("L_CONTROL", aliases=["LCtrl", "LC"], key=Key.LEFT_CTRL)
Modifier("CONTROL", aliases=["Ctrl", "C"], keys=[Key.LEFT_CTRL, Key.RIGHT_CTRL])
Modifier("R_ALT", aliases=["RAlt", "RA", "ROpt", "ROption"], key=Key.RIGHT_ALT)
Modifier("L_ALT", aliases=["LAlt", "LA", "LOpt", "LOption"], key=Key.LEFT_ALT)
Modifier("ALT", aliases=["Alt", "A", "Opt", "Option"], keys=[Key.LEFT_ALT, Key.RIGHT_ALT])
Modifier("R_SHIFT", aliases=["RShift"], key=Key.RIGHT_SHIFT)
Modifier("L_SHIFT", aliases=["LShift"], key=Key.LEFT_SHIFT)
Modifier("SHIFT", aliases=["Shift"], keys=[Key.LEFT_SHIFT, Key.RIGHT_SHIFT])
# purposely we do not have M, MA, or ML to give some distance from the fact
# that these use to be aliases for Alt, not Meta... they may come back in
# the future
Modifier(
    "R_META",
    aliases=["RSuper", "RWin", "RCommand", "RCmd", "RMeta"],
    key=Key.RIGHT_META,
)
Modifier(
    "L_META",
    aliases=["LSuper", "LWin", "LCommand", "LCmd", "LMeta"],
    key=Key.LEFT_META,
)
Modifier(
    "META",
    aliases=["Super", "Win", "Command", "Cmd", "Meta"],
    keys=[Key.LEFT_META, Key.RIGHT_META],
)

# Fn is either invisible to the OS (on some laptop hardware) or it's just a
# normal key, but as a normal key it likely should be flagged as a modifier
# based on how it's typically used
Modifier("FN", aliases=["Fn"], key=Key.KEY_FN)


class CompositeModifier:
    """
    Represents a composite modifier where a proxy key is replaced by
    a group of multiple keys (member keys) when processed.

    The member keys must all be valid Modifiers.
    """
    _COMPOSITE_MODIFIERS = {}
    _PROXY_KEYS = set()  # Fast lookup for proxy keys, enforcement of uniqueness

    def __init__(self, name: str, aliases: List[str], proxy_key: Key, member_keys: List[Key]):
        """
        Initialize a CompositeModifier.

        :param name: Unique name for the composite modifier.
        :param proxy_key: Key to use as the proxy for this modifier.
        :param member_keys: List of Key objects that make up the composite.
        :param aliases: List of string aliases for the modifier.
        """
        # Validate the name
        self.name = validate_new_key_name(name)

        # Validate the proxy key
        if not isinstance(proxy_key, Key):
            raise ValueError(f"Proxy key '{proxy_key}' must be a valid Key.")

        # Check if the proxy key is already in use
        if proxy_key in CompositeModifier._PROXY_KEYS:
            raise ValueError(f"Proxy key '{proxy_key}' already used in a CompositeModifier.")

        # Assign the proxy key and register it
        self.proxy_key = proxy_key
        CompositeModifier._PROXY_KEYS.add(proxy_key)

        # Define the proxy key as a Modifier
        self.modifier = Modifier(name, aliases, key=self.proxy_key)

        # Ensure all member keys are valid Modifiers
        for key in member_keys:
            if not Modifier.is_key_modifier(key):
                raise ValueError(f"CompositeModifier member Key '{key}' is not a Modifier.")
        self.member_keys = member_keys

        # Register this CompositeModifier
        if self.modifier in CompositeModifier._COMPOSITE_MODIFIERS:
            raise ValueError(f"CompositeModifier '{name}' already exists.")
        CompositeModifier._COMPOSITE_MODIFIERS[self.modifier] = self

    @classmethod
    def expand_composite_mods(cls, pressed_mods: List[Key]) -> List[Key]:
        """
        Apply all registered CompositeModifiers to a list of pressed keys.

        Ensures that the final list contains only unique keys.

        :param pressed_mods: List of pressed Keys that are Modifier objects.
        :return: A new list of Keys with composite proxies replaced by their member keys.
        """

        print(f"## ## ## ##  Pressed mods list: {pressed_mods = }")

        # Start with a unique set of pressed modifiers
        unique_pressed_mod_keys = set(pressed_mods)

        print(f"## ## ## ##  Before processing: {unique_pressed_mod_keys = }")

        # Iterate over all proxy keys
        for proxy_key in cls._PROXY_KEYS:
            if proxy_key in unique_pressed_mod_keys:
                # Type hint for VSCode syntax highlighting
                composite_mod: CompositeModifier = cls.get_composite_mod_from_proxy(proxy_key)
                if composite_mod:
                    unique_pressed_mod_keys.remove(proxy_key)  # Remove the proxy key
                    unique_pressed_mod_keys.update(composite_mod.member_keys)  # Add member keys

        print(f"## ## ## ##  After processing: {unique_pressed_mod_keys = }")

        return list(unique_pressed_mod_keys)  # Convert back to a list

    @classmethod
    def get_composite_mod_from_proxy(cls, proxy_key: Key):
        """
        Retrieve the CompositeModifier associated with a given proxy key.

        :param proxy_key: The proxy key to look up.
        :return: The CompositeModifier instance if found, otherwise None.
        """
        for composite_mod in cls._COMPOSITE_MODIFIERS.values():
            # Type annotation to get syntax highlighting on ".proxy_key" to work
            type_hinted_composite_mod: CompositeModifier = composite_mod
            if type_hinted_composite_mod.proxy_key == proxy_key:
                return composite_mod
        return None
