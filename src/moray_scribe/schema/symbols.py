"""Scanner payloads: raw symbols, imports and call references extracted from source."""

from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from .base import StrictModel


class SymbolKind(str, Enum):
    """Kind of a symbol discovered by the scanner."""

    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    CONSTANT = "constant"
    IMPORT = "import"
    UNKNOWN = "unknown"


class ImportRef(StrictModel):
    """A single imported binding.

    One reference is emitted per imported name so ``from pkg import A, B`` yields
    two records and the graph builder can wire each binding independently.
    """

    module: str = Field(min_length=1, description="Dotted module path being imported.")
    name: str | None = Field(default=None, description="Imported name; None for `import x`.")
    alias: str | None = Field(default=None, description="Local alias, if any.")
    is_from: bool = Field(
        default=False,
        description="Whether this came from a `from ... import`.",
    )
    level: int = Field(
        default=0,
        ge=0,
        description="Relative import depth (number of leading dots).",
    )
    lineno: int = Field(ge=1, description="1-based line number of the import statement.")


class RouteInfo(StrictModel):
    """An HTTP route registration detected from a decorator.

    The scanner fills this only when the decorator text alone proves both the
    path (a ``/``-rooted string literal) and at least one HTTP method, so the
    field is a fact, not a guess.
    """

    path: str = Field(min_length=1, description="URL path as written in the decorator.")
    methods: list[str] = Field(
        default_factory=lambda: ["GET"],
        description="Uppercase HTTP methods accepted by the route.",
    )
    decorator: str = Field(
        default="",
        description="Raw decorator source text this route was parsed from.",
    )


class UrlPatternRef(StrictModel):
    """One Django urlconf entry mapping a URL path to a view callable.

    Django registers routes in ``urls.py`` rather than on the view itself, so
    the scanner records ``path("login/", views.login)`` here and the grapher
    attaches the resulting :class:`RouteInfo` to the resolved view node.
    """

    path: str = Field(min_length=1, description="URL pattern as written.")
    target: str = Field(
        min_length=1,
        description="View expression chain, e.g. `views.login` or `LoginView.as_view`.",
    )
    caller_qualname: str = Field(
        min_length=1,
        description="Qualname of the enclosing scope; the module name at module level.",
    )
    lineno: int = Field(ge=1, description="1-based line number of the urlconf call.")


class AttrBinding(StrictModel):
    """A ``self.<attr> = <expr>`` (or ``setattr(self, "<attr>", <expr>)``) site.

    The scanner records the assignment verbatim; deciding whether ``<expr>``
    denotes a class is the grapher's job (R-2). ``owner_qualname`` is the
    module-internal qualname of the enclosing class, so ``self.engine.run()``
    inside any method of that class can later be tied to a concrete type.
    """

    attr: str = Field(min_length=1, description="Attribute name assigned on self.")
    value: str = Field(min_length=1, description="Right-hand side source text.")
    owner_qualname: str = Field(
        min_length=1,
        description="Module-internal qualname of the class owning the attribute.",
    )
    caller_qualname: str = Field(
        min_length=1,
        description="Qualname of the enclosing function; the module name at class body level.",
    )
    lineno: int = Field(ge=1, description="1-based line number of the assignment.")


class DynamicImportRef(StrictModel):
    """A literal-string dynamic import (``importlib.import_module("x.y")``).

    Only constant-string targets are recorded; computed module names are left
    to the LLM inference stage.
    """

    module: str = Field(min_length=1, description="Literal dotted module path requested.")
    caller_qualname: str = Field(
        min_length=1,
        description="Qualname of the enclosing function; the module name for module-level calls.",
    )
    lineno: int = Field(ge=1, description="1-based line number of the call.")


class DanglingCall(StrictModel):
    """An unresolved call site handed to the LLM inference stage.

    ``caller_id`` is a graph node id; ``callee`` keeps the raw source chain.
    Produced by the grapher, consumed by ``describer.inferencer``.
    """

    caller_id: str = Field(min_length=1, description="Graph node id of the caller.")
    callee: str = Field(min_length=1, description="Full source attribute chain of the callee.")
    lineno: int = Field(ge=1, description="1-based line number of the call.")


