"""Local-model description of a pruned symbol graph."""

from __future__ import annotations

from .client import OllamaClient
from .context import PromptContext, build_prompt_context, detect_known_gaps
from .generator import generate_architecture
from .prompts import (
    RETRY_TEMPLATE,
    SYSTEM_PROMPT,
    USER_TEMPLATE,
    build_constraint_hints,
)

__all__ = [
    "RETRY_TEMPLATE",
    "SYSTEM_PROMPT",
    "USER_TEMPLATE",
    "OllamaClient",
    "PromptContext",
    "build_constraint_hints",
    "build_prompt_context",
    "detect_known_gaps",
    "generate_architecture",
]
