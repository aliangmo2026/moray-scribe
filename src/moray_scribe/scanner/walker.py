"""Deterministic Python source discovery with include/exclude globs."""

from __future__ import annotations

import os
from collections.abc import Iterator
from fnmatch import fnmatch
from pathlib import Path

from loguru import logger

from moray_scribe.config import Settings
from moray_scribe.exceptions import ScanError

_SKIP_DIRS = frozenset(
    {"__pycache__", ".venv", "venv", ".git", "node_modules", "build", "dist"}
)


def _matches_any(relative: str, patterns: list[str]) -> bool:
    """Whether ``relative`` matches any glob pattern.

    fnmatch semantics against the root-relative posix path. A leading ``**/``
    also matches paths without a directory component, mirroring glob rules
    where ``**/`` may be empty.
    """
    for pattern in patterns:
        if fnmatch(relative, pattern):
            return True
        if pattern.startswith("**/") and fnmatch(relative, pattern[3:]):
            return True
    return False


def iter_python_files(
    root: Path,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    max_size_kb: int = 512,
) -> Iterator[Path]:
    """Yield Python files under ``root`` that match the configured globs.

    ``root`` may be a directory or a single ``.py`` file (which bypasses the
    glob filters but still respects ``max_size_kb``). Patterns use fnmatch
    semantics against root-relative posix paths and default to the
    ``Settings`` values. Skipped: symlinked files and directories (cycle
    protection), files over ``max_size_kb`` (one warning line), and the noise
    directories ``__pycache__``/``.venv``/``venv``/``.git``/``node_modules``/
    ``build``/``dist``. Output paths are absolute and the order is stable
    (depth-first walk with per-directory lexicographic sorting). The walk is
    lazy: validation errors surface on the first iteration.
    """
    include_patterns = include
    exclude_patterns = exclude
    if include_patterns is None or exclude_patterns is None:
        settings = Settings()
        if include_patterns is None:
            include_patterns = settings.include
        if exclude_patterns is None:
            exclude_patterns = settings.exclude
    max_bytes = max_size_kb * 1024

    base = root.resolve()
    if base.is_file():
        if base.suffix != ".py":
            raise ScanError(f"expected a Python file, got: {root}")
        if base.stat().st_size > max_bytes:
            logger.warning("skipping oversized file: {}", base)
            return
        yield base
        return
    if not base.is_dir():
        raise ScanError(f"scan root does not exist: {root}")

    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in _SKIP_DIRS and not (Path(dirpath) / name).is_symlink()
        )
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            candidate = Path(dirpath) / filename
            if candidate.is_symlink():
                continue
            relative = candidate.relative_to(base).as_posix()
            if not _matches_any(relative, include_patterns):
                continue
            if _matches_any(relative, exclude_patterns):
                continue
            try:
                size = candidate.stat().st_size
            except OSError as exc:
                logger.warning("skipping unreadable file {}: {}", candidate, exc)
                continue
            if size > max_bytes:
                logger.warning("skipping oversized file: {}", candidate)
                continue
            yield candidate
