"""Tests for run_with_result structured summaries and run() compatibility."""

from __future__ import annotations

from pathlib import Path

from moray_scribe.config import Settings
from moray_scribe.pipeline import run, run_with_result


def _cfg(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, out_dir=tmp_path / "out")


def test_scan_result_summary(sample_project_path: Path, tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)

    result = run_with_result("scan", sample_project_path, cfg)

    assert result.mode == "scan"
    assert result.out_path is not None
    assert result.summary["symbols_count"] > 0
    assert result.summary["files_scanned"] > 0
    assert "modules_count" in result.summary


def test_graph_result_summary(sample_project_path: Path, tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)

    result = run_with_result("graph", sample_project_path, cfg)

    assert result.summary["nodes"] > 0
    assert "edges" in result.summary
    assert "hubs_count" in result.summary
    assert "dead_code_count" in result.summary


def test_doc_result_has_md_path(
    monkeypatch,
    sample_project_path: Path,
    tmp_path: Path,
    fake_ollama_client,
) -> None:
    import json

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
    cfg = _cfg(tmp_path)

    result = run_with_result("doc", sample_project_path, cfg)

    assert result.out_path is not None
    assert Path(result.summary["md_path"]).name == "ARCHITECTURE.md"
    assert result.summary["diagrams_count"] >= 1
    assert result.summary["mmd_paths"]


def test_run_preserves_old_behavior(sample_project_path: Path, tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)

    run_path = run("scan", sample_project_path, cfg)
    result = run_with_result("scan", sample_project_path, cfg)

    assert run_path == result.out_path
