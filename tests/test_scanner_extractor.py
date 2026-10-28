"""Unit tests for the libcst symbol extractor."""

from __future__ import annotations

from pathlib import Path

import pytest

from moray_scribe.exceptions import ScanError, ScribeError
from moray_scribe.scanner import extract_file, extract_source
from moray_scribe.schema import ImportRef, RawSymbol, RawSymbolBundle, SymbolKind


def _by_qualname(bundle: RawSymbolBundle) -> dict[str, RawSymbol]:
    return {symbol.qualname: symbol for symbol in bundle.symbols}


def _call_pairs(bundle: RawSymbolBundle) -> set[tuple[str, str]]:
    return {(call.callee, call.caller_qualname) for call in bundle.calls}


def test_extract_core(sample_project_path: Path) -> None:
    bundle = extract_file(sample_project_path / "core.py", root=sample_project_path)

    assert bundle.module == "core"
    assert bundle.file == "core.py"
    symbols = _by_qualname(bundle)
    assert set(symbols) == {
        "Engine",
        "Engine.__init__",
        "Engine.start",
        "Engine.run",
        "build_engine",
    }
    engine = symbols["Engine"]
    assert engine.kind is SymbolKind.CLASS
    assert engine.bases == []
    assert engine.docstring == "Minimal engine with one method call site."
    assert (engine.lineno, engine.end_lineno) == (6, 18)
    assert symbols["Engine.start"].kind is SymbolKind.METHOD
    assert symbols["Engine.start"].parameters == ["self"]
    assert symbols["build_engine"].kind is SymbolKind.FUNCTION
    assert symbols["build_engine"].lineno == 21
    assert ("self.start", "Engine.run") in _call_pairs(bundle)


def test_extract_inheritance(sample_project_path: Path) -> None:
    bundle = extract_file(sample_project_path / "inheritance.py", root=sample_project_path)
    symbols = _by_qualname(bundle)

    assert {"Animal", "Dog", "Puppy"} <= set(symbols)
    assert symbols["Animal"].bases == []
    assert symbols["Dog"].bases == ["Animal"]
    assert symbols["Puppy"].bases == ["Dog", "Animal"]
    assert "Dog.speak" in symbols and "Animal.speak" in symbols
    calls = _call_pairs(bundle)
    assert ("super.__init__", "Dog.__init__") in calls
    assert ("super.speak", "Puppy.speak") in calls


def test_extract_calls(sample_project_path: Path) -> None:
    bundle = extract_file(sample_project_path / "calls.py", root=sample_project_path)
    calls = _call_pairs(bundle)

    assert ("run_pipeline", "calls") in calls
    assert ("build_engine", "run_pipeline") in calls
    assert ("self.engine.run", "Harness.dispatch") in calls
    assert ("obj.start", "chained") in calls
    assert ("obj.start.upper", "chained") in calls
    imported = {(ref.module, ref.name) for ref in bundle.imports}
    assert (".core", "Engine") in imported
    assert (".core", "build_engine") in imported


def test_extract_async(sample_project_path: Path) -> None:
    bundle = extract_file(sample_project_path / "async_ops.py", root=sample_project_path)
    symbols = _by_qualname(bundle)

    assert symbols["fetch"].kind is SymbolKind.FUNCTION
    assert symbols["fetch"].is_async is True
    assert symbols["Fetcher.fetch"].kind is SymbolKind.METHOD
    assert symbols["Fetcher.fetch"].is_async is True
    awaited = [
        call
        for call in bundle.calls
        if call.callee == "self.client.get" and call.caller_qualname == "Fetcher.fetch"
    ]
    assert len(awaited) == 1
    assert awaited[0].is_await is True


def test_extract_decorators(sample_project_path: Path) -> None:
    bundle = extract_file(sample_project_path / "decorators.py", root=sample_project_path)
    symbols = _by_qualname(bundle)

    assert symbols["index"].decorators == ['app.route("/")']
    assert symbols["about"].decorators == ['app.route("/about", methods=["GET"])']
    assert symbols["cached"].decorators == ["functools.cache"]
    assert symbols["me"].decorators == ["login_required", 'app.route("/me")']
    assert all(call.callee not in {"app.route", "functools.cache"} for call in bundle.calls)
    assert bundle.calls == []


def test_extract_nested(sample_project_path: Path) -> None:
    bundle = extract_file(sample_project_path / "nested.py", root=sample_project_path)
    symbols = _by_qualname(bundle)

    assert symbols["Outer.Inner"].kind is SymbolKind.CLASS
    assert symbols["Outer.Inner.reveal"].kind is SymbolKind.METHOD
    assert symbols["Outer.method.helper"].kind is SymbolKind.FUNCTION
    assert symbols["Outer.method.helper"].parent_id == "Outer.method"
    calls = _call_pairs(bundle)
    assert ("helper", "Outer.method") in calls
    assert ("utility", "Outer.method.helper") in calls


def test_extract_imports_mess(sample_project_path: Path) -> None:
    bundle = extract_file(sample_project_path / "imports_mess.py", root=sample_project_path)
    refs = {
        (ref.module, ref.name, ref.alias, ref.is_from, ref.level)
        for ref in bundle.imports
    }

    assert ("os", None, None, False, 0) in refs
    assert ("os.path", None, "osp", False, 0) in refs
    assert ("collections", "OrderedDict", None, True, 0) in refs
    assert ("collections.abc", "Mapping", "ABCMapping", True, 0) in refs
    assert (".", "service", None, True, 1) in refs
    assert (".core", "Engine", None, True, 1) in refs
    assert len(refs) == len(bundle.imports)
    assert all(isinstance(ref, ImportRef) for ref in bundle.imports)


