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
    Creates a new invented Key and a Modifier that will be replaced by
    a group of multiple Keys when used in Combos.

    The group of multiple Keys must all be validatable as modifiers.
    """
    _COMPOSITE_MODIFIERS = {}

    def __init__(self, name: str, aliases: List[str], member_keys: List[Key]):
        """
        Initialize a CompositeModifier.

        :param name: Unique name for the composite modifier.
        :param aliases: List of string aliases for the modifier.
        :param member_keys: List of Key objects that make up the composite.
        """
        # Validate the name
        self.name = validate_new_key_name(name)

        # Generate a unique value for the new Key
        unique_new_enum_value = max(key.value for key in Key) + 1

        # Safely add the new Key to the Key enum
        if name not in Key.__members__:
            add_key_to_enum(Key, self.name, unique_new_enum_value)

        # Define the new Key as a Modifier
        self.invented_key = Key[self.name]
        self.modifier = Modifier(name, aliases, key=self.invented_key)

        # Ensure all replacements are valid Modifiers
        for key in member_keys:
            if not Modifier.is_key_modifier(key):
                raise ValueError(f"Key '{key}' is not associated with a Modifier.")
        self.member_keys = member_keys

        # Register this CompositeModifier
        if self.modifier in CompositeModifier._COMPOSITE_MODIFIERS:
            raise ValueError(f"CompositeModifier '{name}' already exists.")
        CompositeModifier._COMPOSITE_MODIFIERS[self.modifier] = self

    def decompose_composite_mod(self, combo):
        """
        Replace a CompositeModifier artificial Key alias with its member Key aliases.

        :param combo: The Combo object to process.
        :return: A new Combo with the composite modifier replaced.
        """
        from .combo import Combo  # Deferred import to avoid circular import
        if self.modifier in combo.modifiers:
            mods_in_combo = OrderedSet(combo.modifiers)
            mods_in_combo.discard(self.modifier)
            mods_in_combo.update(self.member_keys)
            return Combo(mods_in_combo, combo.key)
        return combo

    @classmethod
    def is_composite_modifier(cls, modifier: Modifier) -> bool:
        """
        Check if a Modifier is a CompositeModifier.

        :param modifier: The Modifier to check.
        :return: True if the Modifier is a CompositeModifier, False otherwise.
        """
        return modifier in cls._COMPOSITE_MODIFIERS

    @classmethod
    def get_composite(cls, modifier: Modifier):
        """
        Retrieve the CompositeModifier for a given Modifier, if it exists.

        :param modifier: The Modifier to look up.
        :return: The CompositeModifier instance or None if not found.
        """
        return cls._COMPOSITE_MODIFIERS.get(modifier)


def add_key_to_enum(enum_cls: EnumMeta, name: str, value: int):
    """
    Dynamically add a new Key to an Enum.

    :param enum_cls: The Enum class to modify.
    :param name: The name of the new Key.
    :param value: The value of the new Key.
    """
    if not isinstance(enum_cls, EnumMeta):
        raise TypeError("Provided class is not an Enum.")

    if name in enum_cls.__members__:
        existing_value = enum_cls[name].value
        if existing_value == value:
            return  # The Key already exists with the same value, no need to add
        raise ValueError(
            f"Key '{name}' already exists with a different value ({existing_value})."
        )

    # Safely add the new member
    temp_dict = {**enum_cls.__members__}
    temp_dict[name] = value
    temp_enum = EnumMeta(enum_cls.__name__, enum_cls.__bases__, temp_dict)

    typed_temp_enum: EnumMeta = temp_enum

    # Overwrite the original Enum class with the updated one
    enum_cls._member_map_       = typed_temp_enum._member_map_
    enum_cls._value2member_map_ = typed_temp_enum._value2member_map_
