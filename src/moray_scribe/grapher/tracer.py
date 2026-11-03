"""Call-chain tracing from entry points through the runtime call graph.

The static architecture view answers "where does a module live"; the sequence
view answers "how does a request actually flow". This module walks the graph
from entry points (routes, ``main``, CLI commands) down the flow-carrying edge
kinds (``calls`` / ``instantiates``), producing deterministic root-to-leaf
paths that the sequence-describe stage turns into Mermaid ``sequenceDiagram``s.

Determinism contract: adjacency is sorted by (descending target PageRank, id,
kind), entries by (descending PageRank, id), and cycles are cut per-path —
so the same graph always yields the same chains in the same order.
"""

from __future__ import annotations

from pydantic import Field, field_validator

from moray_scribe.grapher.analyzer import detect_entry_points
from moray_scribe.schema import EdgeKind, StrictModel, SymbolGraph

# Edge kinds that carry runtime flow. CONTAINS/IMPORTS are structure, not
# behaviour, and would turn every module into a hub of its own contents.
FLOW_KINDS: tuple[EdgeKind, ...] = (EdgeKind.CALLS, EdgeKind.INSTANTIATES)

# Sequence-diagram arrow plus a Chinese relation label, per traced edge kind.
_STEP_ARROWS: dict[str, tuple[str, str]] = {
    EdgeKind.CALLS.value: ("->>", "调用"),
    EdgeKind.INSTANTIATES.value: ("->>", "实例化"),
}

_DEFAULT_MAX_DEPTH = 4
_DEFAULT_MAX_ENTRIES = 5
_DEFAULT_CHAINS_PER_ENTRY = 2
_DEFAULT_MAX_CHAINS = 6


class ChainStep(StrictModel):
    """One hop inside a traced call chain."""

    src: str = Field(min_length=1, description="Caller symbol id.")
    dst: str = Field(min_length=1, description="Callee symbol id.")
    kind: str = Field(description="Edge kind value (calls / instantiates).")
    depth: int = Field(ge=0, description="0-based hop index from the entry.")
    lineno: int = Field(default=0, ge=0, description="First call site line, 0 when unknown.")

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, value: str) -> str:
        allowed = {kind.value for kind in EdgeKind}
        if value not in allowed:
            raise ValueError(f"kind must be one of {sorted(allowed)}, got {value!r}")
        return value


class CallChain(StrictModel):
    """A traced root-to-leaf flow from one entry point."""

    entry: str = Field(min_length=1, description="Entry-point symbol id.")
    steps: list[ChainStep] = Field(
        min_length=1,
        description="Ordered hops; steps[i].depth == i.",
    )
    truncated: bool = Field(
        default=False,
        description="True when the chain was cut by the depth limit, not by a leaf.",
    )

    @property
    def participants(self) -> list[str]:
        """Symbol ids in visit order, starting at the entry (deduplicated)."""
        seen: list[str] = []
        if self.steps:
            seen.append(self.steps[0].src)
        for step in self.steps:
            if step.dst not in seen:
                seen.append(step.dst)
        return seen


def chain_step_arrows(step: ChainStep) -> tuple[str, str]:
    """Map a traced step to its Mermaid sequence arrow and relation label."""
    return _STEP_ARROWS.get(step.kind, ("->>", step.kind))


def _flow_adjacency(
    graph: SymbolGraph,
    kinds: tuple[EdgeKind, ...],
) -> dict[str, list[tuple[str, str, int]]]:
    """node id -> [(dst, kind, lineno)], sorted by (-pagerank(dst), dst, kind)."""
    allowed = set(kinds)
    adjacency: dict[str, list[tuple[str, str, int]]] = {}
    for edge in graph.edges:
        if edge.kind not in allowed:
            continue
        if edge.source not in graph.nodes or edge.target not in graph.nodes:
            continue
        if edge.source == edge.target:
            continue
        lineno = edge.linenos[0] if edge.linenos else 0
        adjacency.setdefault(edge.source, []).append((edge.target, edge.kind.value, lineno))
    for targets in adjacency.values():
        targets.sort(
            key=lambda item: (
                -graph.nodes[item[0]].pagerank,
                item[0],
                item[1],
            )
        )
    return adjacency


