"""Sample service layer that depends on the core module."""

from __future__ import annotations

from .core import Engine, build_engine


class Service:
    """Coordinates work through the core engine."""

    def __init__(self) -> None:
        self.engine: Engine = Engine("service")
        self.backup: Engine = build_engine("backup")

    def run(self) -> str:
        """Start the engine and return its status line."""
        return self.engine.run()
