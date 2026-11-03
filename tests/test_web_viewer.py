"""Tests for the dependency-free web viewer server (assets + source API)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from web_viewer.serve import ViewerState, _count_nodes, _graph_metadata


def _write_graph(path: Path, *, root: Path | None = None, nodes: int = 2) -> Path:
    metadata = {"project": "demo"}
    if root is not None:
        metadata["root"] = str(root)
    payload = {
        "nodes": {
            f"m.f{index}": {
                "id": f"m.f{index}",
                "name": f"f{index}",
                "kind": "function",
                "module": "m",
                "file": "m.py",
                "lineno": 1,
                "end_lineno": 2,
            }
            for index in range(nodes)
        },
        "edges": [],
        "metadata": metadata,
    }
    target = path / "pruned-graph.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


@pytest.fixture()
def project(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "project"
    source.mkdir()
    (source / "m.py").write_text(
        "\n".join(f"line {number}" for number in range(1, 21)), encoding="utf-8"
    )
    out = tmp_path / "out"
    out.mkdir()
    graph = _write_graph(out, root=source)
    return graph, source


def test_state_uses_stamped_root(project: tuple[Path, Path]) -> None:
    graph, source = project
    state = ViewerState(graph, None, None)

    assert state.source_root == source.resolve()
    assert state.project == "demo"
    assert state.nodes == 2


def test_state_explicit_source_root_wins(project: tuple[Path, Path], tmp_path: Path) -> None:
    graph, _source = project
    override = tmp_path / "override"
    override.mkdir()

    state = ViewerState(graph, override, "custom")

    assert state.source_root == override.resolve()
    assert state.project == "custom"


def test_state_resolves_relative_stamped_root(project: tuple[Path, Path]) -> None:
    """Committed demo graphs stamp a relative root (e.g. ``../../examples``)."""
    graph, source = project
    payload = json.loads(graph.read_text(encoding="utf-8"))
    payload["metadata"]["root"] = "../project"
    graph.write_text(json.dumps(payload), encoding="utf-8")

    state = ViewerState(graph, None, None)

    assert state.source_root == source.resolve()


def test_source_slice_returns_numbered_lines(project: tuple[Path, Path]) -> None:
    graph, _source = project
    state = ViewerState(graph, None, None)

    lines = state.source_slice("m.py", 3, 5)

    assert lines == ["3 | line 3", "4 | line 4", "5 | line 5"]


def test_source_slice_clamps_to_file(project: tuple[Path, Path]) -> None:
    graph, _source = project
    state = ViewerState(graph, None, None)

    lines = state.source_slice("m.py", 19, 999)

    assert lines == ["19 | line 19", "20 | line 20"]


def test_source_slice_rejects_traversal(project: tuple[Path, Path]) -> None:
    graph, _source = project
    state = ViewerState(graph, None, None)

    with pytest.raises(ValueError, match="escapes"):
        state.source_slice("../secret.py", 1, 2)


def test_source_slice_missing_file(project: tuple[Path, Path]) -> None:
    graph, _source = project
    state = ViewerState(graph, None, None)

    with pytest.raises(FileNotFoundError):
        state.source_slice("nope.py", 1, 2)


def test_graph_metadata_tolerates_bad_json() -> None:
    assert _graph_metadata("not json") == {}
    assert _graph_metadata('{"metadata": {"a": 1}}') == {"a": "1"}


def test_count_nodes() -> None:
    assert _count_nodes('{"nodes": {"a": {}, "b": {}}}') == 2
    assert _count_nodes("nope") == 0