def _entry_ids(
    graph: SymbolGraph,
    max_entries: int,
    adjacency: dict[str, list[tuple[str, str, int]]],
) -> list[str]:
    """Ranked entry candidates: flagged/detected entries, then top-pagerank callers."""
    entries = {
        node_id for node_id, node in graph.nodes.items() if node.is_entry
    } | set(detect_entry_points(graph))
    if not entries:
        # Nodes without annotated degrees may still be flow sources; trust the
        # freshly built adjacency rather than the (possibly stale) out_degree.
        entries = set(adjacency)
    ranked = sorted(entries, key=lambda node_id: (-graph.nodes[node_id].pagerank, node_id))
    return ranked[: max(0, max_entries)]


def _trace_entry(
    entry: str,
    adjacency: dict[str, list[tuple[str, str, int]]],
    *,
    max_depth: int,
) -> list[tuple[list[ChainStep], bool]]:
    """DFS one entry to every leaf; return (steps, truncated) in trace order.

    Branches are explored highest-pagerank-first (the adjacency is
    pre-sorted), so the most important flows are recorded first. Cycles are
    cut per-path via ``visited``; the depth limit marks a path ``truncated``
    instead of descending further.
    """
    paths: list[tuple[list[ChainStep], bool]] = []
    path: list[ChainStep] = []
    visited = {entry}
    limit = max(1, max_depth)

    def dfs(node: str) -> None:
        descended = False
        for dst, kind, lineno in adjacency.get(node, []):
            if dst in visited:
                continue
            descended = True
            path.append(
                ChainStep(src=node, dst=dst, kind=kind, depth=len(path), lineno=lineno)
            )
            visited.add(dst)
            if len(path) >= limit:
                paths.append(([step.model_copy() for step in path], True))
            else:
                dfs(dst)
            visited.discard(dst)
            path.pop()
        if not descended and path:
            paths.append(([step.model_copy() for step in path], False))

    dfs(entry)
    return paths


def trace_call_chains(
    graph: SymbolGraph,
    *,
    max_depth: int = _DEFAULT_MAX_DEPTH,
    max_entries: int = _DEFAULT_MAX_ENTRIES,
    chains_per_entry: int = _DEFAULT_CHAINS_PER_ENTRY,
    max_chains: int = _DEFAULT_MAX_CHAINS,
    kinds: tuple[EdgeKind, ...] = FLOW_KINDS,
) -> list[CallChain]:
    """Trace deterministic root-to-leaf call chains from entry points.

    ``max_depth`` bounds hops per chain, ``chains_per_entry`` bounds the paths
    kept per entry (DFS order, so the highest-pagerank branches survive), and
    ``max_chains`` bounds the total. Identical ``(entry, steps)`` sequences are
    deduplicated. The graph is never mutated.
    """
    if not graph.nodes:
        return []
    adjacency = _flow_adjacency(graph, kinds)

    chains: list[CallChain] = []
    seen: set[tuple[str, tuple[tuple[str, str, str], ...]]] = set()

    for entry in _entry_ids(graph, max_entries, adjacency):
        kept_for_entry = 0
        for steps, truncated in _trace_entry(entry, adjacency, max_depth=max_depth):
            if kept_for_entry >= max(1, chains_per_entry):
                break
            key = (entry, tuple((step.src, step.dst, step.kind) for step in steps))
            if key in seen:
                continue
            seen.add(key)
            chains.append(CallChain(entry=entry, steps=steps, truncated=truncated))
            kept_for_entry += 1
        if len(chains) >= max_chains:
            break

    return chains[: max(0, max_chains)]
