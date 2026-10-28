"""Unit tests for architecture generation and semantic post-validation."""

from __future__ import annotations

import json
from typing import Any

import pytest

from moray_scribe.config import Settings
from moray_scribe.describer import PromptContext, generate_architecture
from moray_scribe.describer.generator import _post_validate
from moray_scribe.schema import (
    ArchitectureDraft,
    EdgeKind,
    SymbolEdge,
    SymbolGraph,
    SymbolKind,
    SymbolNode,
)

_LONG = "This summary is deliberately long enough to clear the fifty character minimum limit."


def _draft(**overrides: Any) -> ArchitectureDraft:
    base: dict[str, Any] = {
        "title": "Arch",
        "summary": _LONG,
        "diagrams": [
            {
                "kind": "flowchart",
                "title": "Overview",
                "nodes": [{"id": "Core", "label": "Core", "kind": "module"}],
                "edges": [],
            }
        ],
        "modules": [{"module": "core", "summary": "Core.", "symbol_ids": ["core.Engine"]}],
    }
    base.update(overrides)
    return ArchitectureDraft.model_validate(base)


def _valid_draft_json() -> str:
    return _draft().model_dump_json()


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "model_describer": "test-model",
        "max_retries": 1,
        "temperature": 0.1,
        "max_nodes": 200,
        "max_edges": 500,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_single_mode_success(fake_ollama_client: Any, sample_graph: SymbolGraph) -> None:
    client = fake_ollama_client([_valid_draft_json()])

    draft = generate_architecture(
        sample_graph,
        client,
        _settings(),
        project_name="sample",
        root_path="tests/fixtures/sample_project",
    )

    assert draft.title == "Arch"
    assert len(client.backend.call_log) == 1


def test_single_mode_pre_prunes_oversized_graph(fake_ollama_client: Any) -> None:
    hub = SymbolNode(
        id="m.hub", name="hub", qualname="m.hub", kind=SymbolKind.FUNCTION,
        module="m", file="m.py", lineno=1, end_lineno=2,
    )
    leaves = {
        f"m.l{i}": SymbolNode(
            id=f"m.l{i}", name=f"l{i}", qualname=f"m.l{i}", kind=SymbolKind.FUNCTION,
            module="m", file="m.py", lineno=1, end_lineno=2,
        )
        for i in range(39)
    }
    edges = [
        SymbolEdge(source=f"m.l{i}", target="m.hub", kind=EdgeKind.CALLS)
        for i in range(39)
    ]
    graph = SymbolGraph(nodes={"m.hub": hub, **leaves}, edges=edges)
    big_draft = json.dumps(
        {
            "title": "Arch", "summary": _LONG,
            "diagrams": [
                {
                    "kind": "flowchart",
                    "title": "t",
                    "nodes": [{"id": "Hub", "label": "Hub"}],
                    "edges": [],
                }
            ],
            "modules": [{"module": "m", "summary": "M.", "symbol_ids": ["m.hub"]}],
        }
    )
    client = fake_ollama_client([big_draft])
    cfg = _settings(max_nodes=5)

    generate_architecture(graph, client, cfg, project_name="p", root_path="r")

    user_text = client.backend.call_log[0]["messages"][1]["content"]
    assert user_text.count('"i":') <= 5


def test_post_validate_rejects_unknown_module(sample_prompt_context: PromptContext) -> None:
    draft = _draft(modules=[{"module": "ghost", "summary": "G."}])

    errors = _post_validate(draft, sample_prompt_context)

    assert any("unknown module" in error for error in errors)


def test_post_validate_rejects_unknown_key_entities(sample_prompt_context: PromptContext) -> None:
    draft = _draft(modules=[{"module": "core", "summary": "C.", "symbol_ids": ["ghost.Symbol"]}])

    errors = _post_validate(draft, sample_prompt_context)

    assert any("unknown key symbol" in error for error in errors)


def test_post_validate_rejects_overview_too_short(sample_prompt_context: PromptContext) -> None:
    draft = _draft(summary="short")

    errors = _post_validate(draft, sample_prompt_context)

    assert any("at least 50 characters" in error for error in errors)


def test_post_validate_rejects_only_external_diagram(sample_prompt_context: PromptContext) -> None:
    draft = _draft(
        diagrams=[
            {
                "kind": "flowchart",
                "title": "t",
                "nodes": [{"id": "Ext", "label": "Ext", "kind": "external"}],
                "edges": [],
            }
        ]
    )

    errors = _post_validate(draft, sample_prompt_context)

    assert any("non-external node" in error for error in errors)


def test_post_validate_accepts_good_draft(sample_prompt_context: PromptContext) -> None:
    assert _post_validate(_draft(), sample_prompt_context) == []


def test_two_phase_raises(fake_ollama_client: Any, sample_graph: SymbolGraph) -> None:
    client = fake_ollama_client([_valid_draft_json()])

    with pytest.raises(NotImplementedError, match="TODO: v2"):
        generate_architecture(
            sample_graph, client, _settings(),
            project_name="p", root_path="r", mode="two-phase",
        )


def _ctx(node_ids: list[str]) -> PromptContext:
    return PromptContext(
        project_name="p",
        root_path="r",
        nodes_json="[]",
        edges_json="[]",
        known_gaps="None.",
        n_nodes=len(node_ids),
        n_edges=0,
        module_set=sorted({nid.split(".")[0] for nid in node_ids}),
        node_ids=sorted(node_ids),
    )


def test_post_validate_rejects_class_diagram_as_first() -> None:
    draft = ArchitectureDraft.model_validate(
        {
            "title": "T",
            "summary": _LONG,
            "diagrams": [
                {
                    "kind": "classDiagram",
                    "title": "D",
                    "nodes": [{"id": "A", "label": "A", "kind": "class"}],
                }
            ],
        }
    )

    errors = _post_validate(draft, _ctx(["m.a"]))

    assert any("must be a flowchart" in error for error in errors)


def test_post_validate_rejects_one_to_one_node_mapping() -> None:
    ids = [f"m.f{i}" for i in range(30)]
    nodes = [{"id": f"N{i}", "label": f"N{i}"} for i in range(30)]
    draft = ArchitectureDraft.model_validate(
        {
            "title": "T",
            "summary": _LONG,
            "diagrams": [
                {"kind": "flowchart", "title": "d", "nodes": nodes, "edges": []}
            ],
        }
    )

    errors = _post_validate(draft, _ctx(ids))

    assert any("MERGE" in error for error in errors)


def test_post_validate_small_project_skips_granularity() -> None:
    ids = [f"m.f{i}" for i in range(5)]
    nodes = [{"id": f"N{i}", "label": f"N{i}"} for i in range(15)]
    draft = ArchitectureDraft.model_validate(
        {
            "title": "T",
            "summary": _LONG,
            "diagrams": [
                {"kind": "flowchart", "title": "d", "nodes": nodes, "edges": []}
            ],
        }
    )

    errors = _post_validate(draft, _ctx(ids))

    assert not any("MERGE" in error for error in errors)
