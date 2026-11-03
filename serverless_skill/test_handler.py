"""Local simulation of the Work Buddy cloud call.

Runs ``handler`` against an in-memory ``files`` dict and asserts the JSON
result shape, then (with ``--zip``) repeats the whole suite against the
contents extracted from the built ``moray-scribe-skill.zip`` so the shipped
artifact itself is verified end to end.

Usage:
    python serverless_skill/test_handler.py          # test the staged copy
    python serverless_skill/build_zip.py && python serverless_skill/test_handler.py --zip
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
# In --zip self-test mode the subprocess points this at the extracted archive;
# either way index.py and the staged moray_scribe package must resolve from here.
if "MORAY_SKILL_EXTRACTED" in os.environ:
    IMPORT_ROOT = Path(os.environ["MORAY_SKILL_EXTRACTED"])
else:
    IMPORT_ROOT = HERE
IMPORT_ROOT = IMPORT_ROOT.resolve()
if not (IMPORT_ROOT / "index.py").is_file():
    raise SystemExit(f"no index.py under import root {IMPORT_ROOT}")
sys.path.insert(0, str(IMPORT_ROOT))

import index  # noqa: E402  (path setup must run first)

FILES = {
    "app/main.py": (
        "def main():\n"
        "    helper()\n"
        "\n"
        "def helper():\n"
        "    return 42\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    ),
    "app/utils/legacy.py": (
        "def never_called(x):\n"
        "    return x + 1\n"
        "\n"
        "class OldThing:\n"
        "    def do(self):\n"
        "        pass\n"
    ),
    "app/models.py": ("class User:\n    def save(self):\n        return 1\n"),
    "broken.py": "def (((",  # syntax error -> recorded in skipped, not fatal
}


def run_suite() -> None:
    loaded = Path(index.__file__).resolve().parent
    assert loaded == IMPORT_ROOT.resolve(), (
        f"index.py resolved to {loaded}, expected {IMPORT_ROOT.resolve()} "
        "(a site-packages copy may be shadowing the package under test)"
    )

    result = index.handler({"files": FILES})
    print(json.dumps(result, indent=2, ensure_ascii=False))

    assert result["status"] == "success", result
    summary = result["summary"]
    assert summary["total_files_parsed"] == 3, summary
    assert summary["total_files_skipped"] == 1, summary
    assert summary["total_symbols"] >= 5, summary
    assert isinstance(summary["total_edges"], int) and summary["total_edges"] >= 1, summary

    hubs = summary["core_hubs"]
    assert 1 <= len(hubs) <= 10, hubs
    assert all(set(h) == {"id", "score"} for h in hubs), hubs
    scores = [h["score"] for h in hubs]
    assert scores == sorted(scores, reverse=True), scores

    dead = summary["dead_code_candidates"]
    assert "app.utils.legacy.never_called" in dead, dead
    entries = summary["entry_points"]
    assert "app.main.main" in entries, entries

    skipped = result["skipped"]
    assert len(skipped) == 1 and skipped[0]["path"] == "broken.py", skipped
    assert "error" in skipped[0]

    # Everything must survive a JSON round-trip (the platform only sees JSON).
    json.loads(json.dumps(result))

    # top_hubs is honoured and clamped.
    few = index.handler({"files": FILES, "top_hubs": 2})
    assert len(few["summary"]["core_hubs"]) == 2, few
    huge = index.handler({"files": FILES, "top_hubs": 10_000})
    assert len(huge["summary"]["core_hubs"]) <= 50, huge

    # Error paths stay JSON-serializable dicts.
    for bad in ({}, {"files": {}}, {"files": None}, "not json", 42):
        err = index.handler(bad)
        assert isinstance(err, dict) and "error" in err, (bad, err)
        json.loads(json.dumps(err))

    # String-encoded event (some platforms deliver the payload as JSON text).
    as_text = index.handler(json.dumps({"files": FILES}))
    assert as_text["status"] == "success", as_text

    # A key with no derivable module name is skipped, not fatal.
    odd = index.handler({"files": {"__init__.py": "x = 1", "README.md": "hi"}})
    assert odd["status"] == "error" and len(odd["skipped"]) == 2, odd

    print("in-process suite: OK")


def run_from_zip() -> None:
    zip_path = HERE / "moray-scribe-skill.zip"
    if not zip_path.is_file():
        raise SystemExit(f"{zip_path.name} not found — run build_zip.py first")
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp).resolve()
        with zipfile.ZipFile(zip_path) as zf:
            # Zip-slip guard: every member must stay inside the extract root.
            for member in zf.infolist():
                target = (dest / member.filename).resolve()
                if not target.is_relative_to(dest):
                    raise SystemExit(f"unsafe path in zip: {member.filename}")
            zf.extractall(dest)
        env = dict(os.environ)
        env.pop("MORAY_SKILL_EXTRACTED", None)  # the subprocess sets its own
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve())],
            env={**env, "MORAY_SKILL_EXTRACTED": tmp},
            capture_output=True,
            text=True,
            cwd=tmp,
        )
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        if proc.returncode != 0:
            raise SystemExit("zip self-test FAILED")
        print("zip self-test: OK (ran against extracted moray-scribe-skill.zip)")


if __name__ == "__main__":
    if "--zip" in sys.argv:
        run_from_zip()
    else:
        run_suite()