class CallRef(StrictModel):
    """An unresolved call site discovered inside a function body.

    ``callee`` keeps the full source attribute chain (``self.engine.run``,
    ``os.path.join``); alias resolution and symbol matching happen in the
    grapher, not here (R-2).
    """

    callee: str = Field(min_length=1, description="Full source attribute chain of the callee.")
    caller_qualname: str = Field(
        min_length=1,
        description="Qualname of the enclosing function; the module name for module-level calls.",
    )
    resolved: str | None = Field(
        default=None,
        description="Fully qualified target when the scanner can resolve it.",
    )
    lineno: int = Field(ge=1, description="1-based line number of the call.")
    col_offset: int = Field(default=0, ge=0, description="0-based column offset of the call.")
    is_await: bool = Field(default=False, description="Whether the call is awaited.")


class RawSymbol(StrictModel):
    """One module, class, function, method or module-level binding.

    ``id`` and ``qualname`` are the qualified name inside the owning module
    (``Engine.start``); composing the cross-module identifier is the grapher's
    job.
    """

    id: str = Field(
        min_length=1,
        description="Qualified name inside the module, e.g. `Engine.start`.",
    )
    name: str = Field(min_length=1, description="Short symbol name.")
    qualname: str = Field(min_length=1, description="Qualified name inside its module.")
    kind: SymbolKind = Field(description="Symbol kind.")
    module: str = Field(min_length=1, description="Dotted module path that owns the symbol.")
    file: str = Field(min_length=1, description="Scan-root-relative source path.")
    lineno: int = Field(ge=1, description="1-based first line of the definition.")
    end_lineno: int | None = Field(
        default=None,
        ge=1,
        description="1-based last line of the definition, when known.",
    )
    col_offset: int = Field(default=0, ge=0, description="0-based column of the definition.")
    signature: str | None = Field(default=None, description="Rendered signature, if any.")
    docstring: str | None = Field(default=None, description="Cleaned docstring, if present.")
    decorators: list[str] = Field(default_factory=list, description="Decorator expressions.")
    bases: list[str] = Field(default_factory=list, description="Base class expressions.")
    parameters: list[str] = Field(default_factory=list, description="Parameter names.")
    returns: str | None = Field(default=None, description="Return annotation as source text.")
    is_async: bool = Field(default=False, description="Whether the definition is async.")
    is_public: bool = Field(default=True, description="Whether the name is part of the public API.")
    parent_id: str | None = Field(
        default=None,
        description="Enclosing class or function qualname for nested definitions.",
    )
    complexity: int = Field(default=0, ge=0, description="Cyclomatic complexity estimate.")
    tags: list[str] = Field(default_factory=list, description="Free-form scanner tags.")
    route: RouteInfo | None = Field(
        default=None,
        description="HTTP route bound to this symbol by a recognized decorator, if any.",
    )

    @model_validator(mode="after")
    def _check_span(self) -> RawSymbol:
        if self.end_lineno is not None and self.end_lineno < self.lineno:
            raise ValueError(
                f"symbol {self.id!r} ends before it starts "
                f"({self.lineno} > {self.end_lineno})"
            )
        if self.parent_id == self.id:
            raise ValueError(f"symbol {self.id!r} cannot be its own parent")
        return self


class RawSymbolBundle(StrictModel):
    """Everything the scanner learned from a single source file."""

    module: str = Field(min_length=1, description="Dotted module path.")
    file: str = Field(min_length=1, description="Scan-root-relative source path.")
    symbols: list[RawSymbol] = Field(default_factory=list, description="Definitions in the file.")
    imports: list[ImportRef] = Field(default_factory=list, description="Import statements.")
    calls: list[CallRef] = Field(
        default_factory=list,
        description="Call sites in the file, each tagged with its enclosing caller.",
    )
    parse_errors: list[str] = Field(
        default_factory=list,
        description="Recoverable parser diagnostics; an empty list means a clean parse.",
    )
    attr_bindings: list[AttrBinding] = Field(
        default_factory=list,
        description="self.<attr> assignments and setattr(self, ...) sites in the file.",
    )
    dynamic_imports: list[DynamicImportRef] = Field(
        default_factory=list,
        description="Literal-string importlib.import_module/__import__ call sites.",
    )
    url_patterns: list[UrlPatternRef] = Field(
        default_factory=list,
        description="Django urlconf path()/re_path()/url() entries in the file.",
    )
    source_hash: str | None = Field(
        default=None,
        description="Content hash used for cache invalidation.",
    )

    @model_validator(mode="after")
    def _check_unique_symbol_ids(self) -> RawSymbolBundle:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for symbol in self.symbols:
            if symbol.id in seen:
                duplicates.add(symbol.id)
            seen.add(symbol.id)
        if duplicates:
            raise ValueError(f"duplicate symbol ids: {sorted(duplicates)}")
        return self
