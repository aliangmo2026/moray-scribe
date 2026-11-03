"""End-to-end doc pipeline with the dynamic-inference stage (LLM mocked)."""

from __future__ import annotations

from pathlib import Path

import pytest

from moray_scribe.config import Settings
from moray_scribe.describer import InferenceReport, InferredEdge
from moray_scribe.pipeline import run_with_result


def _long() -> str:
    return "This summary is deliberately long enough to clear the fifty character limit."


def test_doc_with_inference_merges_and_renders(
    monkeypatch: pytest.MonkeyPatch,
    sample_project_path: Path,
    tmp_path: Path,
    fake_ollama_client: object,
) -> None:
    import json


    client = fake_ollama_client([json.dumps(_draft_payload())])  # type: ignore[operator]
    monkeypatch.setattr("moray_scribe.pipeline.OllamaClient", lambda **kwargs: client)

    def _fake_inference(graph, cfg, *, root_path, dynamic_pool, project_name=""):
        return InferenceReport(
            candidates_total=len(dynamic_pool),
            selected=1,
            requests=1,
            prompt_tokens=500,
            completion_tokens=40,
            merged=[
                InferredEdge(
                    src="sample_project.calls.Harness.dispatch",
                    dst="sample_project.core.Engine.run",
                    confidence="high",
                    reason="self.engine holds an Engine built in __init__",
                    callee="self.engine.run",
                )
            ],
            gaps=[
                InferredEdge(
                    src="sample_project.calls.Harness.__init__",
                    dst="UNKNOWN",
                    confidence="medium",
                    reason="factory return type unclear",
                    callee="build_engine",
                )
            ],
        )

    monkeypatch.setattr("moray_scribe.pipeline.run_inference_sync", _fake_inference)
    cfg = Settings(_env_file=None).model_copy(update={"inference_enabled": True})

    result = run_with_result("doc", sample_project_path, cfg, out_dir=tmp_path)

    assert result.summary["inference"]["merged"] == 1
    md = (tmp_path / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "## 十一、AI 动态推断【AI 推断】" in md
    assert "sample_project.core.Engine.run" in md
    assert "self.engine.run" in md
    assert "[AI推断·未合图]" in md
    assert "500(入) + 40(出)" in md

    flowchart = (tmp_path / "diagram-00-flowchart.mmd").read_text(encoding="utf-8")
    assert "-.->" in flowchart
    assert "[AI推断]" in flowchart
    assert "subgraph Legend" in flowchart
    assert "虚线 = AI 推断补全" in flowchart


def _draft_payload() -> dict[str, object]:
    return {
        "title": "Arch",
        "summary": _long(),
        "diagrams": [
            {
                "kind": "flowchart",
                "title": "Overview",
                "nodes": [
                    {
                        "id": "App",
                        "label": "App",
                        "kind": "module",
                        "symbol_ids": [
                            "sample_project.calls.Harness",
                            "sample_project.calls.Harness.dispatch",
                        ],
                    },
                    {
                        "id": "Core",
                        "label": "Core",
                        "kind": "module",
                        "symbol_ids": [
                            "sample_project.core.Engine",
                            "sample_project.core.Engine.run",
                        ],
                    },
                ],
                "edges": [],
            }
        ],
        "modules": [],
    }


def test_doc_without_inference_keeps_static_only(
    monkeypatch: pytest.MonkeyPatch,
    sample_project_path: Path,
    tmp_path: Path,
    fake_ollama_client: object,
) -> None:
    import json


    client = fake_ollama_client([json.dumps(_draft_payload())])  # type: ignore[operator]
    monkeypatch.setattr("moray_scribe.pipeline.OllamaClient", lambda **kwargs: client)
    cfg = Settings(_env_file=None)  # inference_enabled defaults to False

    result = run_with_result("doc", sample_project_path, cfg, out_dir=tmp_path)

    assert "inference" not in result.summary
    md = (tmp_path / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "未启用 AI 动态推断" in md
    flowchart = (tmp_path / "diagram-00-flowchart.mmd").read_text(encoding="utf-8")
    assert "-.->" not in flowchart


def test_describer_fallback_ladder_degrades(
    monkeypatch: pytest.MonkeyPatch,
    sample_project_path: Path,
    tmp_path: Path,
    fake_ollama_client: object,
) -> None:
    """Full-budget describer failure retries on a smaller view before giving up."""
    import json

    bad = '{"title": 123}'  # schema-invalid on every attempt
    good = json.dumps(_draft_payload())
    # max_nodes=30 -> ladder [30, 15] against the 53-node sample graph; one
    # attempt per rung: the 30-node view fails, the 15-node view succeeds.
    client = fake_ollama_client([bad, good])  # type: ignore[operator]
    monkeypatch.setattr("moray_scribe.pipeline.OllamaClient", lambda **kwargs: client)
    cfg = Settings(_env_file=None).model_copy(
        update={"max_retries": 0, "max_nodes": 30, "inference_enabled": True}
    )

    def _fake_inference(graph, cfg, *, root_path, dynamic_pool, project_name=""):
        return InferenceReport()

    monkeypatch.setattr("moray_scribe.pipeline.run_inference_sync", _fake_inference)
    # The sequence stage shares the same fake client; stub it out so the
    # call_log stays a pure describer-ladder measurement.
    monkeypatch.setattr(
        "moray_scribe.pipeline.describe_sequences",
        lambda chains, client, cfg, **kwargs: None,
    )

    result = run_with_result("doc", sample_project_path, cfg, out_dir=tmp_path)

    assert result.summary["describer"]["requests"] == 2
    assert len(client.backend.call_log) == 2  # type: ignore[attr-defined]
    md = (tmp_path / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "## 一、概览【AI 推理】" in md
