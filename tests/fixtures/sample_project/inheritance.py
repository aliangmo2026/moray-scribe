"""Sample inheritance patterns."""

from __future__ import annotations


class Animal:
    """Base animal with a voice."""

    def __init__(self, name: str) -> None:
        self.name = name

    def speak(self) -> str:
        """Make a generic sound."""
        return "..."


class Dog(Animal):
    """Single inheritance with an override."""

    def __init__(self, name: str) -> None:
        super().__init__(name)

    def speak(self) -> str:
        """Override with a bark."""
        return "woof"


class Puppy(Dog, Animal):
    """Multiple inheritance, delegating to the parent."""

    def speak(self) -> str:
        """Delegate upward."""
        return super().speak()
