"""Tests for Settings, especially max_output_tokens."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from moray_scribe.config import Settings


def test_max_output_tokens_default() -> None:
    assert Settings(_env_file=None).max_output_tokens == 4096


def test_max_output_tokens_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORAY_SCRIBE_MAX_OUTPUT_TOKENS", "8192")

    assert Settings(_env_file=None).max_output_tokens == 8192


def test_max_output_tokens_lower_bound() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_output_tokens=100)


def test_max_output_tokens_upper_bound() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_output_tokens=99999)


def test_default_request_timeout_is_600() -> None:
    assert Settings(_env_file=None).request_timeout_s == 600


def test_request_timeout_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORAY_SCRIBE_REQUEST_TIMEOUT_S", "900")

    assert Settings(_env_file=None).request_timeout_s == 900


def test_request_timeout_lower_bound() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, request_timeout_s=30)
