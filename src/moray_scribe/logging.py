"""Loguru setup used by the CLI and pipeline entry points."""

from __future__ import annotations

import sys

from loguru import logger


def setup_logging(level: str = "INFO") -> None:
    """Configure a single stderr sink at ``level`` and return control."""
    logger.remove()
    logger.add(
        sys.stderr,
        level=level.upper(),
        backtrace=False,
        diagnose=False,
        enqueue=False,
    )
