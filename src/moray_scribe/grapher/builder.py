"""Merge scanner bundles into a conservative symbol graph.

The guiding rule (§2) is: only create an edge when the target is 100%
identifiable. Anything ambiguous, dynamic, external or attribute-typed is left
unresolved rather than guessed, because a fabricated edge misleads the
downstream LLM more than a missing one.
"""

from __future__ import annotations

from collections.abc import Iterable

from loguru import logger

from moray_scribe.exceptions import GraphError
from moray_scribe.schema import (
    EdgeKind,
    ImportRef,
    RawSymbolBundle,
    SymbolEdge,
    SymbolGraph,
    SymbolKind,
    SymbolNode,
)

from .analyzer import annotate_degrees, detect_dead_code, detect_entry_points


def _node_id(module: str, qualname: str) -> str:
    """Compose the cross-module symbol id from a module and a short qualname."""
    return f"{module}.{qualname}" if module else qualname


class _Indexes:
    """Per-build lookup tables shared by every resolution helper."""

    def __init__(self) -> None:
        # module -> short qualname -> node id
        self.symbols: dict[str, dict[str, str]] = {}
        # module -> class name -> node id
        self.classes: dict[str, dict[str, str]] = {}
        # module -> local alias -> (target module, original name or None)
        self.aliases: dict[str, dict[str, tuple[str, str | None]]] = {}
        # module name -> module node id
        self.modules: dict[str, str] = {}

    def sym(self, module: str, qualname: str) -> str | None:
        return self.symbols.get(module, {}).get(qualname)

    def cls(self, module: str, name: str) -> str | None:
        return self.classes.get(module, {}).get(name)

    def alias(self, module: str, local: str) -> tuple[str, str | None] | None:
        return self.aliases.get(module, {}).get(local)


def _resolve_relative(module: str, level: int, base: str) -> str | None:
    """Resolve a relative import to an absolute dotted path.

    Returns ``""`` for a top-level package (``from . import x`` in a root
    module) and ``None`` when the level exceeds the module depth.
    """
    if level <= 0:
        return base
    parts = module.split(".")
    if level > len(parts):
        return None
    prefix = parts[: len(parts) - level]
    if base:
        return ".".join([*prefix, *base.split(".")]) if prefix else base
    return ".".join(prefix)


def _resolve_plain_dotted(
    module: str,
    segments: list[str],
    idx: _Indexes,
) -> str | None:
    """Resolve a dotted callee whose first segment is an import alias.

    Handles three shapes: ``from x import y [as z]`` (bare ``z`` only),
    ``import a.b [as c]`` no-alias (self-describing chain), and
    ``import a.b as c`` (alias binds the full module).
    """
    first = segments[0]
    entry = idx.alias(module, first)
    if entry is None:
        return None
    mod, orig = entry
    if orig is not None:
        # `from x import y [as z]`: z binds the object x.y, so z.attr is an
        # attribute of unknown type and cannot be confirmed.
        if len(segments) == 1:
            return idx.sym(mod, orig)
        return None
    # Plain import local -> (mod, None).
    if mod == first or mod.startswith(f"{first}."):
        # No-alias import: the callee chain is already the dotted path. Walk
        # from the longest prefix to find the deepest known module.
        for i in range(len(segments), 0, -1):
            prefix = ".".join(segments[:i])
            if prefix in idx.modules:
                if i == len(segments):
                    return None  # calling a module object itself
                rest = ".".join(segments[i:])
                return idx.sym(prefix, rest)
        return None
    # `import a.b as c`: c binds module a.b, so strip the alias and look up rest.
    rest = ".".join(segments[1:])
    if not rest:
        return None
    return idx.sym(mod, rest)


def _resolve_bare(module: str, name: str, idx: _Indexes) -> str | None:
    """Resolve a no-dot callee against same-module symbols then import aliases."""
    found = idx.sym(module, name)
    if found is not None:
        return found
    found = idx.cls(module, name)
    if found is not None:
        return found
    entry = idx.alias(module, name)
    if entry is not None:
        mod, orig = entry
        if orig is not None:
            return idx.sym(mod, orig)
    return None


def _resolve_call(module: str, callee: str, idx: _Indexes) -> str | None:
    """Resolve a scanner callee chain to a node id, or None when unconfirmed."""
    segments = callee.split(".")
    if len(segments) == 1:
        return _resolve_bare(module, segments[0], idx)
    return _resolve_plain_dotted(module, segments, idx)


def _simplify_base(text: str) -> str:
    """Strip generic brackets from a base-class expression: ``Generic[T]`` -> ``Generic``."""
    out: list[str] = []
    depth = 0
    for char in text:
        if char == "[":
            depth += 1
        elif char == "]":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(char)
    return "".join(out).strip()


