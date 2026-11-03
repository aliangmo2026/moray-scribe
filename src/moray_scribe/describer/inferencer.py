"""LLM dynamic inference: resolve the dangling calls static analysis cannot.

Flow (§1 v0.2):

1. **Extract** — the grapher reports every unresolved call site
   (``dynamic_out``); the inferencer deduplicates, ranks by caller PageRank
   and caps the pool at ``cfg.inference_max_calls``.
2. **Infer** — unresolved sites are batched (``cfg.inference_batch_size``
   calls per request), each batch carrying the caller's source snippet and a
   shortlist of candidate graph symbols. Requests run concurrently over
   :class:`~moray_scribe.describer.async_client.AsyncOllamaClient`.
3. **Merge** — ``high``-confidence edges with both endpoints in the graph are
   appended as ``inferred=True`` edges; ``medium``/``low`` answers and unknown
   targets land in ``report.gaps`` for the known-gaps section.

Every merged edge is visually distinct downstream (dashed ``-.->`` plus the
``[AI推断]`` tag) so static truth and AI inference never blur together.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from loguru import logger
from pydantic import Field

from moray_scribe.config import Settings
from moray_scribe.exceptions import ScribeError
from moray_scribe.schema import (
    ArchEdgeKind,
    ArchitectureDraft,
    DanglingCall,
    DiagramKind,
    EdgeKind,
    EdgeStyle,
    MermaidDiagram,
    MermaidEdge,
    StrictModel,
    SymbolEdge,
    SymbolGraph,
    SymbolNode,
)
from moray_scribe.utils.tokens import (
    estimate_tokens,
    fits_budget,
    truncate_to_tokens,
)

from .async_client import AsyncOllamaClient

CONFIDENCE = Literal["high", "medium", "low"]

# Source-context window around each call site, in source lines.
_SNIPPET_BEFORE = 6
_SNIPPET_AFTER = 3
# Candidate shortlist per call and prompt budgets (tokens).
_MAX_CANDIDATES = 12
_BATCH_BUDGET_TOKENS = 5500
_CALL_BLOCK_BUDGET_TOKENS = 700

# Bare callees that are Python builtins: never real graph targets, always noise.
_BUILTIN_CALLEES = frozenset(
    {
        "print", "len", "str", "int", "float", "bool", "dict", "list", "set", "tuple",
        "isinstance", "issubclass", "hasattr", "getattr", "setattr", "delattr",
        "type", "open", "range", "enumerate", "zip", "map", "filter", "sorted",
        "reversed", "sum", "min", "max", "abs", "any", "all", "repr", "format",
        "vars", "dir", "id", "hash", "iter", "next", "callable", "classmethod",
        "staticmethod", "property", "super", "object", "bytes", "bytearray",
        "frozenset", "complex", "divmod", "pow", "round", "slice", "input",
        "exit", "quit", "help",
    }
)

INFER_SYSTEM_PROMPT = """\
You resolve UNRESOLVED function calls in a Python codebase's symbol graph.
For each numbered call site you get the caller's source context and a shortlist \
of candidate target symbol ids that exist in the graph.

Output ONLY JSON of shape:
{"edges": [{"src": "...", "dst": "...", "confidence": "high"|"medium"|"low", "reason": "..."}]}

Rules:
1. One output edge per input call site, in the same order.
2. "src" MUST be the call site's caller id verbatim.
3. "dst" MUST be one of that call site's candidate ids, or exactly "UNKNOWN" \
when the target is external, dynamic, or you cannot decide.
4. confidence: "high" only with hard evidence in the snippet (a matching \
attribute assignment such as `self.x = Engine(...)`, a from-import of the \
symbol, or an unambiguous same-module definition). "medium" when the name and \
context strongly suggest one candidate. "low" for a guess.
5. NEVER invent symbol ids that are not in the candidate list.
6. "reason" is one short sentence citing the concrete evidence line.
"""


class InferredEdge(StrictModel):
    """One LLM-proposed relation between two graph symbols."""

    src: str = Field(min_length=1, description="Caller symbol id (verbatim input).")
    dst: str = Field(min_length=1, description="Target symbol id, or UNKNOWN.")
    confidence: CONFIDENCE = Field(description="high / medium / low.")
    reason: str = Field(default="", description="Short evidence-based rationale.")
    callee: str = Field(
        default="",
        description="Original call chain, attached by the inferencer (not the model).",
    )


class InferredEdgeBatch(StrictModel):
    """The model's answer for one batch of call sites."""

    edges: list[InferredEdge] = Field(default_factory=list)


