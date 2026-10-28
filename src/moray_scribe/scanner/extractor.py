"""libcst-backed symbol extraction for individual Python files."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import libcst as cst
from libcst.metadata import CodeRange, MetadataWrapper, PositionProvider

from moray_scribe.exceptions import ScanError, ScribeError
from moray_scribe.schema import (
    CallRef,
    ImportRef,
    RawSymbol,
    RawSymbolBundle,
    SymbolKind,
)


@dataclass
class _Scope:
    """One nesting level on the visitor's scope stack."""

    kind: Literal["module", "class", "function"]
    names: list[str]  # Name chain from the module, e.g. ["Engine", "start"].
    is_classmethod_ctx: bool = False


def _flatten_callee(expr: cst.BaseExpression) -> str | None:
    """Flatten a callee expression into its source attribute chain.

    Per R-2 the chain is kept verbatim (``self.engine.run``, ``os.path.join``);
    alias resolution happens in the grapher. Returns None for callee shapes the
    scanner deliberately skips (lambdas, awaits and other non-name bases).
    """
    if isinstance(expr, cst.Name):
        return expr.value
    if isinstance(expr, cst.Attribute):
        base = _flatten_callee(expr.value)
        return None if base is None else f"{base}.{expr.attr.value}"
    if isinstance(expr, cst.Call):
        return _flatten_callee(expr.func)
    if isinstance(expr, cst.Subscript):
        base = _flatten_callee(expr.value)
        return None if base is None else f"{base}[]"
    return None


def _relative_level(node: cst.ImportFrom) -> int:
    """Number of leading dots for a relative import.

    libcst <=1.6 exposes ``relative`` as an int while newer releases expose a
    tuple of Dot nodes; the project supports ``libcst>=1.4``, so accept both.
    """
    relative: object = node.relative
    if isinstance(relative, int):
        return relative
    if isinstance(relative, tuple):
        return len(relative)
    return 0


def _derive_module(target: Path, base: Path) -> str:
    """Derive the dotted module name of ``target`` relative to ``base``."""
    try:
        relative = target.relative_to(base)
    except ValueError as exc:
        raise ScanError(f"{target} is not under scan root {base}") from exc
    parts = list(relative.with_suffix("").parts)
    if parts == ["__init__"]:
        raise ScanError(
            f"{target} is a top-level __init__.py; scan a directory that "
            "contains it as a package"
        )
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    module = ".".join(parts)
    if not module:
        raise ScanError(f"cannot derive a module name for {target} under {base}")
    return module


