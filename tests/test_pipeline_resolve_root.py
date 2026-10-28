"""Tests for the pipeline's scan-root resolution helper."""

from __future__ import annotations

from pathlib import Path

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
