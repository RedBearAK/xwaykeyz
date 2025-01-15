class Modmap:
    def __init__(self, name, mappings, when=None):
        self.name = name
        self.mappings = mappings
        self.conditional = when

    def __contains__(self, key):
        return key in self.mappings

    def __getitem__(self, item):
        return self.mappings[item]


class MultiModmap:
    def __init__(self, name, mappings, when=None):
        self.name = name
        self.mappings = mappings
        self.conditional = when

    def __contains__(self, key):
        return key in self.mappings

    def __getitem__(self, item):
        return self.mappings[item]

    def items(self):
        return self.mappings.items()


class CompositeModmap:
    def __init__(self, name, mappings, when=None):
        """
        Create a composite modmap.

        :param name: The name of the composite modmap.
        :param mappings: A dictionary where each proxy key maps to a list of member keys.
        :param when: Optional condition for when the composite modmap applies.
        """
        self.name = name
        self.mappings = mappings
        self.conditional = when

    def __contains__(self, key):
        """Check if a proxy key is in the composite modmap."""
        return key in self.mappings

    def __getitem__(self, key):
        """Get the member keys for a given proxy key."""
        return self.mappings[key]

    def items(self):
        """Iterate over proxy keys and their corresponding member keys."""
        return self.mappings.items()
