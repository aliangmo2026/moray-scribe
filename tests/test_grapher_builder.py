"""Unit tests for the conservative symbol graph builder."""

from __future__ import annotations

import pytest

from moray_scribe.exceptions import GraphError
from moray_scribe.grapher import build_symbol_graph
from moray_scribe.grapher import builder as builder_module
from moray_scribe.scanner import extract_source
from moray_scribe.schema import EdgeKind, RawSymbolBundle, SymbolGraph


def _edge_pairs(graph: SymbolGraph, kind: EdgeKind) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in graph.edges if e.kind is kind}


def _weights(graph: SymbolGraph, source: str, target: str, kind: EdgeKind) -> list[float]:
    return [
        e.weight
        for e in graph.edges
        if e.source == source and e.target == target and e.kind is kind
    ]


def test_build_core(sample_graph: SymbolGraph) -> None:
    assert "core" in sample_graph.nodes
    assert sample_graph.nodes["core"].kind.value == "module"
    assert "core.Engine" in sample_graph.nodes
    assert "core.build_engine" in sample_graph.nodes
    assert sample_graph.nodes["core.Engine"].kind.value == "class"
    assert sample_graph.nodes["core.build_engine"].kind.value == "function"


def test_contains_edges(sample_graph: SymbolGraph) -> None:
    contains = _edge_pairs(sample_graph, EdgeKind.CONTAINS)
    assert ("core", "core.Engine") in contains
    assert ("core", "core.build_engine") in contains
    assert ("core.Engine", "core.Engine.start") in contains
    assert ("core.Engine", "core.Engine.run") in contains
    assert ("nested", "nested.Outer") in contains
    assert ("nested.Outer", "nested.Outer.Inner") in contains
    assert ("nested.Outer.method", "nested.Outer.method.helper") in contains


def test_inheritance_edges(sample_graph: SymbolGraph) -> None:
    inherits = _edge_pairs(sample_graph, EdgeKind.INHERITS)
    assert ("inheritance.Dog", "inheritance.Animal") in inherits
    # Puppy inherits both Dog and Animal.
    assert ("inheritance.Puppy", "inheritance.Dog") in inherits
    assert ("inheritance.Puppy", "inheritance.Animal") in inherits


def test_import_edges(sample_graph: SymbolGraph) -> None:
    imports = _edge_pairs(sample_graph, EdgeKind.IMPORTS)
    assert ("service", "core") in imports
    assert ("calls", "core") in imports
    assert ("imports_mess", "service") in imports
    assert ("imports_mess", "core") in imports
    # External imports (os, collections) never create edges.
    assert not any(target.startswith(("os", "collections")) for _, target in imports)


def test_call_same_module_instantiates() -> None:
    bundle = extract_source(
        "class Widget:\n    pass\n\n\ndef build():\n    return Widget()\n",
        module="m",
        file="m.py",
    )
    graph = build_symbol_graph([bundle])

    assert ("m.build", "m.Widget") in _edge_pairs(graph, EdgeKind.INSTANTIATES)


def test_call_cross_module_instantiates(sample_graph: SymbolGraph) -> None:
    instantiates = _edge_pairs(sample_graph, EdgeKind.INSTANTIATES)
    assert ("service.Service.__init__", "core.Engine") in instantiates


def test_call_via_import_alias() -> None:
    core = extract_source("class Engine:\n    pass\n", module="core", file="core.py")
    user = extract_source(
        "from core import Engine as Eng\n\n\ndef make():\n    return Eng()\n",
        module="user",
        file="user.py",
    )
    graph = build_symbol_graph([core, user])

    assert ("user.make", "core.Engine") in _edge_pairs(graph, EdgeKind.INSTANTIATES)


def test_call_self_skipped(sample_graph: SymbolGraph) -> None:
    calls = _edge_pairs(sample_graph, EdgeKind.CALLS)
    assert not any(target == "core.Engine.run" for _, target in calls)
    assert ("calls.Harness.dispatch", "core.Engine.run") not in calls
    # The stored engine attribute call is never resolved (blacklist).
    assert not any("self." in source for source, _ in calls)


def test_call_super_init(sample_graph: SymbolGraph) -> None:
    calls = _edge_pairs(sample_graph, EdgeKind.CALLS)
    assert ("inheritance.Dog.__init__", "inheritance.Animal.__init__") in calls


def test_call_module_level() -> None:
    bundle = extract_source("def go():\n    pass\n\n\nresult = go()\n", module="m", file="m.py")
    graph = build_symbol_graph([bundle])

    assert ("m", "m.go") in _edge_pairs(graph, EdgeKind.CALLS)


def test_call_external_skipped() -> None:
    bundle = extract_source(
        "import os.path\n\n\ndef go():\n    return os.path.join('a', 'b')\n",
        module="m",
        file="m.py",
    )
    graph = build_symbol_graph([bundle])

    # Only the structural contains edge remains; no call/instantiate edge to os.
    assert _edge_pairs(graph, EdgeKind.CALLS) == set()
    assert _edge_pairs(graph, EdgeKind.INSTANTIATES) == set()


def test_duplicate_edges_merged() -> None:
    bundle = extract_source(
        "def target():\n    pass\n\n\ndef a():\n    target()\n    target()\n",
        module="m",
        file="m.py",
    )
    graph = build_symbol_graph([bundle])

    weights = _weights(graph, "m.a", "m.target", EdgeKind.CALLS)
    assert weights == [2.0]


def test_dangling_edges_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(builder_module, "_resolve_call", lambda *a, **k: "ghost.missing")
    bundle = extract_source("def a():\n    b()\n", module="m", file="m.py")

    graph = build_symbol_graph([bundle], strict=False)

    assert graph.validate_edges()
    assert ("m.a", "ghost.missing") in _edge_pairs(graph, EdgeKind.CALLS)


def test_strict_mode_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(builder_module, "_resolve_call", lambda *a, **k: "ghost.missing")
    bundle = extract_source("def a():\n    b()\n", module="m", file="m.py")

    with pytest.raises(GraphError, match="dangling edges"):
        build_symbol_graph([bundle], strict=True)


def test_empty_bundles() -> None:
    graph = build_symbol_graph([])

    assert graph.node_count == 0
    assert graph.edge_count == 0


def test_strict_mode_passes_on_clean_graph() -> None:
    bundle = extract_source("def a():\n    pass\n", module="m", file="m.py")

    graph = build_symbol_graph([bundle], strict=True)

    assert graph.validate_edges() == []


def test_annotations_marked(sample_graph: SymbolGraph) -> None:
    # Entry points and dead candidates are annotated during the build.
    assert sample_graph.nodes["decorators.index"].is_entry is True
    assert sample_graph.nodes["core.Engine.__init__"].is_entry is True
    # A public function with no references is a dead candidate.
    assert sample_graph.nodes["decorators.cached"].is_dead_candidate is True


def test_bundled_fixture_counts(sample_bundles: list[RawSymbolBundle]) -> None:
    graph = build_symbol_graph(sample_bundles)

    assert graph.node_count == 51
    assert graph.edge_count == 58
