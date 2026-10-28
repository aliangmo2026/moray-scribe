"""Unit tests for scanner file discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from moray_scribe.exceptions import ScanError
from moray_scribe.scanner import iter_python_files

NEW_FIXTURES = (
    "async_ops.py",
    "calls.py",
    "decorators.py",
    "imports_mess.py",
    "inheritance.py",
    "nested.py",
    "params.py",
)


def test_walker_finds_all_sample_project_files(sample_project_path: Path) -> None:
    files = list(iter_python_files(sample_project_path))

    # core.py and service.py predate phase-2; __init__.py is intentionally
    # scanned too, so the inventory is 10 files (9 modules + package marker).
    assert len(files) == 10
    assert {path.name for path in files} == {
        "__init__.py",
        "async_ops.py",
        "calls.py",
        "core.py",
        "decorators.py",
        "imports_mess.py",
        "inheritance.py",
        "nested.py",
        "params.py",
        "service.py",
    }
    assert files == sorted(files, key=lambda path: str(path))
    assert all(path.is_absolute() for path in files)


def test_walker_sees_every_new_fixture(sample_project_path: Path) -> None:
    names = {path.name for path in iter_python_files(sample_project_path)}
    new_names = names & set(NEW_FIXTURES)

    assert len(NEW_FIXTURES) == 7
    assert new_names == set(NEW_FIXTURES)


def test_include_and_exclude_filter_relative_paths(sample_project_path: Path) -> None:
    only_core = list(
        iter_python_files(
            sample_project_path,
            include=["core.py"],
            exclude=["**/test_*.py"],
        )
    )
    assert [path.name for path in only_core] == ["core.py"]

    filtered = list(
        iter_python_files(
            sample_project_path,
            include=["**/*.py"],
            exclude=["calls.py", "**/params.py"],
        )
    )
    names = {path.name for path in filtered}
    assert "calls.py" not in names
    assert "params.py" not in names
    assert "core.py" in names


def test_oversized_file_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "small.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "big.py").write_text("value = 1\n" * 500, encoding="utf-8")

    files = list(iter_python_files(tmp_path, include=["**/*.py"], exclude=[], max_size_kb=1))

    assert [path.name for path in files] == ["small.py"]


def test_default_globs_come_from_settings(
    monkeypatch: pytest.MonkeyPatch,
    sample_project_path: Path,
) -> None:
    monkeypatch.setenv("MORAY_SCRIBE_INCLUDE", '["core.py"]')

    files = list(iter_python_files(sample_project_path))

    assert [path.name for path in files] == ["core.py"]


def test_single_file_path_is_returned(sample_project_path: Path) -> None:
    target = sample_project_path / "core.py"

    files = list(iter_python_files(target))

    assert files == [target.resolve()]


def test_single_non_python_file_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("not python\n", encoding="utf-8")

    with pytest.raises(ScanError, match="expected a Python file"):
        list(iter_python_files(target))


def test_noise_directories_are_pruned(tmp_path: Path) -> None:
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "hidden.py").write_text("x = 1\n", encoding="utf-8")
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "visible.py").write_text("y = 2\n", encoding="utf-8")

    files = list(iter_python_files(tmp_path, include=["**/*.py"], exclude=[]))

    assert [path.name for path in files] == ["visible.py"]


def test_missing_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ScanError, match="does not exist"):
        list(iter_python_files(tmp_path / "missing"))
