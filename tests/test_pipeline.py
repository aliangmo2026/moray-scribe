"""Tests for the scan / graph / diagram / doc pipeline modes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from moray_scribe.config import Settings
from moray_scribe.exceptions import DescriberError
from moray_scribe.pipeline import run

_LONG = "This summary is deliberately long enough to clear the fifty character minimum limit."


def _pipeline_draft_json() -> str:
    # The pipeline scans the fixtures' parent, so modules are package-qualified.
    return json.dumps(
        {
            "title": "Arch",
            "summary": _LONG,
            "diagrams": [
                {
                    "kind": "flowchart",
                    "title": "Overview",
                    "nodes": [{"id": "Core", "label": "Core", "kind": "module"}],
                    "edges": [],
                },
                {
                    "kind": "classDiagram",
                    "title": "Details",
                    "nodes": [{"id": "Eng", "label": "Eng", "kind": "class"}],
                    "edges": [],
                },
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


def test_pipeline_scan_writes_json(sample_project_path: Path, tmp_path: Path) -> None:
    cfg = Settings(_env_file=None)

    target = run("scan", sample_project_path, cfg, out_dir=tmp_path)

    assert target.name == "symbol-graph.json"
    assert target.exists()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert data
    assert {"module", "symbols", "imports"} <= set(data[0])


def test_pipeline_graph_writes_pruned(sample_project_path: Path, tmp_path: Path) -> None:
    cfg = Settings(_env_file=None)

    target = run("graph", sample_project_path, cfg, out_dir=tmp_path)

    assert target.name == "pruned-graph.json"
    assert target.exists()
    graph = json.loads(target.read_text(encoding="utf-8"))
    assert "nodes" in graph and "edges" in graph


def test_pipeline_diagram_uses_fake_client(
    monkeypatch: pytest.MonkeyPatch,
    sample_project_path: Path,
    tmp_path: Path,
    fake_ollama_client: Any,
) -> None:
    client = fake_ollama_client([_pipeline_draft_json()])
    monkeypatch.setattr("moray_scribe.pipeline.OllamaClient", lambda **kwargs: client)
    cfg = Settings(_env_file=None)

    target = run("diagram", sample_project_path, cfg, out_dir=tmp_path)

    assert target.name == "architecture-draft.json"
    assert target.exists()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["title"] == "Arch"
    assert len(client.backend.call_log) == 1


def test_pipeline_doc_writes_architecture_md(
    monkeypatch: pytest.MonkeyPatch,
    sample_project_path: Path,
    tmp_path: Path,
    fake_ollama_client: Any,
) -> None:
    client = fake_ollama_client([_pipeline_draft_json()])
    monkeypatch.setattr("moray_scribe.pipeline.OllamaClient", lambda **kwargs: client)
    cfg = Settings(_env_file=None)

    target = run("doc", sample_project_path, cfg, out_dir=tmp_path)

    assert target.name == "ARCHITECTURE.md"
    content = target.read_text(encoding="utf-8")
    assert "## 一、概览【AI 推理】" in content
    assert "## 四、核心枢纽【引擎统计】" in content
    assert "## 七、已知缺口" in content


def test_pipeline_doc_writes_mmd_per_diagram(
    monkeypatch: pytest.MonkeyPatch,
    sample_project_path: Path,
    tmp_path: Path,
    fake_ollama_client: Any,
) -> None:
    client = fake_ollama_client([_pipeline_draft_json()])
    monkeypatch.setattr("moray_scribe.pipeline.OllamaClient", lambda **kwargs: client)
    cfg = Settings(_env_file=None)

    run("doc", sample_project_path, cfg, out_dir=tmp_path)

    flowchart = tmp_path / "diagram-00-flowchart.mmd"
    class_diagram = tmp_path / "diagram-01-classDiagram.mmd"
    assert flowchart.is_file()
    assert class_diagram.is_file()
    assert flowchart.read_text(encoding="utf-8").startswith("flowchart")
    assert class_diagram.read_text(encoding="utf-8").startswith("classDiagram")


def test_pipeline_doc_no_ollama_raises(
    monkeypatch: pytest.MonkeyPatch,
    sample_project_path: Path,
    tmp_path: Path,
    fake_ollama_client: Any,
) -> None:
    client = fake_ollama_client([ConnectionError("connection refused")])
    monkeypatch.setattr("moray_scribe.pipeline.OllamaClient", lambda **kwargs: client)
    cfg = Settings(_env_file=None)

    with pytest.raises(DescriberError, match="not reachable"):
        run("doc", sample_project_path, cfg, out_dir=tmp_path)


def test_pipeline_doc_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    sample_project_path: Path,
    tmp_path: Path,
    fake_ollama_client: Any,
) -> None:
    draft = _pipeline_draft_json()
    client = fake_ollama_client([draft, draft])
    monkeypatch.setattr("moray_scribe.pipeline.OllamaClient", lambda **kwargs: client)
    cfg = Settings(_env_file=None)
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"

    run("doc", sample_project_path, cfg, out_dir=out_a)
    run("doc", sample_project_path, cfg, out_dir=out_b)

    md_a = hashlib.sha256((out_a / "ARCHITECTURE.md").read_bytes()).hexdigest()
    md_b = hashlib.sha256((out_b / "ARCHITECTURE.md").read_bytes()).hexdigest()
    assert md_a == md_b

    mmd_a = hashlib.sha256(
        (out_a / "diagram-00-flowchart.mmd").read_bytes()
    ).hexdigest()
    mmd_b = hashlib.sha256(
        (out_b / "diagram-00-flowchart.mmd").read_bytes()
    ).hexdigest()
    assert mmd_a == mmd_b


def test_pipeline_scan_uses_sibling_out_by_default(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    cfg = Settings(_env_file=None)

    target = run("scan", pkg, cfg)

    # resolve_scan_root(pkg) -> tmp_path, so the default out dir is tmp_path/out.
    assert target == tmp_path / "out" / "symbol-graph.json"
    assert target.exists()


def test_pipeline_doc_stats_include_pruned_out_symbols(
    monkeypatch: pytest.MonkeyPatch,
    sample_project_path: Path,
    tmp_path: Path,
    fake_ollama_client: Any,
) -> None:
    """Appendix stats must reflect the FULL graph, not the pruned subset."""
    client = fake_ollama_client([_pipeline_draft_json()])
    monkeypatch.setattr("moray_scribe.pipeline.OllamaClient", lambda **kwargs: client)
    monkeypatch.setenv("MORAY_SCRIBE_OUT_DIR", str(tmp_path))
    # Prune hard so pruned.nodes << full.nodes; stats should still use full.
    monkeypatch.setenv("MORAY_SCRIBE_MAX_NODES", "10")

    run("doc", sample_project_path, Settings(_env_file=None))

    md = (tmp_path / "ARCHITECTURE.md").read_text(encoding="utf-8")
    # The full sample_project graph spans 10 files; a 10-node pruned graph has fewer.
    assert "| 分析文件数 | 10 |" in md
