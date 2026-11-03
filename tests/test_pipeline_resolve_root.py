"""Tests for the pipeline's scan-root resolution helper."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from moray_scribe.exceptions import ScribeError
from moray_scribe.pipeline import resolve_scan_root


def test_resolve_scan_root_package(sample_project_path: Path) -> None:
    assert resolve_scan_root(sample_project_path) == sample_project_path.parent


def test_resolve_scan_root_directory(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    assert resolve_scan_root(plain) == plain


def test_resolve_scan_root_preserves_sibling_names(sample_project_path: Path) -> None:
    resolved = resolve_scan_root(sample_project_path)

    assert resolved.name == "fixtures"
    assert (resolved / "sample_project" / "__init__.py").is_file()


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX root layout")
def test_resolve_scan_root_refuses_filesystem_root(tmp_path: Path) -> None:
    """A package mounted at `/` (container idiom) must not scan the volume."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(ScribeError, match="filesystem root"):
        resolve_scan_root(Path("/"))
