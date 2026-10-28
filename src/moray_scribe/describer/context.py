"""Assemble the describer's prompt inputs from a pruned symbol graph."""

from __future__ import annotations

import json

from moray_scribe.grapher.serializer import to_prompt_view
from moray_scribe.schema import StrictModel, SymbolGraph, SymbolKind, SymbolNode


class PromptContext(StrictModel):
    """Everything the describer prompt needs plus the sets for post-validation."""

    project_name: str
    root_path: str
    nodes_json: str
    edges_json: str
    known_gaps: str
    n_nodes: int
    n_edges: int
    module_set: list[str]
    node_ids: list[str]


def _resolve_base_id(base_name: str, owner: SymbolNode, graph: SymbolGraph) -> str | None:
    """Resolve a bare base-class name to a full node id, or None if truly external.

    Resolution order (D-6.4): exact same-module class, a dotted fully-qualified
    class already in the graph, then a globally unique class name. Returns None
    only when the base genuinely cannot be tied to an in-graph class.
    """
    name = base_name.split("[", 1)[0].strip()  # strip generics: Generic[T] -> Generic
    if not name:
        return None
    same_module = f"{owner.module}.{name}"
    node = graph.nodes.get(same_module)
    if node is not None and node.kind is SymbolKind.CLASS:
        return same_module
    if "." in name:
        dotted = graph.nodes.get(name)
        if dotted is not None and dotted.kind is SymbolKind.CLASS:
            return name
    candidates = [
        nid
        for nid, other in graph.nodes.items()
        if other.kind is SymbolKind.CLASS and other.name == name
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def detect_known_gaps(graph: SymbolGraph) -> list[str]:
    """Describe relations the scanner could not resolve, one per line.

    These are known holes, not bugs: telling the model about them keeps it from
    inventing the missing edges. Methods/functions with no recorded calls may use
    dynamic dispatch; unreferenced classes may be reached reflectively; a class
    inheriting a genuinely external base needs out-of-graph documentation.
    Internal bases are resolved first (D-6.4) so they are NOT reported, and the
    same external base shared by many classes is merged into one line.
    """
    gaps: list[str] = []
    external_bases: dict[str, list[str]] = {}
    for node in graph.nodes.values():
        if node.kind in (SymbolKind.METHOD, SymbolKind.FUNCTION) and node.out_degree == 0:
            label = "Method" if node.kind is SymbolKind.METHOD else "Function"
            gaps.append(f"{label} {node.id} has no recorded calls; may use dynamic dispatch.")
        if node.kind is SymbolKind.CLASS and node.in_degree == 0 and not node.is_entry:
            gaps.append(
                f"Class {node.id} has no inbound references; possibly dead or entry via reflection."
            )
        bases = node.metadata.get("bases")
        if not bases:
            continue
        if isinstance(bases, str):
            base_names = [part.strip() for part in bases.split(",")]
        else:
            base_names = [str(part) for part in bases]
        seen_for_node: set[str] = set()
        for base in base_names:
            bare = base.split("[", 1)[0].strip()
            if not bare or bare in seen_for_node:
                continue
            seen_for_node.add(bare)
            if _resolve_base_id(bare, node, graph) is None:
                external_bases.setdefault(bare, []).append(node.id)

    for base in sorted(external_bases):
        owners = external_bases[base]
        if len(owners) == 1:
            gaps.append(
                f"Class {owners[0]} inherits from external base `{base}`; "
                "external base docs may be needed."
            )
        else:
            examples = ", ".join(f"`{oid}`" for oid in owners[:3])
            gaps.append(
                f"{len(owners)} classes inherit from external base `{base}` "
                f"(e.g. {examples}); external base docs may be needed."
            )
    return gaps


def build_prompt_context(
    graph: SymbolGraph,
    *,
    project_name: str,
    root_path: str,
) -> PromptContext:
    """Serialize a graph into the compact prompt payload plus validation sets."""
    view = to_prompt_view(graph)
    nodes_json = json.dumps(view["n"], ensure_ascii=False)
    edges_json = json.dumps(view["e"], ensure_ascii=False)
    gaps = detect_known_gaps(graph)
    known_gaps = "\n".join(f"- {gap}" for gap in gaps) if gaps else "None."
    module_set = sorted({node.module for node in graph.nodes.values()})
    node_ids = sorted(graph.nodes)
    return PromptContext(
        project_name=project_name,
        root_path=root_path,
        nodes_json=nodes_json,
        edges_json=edges_json,
        known_gaps=known_gaps,
        n_nodes=len(graph.nodes),
        n_edges=len(graph.edges),
        module_set=module_set,
        node_ids=node_ids,
    )