class _SymbolVisitor(cst.CSTVisitor):
    """Single-pass visitor collecting symbols, imports and call references.

    Scope bookkeeping happens in ``visit_*``/``leave_*`` pairs. libcst always
    fires ``on_leave`` for every entered node, so the scope and suppression
    stacks stay balanced even for one-liner definitions.
    """

    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, *, module: str, file: str, tree: cst.Module) -> None:
        self._module = module
        self._file = file
        self._tree = tree
        self._scopes: list[_Scope] = []
        self._symbols: list[RawSymbol] = []
        self._imports: list[ImportRef] = []
        self._calls: list[CallRef] = []
        # R-2: the scanner never rewrites callees with this table; it documents
        # the local binding environment and is reconstructible from ``imports``.
        self._aliases: dict[str, tuple[str, str | None]] = {}
        self._suppress_depth = 0
        self._await_calls: set[int] = set()

    # -- helpers ------------------------------------------------------------

    def _position(self, node: cst.CSTNode) -> CodeRange:
        return self.get_metadata(PositionProvider, node)

    def _code(self, node: cst.CSTNode) -> str:
        return self._tree.code_for_node(node).strip()

    def _current_chain(self) -> list[str]:
        return list(self._scopes[-1].names) if self._scopes else []

    def _parent_qualname(self) -> str | None:
        chain = self._current_chain()
        return ".".join(chain) if chain else None

    def _caller_qualname(self) -> str:
        for scope in reversed(self._scopes):
            if scope.kind == "function":
                return ".".join(scope.names)
        # Module-level call; the grapher hangs these on a module node.
        return self._module

    def _render_param(self, param: cst.Param) -> str:
        if param.annotation is None:
            return param.name.value
        return f"{param.name.value}: {self._code(param.annotation.annotation)}"

    def _render_params(self, params: cst.Parameters) -> list[str]:
        rendered = [self._render_param(p) for p in (*params.posonly_params, *params.params)]
        if isinstance(params.star_arg, cst.Param):
            rendered.append(f"*{params.star_arg.name.value}")
        elif isinstance(params.star_arg, cst.ParamStar):
            rendered.append("*")
        rendered.extend(self._render_param(p) for p in params.kwonly_params)
        if params.star_kwarg is not None:
            rendered.append(f"**{params.star_kwarg.name.value}")
        return rendered

    # -- scopes -------------------------------------------------------------

    def visit_Module(self, node: cst.Module) -> None:
        self._scopes.append(_Scope(kind="module", names=[]))

    def leave_Module(self, node: cst.Module) -> None:
        if len(self._scopes) != 1:
            raise ScanError(f"unbalanced scope stack after extracting {self._file}")
        self._scopes.pop()

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        chain = [*self._current_chain(), node.name.value]
        position = self._position(node)
        # TODO: ClassDef.keywords (metaclass=...) are not captured.
        self._symbols.append(
            RawSymbol(
                id=".".join(chain),
                name=node.name.value,
                qualname=".".join(chain),
                kind=SymbolKind.CLASS,
                module=self._module,
                file=self._file,
                lineno=position.start.line,
                end_lineno=position.end.line,
                col_offset=position.start.column,
                docstring=node.get_docstring(clean=True),
                decorators=[self._code(d.decorator) for d in node.decorators],
                bases=[self._code(base.value) for base in node.bases],
                parent_id=self._parent_qualname(),
                is_public=not node.name.value.startswith("_"),
            )
        )
        self._scopes.append(_Scope(kind="class", names=chain))

    def leave_ClassDef(self, node: cst.ClassDef) -> None:
        self._scopes.pop()

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        name = node.name.value
        chain = [*self._current_chain(), name]
        position = self._position(node)
        is_method = self._scopes[-1].kind == "class"
        params = self._render_params(node.params)
        decorators = [self._code(d.decorator) for d in node.decorators]
        self._symbols.append(
            RawSymbol(
                id=".".join(chain),
                name=name,
                qualname=".".join(chain),
                kind=SymbolKind.METHOD if is_method else SymbolKind.FUNCTION,
                module=self._module,
                file=self._file,
                lineno=position.start.line,
                end_lineno=position.end.line,
                col_offset=position.start.column,
                signature=f"{name}({', '.join(params)})",
                docstring=node.get_docstring(clean=True),
                decorators=decorators,
                parameters=params,
                returns=(
                    self._code(node.returns.annotation)
                    if node.returns is not None
                    else None
                ),
                is_async=node.asynchronous is not None,
                is_public=not name.startswith("_"),
                parent_id=self._parent_qualname(),
            )
        )
        self._scopes.append(
            _Scope(
                kind="function",
                names=chain,
                is_classmethod_ctx=any(
                    text.split("(", 1)[0] in {"classmethod", "staticmethod"}
                    for text in decorators
                ),
            )
        )

    def leave_FunctionDef(self, node: cst.FunctionDef) -> None:
        self._scopes.pop()

    # -- suppression regions (decorators, defaults, annotations) ------------

    def visit_Decorator(self, node: cst.Decorator) -> None:
        self._suppress_depth += 1

    def leave_Decorator(self, node: cst.Decorator) -> None:
        self._suppress_depth -= 1

    def visit_Param(self, node: cst.Param) -> None:
        self._suppress_depth += 1

    def leave_Param(self, node: cst.Param) -> None:
        self._suppress_depth -= 1

    def visit_Annotation(self, node: cst.Annotation) -> None:
        self._suppress_depth += 1

    def leave_Annotation(self, node: cst.Annotation) -> None:
        self._suppress_depth -= 1

    # -- calls --------------------------------------------------------------

    def visit_Await(self, node: cst.Await) -> None:
        if isinstance(node.expression, cst.Call):
            self._await_calls.add(id(node.expression))

    def visit_Call(self, node: cst.Call) -> None:
        if self._suppress_depth > 0:
            return
        callee = _flatten_callee(node.func)
        if callee is None:
            return
        position = self._position(node)
        self._calls.append(
            CallRef(
                callee=callee,
                caller_qualname=self._caller_qualname(),
                lineno=position.start.line,
                col_offset=position.start.column,
                is_await=id(node) in self._await_calls,
            )
        )

    # -- imports ------------------------------------------------------------

    def visit_Import(self, node: cst.Import) -> None:
        lineno = self._position(node).start.line
        for alias in node.names:
            dotted = self._code(alias.name)
            asname = self._code(alias.asname.name) if alias.asname is not None else None
            self._imports.append(
                ImportRef(
                    module=dotted,
                    name=None,
                    alias=asname,
                    is_from=False,
                    level=0,
                    lineno=lineno,
                )
            )
            local = asname if asname is not None else dotted.split(".", 1)[0]
            self._aliases[local] = (dotted, None)

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        lineno = self._position(node).start.line
        base = self._code(node.module) if node.module is not None else ""
        level = _relative_level(node)
        module_name = "." * level + base
        names = node.names
        if isinstance(names, cst.ImportStar):
            # TODO: decide whether star imports deserve a record.
            return
        for alias in names:
            if isinstance(alias.name, cst.ImportStar):
                continue
            name = self._code(alias.name)
            asname = self._code(alias.asname.name) if alias.asname is not None else None
            self._imports.append(
                ImportRef(
                    module=module_name,
                    name=name,
                    alias=asname,
                    is_from=True,
                    level=level,
                    lineno=lineno,
                )
            )
            self._aliases[asname or name] = (module_name, name)

    # -- results ------------------------------------------------------------

    @property
    def symbols(self) -> list[RawSymbol]:
        return self._symbols

    @property
    def imports(self) -> list[ImportRef]:
        return self._imports

    @property
    def calls(self) -> list[CallRef]:
        return self._calls