def _resolve_base_class(module: str, base_text: str, idx: _Indexes) -> str | None:
    """Resolve a class base to a node id, same-module first then via aliases."""
    name = _simplify_base(base_text)
    if not name:
        return None
    if "." in name:
        return _resolve_plain_dotted(module, name.split("."), idx)
    found = idx.cls(module, name)
    if found is not None:
        return found
    entry = idx.alias(module, name)
    if entry is not None:
        mod, orig = entry
        if orig is not None:
            return idx.sym(mod, orig)
    return None


def _resolve_super(
    module: str,
    callee: str,
    caller_qualname: str,
    idx: _Indexes,
    inherits: dict[str, list[str]],
    graph: SymbolGraph,
) -> str | None:
    """Resolve ``super().method()`` to a parent's method when unambiguous.

    We do not compute the MRO, so when several parents define the method the
    call is left unresolved (conservative: better no edge than a wrong one).
    """
    rest = callee[len("super.") :] if callee.startswith("super.") else ""
    if not rest or "." not in caller_qualname:
        return None
    class_name = caller_qualname.split(".")[0]
    class_id = idx.cls(module, class_name)
    if class_id is None:
        return None
    candidates: list[str] = []
    for parent_id in inherits.get(class_id, []):
        parent = graph.nodes.get(parent_id)
        if parent is None:
            continue
        target = idx.sym(parent.module, f"{parent.qualname}.{rest}")
        if target is not None:
            candidates.append(target)
    return candidates[0] if len(candidates) == 1 else None


def _caller_node_id(
    module: str,
    module_id: str,
    caller_qualname: str,
    graph: SymbolGraph,
) -> str:
    """Map a scanner caller_qualname to a node id, falling back to the module.

    The scanner emits the bare module name for true module-level calls, so a
    caller equal to the module resolves to the MODULE node; anything else is a
    function/method qualname and is looked up as a symbol.
    """
    if caller_qualname == module:
        return module_id
    candidate = _node_id(module, caller_qualname)
    return candidate if candidate in graph.nodes else module_id


def _record_import_alias(idx: _Indexes, module: str, ref: ImportRef) -> None:
    """Index one ImportRef into the module's local-alias table (phase 1)."""
    target_module = ref.module
    if ref.level > 0:
        resolved = _resolve_relative(module, ref.level, ref.module.lstrip("."))
        if resolved is None:
            return
        target_module = resolved
    table = idx.aliases.setdefault(module, {})
    if ref.is_from:
        if ref.name is None:
            return
        table[ref.alias or ref.name] = (target_module, ref.name)
    else:
        table[ref.alias or ref.module.split(".", 1)[0]] = (target_module, None)


def _emit_import_edges(
    idx: _Indexes,
    module: str,
    module_id: str,
    ref: ImportRef,
    pending: dict[tuple[str, str, EdgeKind], float],
) -> None:
    """Add an IMPORTS edge when the imported module is internal (phase 2)."""
    target_module = ref.module
    if ref.level > 0:
        resolved = _resolve_relative(module, ref.level, ref.module.lstrip("."))
        if resolved is None:
            return
        target_module = resolved

    target_id: str | None = None
    if ref.is_from:
        if target_module and target_module in idx.modules:
            target_id = idx.modules[target_module]
        elif target_module:
            candidate = f"{target_module}.{ref.name}"
            if candidate in idx.modules:
                target_id = idx.modules[candidate]
        elif ref.name and ref.name in idx.modules:
            target_id = idx.modules[ref.name]
    else:
        parts = target_module.split(".")
        for i in range(len(parts), 0, -1):
            prefix = ".".join(parts[:i])
            if prefix in idx.modules:
                target_id = idx.modules[prefix]
                break
    if target_id is None:
        return
    key = (module_id, target_id, EdgeKind.IMPORTS)
    pending[key] = pending.get(key, 0.0) + 1.0


