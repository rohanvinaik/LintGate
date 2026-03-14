"""Tests for lintgate.channels._test_channel_selection — test selection and helpers."""

from __future__ import annotations

import os
from pathlib import Path

from lintgate.channels._test_channel_selection import (
    _check_missing_tests,
    _discover_fallback_test_targets,
    _has_test,
    _is_source_file,
    _no_test_files_exist,
    _select_tests_to_run,
)
from lintgate.types import LintIssue


# ── _is_source_file ──────────────────────────────────────────────────


class TestIsSourceFile:
    """Tests for Python source file identification."""

    def test_regular_py_file(self) -> None:
        assert _is_source_file("/project/mymodule.py", "/project") is True

    def test_test_file_excluded(self) -> None:
        assert _is_source_file("/project/test_foo.py", "/project") is False

    def test_conftest_excluded(self) -> None:
        assert _is_source_file("/project/conftest.py", "/project") is False

    def test_setup_excluded(self) -> None:
        assert _is_source_file("/project/setup.py", "/project") is False

    def test_dunder_file_excluded(self) -> None:
        assert _is_source_file("/project/__init__.py", "/project") is False
        assert _is_source_file("/project/__main__.py", "/project") is False

    def test_non_py_excluded(self) -> None:
        assert _is_source_file("/project/readme.md", "/project") is False


# ── _no_test_files_exist ─────────────────────────────────────────────


class TestNoTestFilesExist:
    """Tests for detecting projects with zero test files."""

    def test_empty_dir_has_no_tests(self, tmp_path) -> None:
        assert _no_test_files_exist(str(tmp_path)) is True

    def test_dir_with_tests_dir(self, tmp_path) -> None:
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_example.py").write_text("def test_x(): pass\n")
        assert _no_test_files_exist(str(tmp_path)) is False

    def test_dir_with_root_test_file(self, tmp_path) -> None:
        (tmp_path / "test_something.py").write_text("def test_y(): pass\n")
        assert _no_test_files_exist(str(tmp_path)) is False


# ── _discover_fallback_test_targets ──────────────────────────────────


class TestDiscoverFallbackTestTargets:
    """Tests for broad fallback test target discovery."""

    def test_finds_tests_dir(self, tmp_path) -> None:
        (tmp_path / "tests").mkdir()
        targets = _discover_fallback_test_targets(str(tmp_path))
        assert len(targets) == 1
        assert targets[0] == str(tmp_path / "tests")

    def test_finds_test_dir(self, tmp_path) -> None:
        (tmp_path / "test").mkdir()
        targets = _discover_fallback_test_targets(str(tmp_path))
        assert len(targets) == 1
        assert targets[0] == str(tmp_path / "test")

    def test_finds_root_test_files(self, tmp_path) -> None:
        (tmp_path / "test_a.py").write_text("")
        (tmp_path / "test_b.py").write_text("")
        targets = _discover_fallback_test_targets(str(tmp_path))
        assert len(targets) == 2

    def test_empty_dir_returns_empty(self, tmp_path) -> None:
        targets = _discover_fallback_test_targets(str(tmp_path))
        assert targets == []


# ── _select_tests_to_run ─────────────────────────────────────────────


class TestSelectTestsToRun:
    """Tests for test target selection logic."""

    def test_returns_impacted_when_available(self) -> None:
        findings: list[LintIssue] = []
        result = _select_tests_to_run(
            impacted_tests=["test_a.py"],
            project_root="/tmp",
            cov_cfg=None,
            surface="hook",
            findings=findings,
        )
        assert result == ["test_a.py"]
        assert findings == []

    def test_returns_empty_without_symbol_enabled(self) -> None:
        findings: list[LintIssue] = []
        result = _select_tests_to_run(
            impacted_tests=[],
            project_root="/tmp",
            cov_cfg={"symbol_enabled": False},
            surface="mcp",
            findings=findings,
        )
        assert result == []

    def test_returns_empty_when_cov_cfg_none(self) -> None:
        findings: list[LintIssue] = []
        result = _select_tests_to_run(
            impacted_tests=[],
            project_root="/tmp",
            cov_cfg=None,
            surface="mcp",
            findings=findings,
        )
        assert result == []

    def test_fallback_targets_for_symbol_gate(self, tmp_path) -> None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("def test_z(): pass\n")
        findings: list[LintIssue] = []
        result = _select_tests_to_run(
            impacted_tests=[],
            project_root=str(tmp_path),
            cov_cfg={"symbol_enabled": True},
            surface="ci",
            findings=findings,
        )
        assert len(result) == 1
        assert str(tmp_path / "tests") in result[0]
        assert len(findings) == 1
        assert findings[0].kind == "symbol_gate_fallback"


# ── _check_missing_tests ────────────────────────────────────────────


class TestCheckMissingTests:
    """Tests for missing test file detection."""

    def test_non_source_file_skipped(self) -> None:
        findings: list[LintIssue] = []
        repairs: list = []
        _check_missing_tests(
            ["/project/test_foo.py"],
            "/project",
            findings,
            repairs,
        )
        assert findings == []

    def test_non_py_file_skipped(self) -> None:
        findings: list[LintIssue] = []
        repairs: list = []
        _check_missing_tests(
            ["/project/readme.md"],
            "/project",
            findings,
            repairs,
        )
        assert findings == []