def _extract_source(
    source: str,
    *,
    module: str,
    file: str,
    error_label: str,
) -> RawSymbolBundle:
    """Internal extraction shared by the string and file entry points."""
    try:
        tree = cst.parse_module(source)
    except cst.ParserSyntaxError as exc:
        raise ScribeError(f"parse failed: {error_label}: {exc}") from exc
    visitor = _SymbolVisitor(module=module, file=file, tree=tree)
    MetadataWrapper(tree).visit(visitor)
    # Python semantics: a same-name redefinition replaces the earlier one, so
    # under @overload the LAST definition is the implementation. This compatibility
    # fix was surfaced by a real project (requests/auth.py), see
    # docs/realworld-report.md problem 1. Keep the LAST definition per qualname;
    # order is preserved (first insertion for ordering, last value for content).
    deduped: dict[str, RawSymbol] = {}
    for symbol in visitor.symbols:
        deduped[symbol.id] = symbol
    return RawSymbolBundle(
        module=module,
        file=file,
        symbols=list(deduped.values()),
        imports=visitor.imports,
        calls=visitor.calls,
        parse_errors=[],
        source_hash=hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )


def extract_source(source: str, *, module: str, file: str) -> RawSymbolBundle:
    """Extract symbols, imports and calls from in-memory source.

    ``module`` and ``file`` are provided by the caller; ``file`` is a
    scan-root-relative posix path used for display and error messages.
    """
    return _extract_source(source, module=module, file=file, error_label=file)


def extract_file(path: Path, *, root: Path | None = None) -> RawSymbolBundle:
    """Read and extract one file; ``root`` anchors the relative module name.

    ``root`` defaults to the file's parent directory, so ``core.py`` yields
    module ``core``. A top-level ``__init__.py`` cannot be named as a module
    and raises :class:`ScanError`; scan a directory that contains it as a
    package instead.
    """
    base = (root if root is not None else path.parent).resolve()
    target = path.resolve()
    module = _derive_module(target, base)
    try:
        source = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ScanError(f"decode failed: {path}") from exc
    except OSError as exc:
        raise ScanError(f"read failed: {path}: {exc}") from exc
    try:
        file = target.relative_to(base).as_posix()
    except ValueError:
        file = target.as_posix()
    return _extract_source(source, module=module, file=file, error_label=str(path))
