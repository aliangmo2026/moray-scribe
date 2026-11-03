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
    assert __version__ == "0.5.0"
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


def test_graph_accepts_include_exclude(
    sample_project_path: Path, tmp_path: Path
) -> None:
    """`graph --exclude` filters files (v0.5: parity with scan/doc; CI needs it)."""
    import json

    out_dir = tmp_path / "g"
    result = _invoke(
        "graph", str(sample_project_path),
        "--out", str(out_dir),
        "--exclude", "**/service.py",
        "--exclude", "**/calls.py",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads((out_dir / "pruned-graph.json").read_text(encoding="utf-8"))
    modules = {node["module"].rsplit(".", 1)[-1] for node in payload["nodes"].values()}
    assert "service" not in modules
    assert "calls" not in modules
    assert "core" in modules


def test_check_accepts_include_exclude(tmp_path: Path) -> None:
    """`check` builds its current graph honoring --exclude when --current is absent."""
    from tests.test_cli_check import _node_payload, _write_graph

    project = tmp_path / "proj"
    project.mkdir()
    (project / "__init__.py").write_text("", encoding="utf-8")
    (project / "app.py").write_text("def runner():\n    return helper()\n\n\n"
                                    "def helper():\n    return 1\n", encoding="utf-8")
    (project / "skipme.py").write_text("def lonely():\n    pass\n", encoding="utf-8")

    baseline = _write_graph(
        tmp_path / "baseline.json",
        [_node_payload("proj.app.runner", 0.6), _node_payload("proj.app.helper", 0.3)],
        [("proj.app.runner", "proj.app.helper")],
    )
    report = tmp_path / "report.md"

    result = _invoke(
        "check", str(project),
        "--baseline", str(baseline),
        "--report", str(report),
        "--fail-on", "none",
        "--exclude", "**/skipme.py",
    )

    assert result.exit_code == 0, result.output
    body = report.read_text(encoding="utf-8")
    # skipme.py's lonely() never entered the graph -> not flagged as new dead.
    assert "lonely" not in body
