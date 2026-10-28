"""Sample domain core used to exercise symbol extraction."""

from __future__ import annotations


class Engine:
    """Minimal engine with one method call site."""

    def __init__(self, name: str) -> None:
        self.name = name

    def start(self) -> str:
        """Return a startup banner."""
        return f"{self.name}:started"

    def run(self) -> str:
        """Run and return the status line."""
        return self.start()


def build_engine(name: str = "default") -> Engine:
    """Construct an :class:`Engine`."""
    return Engine(name)