def test_extract_params(sample_project_path: Path) -> None:
    bundle = extract_file(sample_project_path / "params.py", root=sample_project_path)
    symbols = _by_qualname(bundle)

    assert symbols["full_shape"].parameters == [
        "a: int",
        "b: str",
        "c: float",
        "*args",
        "d: bool",
        "e: str",
        "**kwargs",
    ]
    assert symbols["keyword_only"].parameters == ["*", "key: str"]
    assert symbols["full_shape"].returns == "int"
    assert symbols["full_shape"].signature == (
        "full_shape(a: int, b: str, c: float, *args, d: bool, e: str, **kwargs)"
    )
    assert all("=" not in parameter for parameter in symbols["full_shape"].parameters)


def test_defaults_and_annotations_do_not_emit_calls() -> None:
    source = '''def helper() -> int:
    return 1


def annotated(value: helper() = helper()) -> helper():
    return value
'''

    bundle = extract_source(source, module="calls_in_annotations", file="calls.py")

    assert bundle.calls == []


def test_lambda_and_comprehension_calls_use_enclosing_function() -> None:
    source = '''def helper() -> int:
    return 1


def outer() -> int:
    callback = lambda: helper()
    values = [helper() for _ in range(2)]
    return callback() + sum(values)
'''

    bundle = extract_source(source, module="lambdas", file="lambdas.py")
    callers = _call_pairs(bundle)

    assert ("helper", "outer") in callers
    assert ("callback", "outer") in callers
    assert ("range", "outer") in callers
    assert ("sum", "outer") in callers


def test_subscript_callee_is_flattened() -> None:
    source = "def run() -> None:\n    handlers[0]()\n"

    bundle = extract_source(source, module="subscripts", file="subscripts.py")

    assert [(call.callee, call.caller_qualname) for call in bundle.calls] == [
        ("handlers[]", "run")
    ]


def test_empty_file_returns_empty_bundle() -> None:
    bundle = extract_source("", module="empty", file="empty.py")

    assert bundle.module == "empty"
    assert bundle.file == "empty.py"
    assert bundle.symbols == []
    assert bundle.imports == []
    assert bundle.calls == []
    assert bundle.parse_errors == []


def test_comment_only_file_returns_empty_bundle() -> None:
    bundle = extract_source("# only a comment\n", module="comments", file="comments.py")

    assert bundle.symbols == []
    assert bundle.imports == []
    assert bundle.calls == []


def test_syntax_error_raises_scribe_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("def broken(:\n    pass\n", encoding="utf-8")

    with pytest.raises(ScribeError, match="parse failed"):
        extract_file(broken, root=tmp_path)


def test_decode_error_raises_scan_error(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.py"
    invalid.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(ScanError, match="decode failed"):
        extract_file(invalid, root=tmp_path)


def test_top_level_init_is_rejected(sample_project_path: Path) -> None:
    with pytest.raises(ScanError, match="top-level __init__"):
        extract_file(sample_project_path / "__init__.py", root=sample_project_path)


def test_package_init_derives_package_module(sample_project_path: Path) -> None:
    bundle = extract_file(
        sample_project_path / "__init__.py",
        root=sample_project_path.parent,
    )

    assert bundle.module == "sample_project"
    assert bundle.file == "sample_project/__init__.py"
    assert bundle.symbols == []


@pytest.mark.parametrize(
    ("fixture", "expected_symbols"),
    [
        ("core.py", 5),
        ("service.py", 3),
        ("inheritance.py", 8),
        ("calls.py", 5),
        ("async_ops.py", 7),
        ("decorators.py", 5),
        ("nested.py", 6),
        ("imports_mess.py", 1),
        ("params.py", 2),
    ],
)
def test_fixture_symbol_counts(
    sample_project_path: Path,
    fixture: str,
    expected_symbols: int,
) -> None:
    bundle = extract_file(sample_project_path / fixture, root=sample_project_path)

    assert len(bundle.symbols) == expected_symbols
    assert len({symbol.qualname for symbol in bundle.symbols}) == expected_symbols


def test_extract_overload_keeps_implementation() -> None:
    """@overload redefinitions (stubs + impl) collapse to the last (Python) one."""
    src = """
from typing import overload
@overload
def f(x: int) -> int: ...
@overload
def f(x: str) -> str: ...
def f(x):
    return x
"""
    bundle = extract_source(src, module="m", file="m.py")

    f_symbols = [s for s in bundle.symbols if s.name == "f"]
    assert len(f_symbols) == 1


def test_extract_redefinition_keeps_last_body() -> None:
    """A plain redefinition keeps the final definition's body location."""
    src = "def g():\n    return 1\n\ndef g():\n    return 2\n"
    bundle = extract_source(src, module="m", file="m.py")

    g_symbols = [s for s in bundle.symbols if s.name == "g"]
    assert len(g_symbols) == 1
    # The kept symbol is the LAST definition (starts on line 4).
    assert g_symbols[0].lineno == 4
