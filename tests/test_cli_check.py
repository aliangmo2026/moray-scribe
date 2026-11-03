"""CLI surface tests for `moray-scribe check` (CI arch-check)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from moray_scribe.cli import app

runner = CliRunner()


def _invoke(*args: str) -> Any:
    return runner.invoke(app, list(args), env={"NO_COLOR": "1", "COLUMNS": "160"})


def _node_payload(node_id: str, pagerank: float) -> dict[str, object]:
    return {
        "id": node_id,
        "name": node_id.rsplit(".", 1)[-1],
        "qualname": node_id,
        "kind": "function",
        "module": node_id.split(".")[0],
        "file": f"{node_id.split('.')[0]}.py",
        "lineno": 1,
        "end_lineno": 2,
        "pagerank": pagerank,
    }


def _write_graph(
    path: Path,
    nodes: list[dict[str, object]],
    edges: list[tuple[str, str]],
) -> Path:
    payload = {
        "nodes": {str(node["id"]): node for node in nodes},
        "edges": [
            {"source": src, "target": dst, "kind": "calls", "weight": 1.0}
            for src, dst in edges
        ],
        "metadata": {"project": "demo"},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture()
def baseline(tmp_path: Path) -> Path:
    return _write_graph(
        tmp_path / "baseline.json",
        [
            _node_payload("m.runner", 0.6),
            _node_payload("m.helper", 0.3),
        ],
        [("m.runner", "m.helper")],
    )


@pytest.fixture()
def current_clean(tmp_path: Path) -> Path:
    return _write_graph(
        tmp_path / "current-clean.json",
        [
            _node_payload("m.runner", 0.6),
            _node_payload("m.helper", 0.3),
        ],
        [("m.runner", "m.helper")],
    )


def test_check_passes_on_identical_graphs(
    tmp_path: Path, baseline: Path, current_clean: Path
) -> None:
    result = _invoke(
        "check", str(tmp_path),
        "--baseline", str(baseline),
        "--current", str(current_clean),
        "--fail-on", "dead",
    )

    assert result.exit_code == 0, result.output
    assert "未发现新增死代码" in result.output or "0" in result.output


def test_check_fails_on_new_dead_code(
    tmp_path: Path, baseline: Path
) -> None:
    current = _write_graph(
        tmp_path / "current-dead.json",
        [
            _node_payload("m.runner", 0.6),
            _node_payload("m.helper", 0.3),
            _node_payload("m.orphan", 0.0),
        ],
        [("m.runner", "m.helper")],
    )
    report_path = tmp_path / "report.md"

    result = _invoke(
        "check", str(tmp_path),
        "--baseline", str(baseline),
        "--current", str(current),
        "--report", str(report_path),
        "--fail-on", "dead",
    )

    assert result.exit_code == 1
    assert "m.orphan" in result.output
    assert report_path.is_file()
    assert "新增死代码" in report_path.read_text(encoding="utf-8")


def test_check_fail_on_hub(tmp_path: Path, baseline: Path) -> None:
    current = _write_graph(
        tmp_path / "current-hub.json",
        [
            _node_payload("m.runner", 0.1),
            _node_payload("m.helper", 0.3),
        ],
        [("m.runner", "m.helper")],
    )

    hub_result = _invoke(
        "check", str(tmp_path),
        "--baseline", str(baseline),
        "--current", str(current),
        "--fail-on", "hub",
        "--hub-top", "2",
    )
    assert hub_result.exit_code == 1
    assert "m.runner" in hub_result.output

    dead_result = _invoke(
        "check", str(tmp_path),
        "--baseline", str(baseline),
        "--current", str(current),
        "--fail-on", "dead",
        "--hub-top", "2",
    )
    assert dead_result.exit_code == 0


def test_check_fail_on_none_always_exits_zero(
    tmp_path: Path, baseline: Path
) -> None:
    current = _write_graph(
        tmp_path / "current-dead.json",
        [_node_payload("m.runner", 0.6), _node_payload("m.orphan", 0.0)],
        [],
    )

    result = _invoke(
        "check", str(tmp_path),
        "--baseline", str(baseline),
        "--current", str(current),
        "--fail-on", "none",
    )

    assert result.exit_code == 0
    assert "m.orphan" in result.output


def test_check_missing_baseline_errors(tmp_path: Path) -> None:
    result = _invoke(
        "check", str(tmp_path),
        "--baseline", str(tmp_path / "nope.json"),
        "--current", str(tmp_path / "also-nope.json"),
        "--fail-on", "none",
    )

    assert result.exit_code != 0
    assert "baseline" in result.output.lower()


def test_check_builds_current_graph_when_not_given(
    tmp_path: Path, baseline: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "__init__.py").write_text("", encoding="utf-8")
    (project / "app.py").write_text(
        "def runner():\n    return helper()\n\n"
        "def helper():\n    return 1\n",
        encoding="utf-8",
    )

    result = _invoke(
        "check", str(project),
        "--baseline", str(baseline),
        "--fail-on", "none",
        "--max-nodes", "50",
    )

    assert result.exit_code == 0, result.output
    assert "runner" in result.output


def test_check_help_lists_flags() -> None:
    result = _invoke("check", "--help")

    assert result.exit_code == 0
    for flag in ("--baseline", "--current", "--fail-on", "--hub-top", "--report"):
        assert flag in result.output
