"""Sample nesting patterns."""

from __future__ import annotations


def utility() -> str:
    """Module-level helper used by a nested function."""
    return "util"


class Outer:
    """Class with a nested class and a method-level function."""

    class Inner:
        """Nested class."""

        def reveal(self) -> str:
            """Inner method."""
            return "inner"

    def method(self) -> str:
        """Method with a nested helper."""

        def helper() -> str:
            """Nested function calling a module-level utility."""
            return utility()

        return helper()
