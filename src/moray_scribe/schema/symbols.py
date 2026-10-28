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
