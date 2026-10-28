"""Symbol graph contracts shared by the graph, describer and renderer layers."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field, model_validator

from ..exceptions import GraphError, SchemaError
from .base import StrictModel
from .symbols import SymbolKind


class EdgeKind(str, Enum):
    """Relationship represented by a directed symbol edge."""

    IMPORTS = "imports"
    CALLS = "calls"
    INHERITS = "inherits"
    CONTAINS = "contains"
    INSTANTIATES = "instantiates"
    USES = "uses"
    REFERENCES = "references"


class SymbolNode(StrictModel):
    """A deduplicated symbol plus the graph metrics attached to it."""

    id: str = Field(
        min_length=1,
        description="Stable qualified identifier, matching the scanner's symbol id.",
    )
    name: str = Field(min_length=1, description="Short symbol name.")
    qualname: str = Field(default="", description="Qualified name inside its module.")
    kind: SymbolKind = Field(description="Symbol kind.")
    module: str = Field(min_length=1, description="Dotted module path.")
    file: str = Field(min_length=1, description="Repository-relative source path.")
    lineno: int = Field(ge=1, description="1-based first line of the definition.")
    end_lineno: int = Field(ge=1, description="1-based last line of the definition.")
    signature: str | None = Field(default=None, description="Rendered signature, if any.")
    docstring: str | None = Field(default=None, description="Cleaned docstring, if present.")
    decorators: list[str] = Field(default_factory=list, description="Decorator expressions.")
    parameters: list[str] = Field(
        default_factory=list,
        description="Parameter renderings, e.g. `name: Type`, `*args`.",
    )
    parent_id: str | None = Field(default=None, description="Enclosing definition id, if any.")
    is_async: bool = Field(default=False, description="Whether the definition is async.")
    is_public: bool = Field(default=True, description="Whether the symbol is public API.")
    complexity: int = Field(default=0, ge=0, description="Cyclomatic complexity estimate.")
    in_degree: int = Field(default=0, ge=0, description="Incoming edge count.")
    out_degree: int = Field(default=0, ge=0, description="Outgoing edge count.")
    pagerank: float = Field(default=0.0, ge=0.0, description="PageRank score in [0, 1].")
    is_dead: bool = Field(default=False, description="Whether the symbol looks unreachable.")
    is_entry: bool = Field(
        default=False,
        description="Whether the symbol is an entry point (main, route, CLI command).",
    )
    is_dead_candidate: bool = Field(
        default=False,
        description="Zero external in-degree and not an entry point.",
    )
    is_module_facade: bool = Field(
        default=False,
        description="Whether the node is a package __init__.py facade.",
    )
    tags: list[str] = Field(default_factory=list, description="Free-form tags.")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Free-form analysis scratch space. Convention: 'bases' holds the raw "
            "base-class source list for a class node."
        ),
    )

    @model_validator(mode="after")
    def _check_span(self) -> SymbolNode:
        if self.end_lineno < self.lineno:
            raise ValueError(
                f"node {self.id!r} ends before it starts ({self.lineno} > {self.end_lineno})"
            )
        return self


class SymbolEdge(StrictModel):
    """A directed, weighted relationship between two symbol nodes."""

    source: str = Field(min_length=1, description="Source symbol id.")
    target: str = Field(min_length=1, description="Target symbol id.")
    kind: EdgeKind = Field(description="Relationship kind.")
    weight: float = Field(default=1.0, gt=0.0, description="Aggregated relationship weight.")
    linenos: list[int] = Field(
        default_factory=list,
        description="Call/import sites that produced this edge.",
    )
    resolved: bool = Field(
        default=True,
        description="False when the target was matched heuristically.",
    )


class SymbolGraph(StrictModel):
    """Whole-repository symbol graph built incrementally.

    Node dictionary keys must match ``SymbolNode.id``. Edge endpoints are not
    validated on construction (R-1): the builder may append edges before every
    node exists, then enforce closure via ``validate_edges`` or load a
    finalized graph with ``from_json_strict``.
    """

    nodes: dict[str, SymbolNode] = Field(
        default_factory=dict,
        description="Nodes keyed by symbol id.",
    )
    edges: list[SymbolEdge] = Field(default_factory=list, description="Directed edges.")
    root: str | None = Field(default=None, description="Optional entry-point node id.")
    metadata: dict[str, str] = Field(default_factory=dict, description="Graph-level extras.")

    @model_validator(mode="after")
    def _check_node_keys(self) -> SymbolGraph:
        mismatched = sorted(key for key, node in self.nodes.items() if key != node.id)
        if mismatched:
            raise ValueError(f"node keys do not match node ids: {mismatched}")
        return self

    @model_validator(mode="after")
    def _check_root(self) -> SymbolGraph:
        if self.root is not None and self.root not in self.nodes:
            raise ValueError(f"root {self.root!r} is not a node in this graph")
        return self

    @property
    def node_count(self) -> int:
        """Number of symbols in the graph."""
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        """Number of directed edges in the graph."""
        return len(self.edges)

    def get_node(self, node_id: str) -> SymbolNode | None:
        """Return a node by id, or ``None`` when it is absent."""
        return self.nodes.get(node_id)

    def add_node(self, node: SymbolNode) -> None:
        """Insert a node, rejecting a duplicate id."""
        if node.id in self.nodes:
            raise SchemaError(f"duplicate node id: {node.id}")
        self.nodes[node.id] = node

    def add_edge(self, edge: SymbolEdge) -> None:
        """Append an edge without endpoint validation.

        Building may produce dangling edges (R-1); call ``validate_edges`` once
        the graph is assembled to decide whether they are fatal.
        """
        self.edges.append(edge)

    def remove_node(self, node_id: str) -> bool:
        """Remove a node and every incident edge; return whether it existed."""
        if node_id not in self.nodes:
            return False
        self.edges[:] = [
            edge for edge in self.edges if edge.source != node_id and edge.target != node_id
        ]
        del self.nodes[node_id]
        if self.root == node_id:
            self.root = None
        return True

    def remove_edge(
        self,
        source: str,
        target: str,
        *,
        kind: EdgeKind | None = None,
    ) -> int:
        """Remove matching edges and return how many were deleted."""
        kept = [
            edge
            for edge in self.edges
            if not (
                edge.source == source
                and edge.target == target
                and (kind is None or edge.kind == kind)
            )
        ]
        removed = len(self.edges) - len(kept)
        self.edges[:] = kept
        return removed

    def has_edge(
        self,
        source: str,
        target: str,
        *,
        kind: EdgeKind | None = None,
    ) -> bool:
        """Whether at least one matching edge exists."""
        return any(
            edge.source == source
            and edge.target == target
            and (kind is None or edge.kind == kind)
            for edge in self.edges
        )

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialize the graph to JSON."""
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_json(cls, payload: str) -> SymbolGraph:
        """Deserialize a graph from JSON without endpoint validation."""
        return cls.model_validate_json(payload)

    @classmethod
    def from_json_strict(cls, payload: str) -> SymbolGraph:
        """Deserialize a finalized graph, rejecting any dangling edge."""
        graph = cls.model_validate_json(payload)
        problems = graph.validate_edges()
        if problems:
            raise GraphError("dangling edges: " + "; ".join(problems))
        return graph

    def validate_edges(self, known: set[str] | None = None) -> list[str]:
        """Describe every edge whose endpoints are absent from ``known``.

        ``known`` defaults to this graph's own node ids. Never raises; callers
        decide whether dangling edges are fatal.
        """
        known_ids = known if known is not None else set(self.nodes)
        problems: list[str] = []
        for index, edge in enumerate(self.edges):
            for role in ("source", "target"):
                endpoint = edge.source if role == "source" else edge.target
                if endpoint in known_ids:
                    continue
                problems.append(
                    f"edge[{index}] ({edge.source} -> {edge.target}) "
                    f"references unknown {role} {endpoint!r}"
                )
        return problems
