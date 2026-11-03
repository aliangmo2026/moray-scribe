"""LLM narration of traced call chains as Mermaid ``sequenceDiagram`` flows.

Flow (v0.4, 任务一):

1. **Trace** — :func:`moray_scribe.grapher.tracer.trace_call_chains` produces
   deterministic root-to-leaf call chains from entry points.
2. **Narrate** — the chains are serialized into one JSON-mode request; the
   model returns :class:`~moray_scribe.schema.SequencePlan` (participants +
   ordered messages per chain, one flow per chain, same order).
3. **Self-heal** — a semantic validator rejects plans whose flow count does
   not match the chain count; the client's corrective retry runs first, then a
   shrink ladder (all chains → half → third) with prior-error pressure.
4. **Fallback** — if every rung fails, the engine renders deterministic flows
   straight from the chains (``source="engine"``), so the document section is
   never empty and never lies about its author.

The rendered artifact is a standard Mermaid ``sequenceDiagram`` produced by
:func:`moray_scribe.renderer.mermaid.sequence_flow_to_mermaid`.
"""

from __future__ import annotations

from collections.abc import Callable

from loguru import logger
from pydantic import Field

from moray_scribe.config import Settings
from moray_scribe.exceptions import DescriberError
from moray_scribe.grapher.tracer import CallChain
from moray_scribe.schema import (
    SequenceFlow,
    SequenceMessage,
    SequenceParticipant,
    SequencePlan,
    StrictModel,
    SymbolGraph,
)

from .client import OllamaClient

# Self-heal ladder: all chains → half (floor 2) → third (floor 1), deduped.
_SEQUENCE_LADDER_BUDGETS = 3

SEQUENCE_SYSTEM_PROMPT = """\
You narrate TRACED CALL CHAINS of a Python codebase as business sequence flows.
The engine renders each flow as a Mermaid sequenceDiagram; you only supply the
structure.

Output ONLY JSON of shape:
{"flows": [{"title": "...", "summary": "...",
            "participants": [{"key": "Api", "label": "后端 API"}],
            "messages": [{"sender": "Api", "receiver": "Db",
                          "label": "save()", "kind": "sync"}]}]}

Rules:
1. One flow per numbered input chain, in the SAME ORDER and SAME COUNT.
2. Each flow's participants are swim lanes: modules/classes/actors of that
   chain (plus an end-user actor when the chain starts at an HTTP route).
   "key" must match [A-Za-z_][A-Za-z0-9_]*; "label" may be Chinese, max 40 chars.
3. "messages" follow the chain's call order; labels stay short (max 80 chars),
   citing the actual function/route names from the chain.
4. "kind": "sync" for calls, "reply" for returns, "async" for fire-and-forget.
5. Every sender/receiver MUST be a declared participant key of the same flow.
6. Never invent calls that are not in the chain. 1-8 participants, 1-24
   messages per flow.
"""


class SequenceOutcome(StrictModel):
    """Result of one sequence-describe stage run."""

    flows: list[SequenceFlow] = Field(
        default_factory=list,
        description="Finalized flows, one per input chain (input order).",
    )
    degraded: bool = Field(
        default=False,
        description="True when the engine fallback authored the flows.",
    )
    requests: int = Field(default=0, description="Ollama requests issued (all rungs).")
    prompt_tokens: int = Field(default=0, ge=0, description="Prompt tokens reported.")
    completion_tokens: int = Field(default=0, ge=0, description="Completion tokens.")
    rejected_rungs: int = Field(
        default=0,
        description="Ladder rungs that failed before success/fallback.",
    )


# --------------------------------------------------------------------------
# Prompt assembly
# --------------------------------------------------------------------------


def _render_chain(chain: CallChain, graph: SymbolGraph) -> list[str]:
    lines = [f"### chain: entry=`{chain.entry}`"]
    for step in chain.steps:
        src = graph.nodes.get(step.src)
        dst = graph.nodes.get(step.dst)
        src_module = src.module if src else step.src.split(".")[0]
        dst_module = dst.module if dst else step.dst.split(".")[0]
        where = f" (line {step.lineno})" if step.lineno else ""
        lines.append(
            f"  {step.depth + 1}. `{step.src}` [{src_module}] "
            f"--{step.kind}--> `{step.dst}` [{dst_module}]{where}"
        )
    if chain.truncated:
        lines.append("  (truncated by depth limit)")
    return lines


def build_sequence_prompt(
    chains: list[CallChain],
    graph: SymbolGraph,
    *,
    project_name: str = "",
) -> str:
    """Serialize traced chains into the user prompt, deterministically."""
    parts = [
        "# Traced call chains",
        f"Project: {project_name}",
        f"Return exactly {len(chains)} flow(s), one per chain, same order.",
        "",
    ]
    for chain in chains:
        parts.extend(_render_chain(chain, graph))
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Semantic validation
# --------------------------------------------------------------------------


def _plan_validator(chain_count: int) -> Callable[[SequencePlan], list[str]]:
    def _validate(plan: SequencePlan) -> list[str]:
        errors: list[str] = []
        if len(plan.flows) != chain_count:
            errors.append(
                f"flows must contain exactly {chain_count} item(s) (one per input "
                f"chain, same order), got {len(plan.flows)}"
            )
        for index, flow in enumerate(plan.flows):
            if not flow.messages:
                errors.append(f"flows[{index}] has no messages")
            if not flow.participants:
                errors.append(f"flows[{index}] has no participants")
        return errors

    return _validate


