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
