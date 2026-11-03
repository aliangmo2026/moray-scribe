"""Contract tests for .github/workflows/arch-check.yml.

The YAML is a shipped artifact of this repo; these tests keep it honest: if
someone renames a flag the workflow depends on, or drops the PR-comment
permission, CI breaks silently — here it breaks loudly in pytest instead.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "arch-check.yml"


def _load() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_workflow_file_exists() -> None:
    assert WORKFLOW.is_file()


def test_triggers_on_pull_request() -> None:
    data = _load()
    # PyYAML parses the bare key `on:` as boolean True.
    triggers = data.get("on", data.get(True))
    assert triggers is not None, "missing `on:` trigger block"
    assert "pull_request" in triggers
    assert "workflow_dispatch" in triggers


def test_permissions_allow_pr_comments_only() -> None:
    data = _load()
    permissions = data["permissions"]
    assert permissions["contents"] == "read"
    assert permissions["pull-requests"] == "write"


def test_checkout_fetches_full_history() -> None:
    data = _load()
    steps = data["jobs"]["architecture"]["steps"]
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0


def test_pipeline_commands_reference_real_cli_flags() -> None:
    data = _load()
    runs = [
        step.get("run", "")
        for step in data["jobs"]["architecture"]["steps"]
        if "run" in step
    ]
    joined = "\n".join(runs)
    # Install from PyPI, not from the checked-out repo (the target project is
    # arbitrary; moray-scribe is the tool).
    install_lines = [line for line in runs if "pip install" in line]
    assert install_lines and all("moray-scribe" in line for line in install_lines)
    assert "moray-scribe graph" in joined
    assert "moray-scribe check" in joined
    for flag in ("--baseline", "--current", "--report", "--fail-on"):
        assert flag in joined


def test_comment_step_runs_even_when_check_fails() -> None:
    data = _load()
    steps = data["jobs"]["architecture"]["steps"]
    by_name = {step.get("name", ""): step for step in steps}
    comment = next(
        step for name, step in by_name.items() if "Comment" in name or "评论" in name
    )
    assert comment.get("if") == "always()"
    assert "gh pr comment" in comment.get("run", "")
    assert comment.get("env", {}).get("GH_TOKEN") == "${{ github.token }}"


def test_final_verdict_step_propagates_exit_code() -> None:
    data = _load()
    steps = data["jobs"]["architecture"]["steps"]
    verdicts = [
        step
        for step in steps
        if "exit $(cat" in step.get("run", "") or "exit \"$(cat" in step.get("run", "")
    ]
    assert verdicts, "missing the step that re-applies the check exit code"
    assert verdicts[-1].get("if") == "always()"


def test_report_is_uploaded_as_artifact() -> None:
    data = _load()
    steps = data["jobs"]["architecture"]["steps"]
    uploads = [step for step in steps if step.get("uses", "").startswith("actions/upload-artifact")]
    assert uploads
    joined = "\n".join(str(step.get("with", {}).get("path", "")) for step in uploads)
    assert "report.md" in joined