class InferenceReport(StrictModel):
    """Outcome of one dynamic inference pass, for docs and summaries."""

    candidates_total: int = Field(default=0, description="Dangling calls found.")
    selected: int = Field(default=0, description="Calls actually sent to the model.")
    requests: int = Field(default=0, description="Ollama requests issued.")
    prompt_tokens: int = Field(default=0, ge=0, description="Prompt tokens reported by Ollama.")
    completion_tokens: int = Field(
        default=0, ge=0, description="Completion tokens reported by Ollama."
    )
    merged: list[InferredEdge] = Field(
        default_factory=list, description="High-confidence edges accepted for merging."
    )
    gaps: list[InferredEdge] = Field(
        default_factory=list,
        description="Medium/low-confidence or UNKNOWN-target answers, kept as annotations.",
    )
    rejected: int = Field(default=0, description="Malformed or unmatchable answers.")

    @property
    def total_tokens(self) -> int:
        """Prompt + completion tokens reported by Ollama."""
        return self.prompt_tokens + self.completion_tokens


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


def extract_dangling_calls(
    graph: SymbolGraph,
    dynamic_pool: list[tuple[str, str, int]],
    *,
    max_calls: int = 60,
) -> list[DanglingCall]:
    """Deduplicate, rank and cap the builder's unresolved-call pool.

    Ranking is by caller PageRank (hubs first), then id, then callee, so the
    capped selection is deterministic. Builtins and calls whose callers are
    not graph nodes are dropped.
    """
    best: dict[tuple[str, str], int] = {}
    for caller_id, callee, lineno in dynamic_pool:
        if caller_id not in graph.nodes:
            continue
        root = callee.split(".", 1)[0]
        if root in _BUILTIN_CALLEES:
            continue
        key = (caller_id, callee)
        if key not in best or lineno < best[key]:
            best[key] = lineno

    def _rank(item: tuple[tuple[str, str], int]) -> tuple[float, str, str]:
        (caller_id, callee), _lineno = item
        caller = graph.nodes.get(caller_id)
        return (-(caller.pagerank if caller else 0.0), caller_id, callee)

    ranked = sorted(best.items(), key=_rank)[: max(0, max_calls)]
    return [DanglingCall(caller_id=c, callee=call, lineno=ln) for (c, call), ln in ranked]


# --------------------------------------------------------------------------
# Prompt assembly
# --------------------------------------------------------------------------


def _candidate_ids(graph: SymbolGraph, callee: str, limit: int = _MAX_CANDIDATES) -> list[str]:
    """Graph symbols whose short name equals the callee's last segment."""
    target_name = callee.rsplit(".", 1)[-1].strip()
    if not target_name:
        return []
    matches = [node for node in graph.nodes.values() if node.name == target_name]
    matches.sort(key=lambda node: (-node.pagerank, node.id))
    return [node.id for node in matches[:limit]]


