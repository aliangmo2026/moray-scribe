"""Scanner tests for the v0.2 dynamic heuristics: bindings and dynamic imports."""

from __future__ import annotations

from moray_scribe.scanner import extract_source


def test_self_assignment_recorded() -> None:
    bundle = extract_source(
        "class A:\n"
        "    def __init__(self):\n"
        "        self.engine = Engine()\n",
        module="m",
        file="m.py",
    )

    assert len(bundle.attr_bindings) == 1
    binding = bundle.attr_bindings[0]
    assert binding.attr == "engine"
    assert binding.value == "Engine()"
    assert binding.owner_qualname == "A"
    assert binding.caller_qualname == "A.__init__"
    assert binding.lineno == 3


def test_annotated_assignment_recorded() -> None:
    bundle = extract_source(
        "class A:\n"
        "    def __init__(self):\n"
        "        self.engine: Engine = build_engine()\n",
        module="m",
        file="m.py",
    )

    assert [b.value for b in bundle.attr_bindings] == ["build_engine()"]


def test_non_self_assignments_ignored() -> None:
    bundle = extract_source(
        "x = Engine()\n"
        "class A:\n"
        "    y = Engine()\n"
        "    def f(self):\n"
        "        other.x = Engine()\n",
        module="m",
        file="m.py",
    )

    assert bundle.attr_bindings == []


def test_setattr_literal_recorded() -> None:
    bundle = extract_source(
        "class A:\n"
        "    def setup(self):\n"
        "        setattr(self, 'engine', Engine())\n"
        "        setattr(self, other_name, Engine())\n",
        module="m",
        file="m.py",
    )

    assert len(bundle.attr_bindings) == 1
    assert bundle.attr_bindings[0].attr == "engine"
    assert bundle.attr_bindings[0].caller_qualname == "A.setup"


def test_setattr_outside_class_ignored() -> None:
    bundle = extract_source(
        "def f(obj):\n    setattr(obj, 'engine', Engine())\n",
        module="m",
        file="m.py",
    )

    assert bundle.attr_bindings == []


def test_importlib_dynamic_imports_recorded() -> None:
    bundle = extract_source(
        "import importlib\n"
        "from importlib import import_module as im\n"
        "\n"
        "def load(name):\n"
        "    a = importlib.import_module('pkg.sub')\n"
        "    b = im('pkg.other')\n"
        "    c = __import__('x.y')\n"
        "    d = importlib.import_module(name)  # not a literal\n"
        "    return a, b, c, d\n",
        module="m",
        file="m.py",
    )

    assert [(ref.module, ref.caller_qualname) for ref in bundle.dynamic_imports] == [
        ("pkg.sub", "load"),
        ("pkg.other", "load"),
        ("x.y", "load"),
    ]


def test_import_module_unaliased_recorded() -> None:
    bundle = extract_source(
        "from importlib import import_module\n"
        "\n"
        "def load():\n"
        "    return import_module('pkg.sub')\n",
        module="m",
        file="m.py",
    )

    assert [ref.module for ref in bundle.dynamic_imports] == ["pkg.sub"]


def test_lookalike_import_module_ignored() -> None:
    bundle = extract_source(
        "def import_module(name):\n    return name\n"
        "\n"
        "def load():\n    return import_module('pkg.sub')\n",
        module="m",
        file="m.py",
    )

    assert bundle.dynamic_imports == []
