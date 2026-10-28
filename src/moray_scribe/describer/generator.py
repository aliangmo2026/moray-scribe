"""Turn a pruned symbol graph into a schema-valid architecture draft."""

from __future__ import annotations

from typing import Literal

from moray_scribe.config import Settings
from moray_scribe.grapher import prune
from moray_scribe.schema import ArchitectureDraft, DiagramKind, NodeKind, SymbolGraph

from .client import OllamaClient
from .context import PromptContext, build_prompt_context
from .prompts import SYSTEM_PROMPT, USER_TEMPLATE


def generate_architecture(
    graph: SymbolGraph,
    client: OllamaClient,
    cfg: Settings,
    *,
    project_name: str,
    root_path: str,
    mode: Literal["single", "two-phase"] = "single",
) -> ArchitectureDraft:
    """Generate one architecture draft from a (possibly oversized) symbol graph.

    ``mode="single"`` is the v1 path: optionally prune to ``cfg.max_nodes``, build
    the prompt context, then make one schema-validated Ollama call whose semantic
    validator is :func:`_post_validate`. ``mode="two-phase"`` is reserved for v2.
    """
    if mode == "two-phase":
        raise NotImplementedError("TODO: v2")

    working = graph
    if len(working.nodes) > cfg.max_nodes:
        working = prune(working, max_nodes=cfg.max_nodes, max_edges=cfg.max_edges)

    context = build_prompt_context(working, project_name=project_name, root_path=root_path)
    user = USER_TEMPLATE.format(
        project_name=context.project_name,
        root_path=context.root_path,
        n_nodes=context.n_nodes,
        n_edges=context.n_edges,
        nodes_json=context.nodes_json,
        edges_json=context.edges_json,
        known_gaps=context.known_gaps,
    )
    return client.chat_json(
        model=cfg.model_describer,
        system=SYSTEM_PROMPT,
        user=user,
        schema=ArchitectureDraft,
        temperature=cfg.temperature,
        retries=cfg.max_retries,
        max_tokens=cfg.max_output_tokens,
        validator=lambda draft: _post_validate(draft, context),
    )


def _post_validate(draft: ArchitectureDraft, ctx: PromptContext) -> list[str]:
    """Fourth-layer semantic checks the JSON schema cannot express.

    Returns human-readable error strings; an empty list means the draft passes.
    """
    errors: list[str] = []
    known_modules = set(ctx.module_set)
    known_ids = set(ctx.node_ids)

    for note in draft.modules:
        if note.module not in known_modules:
            errors.append(f"unknown module {note.module!r} in modules[]")
        for symbol_id in note.symbol_ids:
            if symbol_id not in known_ids:
                errors.append(
                    f"unknown key symbol {symbol_id!r} in modules[{note.module!r}].symbol_ids"
                )

    if not draft.diagrams:
        errors.append("draft must contain at least one diagram")
    else:
        for index, diagram in enumerate(draft.diagrams):
            if not any(node.kind != NodeKind.EXTERNAL for node in diagram.nodes):
                errors.append(
                    f"diagram[{index}] must contain at least one non-external node"
                )
        # R5-1: the first diagram must be the architecture flowchart.
        if draft.diagrams[0].kind is not DiagramKind.FLOWCHART:
            errors.append(
                "diagrams[0] must be a flowchart — architecture diagram first, "
                "detail diagrams later."
            )
        # R5-2: reject a 1:1 symbol-to-node mapping on non-trivial projects.
        if len(ctx.node_ids) >= 20:
            budget = max(10, int(len(ctx.node_ids) * 0.6))
            first_node_count = len(draft.diagrams[0].nodes)
            if first_node_count > budget:
                errors.append(
                    f"diagrams[0].nodes has {first_node_count} nodes but budget for "
                    f"abstraction is {budget} (60% of {len(ctx.node_ids)} input "
                    f"symbols). MERGE closely-related subsystems — do NOT map "
                    f"symbols 1:1 to nodes."
                )

    if len(draft.summary) < 50:
        errors.append(f"summary must be at least 50 characters (got {len(draft.summary)})")

    return errors