def build_symbol_graph(
    bundles: Iterable[RawSymbolBundle],
    *,
    strict: bool = False,
    debug_out: list[tuple[str, str]] | None = None,
) -> SymbolGraph:
    """Merge scanner bundles into one :class:`SymbolGraph`.

    ``strict`` runs ``validate_edges`` after construction and raises
    :class:`GraphError` on any dangling edge. ``debug_out`` collects unresolved
    bare calls as ``(caller_id, callee)`` for tuning, never entering the graph.
    """
    materialized = list(bundles)
    graph = SymbolGraph()
    idx = _Indexes()
    pending: dict[tuple[str, str, EdgeKind], float] = {}
    inherits: dict[str, list[str]] = {}
    unresolved: list[tuple[str, str]] = []

    def _emit(src: str | None, dst: str | None, kind: EdgeKind) -> None:
        if src is None or dst is None or src == dst:
            return
        key = (src, dst, kind)
        pending[key] = pending.get(key, 0.0) + 1.0

    # -- Phase 1: nodes + indexes ------------------------------------------
    for bundle in materialized:
        module = bundle.module
        module_id = module if module else f"_file_{bundle.file}"
        if module in idx.modules:
            logger.warning("duplicate module {}: keeping first", module)
        else:
            idx.modules[module] = module_id
            facade = bundle.file.replace("\\", "/").endswith("__init__.py")
            graph.add_node(
                SymbolNode(
                    id=module_id,
                    name=module.split(".")[-1] or module or bundle.file,
                    qualname="",
                    kind=SymbolKind.MODULE,
                    module=module or bundle.file,
                    file=bundle.file,
                    lineno=1,
                    end_lineno=1,
                    is_module_facade=facade,
                )
            )
        for raw in bundle.symbols:
            node_id = _node_id(module, raw.qualname)
            if node_id in graph.nodes:
                logger.warning("duplicate symbol {}: keeping first", node_id)
                continue
            graph.add_node(
                SymbolNode(
                    id=node_id,
                    name=raw.name,
                    qualname=raw.qualname,
                    kind=raw.kind,
                    module=module,
                    file=raw.file,
                    lineno=raw.lineno,
                    end_lineno=raw.end_lineno or raw.lineno,
                    signature=raw.signature,
                    docstring=raw.docstring,
                    decorators=raw.decorators,
                    parameters=raw.parameters,
                    parent_id=raw.parent_id,
                    is_async=raw.is_async,
                    is_public=raw.is_public,
                    complexity=raw.complexity,
                    metadata={"bases": list(raw.bases)},
                )
            )
            idx.symbols.setdefault(module, {})[raw.qualname] = node_id
            if raw.kind is SymbolKind.CLASS:
                idx.classes.setdefault(module, {})[raw.name] = node_id
        for ref in bundle.imports:
            _record_import_alias(idx, module, ref)

    # -- Phase 2: edges -----------------------------------------------------
    for bundle in materialized:
        module = bundle.module
        mod_node_id = idx.modules.get(module)
        if mod_node_id is None:  # pragma: no cover - defensive
            continue

        # a) contains: parent -> child (or module -> top-level definition)
        for raw in bundle.symbols:
            node_id = _node_id(module, raw.qualname)
            if raw.parent_id:
                parent_id = _node_id(module, raw.parent_id)
                if parent_id not in graph.nodes:
                    parent_id = mod_node_id
            else:
                parent_id = mod_node_id
            _emit(parent_id, node_id, EdgeKind.CONTAINS)

        # b) inherits: resolve each base, record parent ids for super() handling
        for raw in bundle.symbols:
            if raw.kind is not SymbolKind.CLASS:
                continue
            node_id = _node_id(module, raw.qualname)
            parents: list[str] = []
            for base_text in raw.bases:
                target = _resolve_base_class(module, base_text, idx)
                if target is not None and target in graph.nodes and target != node_id:
                    _emit(node_id, target, EdgeKind.INHERITS)
                    parents.append(target)
            inherits[node_id] = parents

        # b2) module imports edges (target index is complete by now)
        for ref in bundle.imports:
            _emit_import_edges(idx, module, mod_node_id, ref, pending)

        # c) calls / instantiations
        for call in bundle.calls:
            src = _caller_node_id(module, mod_node_id, call.caller_qualname, graph)
            callee = call.callee
            first = callee.split(".")[0]
            if first == "self":
                continue  # blacklist: no attribute type inference
            if first == "super":
                target = _resolve_super(
                    module, callee, call.caller_qualname, idx, inherits, graph
                )
                _emit(src, target, EdgeKind.CALLS)
                continue
            target = _resolve_call(module, callee, idx)
            if target is None:
                if "." not in callee:
                    unresolved.append((src, callee))
                continue
            target_node = graph.nodes.get(target)
            kind = (
                EdgeKind.INSTANTIATES
                if target_node is not None and target_node.kind is SymbolKind.CLASS
                else EdgeKind.CALLS
            )
            _emit(src, target, kind)

    # -- Phase 3: emit, degrees, annotations --------------------------------
    for (src, dst, kind), weight in pending.items():
        graph.add_edge(SymbolEdge(source=src, target=dst, kind=kind, weight=weight))

    annotate_degrees(graph)
    for entry_id in detect_entry_points(graph):
        node = graph.nodes.get(entry_id)
        if node is not None:
            node.is_entry = True
    for dead_id in detect_dead_code(graph):
        node = graph.nodes.get(dead_id)
        if node is not None:
            node.is_dead_candidate = True

    if debug_out is not None:
        debug_out.extend(unresolved)

    if strict:
        problems = graph.validate_edges()
        if problems:
            raise GraphError("dangling edges: " + "; ".join(problems))
    return graph
