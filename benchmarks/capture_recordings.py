"""Capture REAL Ollama responses into replay recordings for the test suite.

Usage (requires a running Ollama with the model installed)::

    .venv/Scripts/python.exe benchmarks/capture_recordings.py --model qwen2.5:7b

Writes wire-shaped ``{"message": {"content": ...}}`` responses (exactly what
``POST /api/chat`` returns with ``stream: false``) into
``tests/fixtures/recordings/<model>/<name>.json``. The ``mock_ollama`` fixture
replays these files, so the whole test suite runs with zero network access to
a real Ollama while still exercising real model output shapes.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RECORDINGS_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "recordings"

# (name, system, user, format_json) — one recording each. The prompts mirror
# the production stages (describer draft, sequence plan, graph-RAG chat) so
# the replayed content matches what each consumer expects.
CAPTURES: list[tuple[str, str, str, bool]] = [
    (
        "describer-draft",
        "You output ONLY a JSON architecture draft describing a tiny module graph.",
        'Produce a draft for a module "core" with function "Engine.run". '
        'JSON shape: {"title": str, "summary": str(>=50 chars), "diagrams": [], '
        '"modules": [{"module": "core", "summary": "...", "responsibilities": [], '
        '"symbol_ids": [], "risk": "low"}], "core_hubs": [], "dead_code": [], '
        '"risks": [], "recommendations": []}',
        True,
    ),
    (
        "sequence-plan",
        "You narrate traced call chains as business sequence flows. Output ONLY JSON.",
        'One chain: entry=`app.main` -> `core.Engine` [instantiates]. '
        'Return {"flows": [{"title": "...", "summary": "...", "participants": '
        '[{"key": "App", "label": "app 模块"}, {"key": "Core", "label": "core 模块"}], '
        '"messages": [{"sender": "App", "receiver": "Core", "label": "Engine()", '
        '"kind": "sync"}]}]}',
        True,
    ),
    (
        "chat-answer",
        "You answer questions about a codebase using ONLY the provided graph context. "
        'Output ONLY JSON {"answer": "...", "nodes": []}.',
        "Graph nodes: app.login -> app.check_password. Question: 修改 login 会影响什么？",
        True,
    ),
]


# The tool talks to a local Ollama by design, so loopback/LAN targets are the
# point; the SSRF hardening that DOES apply is the same as the viewer's chat
# backend: an explicit http(s) scheme allowlist and redirects disabled, so the
# fixed /api/chat path can never be re-pointed by a hostile host response.
_ALLOWED_SCHEMES = ("http", "https")


def validate_host(host: str) -> str:
    """Reject schemes outside the http(s) allowlist; return the trimmed host."""
    stripped = (host or "").strip()
    if "://" not in stripped:
        raise ValueError("Ollama host must include an explicit http(s) scheme")
    scheme = stripped.split("://", 1)[0].lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"Ollama host scheme must be one of {_ALLOWED_SCHEMES}, got {scheme!r}"
        )
    return stripped


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Block redirects so the fixed Ollama path cannot be re-pointed."""

    def redirect_request(
        self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any
    ) -> None:
        raise ValueError(f"Ollama host tried to redirect ({code}); refusing")


def capture(host: str, model: str, system: str, user: str, json_mode: bool) -> dict[str, object]:
    host = validate_host(host)
    payload: dict[str, object] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 512},
    }
    if json_mode:
        payload["format"] = "json"
    request = urllib.request.Request(
        f"{host}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirectHandler())
    with opener.open(request, timeout=600) as response:
        body = json.loads(response.read().decode("utf-8"))
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "endpoint": "/api/chat",
        "prompt": {"system": system, "user": user},
        "response": {
            key: body.get(key)
            for key in ("model", "message", "prompt_eval_count", "eval_count")
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture real Ollama responses for replay.")
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--force", action="store_true", help="Overwrite existing recordings.")
    args = parser.parse_args(argv)

    try:
        validated_host = validate_host(args.host)
    except ValueError as exc:
        print(f"[FAIL] {exc}")
        return 2
    parser.set_defaults(host=validated_host)

    out_dir = RECORDINGS_DIR / args.model.replace(":", "_")
    out_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for name, system, user, json_mode in CAPTURES:
        target = out_dir / f"{name}.json"
        if target.is_file() and not args.force:
            print(f"[skip] {target.name} exists (use --force)")
            continue
        try:
            record = capture(args.host, args.model, system, user, json_mode)
        except Exception as exc:
            print(f"[FAIL] {name}: {exc}")
            failures += 1
            continue
        content = record["response"]["message"]["content"]  # type: ignore[index]
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        preview = str(content)[:100].replace("\n", " ")
        print(f"[ok] {target} :: {preview}...")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
