"""Unit tests for token estimation."""

from __future__ import annotations

from moray_scribe.utils.tokens import estimate_tokens, fits_budget


def test_estimate_tokens_empty() -> None:
    assert estimate_tokens("") == 0


def test_estimate_tokens_monotone() -> None:
    short = estimate_tokens("hello")
    long = estimate_tokens("hello world this is a longer sentence")

    assert short > 0
    assert long > short


def test_estimate_tokens_returns_positive_for_text() -> None:
    assert estimate_tokens("hello world") > 0


def test_estimate_tokens_fallback_model() -> None:
    # An unknown model must not raise; it falls back to cl100k_base.
    assert estimate_tokens("hello", model="nonexistent-model-xyz") > 0


def test_fits_budget_true_and_false() -> None:
    assert fits_budget("hi", max_tokens=100) is True
    assert fits_budget("word " * 100, max_tokens=5) is False
