"""Exception hierarchy shared by every moray-scribe stage."""

from __future__ import annotations

__all__ = [
    "DescriberError",
    "GraphError",
    "RenderError",
    "ScanError",
    "SchemaError",
    "ScribeError",
]


class ScribeError(Exception):
    """Base class for all expected moray-scribe failures."""


class ScanError(ScribeError):
    """Raised when source discovery or extraction fails."""


class GraphError(ScribeError):
    """Raised when graph construction or analysis fails."""


class DescriberError(ScribeError):
    """Raised when the Ollama describer cannot produce a valid draft."""


class RenderError(ScribeError):
    """Raised when Mermaid or Markdown rendering fails."""


class SchemaError(ScribeError):
    """Raised when a cross-layer payload violates an invariant."""
