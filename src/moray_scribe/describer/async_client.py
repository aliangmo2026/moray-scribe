"""Concurrency-capped async Ollama client for the dynamic inference stage.

The sync :class:`~moray_scribe.describer.client.OllamaClient` serves the
single-shot describer; inference fans out dozens of small requests, which
must not serialize (§4.3). This client wraps ``ollama.AsyncClient`` with a
semaphore, retries invalid JSON once with a correction hint, and accumulates
prompt/completion token usage reported by Ollama.
"""

from __future__ import annotations

import asyncio
from typing import TypeVar

import httpx
import ollama
from pydantic import BaseModel, ValidationError

from moray_scribe.exceptions import DescriberError

from .client import _build_correction_hint, _http_status, num_ctx_for

BaseModelT = TypeVar("BaseModelT", bound=BaseModel)


class AsyncOllamaClient:
    """Async JSON-mode chat helper with bounded concurrency and usage stats."""

    host: str
    timeout_s: int

    def __init__(self, host: str, timeout_s: int = 600, concurrency: int = 4) -> None:
        self.host = host
        self.timeout_s = timeout_s
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._client = ollama.AsyncClient(host=host, timeout=timeout_s)
        self.requests = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def _num_ctx(self, system: str, user: str, max_tokens: int) -> int:
        """Context window for one request: prompt + headroom, clamped for CPU."""
        return num_ctx_for(system, user, max_tokens)

    async def _chat_once(
        self,
        model: str,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """One async chat round-trip; usage counters advance on success."""
        try:
            async with self._semaphore:
                response = await self._client.chat(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    format="json",
                    options={
                        "temperature": temperature,
                        "num_predict": max_tokens,
                        "num_ctx": self._num_ctx(system, user, max_tokens),
                    },
                )
        except (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError) as exc:
            raise DescriberError(
                f"Ollama service not reachable at {self.host}\n"
                "hint: run 'ollama serve' in another terminal. "
                "Verify the host in MORAY_SCRIBE_OLLAMA_HOST."
            ) from exc
        except httpx.ReadTimeout as exc:
            raise DescriberError(
                f"Inference generation timed out after {self.timeout_s}s. "
                "hint: lower MORAY_SCRIBE_INFERENCE_BATCH_SIZE, raise "
                "MORAY_SCRIBE_REQUEST_TIMEOUT_S, or use a faster model."
            ) from exc
        except (httpx.HTTPStatusError, ollama.ResponseError) as exc:
            status = _http_status(exc)
            detail = f"HTTP {status}" if status is not None else f"an error ({exc})"
            raise DescriberError(
                f"Ollama returned {detail} for model '{model}'\n"
                "hint: run 'ollama list' to see installed models."
            ) from exc
        except Exception as exc:
            raise DescriberError(
                f"Unexpected error calling Ollama: {exc!r}"
            ) from exc
        self.requests += 1
        self.prompt_tokens += int(response.get("prompt_eval_count") or 0)
        self.completion_tokens += int(response.get("eval_count") or 0)
        return str(response["message"]["content"])

    async def chat_json(
        self,
        model: str,
        system: str,
        user: str,
        schema: type[BaseModelT],
        *,
        temperature: float = 0.1,
        retries: int = 1,
        max_tokens: int = 2048,
    ) -> BaseModelT:
        """Request one JSON object; one corrective retry on schema rejection."""
        cur_system, cur_user = system, user
        attempts = max(0, retries) + 1
        last_text = ""
        for _ in range(attempts):
            text = await self._chat_once(model, cur_system, cur_user, temperature, max_tokens)
            last_text = text
            try:
                return schema.model_validate_json(text)
            except ValidationError as exc:
                error_text = _build_correction_hint(exc, schema)
                cur_system = (
                    f"{system}\n\n# Correction required\nYour previous output was "
                    f"rejected.\nError:\n{error_text}\nOutput ONLY the corrected JSON."
                )
                cur_user = (
                    f"{user}\n\n# Your previous output\n{text}\n\n"
                    "Output ONLY the corrected JSON object. No prose, no markdown."
                )
        raise DescriberError(
            f"async chat_json failed after {attempts} attempt(s) for model {model!r}; "
            f"last output: {last_text!r}"
        )
