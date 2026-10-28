"""Unit tests for prompt-context assembly and known-gap detection."""

from __future__ import annotations

import json

from moray_scribe.describer import PromptContext, build_prompt_context, detect_known_gaps
from moray_scribe.schema import (
    EdgeKind,
    SymbolEdge,
    SymbolGraph,
    SymbolKind,
    SymbolNode,
)


def _node(
    symbol_id: str,
    *,
    kind: SymbolKind = SymbolKind.FUNCTION,
    is_entry: bool = False,
    out_degree: int = 0,
    in_degree: int = 0,
    metadata: dict[str, str] | None = None,
) -> SymbolNode:
    return SymbolNode(
        id=symbol_id,
        name=symbol_id.rsplit(".", 1)[-1],
        qualname=symbol_id,
        kind=kind,
        module="m",
        file="m.py",
        lineno=1,
        end_lineno=2,
        is_entry=is_entry,
        out_degree=out_degree,
        in_degree=in_degree,
        metadata=metadata or {},
    )


def test_build_context_shapes(sample_prompt_context: PromptContext) -> None:
    ctx = sample_prompt_context

    assert ctx.project_name == "sample"
    assert ctx.n_nodes > 0
    assert ctx.n_edges > 0
    assert ctx.nodes_json and ctx.edges_json
    assert ctx.module_set and ctx.node_ids
    # nodes_json must be valid JSON.
    parsed = json.loads(ctx.nodes_json)
    assert isinstance(parsed, list)


def test_module_set_matches_graph(
    sample_prompt_context: PromptContext, sample_graph: SymbolGraph
) -> None:
    expected = sorted({node.module for node in sample_graph.nodes.values()})
    assert sample_prompt_context.module_set == expected
    assert "core" in sample_prompt_context.module_set


def test_node_ids_match_graph(
    sample_prompt_context: PromptContext, sample_graph: SymbolGraph
) -> None:
    assert sample_prompt_context.node_ids == sorted(sample_graph.nodes)
    assert "core.Engine" in sample_prompt_context.node_ids


def test_known_gaps_dead_method() -> None:
    orphan = _node("m.Orphan", kind=SymbolKind.METHOD, out_degree=0)
    graph = SymbolGraph(nodes={"m.Orphan": orphan})

    gaps = detect_known_gaps(graph)

    assert any("m.Orphan" in gap and "dynamic dispatch" in gap for gap in gaps)


def test_known_gaps_external_base() -> None:
    klass = _node(
        "m.Derived",
        kind=SymbolKind.CLASS,
        in_degree=1,
        metadata={"bases": "ExternalBase"},
    )
    graph = SymbolGraph(nodes={"m.Derived": klass})

    gaps = detect_known_gaps(graph)

    assert any("ExternalBase" in gap and "external base" in gap for gap in gaps)


def test_known_gaps_none_when_clean() -> None:
    module = _node("m", kind=SymbolKind.MODULE)
    entry = _node("m.C", kind=SymbolKind.CLASS, is_entry=True, in_degree=1)
    method = _node("m.C.run", kind=SymbolKind.METHOD, out_degree=1)
    graph = SymbolGraph(
        nodes={"m": module, "m.C": entry, "m.C.run": method},
        edges=[SymbolEdge(source="m.C.run", target="m.C", kind=EdgeKind.CALLS)],
    )

    assert detect_known_gaps(graph) == []


def test_build_context_none_gaps_text() -> None:
    module = _node("m", kind=SymbolKind.MODULE)
    graph = SymbolGraph(nodes={"m": module})

    ctx = build_prompt_context(graph, project_name="p", root_path="r")

    assert ctx.known_gaps == "None."


def test_known_gaps_external_base_via_pipeline() -> None:
    from moray_scribe.grapher import build_symbol_graph
    from moray_scribe.scanner import extract_source

    bundle = extract_source(
        "class Widget(UnknownBase):\n    pass\n",
        module="m",
        file="m.py",
    )
    graph = build_symbol_graph([bundle])

    gaps = detect_known_gaps(graph)

    assert any("UnknownBase" in gap and "external base" in gap for gap in gaps)


def test_known_gaps_internal_base_not_reported() -> None:
    base = _node("m.Base", kind=SymbolKind.CLASS, in_degree=1)
    derived = _node(
        "m.Derived", kind=SymbolKind.CLASS, in_degree=1, metadata={"bases": ["Base"]}
    )
    graph = SymbolGraph(nodes={"m.Base": base, "m.Derived": derived})

    assert not any("external base" in gap for gap in detect_known_gaps(graph))


def test_known_gaps_internal_base_via_unique_global_name() -> None:
    base = _node("lib.Base", kind=SymbolKind.CLASS, in_degree=1)
    derived = _node(
        "app.Derived", kind=SymbolKind.CLASS, in_degree=1, metadata={"bases": ["Base"]}
    )
    graph = SymbolGraph(nodes={"lib.Base": base, "app.Derived": derived})

    # "Base" resolves to the unique lib.Base, so it is NOT an external base.
    assert not any("external base" in gap for gap in detect_known_gaps(graph))


def test_known_gaps_true_external_base_reported_once() -> None:
    derived = _node(
        "m.Derived", kind=SymbolKind.CLASS, in_degree=1, metadata={"bases": ["IOError"]}
    )
    graph = SymbolGraph(nodes={"m.Derived": derived})

    external = [g for g in detect_known_gaps(graph) if "external base" in g]
    assert len(external) == 1
    assert "IOError" in external[0]


def test_known_gaps_external_base_dedup() -> None:
    nodes = {
        f"m.{name}": _node(
            f"m.{name}", kind=SymbolKind.CLASS, in_degree=1, metadata={"bases": ["ABC"]}
        )
        for name in ("A", "B", "C")
    }
    graph = SymbolGraph(nodes=nodes)

    external = [g for g in detect_known_gaps(graph) if "external base" in g and "ABC" in g]
    assert len(external) == 1
    assert "3 classes" in external[0]


def test_known_gaps_dotted_base_resolves() -> None:
    base = _node("lib.Base", kind=SymbolKind.CLASS, in_degree=1)
    derived = _node(
        "app.Derived",
        kind=SymbolKind.CLASS,
        in_degree=1,
        metadata={"bases": ["lib.Base"]},
    )
    graph = SymbolGraph(nodes={"lib.Base": base, "app.Derived": derived})

    # A fully-qualified dotted base resolves to the in-graph class.
    assert not any("external base" in gap for gap in detect_known_gaps(graph))