class _SourceReader:
    """Line cache for snippet extraction, rooted at the scan root."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache: dict[str, list[str]] = {}

    def lines(self, relative_file: str) -> list[str]:
        if relative_file not in self._cache:
            try:
                text = (self._root / relative_file).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                self._cache[relative_file] = []
            else:
                self._cache[relative_file] = text.splitlines()
        return self._cache[relative_file]


def _snippet_block(
    call: DanglingCall,
    caller: SymbolNode,
    candidates: list[str],
    reader: _SourceReader,
) -> str:
    """Render one call site as a compact prompt block."""
    lines = reader.lines(caller.file)
    start = max(1, call.lineno - _SNIPPET_BEFORE)
    end = min(len(lines), call.lineno + _SNIPPET_AFTER)
    snippet_lines: list[str] = []
    if lines:
        width = len(str(end))
        for number in range(start, end + 1):
            marker = "->" if number == call.lineno else "  "
            snippet_lines.append(f"  {marker} {number:>{width}} | {lines[number - 1]}")
    header = (
        f"call {call.caller_id} -> `{call.callee}` (line {call.lineno})\n"
        f"kind: {caller.kind.value}, module: {caller.module}, file: {caller.file}\n"
        f"signature: {caller.signature or caller.name}\n"
    )
    if caller.docstring:
        header += f"docstring: {caller.docstring[:120]}\n"
    body = "context:\n" + "\n".join(snippet_lines) if snippet_lines else "context: (unavailable)"
    candidates_text = ", ".join(candidates) if candidates else "(none)"
    return f"{header}{body}\ncandidates: {candidates_text}"


def _shrink_block(block: str, budget: int) -> str | None:
    """Trim a block to the token budget; None when nothing can be kept.

    The context body is dropped first (the header and the candidate list are
    what the response contract depends on), then a hard character cut.
    """
    if fits_budget(block, max_tokens=budget):
        return block
    header, _, _rest = block.partition("context:\n")
    candidates = block.rsplit("candidates: ", 1)[-1]
    trimmed = f"{header}context: (elided)\ncandidates: {candidates}"
    if fits_budget(trimmed, max_tokens=budget):
        return trimmed
    return truncate_to_tokens(trimmed, max_tokens=budget) or None


def build_batch_prompts(
    calls: list[DanglingCall],
    graph: SymbolGraph,
    *,
    root_path: str | Path,
    project_name: str = "",
    batch_size: int = 8,
    budget_tokens: int = _BATCH_BUDGET_TOKENS,
) -> list[tuple[list[DanglingCall], str]]:
    """Group calls into token-budgeted batches with their assembled user prompts.

    Each batch is ``(calls, user_prompt)``; call ``i`` of the returned list
    corresponds to call site number ``i + 1`` inside the prompt, which is the
    contract the response parser relies on. Calls with no candidate targets
    (the model could only answer UNKNOWN) and blocks that cannot fit the
    per-call budget are dropped before batching.
    """
    reader = _SourceReader(Path(root_path))
    blocks: list[tuple[DanglingCall, str]] = []
    for call in calls:
        caller = graph.nodes.get(call.caller_id)
        if caller is None:
            continue
        candidates = _candidate_ids(graph, call.callee)
        if not candidates:
            continue
        block = _shrink_block(
            _snippet_block(call, caller, candidates, reader), _CALL_BLOCK_BUDGET_TOKENS
        )
        if block is None:
            continue
        blocks.append((call, block))

    preamble = (
        "# Unresolved call sites\n"
        f"Project: {project_name}\n"
        "For each call site decide the most likely target among its candidates.\n"
    )
    batches: list[tuple[list[DanglingCall], str]] = []
    current: list[DanglingCall] = []
    current_parts: list[str] = []
    current_tokens = 0
    for index, (call, block) in enumerate(blocks):
        numbered = f"### {index + 1}. {block}"
        cost = estimate_tokens(numbered)
        if current and current_tokens + cost > budget_tokens:
            batches.append((current, preamble + "\n".join(current_parts)))
            current, current_parts, current_tokens = [], [], 0
        current.append(call)
        current_parts.append(numbered)
        current_tokens += cost
        if len(current) >= batch_size:
            batches.append((current, preamble + "\n".join(current_parts)))
            current, current_parts, current_tokens = [], [], 0
    if current:
        batches.append((current, preamble + "\n".join(current_parts)))
    return batches


# --------------------------------------------------------------------------
# Response parsing
# --------------------------------------------------------------------------


def _normalize_symbol_ref(text: str) -> str:
    """Strip decorations models add around a symbol id.

    Small models routinely echo ``flask_demo.auth.login()`` or
    ```flask_demo.auth.login`` instead of the bare id. Backticks/quotes and a
    trailing signature ``(...)`` are removed so the answer can still be matched;
    the graph's own ids never contain parentheses, so this is lossless.
    """
    ref = text.strip().strip("`'\"")
    if ref.endswith(")") and "(" in ref:
        ref = ref[: ref.index("(")].rstrip()
    return ref.strip().strip("`'\"")


