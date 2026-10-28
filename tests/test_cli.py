"""CLI surface tests: help, version and the five advertised subcommands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from moray_scribe import __version__
from moray_scribe.cli import app

runner = CliRunner()


def _invoke(*args: str) -> Any:
    """Invoke the Typer app with deterministic help formatting."""
    return runner.invoke(app, list(args), env={"NO_COLOR": "1", "COLUMNS": "160"})


def test_help_exits_zero() -> None:
    result = _invoke("--help")

    assert result.exit_code == 0
    assert "moray-scribe" in result.output


def test_version_prints_package_version() -> None:
    result = _invoke("--version")

    assert result.exit_code == 0
    assert __version__ == "0.1.0"
    assert result.output.strip() == __version__


def test_help_lists_all_subcommands() -> None:
    result = _invoke("--help")

    assert result.exit_code == 0
    for command in ("scan", "graph", "diagram", "doc", "serve-mcp"):
        assert command in result.output


@pytest.mark.parametrize("command", ["scan", "graph", "diagram", "doc", "serve-mcp"])
def test_each_subcommand_help_supports_common_options(command: str) -> None:
    result = _invoke(command, "--help")

    assert result.exit_code == 0
    assert "--config" in result.output
    assert "--verbose" in result.output


def test_sample_project_fixture_is_complete(sample_project_path: Path) -> None:
    assert (sample_project_path / "__init__.py").is_file()
    assert (sample_project_path / "core.py").is_file()
    assert (sample_project_path / "service.py").is_file()


@pytest.mark.parametrize("command", ["doc", "diagram"])
def test_doc_and_diagram_accept_timeout_flag(command: str) -> None:
    result = _invoke(command, "--help")

    assert result.exit_code == 0
    assert "--timeout" in result.output


def test_timeout_flag_overrides_settings(
    monkeypatch: pytest.MonkeyPatch, sample_project_path: Path, tmp_path: Path
) -> None:
    """`doc --timeout 900` must reach the pipeline config, not just parse."""
    from moray_scribe import cli

    seen: dict[str, Any] = {}

    def fake_run(mode: Any, path: Any, cfg: Any, *, out_dir: Any = None) -> Any:
        seen["timeout"] = cfg.request_timeout_s
        target = tmp_path / "ARCHITECTURE.md"
        target.write_text("stub", encoding="utf-8")
        return target

    monkeypatch.setattr(cli, "run", fake_run)
    result = _invoke(
        "doc", str(sample_project_path), "--timeout", "900", "--out", str(tmp_path)
    )

    assert result.exit_code == 0
    assert seen["timeout"] == 900