# --------------------------------------------------------------------------
# Engine (deterministic) fallback
# --------------------------------------------------------------------------


def _participant_key(module: str, used: set[str]) -> str:
    cleaned = "".join(
        char
        for char in module
        if ("a" <= char <= "z") or ("A" <= char <= "Z") or ("0" <= char <= "9") or char == "_"
    )
    if not cleaned:
        cleaned = "P"
    if not (("a" <= cleaned[0] <= "z") or ("A" <= cleaned[0] <= "Z")):
        cleaned = f"P_{cleaned}"
    candidate = cleaned
    counter = 1
    while candidate in used:
        candidate = f"{cleaned}_{counter}"
        counter += 1
    used.add(candidate)
    return candidate


def engine_sequence_flow(chain: CallChain, graph: SymbolGraph) -> SequenceFlow:
    """Deterministically turn one traced chain into a sequence flow.

    Swim lanes are the visited symbols' modules in visit order; messages mirror
    the traced hops one-to-one (`callee()` labels, sync kind).
    """
    used_keys: set[str] = set()
    key_by_module: dict[str, str] = {}
    participants: list[SequenceParticipant] = []

    def _module_of(symbol_id: str) -> str:
        node = graph.nodes.get(symbol_id)
        if node is not None:
            return node.module
        return symbol_id.split(".")[0] if "." in symbol_id else symbol_id

    def _key_of(module: str) -> str:
        if module not in key_by_module:
            key = _participant_key(module, used_keys)
            key_by_module[module] = key
            participants.append(SequenceParticipant(key=key, label=module))
        return key_by_module[module]

    entry_module = _module_of(chain.entry)
    entry_key = _key_of(entry_module)

    messages: list[SequenceMessage] = []
    for step in chain.steps:
        dst_node = graph.nodes.get(step.dst)
        callee = dst_node.name if dst_node else step.dst.rsplit(".", 1)[-1]
        messages.append(
            SequenceMessage(
                sender=_key_of(_module_of(step.src)),
                receiver=_key_of(_module_of(step.dst)),
                label=f"{callee}()"[:80],
            )
        )
    return SequenceFlow(
        entry=chain.entry,
        title=f"调用链：{chain.entry}",
        summary="由引擎从入口点确定性追踪生成（LLM 阶段不可用或未通过校验）。",
        participants=participants or [SequenceParticipant(key=entry_key, label=entry_module)],
        messages=messages or [SequenceMessage(sender=entry_key, receiver=entry_key, label="noop")],
        source="engine",
    )


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _ladder_budgets(chain_count: int) -> list[int]:
    """Rung budgets: [n, max(2, n//2), max(1, n//3)], clamped and deduped."""
    budgets: list[int] = []
    for budget in (
        chain_count,
        max(2, chain_count // 2),
        max(1, chain_count // 3),
    ):
        budget = min(chain_count, budget)
        if budget not in budgets:
            budgets.append(budget)
    return budgets


def describe_sequences(
    chains: list[CallChain],
    client: OllamaClient,
    cfg: Settings,
    *,
    graph: SymbolGraph,
    project_name: str = "",
) -> SequenceOutcome:
    """Narrate chains via the model, degrading to engine flows on failure.

    Never raises for model-side failures: every failed rung shrinks the chain
    set, and the deterministic fallback guarantees one flow per input chain.
    """
    if not chains:
        return SequenceOutcome()

    system = SEQUENCE_SYSTEM_PROMPT
    base_user = build_sequence_prompt(chains, graph, project_name=project_name)
    prior_errors: list[str] = []
    outcome = SequenceOutcome()

    for rung, budget in enumerate(_ladder_budgets(len(chains))):
        rung_chains = chains[:budget]
        user = base_user
        if rung > 0:
            user = (
                f"{base_user}\n\n# Correction pressure\n"
                f"Previous attempts were rejected ({len(prior_errors)} failure(s)). "
                f"Return EXACTLY {budget} flow(s) for the FIRST {budget} chains only."
            )
        try:
            plan = client.chat_json(
                cfg.model_describer,
                system,
                user,
                SequencePlan,
                temperature=cfg.temperature,
                retries=cfg.max_retries,
                max_tokens=cfg.max_output_tokens,
                validator=_plan_validator(len(rung_chains)),
            )
        except DescriberError as exc:
            outcome.rejected_rungs += 1
            prior_errors.append(str(exc)[:300])
            logger.warning(
                "sequence rung {} failed ({} chains): {}",
                rung + 1,
                budget,
                str(exc)[:160],
            )
            continue
        outcome.flows = [
            SequenceFlow(
                entry=rung_chains[index].entry,
                title=draft.title,
                summary=draft.summary,
                participants=draft.participants,
                messages=draft.messages,
                source="ai",
            )
            for index, draft in enumerate(plan.flows)
        ]
        break

    if not outcome.flows:
        outcome.degraded = True
        outcome.flows = [engine_sequence_flow(chain, graph) for chain in chains]

    outcome.requests = client.requests
    outcome.prompt_tokens = client.prompt_tokens
    outcome.completion_tokens = client.completion_tokens
    logger.info(
        "sequences: {} flow(s), degraded={}, requests={}, rejected_rungs={}",
        len(outcome.flows),
        outcome.degraded,
        outcome.requests,
        outcome.rejected_rungs,
    )
    return outcome