def _match_caller(answer_src: str, calls: list[DanglingCall]) -> str | None:
    """Match a model ``src`` answer to one of this batch's caller ids.

    Exact match wins; otherwise a normalized match is accepted only when it
    identifies exactly one caller (ambiguity would mean a guessed edge).
    """
    exact = {call.caller_id for call in calls}
    if answer_src in exact:
        return answer_src
    normalized = _normalize_symbol_ref(answer_src)
    if normalized in exact:
        return normalized
    candidates = {
        call.caller_id
        for call in calls
        if _normalize_symbol_ref(call.caller_id) == normalized
    }
    return candidates.pop() if len(candidates) == 1 else None


def _parse_batch_response(
    calls: list[DanglingCall],
    raw_json: str,
    graph: SymbolGraph,
    report: InferenceReport,
) -> None:
    """Fold one batch answer into ``report`` (merged / gaps / rejected)."""
    try:
        batch = InferredEdgeBatch.model_validate_json(raw_json)
    except Exception:
        report.rejected += len(calls)
        return
    # First answer per caller wins: a well-formed reply has one edge per call,
    # and a chatty model's extra repeats must not overwrite the primary one.
    by_src: dict[str, InferredEdge] = {}
    for edge in batch.edges:
        matched = _match_caller(edge.src, calls)
        if matched is None:
            continue
        by_src.setdefault(matched, edge)
    used: set[str] = set()
    for call in calls:
        answer = by_src.get(call.caller_id)
        if answer is None or call.caller_id in used:
            report.rejected += 1
            continue
        used.add(call.caller_id)
        enriched = answer.model_copy(update={"callee": call.callee, "src": call.caller_id})
        dst = _normalize_symbol_ref(answer.dst)
        if dst == "UNKNOWN" or dst not in graph.nodes:
            enriched = enriched.model_copy(update={"dst": dst})
            report.gaps.append(enriched)
            continue
        if call.caller_id == dst:
            report.rejected += 1
            continue
        if graph.has_edge(call.caller_id, dst, kind=EdgeKind.CALLS):
            continue  # already known statically; nothing to add
        if answer.confidence == "high":
            report.merged.append(enriched.model_copy(update={"dst": dst}))
        else:
            report.gaps.append(enriched.model_copy(update={"dst": dst}))


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


async def run_inference(
    graph: SymbolGraph,
    cfg: Settings,
    *,
    root_path: str | Path,
    dynamic_pool: list[tuple[str, str, int]],
    project_name: str = "",
) -> InferenceReport:
    """Run the full dynamic inference pass over ``graph``.

    Never raises for model-side issues that only cost coverage: malformed
    batches are counted as rejected. Transport failures (Ollama unreachable)
    still raise :class:`DescriberError` so the caller can decide to degrade.
    """
    report = InferenceReport(candidates_total=len(dynamic_pool))
    calls = extract_dangling_calls(graph, dynamic_pool, max_calls=cfg.inference_max_calls)
    if not calls:
        return report

    client = AsyncOllamaClient(
        host=cfg.ollama_host,
        timeout_s=cfg.request_timeout_s,
        concurrency=cfg.inference_concurrency,
    )
    batches = build_batch_prompts(
        calls,
        graph,
        root_path=root_path,
        project_name=project_name,
        batch_size=cfg.inference_batch_size,
    )
    # "selected" means actually sent: calls without candidate targets are
    # dropped during batching and never reach the model.
    report.selected = sum(len(batch_calls) for batch_calls, _ in batches)
    logger.info(
        "dynamic inference: {} dangling call(s), {} request batch(es), model={}",
        len(calls),
        len(batches),
        cfg.model_inferencer,
    )

    async def _one(batch_calls: list[DanglingCall], user: str) -> None:
        answer = await client.chat_json(
            cfg.model_inferencer,
            INFER_SYSTEM_PROMPT,
            user,
            InferredEdgeBatch,
            temperature=cfg.temperature,
            retries=cfg.max_retries,
            max_tokens=4096,
        )
        _parse_batch_response(batch_calls, answer.model_dump_json(), graph, report)

    results = await asyncio.gather(
        *(_one(batch_calls, user) for batch_calls, user in batches),
        return_exceptions=True,
    )
    for failure in results:
        if isinstance(failure, BaseException):
            if isinstance(failure, ScribeError):
                raise failure
            logger.warning("inference batch failed: {!r}", failure)

    report.requests = client.requests
    report.prompt_tokens = client.prompt_tokens
    report.completion_tokens = client.completion_tokens
    logger.info(
        "dynamic inference: merged={} gaps={} rejected={} tokens={}",
        len(report.merged),
        len(report.gaps),
        report.rejected,
        report.total_tokens,
    )
    return report


