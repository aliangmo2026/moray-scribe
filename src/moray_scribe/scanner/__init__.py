"""Source discovery and symbol extraction."""

from __future__ import annotations

from .extractor import extract_file, extract_source
from .walker import iter_python_files

__all__ = ["extract_file", "extract_source", "iter_python_files"]
