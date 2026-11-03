"""Unit tests for the LLM dynamic inference stage (model fully mocked)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from moray_scribe.describer.inferencer import (
    InferenceReport,
    InferredEdge,
    InferredEdgeBatch,
    _parse_batch_response,
    apply_inferred_edges_to_draft,
    build_batch_prompts,
    extract_dangling_calls,
    merge_inferred_edges,
)
from moray_scribe.grapher import build_symbol_graph
from moray_scribe.scanner import extract_source
from moray_scribe.schema import ArchitectureDraft, DanglingCall, EdgeKind, SymbolGraph


@pytest.fixture()
def small_graph() -> tuple[SymbolGraph, list[tuple[str, str, int]]]:
    core = extract_source(
        "class Engine:\n    def run(self):\n        return 1\n"
        "\n"
        "def build_engine():\n    return Engine()\n",
        module="core",
        file="core.py",
    )
    app = extract_source(
        "from core import Engine\n"
        "\n"
        "class Harness:\n"
        "    def __init__(self):\n"
        "        self.engine = build_engine()\n"
        "\n"
        "    def dispatch(self):\n"
        "        return self.engine.run()\n",
        module="app",
        file="app.py",
    )
    pool: list[tuple[str, str, int]] = []
    graph = build_symbol_graph([core, app], dynamic_out=pool)
    return graph, pool


def _tmp_project(tmp_path: Path, graph: SymbolGraph) -> Path:
    core = tmp_path / "core.py"
    app = tmp_path / "app.py"
    core.write_text(
        "class Engine:\n    def run(self):\n        return 1\n"
        "\n"
        "def build_engine():\n    return Engine()\n",
        encoding="utf-8",
    )
    app.write_text(
        "from core import Engine\n"
        "\n"
        "class Harness:\n"
        "    def __init__(self):\n"
        "        self.engine = build_engine()\n"
        "\n"
        "    def dispatch(self):\n"
        "        return self.engine.run()\n",
        encoding="utf-8",
    )
    assert graph.nodes["core.Engine"].file == "core.py"
    return tmp_path


def test_extract_dangling_calls_dedupes(small_graph) -> None:
    graph, pool = small_graph
    pool.append(("app.Harness.dispatch", "self.engine.run", 99))  # duplicate site

    calls = extract_dangling_calls(graph, pool, max_calls=10)

    assert {(c.caller_id, c.callee) for c in calls} == {
        ("app.Harness.dispatch", "self.engine.run"),
        ("app.Harness.__init__", "build_engine"),
    }
    # The duplicate keeps the earliest line number.
    assert min(c.lineno for c in calls if c.callee == "self.engine.run") == 8


def test_extract_dangling_calls_builtin_filtered(small_graph) -> None:
    graph, pool = small_graph
    pool.append(("app.Harness.dispatch", "print", 20))

    calls = extract_dangling_calls(graph, pool, max_calls=10)

    assert all(c.callee != "print" for c in calls)


def test_extract_dangling_calls_max_cap(small_graph) -> None:
    graph, pool = small_graph
    calls = extract_dangling_calls(graph, pool, max_calls=1)

    assert len(calls) == 1


def test_build_batch_prompts_carries_context(tmp_path: Path, small_graph) -> None:
    graph, pool = small_graph
    root = _tmp_project(tmp_path, graph)
    calls = extract_dangling_calls(graph, pool, max_calls=10)

    batches = build_batch_prompts(calls, graph, root_path=root, batch_size=1)

    assert len(batches) == 2
    first_calls, first_prompt = batches[0]
    assert len(first_calls) == 1
    assert first_calls[0].callee in first_prompt
    assert "candidates: " in first_prompt
    assert "->" in first_prompt  # the call-line marker
    # Snippet content comes from the real file on disk.
    assert any("return self.engine.run()" in prompt for _, prompt in batches)


def test_build_batch_prompts_batches_by_size(tmp_path: Path, small_graph) -> None:
    graph, pool = small_graph
    root = _tmp_project(tmp_path, graph)
    calls = extract_dangling_calls(graph, pool, max_calls=10)

    batches = build_batch_prompts(calls, graph, root_path=root, batch_size=8)

    assert len(batches) == 1
    assert len(batches[0][0]) == 2


def test_parse_batch_response_high_merged_low_gapped(small_graph) -> None:
    graph, pool = small_graph
    calls = extract_dangling_calls(graph, pool, max_calls=10)
    report = InferenceReport()

    _parse_batch_response(
        calls,
        json.dumps(
            {
                "edges": [
                    {
                        "src": "app.Harness.dispatch",
                        "dst": "core.Engine.run",
                        "confidence": "high",
                        "reason": "self.engine assigned in __init__",
                    },
                    {
                        "src": "app.Harness.__init__",
                        "dst": "core.Engine",
                        "confidence": "medium",
                        "reason": "build_engine likely constructs Engine",
                    },
                ]
            }
        ),
        graph,
        report,
    )

    assert [(e.src, e.dst) for e in report.merged] == [
        ("app.Harness.dispatch", "core.Engine.run")
    ]
    assert report.merged[0].callee == "self.engine.run"
    assert [(e.src, e.dst) for e in report.gaps] == [("app.Harness.__init__", "core.Engine")]


def test_parse_batch_response_unknown_target_gapped(small_graph) -> None:
    graph, pool = small_graph
    calls = extract_dangling_calls(graph, pool, max_calls=10)
    report = InferenceReport()

    _parse_batch_response(
        calls,
        json.dumps(
            {
                "edges": [
                    {
                        "src": "app.Harness.dispatch",
                        "dst": "UNKNOWN",
                        "confidence": "low",
                        "reason": "cannot decide",
                    }
                ]
            }
        ),
        graph,
        report,
    )

    assert report.merged == []
    assert len(report.gaps) == 1
    assert report.gaps[0].dst == "UNKNOWN"


def test_parse_batch_response_invented_target_rejected(small_graph) -> None:
    graph, pool = small_graph
    all_calls = extract_dangling_calls(graph, pool, max_calls=10)
    calls = [c for c in all_calls if c.caller_id == "app.Harness.dispatch"]
    report = InferenceReport()

    _parse_batch_response(
        calls,
        json.dumps(
            {
                "edges": [
                    {
                        "src": "app.Harness.dispatch",
                        "dst": "ghost.Module.run",
                        "confidence": "high",
                        "reason": "hallucinated",
                    }
                ]
            }
        ),
        graph,
        report,
    )

    assert report.merged == []
    assert len(report.gaps) == 1  # unknown dst lands in gaps, not merged
    assert report.rejected == 0


def test_parse_batch_response_malformed_json_counts_rejected(small_graph) -> None:
    graph, pool = small_graph
    calls = extract_dangling_calls(graph, pool, max_calls=10)
    report = InferenceReport()

    _parse_batch_response(calls, "this is not json", graph, report)

    assert report.rejected == len(calls)
    assert report.merged == []


def test_merge_inferred_edges_marks_and_annotates(small_graph) -> None:
    graph, _pool = small_graph
    report = InferenceReport(
        merged=[
            InferredEdge(
                src="app.Harness.dispatch",
                dst="core.Engine.run",
                confidence="high",
                reason="binding evidence",
                callee="self.engine.run",
            )
        ]
    )

    added = merge_inferred_edges(graph, report)

    assert added == 1
    edge = next(
        e
        for e in graph.edges
        if e.source == "app.Harness.dispatch" and e.target == "core.Engine.run"
    )
    assert edge.inferred is True
    assert edge.confidence == "high"
    assert edge.reason == "binding evidence"
    assert edge.kind is EdgeKind.CALLS
    calls_in = [
        e
        for e in graph.edges
        if e.target == "core.Engine.run" and e.kind is EdgeKind.CALLS
    ]
    assert len(calls_in) == 1


def test_merge_inferred_edges_skips_existing_static_edge(small_graph) -> None:
    graph, _pool = small_graph
    report = InferenceReport(
        merged=[
            InferredEdge(
                src="app.Harness.dispatch",
                dst="core.Engine.run",
                confidence="high",
                reason="dup",
            )
        ]
    )
    assert merge_inferred_edges(graph, report) == 1
    assert merge_inferred_edges(graph, report) == 0
    calls = [
        e for e in graph.edges if e.source == "app.Harness.dispatch"
        and e.target == "core.Engine.run"
    ]
    assert len(calls) == 1


def test_apply_inferred_edges_to_draft_injects_dashed(tmp_path: Path, small_graph) -> None:
    graph, _pool = small_graph
    report = InferenceReport(
        merged=[
            InferredEdge(
                src="app.Harness.dispatch",
                dst="core.Engine.run",
                confidence="high",
                reason="binding evidence",
            )
        ]
    )
    merge_inferred_edges(graph, report)
    draft = ArchitectureDraft.model_validate(
        {
            "title": "Demo",
            "summary": "x" * 60,
            "diagrams": [
                {
                    "kind": "flowchart",
                    "title": "Main",
                    "nodes": [
                        {
                            "id": "App",
                            "label": "App",
                            "kind": "module",
                            "symbol_ids": ["app.Harness", "app.Harness.dispatch"],
                        },
                        {
                            "id": "Core",
                            "label": "Core",
                            "kind": "module",
                            "symbol_ids": ["core.Engine", "core.Engine.run"],
                        },
                    ],
                    "edges": [],
                }
            ],
            "modules": [],
        }
    )

    added = apply_inferred_edges_to_draft(draft, graph)

    assert added == 1
    edge = draft.diagrams[0].edges[0]
    assert edge.source == "App"
    assert edge.target == "Core"
    assert edge.inferred is True
    assert "[AI推断]" in edge.label
    assert edge.style.value == "dashed"


def test_apply_inferred_edges_noop_without_inferred(small_graph) -> None:
    graph, _pool = small_graph
    draft = ArchitectureDraft.model_validate(
        {
            "title": "Demo",
            "summary": "x" * 60,
            "diagrams": [],
            "modules": [],
        }
    )

    assert apply_inferred_edges_to_draft(draft, graph) == 0


def test_inferred_edge_batch_schema_roundtrip() -> None:
    batch = InferredEdgeBatch.model_validate(
        {
            "edges": [
                {"src": "a", "dst": "b", "confidence": "high", "reason": "r"},
                # extra fields must be rejected (StrictModel)
            ]
        }
    )
    assert len(batch.edges) == 1

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        InferredEdgeBatch.model_validate(
            {
                "edges": [
                    {"src": "a", "dst": "b", "confidence": "high", "reason": "r", "x": 1}
                ]
            }
        )


def test_inference_report_tokens() -> None:
    report = InferenceReport(prompt_tokens=10, completion_tokens=5)
    assert report.total_tokens == 15



def test_parse_batch_response_tolerates_signature_decorated_src(small_graph) -> None:
    """Small models echo ``caller()`` / backticked ids; the parser normalizes."""
    graph, pool = small_graph
    calls = extract_dangling_calls(graph, pool, max_calls=10)
    report = InferenceReport()

    _parse_batch_response(
        calls,
        json.dumps(
            {
                "edges": [
                    {
                        "src": "app.Harness.dispatch(self, payload)",
                        "dst": "core.Engine.run()",
                        "confidence": "high",
                        "reason": "self.engine.run() in the snippet",
                    }
                ]
            }
        ),
        graph,
        report,
    )

    assert [(e.src, e.dst) for e in report.merged] == [
        ("app.Harness.dispatch", "core.Engine.run")
    ]
    # One answer for one call; the other pooled call is correctly rejected.
    assert report.rejected == len(calls) - 1


def test_parse_batch_response_tolerates_backticked_src(small_graph) -> None:
    graph, pool = small_graph
    calls = extract_dangling_calls(graph, pool, max_calls=10)
    report = InferenceReport()

    _parse_batch_response(
        calls,
        json.dumps(
            {
                "edges": [
                    {
                        "src": "`app.Harness.dispatch`",
                        "dst": "`core.Engine.run`",
                        "confidence": "medium",
                        "reason": "attribute assignment in __init__",
                    }
                ]
            }
        ),
        graph,
        report,
    )

    assert [(e.src, e.dst) for e in report.gaps] == [
        ("app.Harness.dispatch", "core.Engine.run")
    ]


def test_parse_batch_response_ambiguous_normalized_src_rejected(small_graph) -> None:
    """A normalized src that could mean two calls must not be guessed."""
    graph, _pool = small_graph
    calls = [
        DanglingCall(caller_id="app.Harness.dispatch", callee="self.engine.run", lineno=1),
        DanglingCall(caller_id="app.Harness.dispatch", callee="self.engine.stop", lineno=2),
    ]
    report = InferenceReport()

    _parse_batch_response(
        calls,
        json.dumps(
            {
                "edges": [
                    {
                        "src": "app.Harness.dispatch()",
                        "dst": "core.Engine.run",
                        "confidence": "high",
                        "reason": "guess",
                    }
                ]
            }
        ),
        graph,
        report,
    )

    # Both calls share the same caller id, so the answer is unambiguous for
    # that id and is applied to it once.
    assert report.rejected == 1