def run_inference_sync(
    graph: SymbolGraph,
    cfg: Settings,
    *,
    root_path: str | Path,
    dynamic_pool: list[tuple[str, str, int]],
    project_name: str = "",
) -> InferenceReport:
    """Blocking wrapper around :func:`run_inference` for the sync pipeline."""
    return asyncio.run(
        run_inference(
            graph,
            cfg,
            root_path=root_path,
            dynamic_pool=dynamic_pool,
            project_name=project_name,
        )
    )


def merge_inferred_edges(graph: SymbolGraph, report: InferenceReport) -> int:
    """Append every accepted edge from ``report`` to ``graph``; return the count.

    Merged edges carry ``inferred=True`` so renderer and markdown can mark
    them. Degrees are re-annotated so statistics reflect the enriched graph.
    """
    added = 0
    for edge in report.merged:
        if edge.src not in graph.nodes or edge.dst not in graph.nodes:
            continue
        if graph.has_edge(edge.src, edge.dst, kind=EdgeKind.CALLS):
            continue
        graph.add_edge(
            SymbolEdge(
                source=edge.src,
                target=edge.dst,
                kind=EdgeKind.CALLS,
                weight=1.0,
                resolved=False,
                inferred=True,
                confidence=edge.confidence,
                reason=edge.reason,
            )
        )
        added += 1
    if added:
        from moray_scribe.grapher.analyzer import annotate_degrees

        annotate_degrees(graph)
    return added


def apply_inferred_edges_to_draft(draft: ArchitectureDraft, graph: SymbolGraph) -> int:
    """Post-pass: draw merged inferred edges as dashed ``[AI推断]`` arrows.

    A graph edge lands in a flowchart when both endpoints map to diagram
    nodes, first via ``symbol_ids`` and otherwise via a unique module match.
    Existing edges between the same pair are never duplicated. Returns the
    number of arrows added across all diagrams.
    """
    inferred = [edge for edge in graph.edges if edge.inferred]
    if not inferred:
        return 0
    added = 0
    for diagram in draft.diagrams:
        if diagram.kind is not DiagramKind.FLOWCHART:
            continue
        added += _inject_into_diagram(diagram, inferred, graph)
    return added


def _resolve_diagram_node(
    diagram: MermaidDiagram,
    symbol_id: str,
    graph: SymbolGraph,
) -> str | None:
    """Map a graph symbol id to a Mermaid node id inside ``diagram``."""
    for node in sorted(diagram.nodes, key=lambda item: item.id):
        if symbol_id in node.symbol_ids:
            return node.id
    symbol = graph.nodes.get(symbol_id)
    if symbol is None:
        return None
    module_matches = [
        node.id
        for node in sorted(diagram.nodes, key=lambda item: item.id)
        if node.module is not None and node.module == symbol.module
    ]
    return module_matches[0] if len(module_matches) == 1 else None


def _inject_into_diagram(
    diagram: MermaidDiagram,
    inferred: list[SymbolEdge],
    graph: SymbolGraph,
) -> int:
    existing = {(edge.source, edge.target) for edge in diagram.edges}
    added = 0
    for edge in inferred:
        src = _resolve_diagram_node(diagram, edge.source, graph)
        dst = _resolve_diagram_node(diagram, edge.target, graph)
        if src is None or dst is None or src == dst:
            continue
        if (src, dst) in existing:
            continue
        diagram.edges.append(
            MermaidEdge(
                source=src,
                target=dst,
                label="[AI推断]",
                kind=ArchEdgeKind.CALLS,
                style=EdgeStyle.DASHED,
                inferred=True,
            )
        )
        existing.add((src, dst))
        added += 1
    return added
