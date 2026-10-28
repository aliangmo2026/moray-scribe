"""Sample call patterns for the scanner."""

from __future__ import annotations

from .core import Engine, build_engine


def run_pipeline() -> str:
    """Call a factory and a method through attributes."""
    engine: Engine = build_engine("pipeline")
    return engine.start()


def chained() -> str:
    """Call a method on a temporary result."""
    obj = build_engine("chain")
    return obj.start().upper()


class Harness:
    """Wrap an engine and trigger it through an attribute chain."""

    def __init__(self) -> None:
        self.engine: Engine = build_engine("harness")

    def dispatch(self) -> str:
        """Run the stored engine through self."""
        return self.engine.run()


MODULE_BANNER = run_pipeline()
