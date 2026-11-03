"""Grapher tests for the v0.2 dynamic heuristics (bindings, self-calls, imports)."""

from __future__ import annotations

from moray_scribe.grapher import build_symbol_graph
from moray_scribe.scanner import extract_source
from moray_scribe.schema import EdgeKind, SymbolGraph


def _edge_pairs(graph: SymbolGraph, kind: EdgeKind) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in graph.edges if e.kind is kind}


def test_self_attr_call_resolved_cross_module() -> None:
    core = extract_source(
        "class Engine:\n    def run(self):\n        return 1\n",
        module="core",
        file="core.py",
    )
    app = extract_source(
        "from core import Engine\n"
        "\n"
        "class Service:\n"
        "    def __init__(self):\n"
        "        self.engine = Engine()\n"
        "\n"
        "    def go(self):\n"
        "        return self.engine.run()\n",
        module="app",
        file="app.py",
    )
    graph = build_symbol_graph([core, app])

    assert ("app.Service.go", "core.Engine.run") in _edge_pairs(graph, EdgeKind.CALLS)


def test_setattr_binding_resolves_call() -> None:
    core = extract_source(
        "class Engine:\n    def run(self):\n        return 1\n",
        module="core",
        file="core.py",
    )
    app = extract_source(
        "from core import Engine\n"
        "\n"
        "class Service:\n"
        "    def setup(self):\n"
        "        setattr(self, 'engine', Engine())\n"
        "\n"
        "    def go(self):\n"
        "        return self.engine.run()\n",
        module="app",
        file="app.py",
    )
    graph = build_symbol_graph([core, app])

    assert ("app.Service.go", "core.Engine.run") in _edge_pairs(graph, EdgeKind.CALLS)


def test_factory_assignment_is_not_a_type() -> None:
    """A factory function's return type is unknown; the attr stays unmapped."""
    core = extract_source(
        "class Engine:\n    def run(self):\n        return 1\n"
        "\n"
        "def build_engine():\n    return Engine()\n",
        module="core",
        file="core.py",
    )
    app = extract_source(
        "from core import build_engine\n"
        "\n"
        "class Service:\n"
        "    def __init__(self):\n"
        "        self.engine = build_engine()\n"
        "\n"
        "    def go(self):\n"
        "        return self.engine.run()\n",
        module="app",
        file="app.py",
    )
    graph = build_symbol_graph([core, app])

    assert ("app.Service.go", "core.Engine.run") not in _edge_pairs(graph, EdgeKind.CALLS)


def test_ambiguous_binding_left_unresolved() -> None:
    """Two classes assigned to the same attr -> no mapping, no edge."""
    core = extract_source(
        "class Engine:\n    def run(self):\n        return 1\n"
        "\n"
        "class Backup:\n    def run(self):\n        return 2\n",
        module="core",
        file="core.py",
    )
    app = extract_source(
        "from core import Engine, Backup\n"
        "\n"
        "class Service:\n"
        "    def primary(self):\n"
        "        self.engine = Engine()\n"
        "\n"
        "    def fallback(self):\n"
        "        self.engine = Backup()\n"
        "\n"
        "    def go(self):\n"
        "        return self.engine.run()\n",
        module="app",
        file="app.py",
    )
    graph = build_symbol_graph([core, app])

    assert ("app.Service.go", "core.Engine.run") not in _edge_pairs(graph, EdgeKind.CALLS)
    assert ("app.Service.go", "core.Backup.run") not in _edge_pairs(graph, EdgeKind.CALLS)


def test_own_class_method_call_resolves() -> None:
    bundle = extract_source(
        "class A:\n"
        "    def start(self):\n"
        "        return 1\n"
        "\n"
        "    def run(self):\n"
        "        return self.start()\n",
        module="m",
        file="m.py",
    )
    graph = build_symbol_graph([bundle])

    assert ("m.A.run", "m.A.start") in _edge_pairs(graph, EdgeKind.CALLS)


def test_unbound_self_chain_goes_to_dynamic_out() -> None:
    app = extract_source(
        "class Service:\n"
        "    def go(self):\n"
        "        return self.engine.run()\n",
        module="app",
        file="app.py",
    )
    dynamic: list[tuple[str, str, int]] = []
    graph = build_symbol_graph([app], dynamic_out=dynamic)

    assert _edge_pairs(graph, EdgeKind.CALLS) == set()
    assert ("app.Service.go", "self.engine.run", 3) in dynamic


def test_dynamic_import_creates_edge_from_function() -> None:
    target = extract_source(
        "VALUE = 1\n",
        module="pkg.sub",
        file="pkg/sub.py",
    )
    loader = extract_source(
        "import importlib\n"
        "\n"
        "def load():\n"
        "    return importlib.import_module('pkg.sub')\n",
        module="loader",
        file="loader.py",
    )
    graph = build_symbol_graph([target, loader])

    assert ("loader.load", "pkg.sub") in _edge_pairs(graph, EdgeKind.IMPORTS)


def test_dynamic_import_external_ignored() -> None:
    loader = extract_source(
        "import importlib\n"
        "\n"
        "def load():\n"
        "    return importlib.import_module('external_thing')\n",
        module="loader",
        file="loader.py",
    )
    graph = build_symbol_graph([loader])

    assert _edge_pairs(graph, EdgeKind.IMPORTS) == set()
