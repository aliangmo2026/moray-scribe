"""Runtime configuration loaded from environment variables and ``.env``."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All tunable moray-scribe runtime parameters."""

    model_config = SettingsConfigDict(
        env_prefix="MORAY_SCRIBE_",
        env_file=".env",
        extra="ignore",
    )

    ollama_host: str = "http://localhost:11434"
    model_describer: str = "qwen2.5-coder:14b"
    model_summarizer: str = "qwen2.5-coder:7b"
    max_nodes: int = 200
    max_edges: int = 500
    max_file_size_kb: int = 512
    request_timeout_s: int = Field(
        600,
        ge=60,
        le=3600,
        description=(
            "Ollama request timeout in seconds. Empirical default for the doc "
            "stage with a 7B model: drafting a complex architecture draft "
            "typically takes 2-8 minutes on CPU."
        ),
    )
    max_retries: int = 2
    temperature: float = 0.1
    include: list[str] = ["**/*.py"]
    exclude: list[str] = ["**/test_*.py", "**/__pycache__/**", "**/.venv/**"]
    out_dir: Path = Path("out")
    max_output_tokens: int = Field(
        4096,
        ge=256,
        le=32768,
        description=(
            "Max tokens Ollama may generate per attempt. Raise for large graphs "
            "(e.g. 8192+) or the draft can be truncated."
        ),
    )
    inference_enabled: bool = Field(
        False,
        description=(
            "Run the LLM dynamic-inference stage during `doc` (dangling-call "
            "resolution). Off by default so deterministic runs stay deterministic; "
            "enable with --infer or MORAY_SCRIBE_INFERENCE_ENABLED=true."
        ),
    )
    model_inferencer: str = Field(
        "qwen2.5:7b",
        description="Ollama model used for dangling-call inference (small and fast is best).",
    )
    inference_batch_size: int = Field(
        8,
        ge=1,
        le=64,
        description="Dangling call sites per inference request.",
    )
    inference_max_calls: int = Field(
        60,
        ge=0,
        le=10000,
        description="Cap on dangling calls sent to the model per run (top PageRank first).",
    )
    inference_concurrency: int = Field(
        4,
        ge=1,
        le=16,
        description="Max concurrent inference requests to Ollama.",
    )


def load_settings(path: Path | None = None) -> Settings:
    """Load settings from the environment, optionally layering one ``.env`` file."""
    if path is None:
        return Settings()
    if not path.is_file():
        raise FileNotFoundError(f"configuration file not found: {path}")
    return Settings(_env_file=path)
