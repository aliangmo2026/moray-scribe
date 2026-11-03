"""Build ``moray-scribe-skill.zip`` for the Work Buddy serverless platform.

Stages the static-analysis-only subset of ``src/moray_scribe`` (schema,
scanner, grapher, utils + the two support modules they need) into
``serverless_skill/moray_scribe/`` and zips it together with ``index.py``,
``requirements.txt`` and ``README.md``. The describer, renderer, mcp and CLI
layers are never copied — this package must not ship any Ollama-facing code.

Usage:  python serverless_skill/build_zip.py
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "src" / "moray_scribe"
STAGE = HERE / "moray_scribe"
ZIP_PATH = HERE / "moray-scribe-skill.zip"
MAX_BYTES = 3 * 1024 * 1024  # Work Buddy upload limit: 3 MiB

# Core static-analysis packages — nothing else may be staged.
PACKAGES = ("schema", "scanner", "grapher", "utils")
# Top-level support modules the copied packages import.
MODULES = ("__init__.py", "exceptions.py", "config.py")
# Layers that must never reach the cloud package (Ollama-facing or heavier).
BANNED = ("describer", "renderer", "mcp", "cli.py", "pipeline.py", "__main__.py", "logging.py")

ZIP_TOP_FILES = ("index.py", "requirements.txt", "README.md", "SKILL.md")


def _fail(message: str) -> None:
    raise SystemExit(f"build failed: {message}")


def stage() -> None:
    if not SRC.is_dir():
        _fail(f"missing source tree {SRC}")
    for name in ZIP_TOP_FILES:
        if not (HERE / name).is_file():
            _fail(f"missing {HERE / name}")
    for banned in BANNED:
        if (STAGE / banned).exists():
            _fail(f"staging area still contains banned path {banned}")

    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    for pkg in PACKAGES:
        src_pkg = SRC / pkg
        if not src_pkg.is_dir():
            _fail(f"missing package {src_pkg}")
        shutil.copytree(
            src_pkg,
            STAGE / pkg,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
    for mod in MODULES:
        shutil.copy2(SRC / mod, STAGE / mod)

    for banned in BANNED:
        if (STAGE / banned).exists():
            _fail(f"banned path leaked into staged package: {banned}")


def package() -> int:
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in ZIP_TOP_FILES:
            zf.write(HERE / name, name)
        for path in sorted(STAGE.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
                continue
            zf.write(path, path.relative_to(HERE).as_posix())
    size = ZIP_PATH.stat().st_size
    if size > MAX_BYTES:
        _fail(f"{ZIP_PATH.name} is {size:,} bytes, exceeds the {MAX_BYTES:,}-byte limit")
    return size


def main() -> None:
    stage()
    size = package()
    print(f"staged packages: {', '.join(PACKAGES)} (+ {', '.join(MODULES)})")
    print(f"wrote {ZIP_PATH.name}: {size:,} bytes ({size / 1024:.1f} KiB) — under 3 MiB")
    print(f"next: python {Path(__file__).name.replace('build_zip.py', 'test_handler.py')} --zip")


if __name__ == "__main__":
    sys.exit(main())
