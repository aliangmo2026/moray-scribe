"""Ollama client that only accepts schema-valid, semantically valid JSON."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import TypeVar

import httpx
import ollama
from pydantic import BaseModel, ValidationError

from moray_scribe.exceptions import DescriberError

BaseModelT = TypeVar("BaseModelT", bound=BaseModel)

# Hard cap on generated tokens per attempt. Ollama defaults to unbounded output;
# on real hardware a small model asked for format="json" can loop instead of
# emitting EOS, running until the request times out. Capping output keeps a
# successful draft fast and a failing one bounded (finding #3).
_MAX_OUTPUT_TOKENS = 4096


def _field_names_of(model_name: str, schema: type[BaseModel]) -> list[str]:
    """Sorted property names of a ``$defs`` sub-model named ``model_name``."""
    definitions = schema.model_json_schema().get("$defs", {})
    node = definitions.get(model_name, {})
    return sorted(node.get("properties", {}))


def _model_name_at(schema: type[BaseModel], loc: Sequence[object]) -> str:
    """Walk the JSON Schema along ``loc`` and return the innermost model name.

    ``loc`` is a pydantic error location; the last element may be an extra field
    that is not in any model, so the name returned is the model that *contains*
    it (e.g. ``diagrams.0.nodes.0.dependencies`` -> ``MermaidNode``).
    """
    document = schema.model_json_schema()
    definitions = document.get("$defs", {})

    def _deref(node: object) -> tuple[str | None, dict[str, object]]:
        if isinstance(node, dict) and isinstance(node.get("$ref"), str):
            name = node["$ref"].split("/")[-1]
            return name, definitions.get(name, {})
        return None, node if isinstance(node, dict) else {}

    current: object = document
    current_name = schema.__name__
    for part in loc:
        if not isinstance(current, dict):
            break
        if current.get("type") == "array":
            item_name, current = _deref(current.get("items", {}))
            if item_name:
                current_name = item_name
            continue  # `part` is the array index.
        props = current.get("properties", {})
        if part in props:
            prop_name, current = _deref(props[part])
            if prop_name:
                current_name = prop_name
    return current_name


def _build_correction_hint(exc: ValidationError, schema: type[BaseModel]) -> str:
    """Render a ValidationError for the retry context.

    For ``extra_forbidden`` errors it reverse-maps the location to the offending
    sub-model and lists that model's allowed fields, so the model can drop the
    extra key instead of guessing (finding #4 / D-6.6).
    """
    lines: list[str] = []
    for err in exc.errors():
        loc_parts = list(err.get("loc", ()))
        loc = ".".join(str(part) for part in loc_parts)
        if err.get("type") == "extra_forbidden":
            model_name = _model_name_at(schema, loc_parts)
            allowed = ", ".join(f"`{name}`" for name in _field_names_of(model_name, schema))
            lines.append(
                f"- `{loc}` is not allowed. `{model_name}` accepts ONLY these fields: "
                f"{allowed}. Put relations in `edges`, not inside nodes."
            )
        else:
            lines.append(f"- `{loc}`: {err.get('msg', '')}")
    return "\n".join(lines)


def _generic_constraints(schema: type[BaseModel]) -> list[str]:
    """Reflect every pattern/min_length/max_length constraint from the JSON Schema.

    This keeps the client free of business rules (§10.6): it only surfaces the
    schema's own hard constraints back to the model on a rejected attempt.
    """
    document = schema.model_json_schema()
    hints: list[str] = []
    seen: set[str] = set()

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            pattern = node.get("pattern")
            if isinstance(pattern, str) and pattern not in seen:
                seen.add(pattern)
                hints.append(f"string must match pattern {pattern}")
            min_length = node.get("min_length")
            if isinstance(min_length, int):
                text = f"string must have at least {min_length} characters"
                if text not in seen:
                    seen.add(text)
                    hints.append(text)
            max_length = node.get("max_length")
            if isinstance(max_length, int):
                text = f"string must have at most {max_length} characters"
                if text not in seen:
                    seen.add(text)
                    hints.append(text)
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(document)
    return hints


def _http_status(exc: Exception) -> int | None:
    """Best-effort HTTP status extraction across client exception types.

    ``ollama.ResponseError`` carries ``status_code`` directly (defaulting to
    -1 for stream-level errors); ``httpx.HTTPStatusError`` carries it on
    ``response``.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status > 0:
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


class OllamaClient:
    """JSON-mode chat helper for one Ollama host."""

    host: str
    timeout_s: int

    def __init__(self, host: str, timeout_s: int = 600) -> None:
        self.host = host
        self.timeout_s = timeout_s
        self._client = ollama.Client(host=host, timeout=timeout_s)

    def _chat_once(
        self,
        model: str,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Perform one chat round-trip and return the raw text content.

        Transport failures are classified by cause, not lumped into one
        "not reachable" message: an unreachable service, a reachable-but-slow
        generation and an HTTP error need different user actions. The ollama
        client translates some httpx exceptions before they surface here
        (``httpx.ConnectError`` -> builtin ``ConnectionError``,
        ``httpx.HTTPStatusError`` -> ``ollama.ResponseError``), so both the
        httpx-native type and its translated form are handled.
        """
        try:
            response = self._client.chat(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                format="json",
                options={
                    "temperature": temperature,
                    "num_predict": max_tokens,
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
                f"Generation timed out after {self.timeout_s}s. The model is reachable "
                "but this workload is too slow for the current hardware/timeout.\n"
                "hint: try one or more of: (1) MORAY_SCRIBE_REQUEST_TIMEOUT_S=900, "
                "(2) MORAY_SCRIBE_MAX_OUTPUT_TOKENS=2048, (3) a smaller/faster model "
                "like qwen2.5:1.5b, (4) run on GPU if available, (5) reduce --max-nodes."
            ) from exc
        except (httpx.HTTPStatusError, ollama.ResponseError) as exc:
            status = _http_status(exc)
            detail = f"HTTP {status}" if status is not None else f"an error ({exc})"
            raise DescriberError(
                f"Ollama returned {detail} for model '{model}'\n"
                "hint: run 'ollama list' to see installed models. "
                f"Pull the model with 'ollama pull {model}' if missing."
            ) from exc
        except Exception as exc:
            raise DescriberError(
                f"Unexpected error calling Ollama: {exc!r}\n"
                "hint: run 'moray-scribe health' to check environment, "
                "then report with the error text."
            ) from exc
        return str(response["message"]["content"])

    def _retry_prompts(
        self,
        base_system: str,
        base_user: str,
        previous_output: str,
        error_text: str,
        hints: list[str],
    ) -> tuple[str, str]:
        """Build the corrected system/user pair for the next attempt."""
        hint_lines = "\n".join(f"- {hint}" for hint in hints) or "- (none)"
        system = (
            f"{base_system}\n\n"
            "# Correction required\n"
            "Your previous output was rejected.\n"
            f"Error:\n{error_text}\n"
            f"Valid constraints you MUST satisfy:\n{hint_lines}\n"
            "Output ONLY the corrected JSON. No prose."
        )
        user = (
            f"{base_user}\n\n"
            "# Your previous output\n"
            f"{previous_output}\n\n"
            "Output ONLY the corrected JSON object. No prose, no markdown."
        )
        return system, user

    def chat_json(
        self,
        model: str,
        system: str,
        user: str,
        schema: type[BaseModelT],
        *,
        temperature: float = 0.1,
        retries: int = 2,
        max_tokens: int | None = None,
        validator: Callable[[BaseModelT], list[str]] | None = None,
    ) -> BaseModelT:
        """Request JSON, validate it, run an optional semantic validator, retry on failure.

        Each rejected attempt appends a correction block (the error text plus the
        schema's hard constraints) to the system prompt and echoes the previous
        output into the user prompt before re-asking. Exhausting all attempts
        raises :class:`DescriberError` carrying the last raw output and every
        error seen. Connection failures surface immediately as a
        "not reachable" :class:`DescriberError`.
        """
        hints = _generic_constraints(schema)
        base_system, base_user = system, user
        cur_system, cur_user = base_system, base_user
        errors: list[str] = []
        attempts = max(0, retries) + 1
        last_text = ""
        budget = max_tokens if max_tokens is not None else _MAX_OUTPUT_TOKENS

        for _ in range(attempts):
            text = self._chat_once(model, cur_system, cur_user, temperature, budget)
            last_text = text
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append(exc.msg)
                cur_system, cur_user = self._retry_prompts(
                    base_system, base_user, text, exc.msg, hints
                )
                continue
            try:
                obj = schema.model_validate_json(text)
            except ValidationError as exc:
                error_text = _build_correction_hint(exc, schema)
                errors.append(error_text)
                cur_system, cur_user = self._retry_prompts(
                    base_system, base_user, text, error_text, hints
                )
                continue
            if validator is not None:
                semantic_errors = validator(obj)
                if semantic_errors:
                    error_text = "\n".join(semantic_errors)
                    errors.append(error_text)
                    cur_system, cur_user = self._retry_prompts(
                        base_system, base_user, text, error_text, hints
                    )
                    continue
            return obj

        raise DescriberError(
            f"chat_json failed after {attempts} attempt(s) for model {model!r}; "
            f"last output: {last_text!r}; errors: {errors}"
        )
