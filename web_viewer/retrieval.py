"""Graph-RAG retrieval for the web viewer's /api/chat endpoint.

Given a natural-language question, pick the most relevant sub-graph of the
symbol graph — seeds by lexical score, then expansion by **reverse BFS**
(who calls this? → impact analysis) plus one forward hop (what does it call?),
prioritized by PageRank and capped by budget. Source slices for the strongest
seeds ground the model in real code.

Pure standard library: the viewer must keep running with zero dependencies.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from pathlib import Path

# Budget defaults; all knobs are keyword arguments so tests can shrink them.
_DEFAULT_MAX_NODES = 24
_DEFAULT_SEEDS = 6
_DEFAULT_REVERSE_HOPS = 2
_DEFAULT_FORWARD_HOPS = 1
_DEFAULT_PER_HOP = 8
_DEFAULT_MAX_EDGES = 40
_DEFAULT_MAX_SLICES = 4
_SLICE_CONTEXT_BEFORE = 3
_SLICE_CONTEXT_AFTER = 12
_MAX_TOKENS = 24

# Field weights for lexical matching (id/name beat module/route beat docs).
_W_ID_NAME = 3.0
_W_MODULE_ROUTE = 2.0
_W_DOCS = 1.0
_W_PAGERANK = 8.0

_TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*|[\u4e00-\u9fff]+")


def tokenize_question(text: str) -> list[str]:
    """Split a question into ASCII words plus CJK runs and their bigrams.

    ASCII identifiers are matched whole (``login``, ``write_session``); CJK runs
    longer than two characters are additionally exploded into overlapping
    bigrams so a phrase like 修改用户登录逻辑 still surfaces 登录.
    """
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text.lower()):
        tokens.append(raw)
        if len(raw) >= 2 and all("\u4e00" <= char <= "\u9fff" for char in raw):
            for index in range(len(raw) - 1):
                bigram = raw[index : index + 2]
                if bigram not in tokens:
                    tokens.append(bigram)
        if len(tokens) >= _MAX_TOKENS:
            break
    return tokens[:_MAX_TOKENS]


def _haystacks(node: dict[str, object]) -> list[tuple[float, str]]:
    route = node.get("route")
    route_path = ""
    if isinstance(route, dict):
        route_path = str(route.get("path", ""))
    doc = node.get("docstring") or node.get("doc") or ""
    signature = node.get("signature") or ""
    return [
        (_W_ID_NAME, str(node.get("id", ""))),
        (_W_ID_NAME, str(node.get("name", ""))),
        (_W_MODULE_ROUTE, str(node.get("module", ""))),
        (_W_MODULE_ROUTE, route_path),
        (_W_DOCS, str(doc)),
        (_W_DOCS, str(signature)),
    ]


@dataclass
class Subgraph:
    """The retrieved context for one question."""

    node_ids: list[str] = field(default_factory=list)
    seeds: list[str] = field(default_factory=list)
    edges: list[dict[str, object]] = field(default_factory=list)
    slices: list[dict[str, object]] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    node_records: list[dict[str, object]] = field(default_factory=list)

    def to_context(self, max_context_nodes: int = _DEFAULT_MAX_NODES) -> dict[str, object]:
        """Compact payload handed to the model (and echoed in the API response)."""
        return {
            "nodes": self.node_records[:max_context_nodes],
            "edges": self.edges,
            "slices": self.slices,
        }


class RetrievalIndex:
    """Read-only view over one pruned-graph JSON payload."""

    def __init__(self, payload: object, *, source_root: Path | None) -> None:
        nodes_raw: dict[str, dict[str, object]] = {}
        if isinstance(payload, dict) and isinstance(payload.get("nodes"), dict):
            for node_id, node in payload["nodes"].items():
                if isinstance(node, dict):
                    nodes_raw[str(node_id)] = node
        self.nodes = nodes_raw

        edges: list[dict[str, object]] = []
        if isinstance(payload, dict) and isinstance(payload.get("edges"), list):
            for edge in payload["edges"]:
                if not isinstance(edge, dict):
                    continue
                source = str(edge.get("source", ""))
                target = str(edge.get("target", ""))
                if source in self.nodes and target in self.nodes:
                    edges.append(
                        {
                            "source": source,
                            "target": target,
                            "kind": str(edge.get("kind", "calls")),
                        }
                    )
        self.edges = edges
        self.out_adj: dict[str, list[str]] = {}
        self.in_adj: dict[str, list[str]] = {}
        for edge in self.edges:
            self.out_adj.setdefault(str(edge["source"]), []).append(str(edge["target"]))
            self.in_adj.setdefault(str(edge["target"]), []).append(str(edge["source"]))
        self.source_root = source_root

    # -- scoring ------------------------------------------------------------

    def lexical_score(self, node_id: str, tokens: list[str]) -> float:
        """Pure lexical match score (no PageRank); seeds require this > 0."""
        node = self.nodes[node_id]
        score = 0.0
        for token in tokens:
            best = 0.0
            for weight, haystack in _haystacks(node):
                if haystack and token in haystack.lower():
                    best = max(best, weight)
            score += best
        return score

    def score_node(self, node_id: str, tokens: list[str]) -> float:
        """Lexical score plus a PageRank boost, for ranking (never for seeding)."""
        score = self.lexical_score(node_id, tokens)
        with contextlib.suppress(TypeError, ValueError):
            score += float(self.nodes[node_id].get("pagerank") or 0.0) * _W_PAGERANK
        return score

    def seed_nodes(self, question: str, *, max_seeds: int = _DEFAULT_SEEDS) -> list[str]:
        """Top-scoring node ids for the question; empty when nothing matches.

        A node only qualifies when it matches lexically — the PageRank boost
        orders seeds but must never turn an unrelated hub into one.
        """
        tokens = tokenize_question(question)
        if not tokens:
            return []
        scored: list[tuple[float, str]] = []
        for node_id in self.nodes:
            if self.lexical_score(node_id, tokens) <= 0.0:
                continue
            scored.append((self.score_node(node_id, tokens), node_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [node_id for _score, node_id in scored[: max(0, max_seeds)]]

    # -- expansion ----------------------------------------------------------

    def _hop(self, frontier: set[str], adjacency: dict[str, list[str]], cap: int) -> list[str]:
        """One BFS level over ``adjacency``, capped by PageRank then id."""
        candidates: set[str] = set()
        for node_id in frontier:
            candidates.update(adjacency.get(node_id, ()))
        candidates -= frontier
        known = [node_id for node_id in candidates if node_id in self.nodes]
        known.sort(
            key=lambda node_id: (-float(self.nodes[node_id].get("pagerank") or 0.0), node_id)
        )
        return known[: max(0, cap)]

    def select(
        self,
        question: str,
        *,
        max_nodes: int = _DEFAULT_MAX_NODES,
        reverse_hops: int = _DEFAULT_REVERSE_HOPS,
        forward_hops: int = _DEFAULT_FORWARD_HOPS,
        per_hop: int = _DEFAULT_PER_HOP,
        max_slices: int = _DEFAULT_MAX_SLICES,
    ) -> Subgraph:
        """Retrieve the question's sub-graph (never mutates the index)."""
        seeds = self.seed_nodes(question)
        result = Subgraph(seeds=list(seeds), node_ids=[])
        if not seeds:
            return result

        budget = max(1, max_nodes)
        ordered: list[str] = []
        seen: set[str] = set()

        def _add(node_ids: list[str]) -> None:
            for node_id in node_ids:
                if len(ordered) >= budget:
                    return
                if node_id not in seen:
                    seen.add(node_id)
                    ordered.append(node_id)

        _add(seeds)
        reverse_frontier = set(seeds)
        for _hop_index in range(max(0, reverse_hops)):
            # The total budget is the binding constraint; a fat middle hop must
            # not starve later hops when max_nodes < per_hop.
            level = self._hop(
                reverse_frontier, self.in_adj, min(per_hop, budget - len(ordered))
            )
            if not level:
                break
            _add(level)
            reverse_frontier = set(level)
        forward_frontier = set(seeds)
        for _hop_index in range(max(0, forward_hops)):
            level = self._hop(
                forward_frontier, self.out_adj, min(per_hop, budget - len(ordered))
            )
            if not level:
                break
            _add(level)
            forward_frontier = set(level)

        if len(ordered) < budget:
            self._fill_by_adjacency(
                ordered, seen, budget,
                include_callers=reverse_hops > 0,
                include_callees=forward_hops > 0,
            )

        result.node_ids = ordered
        result.scores = {
            node_id: self.score_node(node_id, tokenize_question(question))
            for node_id in ordered
        }
        result.node_records = [self._record(node_id) for node_id in ordered]
        result.edges = self._induced_edges(ordered)
        result.slices = self._slices(seeds, max_slices=max_slices)
        return result

    def _fill_by_adjacency(
        self,
        ordered: list[str],
        seen: set[str],
        budget: int,
        *,
        include_callers: bool,
        include_callees: bool,
    ) -> None:
        """Top up the budget with the strongest direct neighbours of the set.

        BFS level caps can leave the budget unfilled on hub-heavy graphs; the
        remaining room goes to the highest-pagerank direct neighbours, exactly
        like the grapher pruner's fill step. The fill never crosses a direction
        the caller disabled: ``reverse_hops=0`` keeps callers out entirely.
        """
        if len(ordered) >= budget:
            return
        neighbours: set[str] = set()
        for node_id in list(ordered):
            if include_callers:
                neighbours.update(self.in_adj.get(node_id, ()))
            if include_callees:
                neighbours.update(self.out_adj.get(node_id, ()))
        candidates = [node_id for node_id in neighbours if node_id in self.nodes]
        candidates.sort(
            key=lambda node_id: (
                -float(self.nodes[node_id].get("pagerank") or 0.0),
                node_id,
            )
        )
        for node_id in candidates:
            if len(ordered) >= budget:
                return
            if node_id not in seen:
                seen.add(node_id)
                ordered.append(node_id)

    def _record(self, node_id: str) -> dict[str, object]:
        """Compact projection of one node for the model context."""
        node = self.nodes[node_id]
        try:
            pagerank = round(float(node.get("pagerank") or 0.0), 6)
        except (TypeError, ValueError):
            pagerank = 0.0
        route = node.get("route")
        return {
            "id": node_id,
            "name": str(node.get("name", "")),
            "kind": str(node.get("kind", "unknown")),
            "module": str(node.get("module", "")),
            "file": str(node.get("file", "")),
            "pagerank": pagerank,
            "route": route if isinstance(route, dict) else None,
        }

    def _induced_edges(self, node_ids: list[str]) -> list[dict[str, object]]:
        keep = set(node_ids)
        edges = [edge for edge in self.edges if edge["source"] in keep and edge["target"] in keep]

        def _weight(edge: dict[str, object]) -> float:
            source = float(self.nodes[str(edge["source"])].get("pagerank") or 0.0)
            target = float(self.nodes[str(edge["target"])].get("pagerank") or 0.0)
            return source + target

        edges.sort(key=lambda edge: (-_weight(edge), str(edge["source"]), str(edge["target"])))
        return edges[:_DEFAULT_MAX_EDGES]

    def _slices(
        self,
        seeds: list[str],
        *,
        max_slices: int = _DEFAULT_MAX_SLICES,
    ) -> list[dict[str, object]]:
        """Source excerpts for the strongest seeds, traversal-guarded."""
        if self.source_root is None:
            return []
        slices: list[dict[str, object]] = []
        for node_id in seeds:
            if len(slices) >= max_slices:
                break
            node = self.nodes[node_id]
            relative = str(node.get("file") or "")
            if not relative:
                continue
            try:
                lineno = int(node.get("lineno") or 1)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                lineno = 1
            candidate = (self.source_root / relative).resolve()
            if not candidate.is_relative_to(self.source_root):
                continue
            if not candidate.is_file():
                continue
            try:
                lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            start = max(1, lineno - _SLICE_CONTEXT_BEFORE)
            end = min(len(lines), lineno + _SLICE_CONTEXT_AFTER)
            slices.append(
                {
                    "node": node_id,
                    "file": relative,
                    "start": start,
                    "lines": lines[start - 1 : end],
                }
            )
        return slices


def select_subgraph(
    index: RetrievalIndex,
    question: str,
    *,
    max_nodes: int = _DEFAULT_MAX_NODES,
    reverse_hops: int = _DEFAULT_REVERSE_HOPS,
    forward_hops: int = _DEFAULT_FORWARD_HOPS,
    per_hop: int = _DEFAULT_PER_HOP,
    max_slices: int = _DEFAULT_MAX_SLICES,
) -> Subgraph:
    """Module-level convenience wrapper around :meth:`RetrievalIndex.select`."""
    return index.select(
        question,
        max_nodes=max_nodes,
        reverse_hops=reverse_hops,
        forward_hops=forward_hops,
        per_hop=per_hop,
        max_slices=max_slices,
    )
