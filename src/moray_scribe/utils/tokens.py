"""Token estimation used to keep prompts inside a model's context window."""

from __future__ import annotations

import tiktoken


def estimate_tokens(text: str, *, model: str = "gpt-4o") -> int:
    """Estimate the token count of ``text`` for the given tokenizer family.

    Unknown model names fall back to the ``cl100k_base`` encoding rather than
    raising, so callers never need to guard against a ``KeyError``. Empty input
    is zero tokens.
    """
    if not text:
        return 0
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(text))


def fits_budget(text: str, *, max_tokens: int, model: str = "gpt-4o") -> bool:
    """Whether ``text`` stays within ``max_tokens`` for the given model."""
    return estimate_tokens(text, model=model) <= max_tokens


def truncate_to_tokens(text: str, *, max_tokens: int, model: str = "gpt-4o") -> str:
    """Cut ``text`` down to at most ``max_tokens``, preferring whole lines.

    Whole lines are kept while they fit; a single oversized line (or a fully
    consumed budget) falls back to a binary-searched character cut so the
    result never exceeds the budget. Empty output for empty input.
    """
    if max_tokens <= 0:
        return ""
    if not text or fits_budget(text, max_tokens=max_tokens, model=model):
        return text

    kept: list[str] = []
    total = 0
    for line in text.splitlines(keepends=True):
        cost = estimate_tokens(line, model=model)
        if total + cost > max_tokens:
            break
        kept.append(line)
        total += cost
    if kept:
        return "".join(kept)

    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid], model=model) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]
