"""Local-model description of a pruned symbol graph."""

from __future__ import annotations

from .async_client import AsyncOllamaClient
from .client import OllamaClient
from .context import PromptContext, build_prompt_context, detect_known_gaps
from .generator import generate_architecture
from .inferencer import (
    INFER_SYSTEM_PROMPT,
    InferenceReport,
    InferredEdge,
    InferredEdgeBatch,
    apply_inferred_edges_to_draft,
    build_batch_prompts,
    extract_dangling_calls,
    merge_inferred_edges,
    run_inference,
    run_inference_sync,
)
from .prompts import (
    RETRY_TEMPLATE,
    SYSTEM_PROMPT,
    USER_TEMPLATE,
    build_constraint_hints,
)

__all__ = [
    "INFER_SYSTEM_PROMPT",
    "RETRY_TEMPLATE",
    "SYSTEM_PROMPT",
    "USER_TEMPLATE",
    "AsyncOllamaClient",
    "InferenceReport",
    "InferredEdge",
    "InferredEdgeBatch",
    "OllamaClient",
    "PromptContext",
    "apply_inferred_edges_to_draft",
    "build_batch_prompts",
    "build_constraint_hints",
    "build_prompt_context",
    "detect_known_gaps",
    "extract_dangling_calls",
    "generate_architecture",
    "merge_inferred_edges",
    "run_inference",
    "run_inference_sync",
]
