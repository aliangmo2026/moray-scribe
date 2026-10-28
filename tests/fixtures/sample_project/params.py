"""Sample parameter shapes."""

from __future__ import annotations


def full_shape(
    a: int,
    b: str = "x",
    /,
    c: float = 1.0,
    *args: int,
    d: bool,
    e: str = "e",
    **kwargs: object,
) -> int:
    """Every parameter shape at once."""
    return a + int(c) + len(args) + int(d)


def keyword_only(*, key: str) -> str:
    """Bare star marker before a keyword-only parameter."""
    return key
