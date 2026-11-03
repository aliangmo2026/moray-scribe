"""End-to-end doc pipeline with the sequence-diagram stage (LLM mocked)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from moray_scribe.config import Settings
from moray_scribe.describer.sequencer import SequenceOutcome, engine_sequence_flow
from moray_scribe.exceptions import DescriberError
from moray_scribe.grapher import CallChain
from moray_scribe.pipeline import run_with_result


def _long() -> str:
    return "This summary is deliberately long enough to clear the fifty character limit."


def _draft_payload() -> dict[str, object]:
    return {
        "title": "Arch",
        "summary": _long(),
        "diagrams": [
            {
                "kind": "flowchart",
                "title": "Overview",
                "nodes": [{"id": "Core", "label": "Core", "kind": "module"}],
                "edges": [],
            }
        ],
        "modules": [],
    }


def _fake_sequences(min_flows: int = 1) -> Any:
    """Factory: a describe_sequences stand-in that records its input chains."""

    def _run(chains: list[CallChain], client: Any, cfg: Any, **kwargs: Any):
        graph = kwargs["graph"]
        flows = [engine_sequence_flow(chain, graph) for chain in chains]
        assert len(flows) >= min_flows or not flows
        return SequenceOutcome(flows=flows)

    return _run


def test_doc_renders_sequence_section(
    monkeypatch: pytest.MonkeyPatch,
    sample_project_path: Path,
    tmp_path: Path,
    fake_ollama_client: Any,
) -> None:
    client = fake_ollama_client([json.dumps(_draft_payload())])
    monkeypatch.setattr("moray_scribe.pipeline.OllamaClient", lambda **kwargs: client)
    seen: dict[str, Any] = {}

    def _spy(chains: list[CallChain], client_: Any, cfg: Any, **kwargs: Any):
        seen["entries"] = [chain.entry for chain in chains]
        return _fake_sequences()(chains, client_, cfg, **kwargs)

    monkeypatch.setattr("moray_scribe.pipeline.describe_sequences", _spy)
    cfg = Settings(_env_file=None)

    result = run_with_result("doc", sample_project_path, cfg, out_dir=tmp_path)

    # The sample fixture project yields exactly three traced chains.
    assert len(seen["entries"]) >= 3
    assert result.summary["sequences_count"] >= 3
    assert result.summary["sequences_degraded"] is False

    md = (tmp_path / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "## 九、核心业务时序图【AI 推理】" in md
    assert "### 1. 调用链：" in md
    assert "```mermaid\nsequenceDiagram" in md
    assert "**入口**：`" in md

    seq_mmd = tmp_path / "sequence-00.mmd"
    assert seq_mmd.is_file()
    assert seq_mmd.read_text(encoding="utf-8").startswith("sequenceDiagram")


def test_doc_sequence_stage_crash_still_renders_section(
    monkeypatch: pytest.MonkeyPatch,
    sample_project_path: Path,
    tmp_path: Path,
    fake_ollama_client: Any,
) -> None:
    """A sequence-stage explosion must never kill the document."""
    client = fake_ollama_client([json.dumps(_draft_payload())])
    monkeypatch.setattr("moray_scribe.pipeline.OllamaClient", lambda **kwargs: client)
    monkeypatch.setattr(
        "moray_scribe.pipeline.describe_sequences",
        lambda chains, client_, cfg, **kwargs: (_ for _ in ()).throw(
            DescriberError("boom")
        ),
    )
    cfg = Settings(_env_file=None)

    result = run_with_result("doc", sample_project_path, cfg, out_dir=tmp_path)

    assert result.summary["sequences_count"] == 0
    md = (tmp_path / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "## 九、核心业务时序图" in md
    assert "未追踪到可绘制时序图的调用链" in md


def test_doc_without_chains_skips_sequence_stage(
    monkeypatch: pytest.MonkeyPatch,
    sample_project_path: Path,
    tmp_path: Path,
    fake_ollama_client: Any,
) -> None:
    client = fake_ollama_client([json.dumps(_draft_payload())])
    monkeypatch.setattr("moray_scribe.pipeline.OllamaClient", lambda **kwargs: client)
    called = {"count": 0}

    def _spy(chains: list[CallChain], client_: Any, cfg: Any, **kwargs: Any):
        called["count"] += 1
        return _fake_sequences()(chains, client_, cfg, **kwargs)

    monkeypatch.setattr("moray_scribe.pipeline.describe_sequences", _spy)
    # No flow-carrying edges: exclude every module except the empty __init__,
    # so the graph has no entries and no chains.
    cfg = Settings(_env_file=None).model_copy(
        update={"include": ["**/__init__.py"], "max_nodes": 10}
    )

    run_with_result("doc", sample_project_path, cfg, out_dir=tmp_path)

    assert called["count"] == 0
