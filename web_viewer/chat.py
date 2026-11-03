"""Ollama-backed chat answering questions over a retrieved sub-graph.

The backend POSTs to a fixed ``/api/chat`` path on the configured Ollama host
(scheme restricted to http/https, redirects disabled — the URL never carries
user input beyond the validated host), asks for a JSON answer plus cited node
ids, and filters the citations against the retrieved context so the UI can
only ever highlight real graph nodes.
"""

from __future__ import annotations

import json
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

# Cap on cited node ids accepted from the model.
_MAX_CITED_NODES = 8
# Body size cap for the question the viewer posts to us.
_MAX_QUESTION_CHARS = 2000

_ALLOWED_SCHEMES = ("http", "https")

CHAT_SYSTEM_PROMPT = """\
You answer questions about a Python codebase using ONLY the retrieved symbol
graph and source slices provided below. The graph is deterministic output of
static analysis (moray-scribe); trust it over your own assumptions.

Output ONLY JSON of shape:
{"answer": "...", "nodes": ["symbol.id.one", "symbol.id.two"]}

Rules:
1. "answer" is a concise, grounded answer in the user's language (Chinese if
   the question is Chinese). Cite concrete symbol ids, files and call hops.
2. "nodes" lists up to 8 symbol ids you referenced, taken VERBATIM from the
   provided graph nodes. Never invent ids. Use [] when nothing applies.
3. If the retrieved context is insufficient, say exactly what extra symbol or
   file to look at instead of guessing.
4. For impact questions (修改X会影响什么), walk the incoming "called by" edges
   in the graph and answer with the affected callers, ranked by PageRank.
"""

_CONTEXT_INSTRUCTIONS = """\
# Retrieved graph context (deterministic static analysis)
Nodes (id | kind | module | file | pagerank):
"""


def render_user_prompt(
    question: str,
    context: dict[str, Any],
    *,
    project: str = "",
) -> str:
    """Render the retrieved sub-graph + slices + question as the user prompt."""
    parts: list[str] = [f"Project: {project}" if project else "", _CONTEXT_INSTRUCTIONS]
    nodes = context.get("nodes") or []
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            route = node.get("route")
            route_text = ""
            if isinstance(route, dict) and route.get("path"):
                route_text = f" route={route.get('path')}"
            parts.append(
                f"- {node.get('id')} | {node.get('kind')} | {node.get('module')} | "
                f"{node.get('file')}{route_text} | {node.get('pagerank')}"
            )
    edges = context.get("edges") or []
    if isinstance(edges, list) and edges:
        parts.append("Edges (caller -> callee | kind):")
        for edge in edges[:40]:
            if isinstance(edge, dict):
                parts.append(f"- {edge.get('source')} -> {edge.get('target')} | {edge.get('kind')}")
    slices = context.get("slices") or []
    if isinstance(slices, list):
        for sl in slices:
            if not isinstance(sl, dict):
                continue
            parts.append(
                f"# source {sl.get('file')}:{sl.get('start')}"
            )
            lines = sl.get("lines") or []
            if isinstance(lines, list):
                parts.extend(str(line) for line in lines[:20])
    parts.append("")
    parts.append(f"# Question\n{question}")
    parts.append(
        "\nRespond with ONLY the JSON object {\"answer\", \"nodes\"}. "
        f"\"nodes\" must be ids from the node list above (max {_MAX_CITED_NODES})."
    )
    return "\n".join(parts)


class ChatError(Exception):
    """Raised when the chat backend cannot produce an answer."""


def _validate_host(host: str) -> str:
    """Reject schemes/hosts outside the http(s) allowlist (SSRF guard)."""
    stripped = (host or "").strip()
    if "://" not in stripped:
        raise ChatError("Ollama host must include an explicit http(s) scheme")
    scheme = stripped.split("://", 1)[0].lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ChatError(
            f"Ollama host scheme must be one of {_ALLOWED_SCHEMES}, got {scheme!r}"
        )
    return stripped


class OllamaChatBackend:
    """Answers one question per call via Ollama's ``/api/chat`` endpoint."""

    def __init__(self, host: str, model: str, timeout_s: int = 180) -> None:
        self.host = _validate_host(host)
        self.model = model
        self.timeout_s = timeout_s
        # Redirects disabled: the fixed path must never be re-pointed.
        self._opener = self._build_opener()

    @staticmethod
    def _build_opener() -> urllib_request.OpenerDirector:
        return urllib_request.build_opener(_NoRedirectHandler())

    def answer(self, question: str, context: dict[str, Any]) -> dict[str, Any]:
        """Return {"answer", "nodes", "degraded"} for one question.

        Raises :class:`ChatError` on transport failure. A model reply that is
        not valid JSON still degrades to a prose answer (``degraded=True``)
        rather than failing the request.
        """
        user = render_user_prompt(question, context)
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": CHAT_SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.2},
            }
        ).encode("utf-8")
        request = urllib_request.Request(
            f"{self.host}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout_s) as response:
                body = json.loads(response.read().decode("utf-8"))
        except ChatError:
            raise
        except urllib_error.HTTPError as exc:
            raise ChatError(f"Ollama HTTP {exc.code} for model '{self.model}'") from exc
        except (urllib_error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise ChatError(f"Ollama chat failed: {exc}") from exc

        text = ""
        message = body.get("message") if isinstance(body, dict) else None
        if isinstance(message, dict):
            text = str(message.get("content") or "")
        if not text.strip():
            raise ChatError(f"Ollama returned an empty answer for model '{self.model}'")

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"answer": text.strip(), "nodes": [], "degraded": True}

        answer_text = str(parsed.get("answer") or "").strip() if isinstance(parsed, dict) else ""
        if not answer_text:
            return {"answer": text.strip(), "nodes": [], "degraded": True}

        allowed = {
            str(node.get("id"))
            for node in (context.get("nodes") or [])
            if isinstance(node, dict)
        }
        cited: list[str] = []
        raw_nodes = parsed.get("nodes") if isinstance(parsed, dict) else None
        if isinstance(raw_nodes, list):
            for item in raw_nodes:
                node_id = str(item).strip().strip("`'\"")
                if node_id in allowed and node_id not in cited:
                    cited.append(node_id)
                if len(cited) >= _MAX_CITED_NODES:
                    break
        return {"answer": answer_text, "nodes": cited, "degraded": False}


class _NoRedirectHandler(urllib_request.HTTPRedirectHandler):
    """Block redirects so the fixed Ollama path cannot be re-pointed."""

    def redirect_request(
        self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any
    ) -> None:
        raise ChatError(f"Ollama host tried to redirect ({code}); refusing")
