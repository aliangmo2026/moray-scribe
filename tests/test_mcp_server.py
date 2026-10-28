"""Tests for the MCP server: tool registration, summaries, slicing and hints."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from moray_scribe.mcp import app
from moray_scribe.mcp.server import (
    ToolHintError,
    scribe_graph,
    scribe_health,
    scribe_list_symbols,
    scribe_read_artifact,
    scribe_scan,
)


def _list_tools() -> list[Any]:
    return asyncio.run(app.list_tools())


def test_list_tools_returns_six() -> None:
    tools = _list_tools()

    assert len(tools) == 6
    names = {tool.name for tool in tools}
    assert names == {
        "scribe_health",
        "scribe_scan",
        "scribe_graph",
        "scribe_doc",
        "scribe_read_artifact",
        "scribe_list_symbols",
    }


def test_tool_descriptions_non_trivial() -> None:
    for tool in _list_tools():
        assert tool.description is not None
        assert len(tool.description) > 50, f"{tool.name} description too short"


def test_scribe_health_shape() -> None:
    result = scribe_health()

    assert "ollama_reachable" in result
    assert "model_available" in result
    assert "out_writable" in result
    assert isinstance(result["ollama_reachable"], bool)
    assert isinstance(result["model_available"], bool)
    assert isinstance(result["out_writable"], bool)


def test_scribe_scan_returns_summary(
    monkeypatch: pytest.MonkeyPatch,
    sample_project_path: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MORAY_SCRIBE_OUT_DIR", str(tmp_path))

    result = scribe_scan(str(sample_project_path))

    assert result["symbols_count"] > 0
    assert result["files_scanned"] > 0
    assert Path(result["out_path"]).name == "symbol-graph.json"
    assert Path(result["out_path"]).exists()


def test_scribe_graph_returns_hubs(
    monkeypatch: pytest.MonkeyPatch,
    sample_project_path: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MORAY_SCRIBE_OUT_DIR", str(tmp_path))

    result = scribe_graph(str(sample_project_path))

    assert result["nodes"] > 0
    assert result["edges"] >= 0
    assert "hubs_count" in result
    assert "dead_code_count" in result


def test_scribe_doc_uses_fake_client(
    monkeypatch: pytest.MonkeyPatch,
    sample_project_path: Path,
    tmp_path: Path,
    fake_ollama_client: Any,
) -> None:
    draft = json.dumps(
        {
            "title": "Arch",
            "summary": "A summary long enough to clear the fifty character minimum rule.",
            "diagrams": [
                {
                    "kind": "flowchart",
                    "title": "Overview",
                    "nodes": [{"id": "Core", "label": "Core", "kind": "module"}],
                    "edges": [],
                }
            ],
            "modules": [
                {
                    "module": "sample_project.core",
                    "summary": "Core.",
                    "symbol_ids": ["sample_project.core.Engine"],
                }
            ],
        }
    )
    client = fake_ollama_client([draft])
    monkeypatch.setattr("moray_scribe.pipeline.OllamaClient", lambda **kwargs: client)
    monkeypatch.setenv("MORAY_SCRIBE_OUT_DIR", str(tmp_path))

    result = _call_doc(sample_project_path)

    assert Path(result["md_path"]).name == "ARCHITECTURE.md"
    assert Path(result["md_path"]).exists()
    assert result["mmd_paths"]


def _call_doc(sample_project_path: Path) -> dict[str, Any]:
    from moray_scribe.mcp.server import scribe_doc

    return scribe_doc(str(sample_project_path))


def test_scribe_read_artifact_section_slice(tmp_path: Path) -> None:
    md = tmp_path / "ARCHITECTURE.md"
    md.write_text(
        "# Title\n\nintro\n\n## Core Hubs\n\nhub-one\nhub-two\n\n## Dead Code\n\ndead-one\n",
        encoding="utf-8",
    )

    chunk = scribe_read_artifact(str(md), section="Core Hubs")

    assert "hub-one" in chunk
    assert "hub-two" in chunk
    assert "## Core Hubs" in chunk
    assert "dead-one" not in chunk
    assert "intro" not in chunk


def test_scribe_read_artifact_size_limit(tmp_path: Path) -> None:
    md = tmp_path / "big.md"
    md.write_text("x" * 5000, encoding="utf-8")

    chunk = scribe_read_artifact(str(md), max_bytes=100)

    assert "[truncated at 100 bytes]" in chunk
    assert len(chunk) < 400


def test_scribe_list_symbols_pagination(
    tmp_path: Path,
    sample_graph: Any,
) -> None:
    graph_path = tmp_path / "pruned-graph.json"
    graph_path.write_text(sample_graph.model_dump_json(indent=2), encoding="utf-8")

    page = scribe_list_symbols(str(graph_path), offset=0, limit=3)
    assert page["limit"] == 3
    assert page["count"] == 3
    assert page["total"] >= 3

    second = scribe_list_symbols(str(graph_path), offset=3, limit=3)
    assert second["offset"] == 3
    first_ids = [s["id"] for s in page["symbols"]]
    second_ids = [s["id"] for s in second["symbols"]]
    assert set(first_ids).isdisjoint(second_ids)


def test_scribe_list_symbols_limit_rejected(tmp_path: Path, sample_graph: Any) -> None:
    graph_path = tmp_path / "pruned-graph.json"
    graph_path.write_text(sample_graph.model_dump_json(indent=2), encoding="utf-8")

    with pytest.raises(ToolHintError, match="hint") as excinfo:
        scribe_list_symbols(str(graph_path), limit=101)
    assert "100" in str(excinfo.value)


def test_error_has_hint(
    monkeypatch: pytest.MonkeyPatch,
    sample_project_path: Path,
    tmp_path: Path,
    fake_ollama_client: Any,
) -> None:
    client = fake_ollama_client([ConnectionError("connection refused")])
    monkeypatch.setattr("moray_scribe.pipeline.OllamaClient", lambda **kwargs: client)
    monkeypatch.setenv("MORAY_SCRIBE_OUT_DIR", str(tmp_path))

    with pytest.raises(ToolHintError) as excinfo:
        _call_doc(sample_project_path)

    assert "hint:" in str(excinfo.value)
    assert "Ollama" in str(excinfo.value)


def test_read_artifact_missing_path_has_hint(tmp_path: Path) -> None:
    with pytest.raises(ToolHintError, match="hint"):
        scribe_read_artifact(str(tmp_path / "nope.md"))
