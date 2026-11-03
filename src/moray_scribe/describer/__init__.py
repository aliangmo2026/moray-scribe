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
from .sequencer import (
    SEQUENCE_SYSTEM_PROMPT,
    SequenceOutcome,
    build_sequence_prompt,
    describe_sequences,
    engine_sequence_flow,
)

__all__ = [
    "INFER_SYSTEM_PROMPT",
    "RETRY_TEMPLATE",
    "SEQUENCE_SYSTEM_PROMPT",
    "SYSTEM_PROMPT",
    "USER_TEMPLATE",
    "AsyncOllamaClient",
    "InferenceReport",
    "InferredEdge",
    "InferredEdgeBatch",
    "OllamaClient",
    "PromptContext",
    "SequenceOutcome",
    "apply_inferred_edges_to_draft",
    "build_batch_prompts",
    "build_constraint_hints",
    "build_prompt_context",
    "build_sequence_prompt",
    "describe_sequences",
    "detect_known_gaps",
    "engine_sequence_flow",
    "extract_dangling_calls",
    "generate_architecture",
    "merge_inferred_edges",
    "run_inference",
    "run_inference_sync",
]
