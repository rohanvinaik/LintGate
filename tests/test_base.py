"""Tests for lintgate/linters/base.py."""

from __future__ import annotations

from unittest.mock import patch

from lintgate.linters.base import (
    BaseLinter,
    Linter,
    _find_venv_bin,
    _resolve_executable,
)
from lintgate.types import LinterContext, LinterResult, LintIssue


# ── _find_venv_bin ──────────────────────────────────────────────────────


def test_find_venv_bin_none_root():
    assert _find_venv_bin(None) is None


def test_find_venv_bin_no_venv(tmp_path):
    assert _find_venv_bin(str(tmp_path)) is None


def test_find_venv_bin_dot_venv(tmp_path):
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    result = _find_venv_bin(str(tmp_path))
    assert result == str(bin_dir)


def test_find_venv_bin_prefers_dot_venv(tmp_path):
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    result = _find_venv_bin(str(tmp_path))
    assert ".venv" in result


# ── _resolve_executable ─────────────────────────────────────────────────


def test_resolve_executable_venv(tmp_path):
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    ruff = bin_dir / "ruff"
    ruff.write_text("#!/bin/sh\n")
    result = _resolve_executable("ruff", str(tmp_path))
    assert result == str(ruff)


def test_resolve_executable_system_fallback():
    with patch("lintgate.linters.base.shutil.which", return_value="/usr/bin/python3"):
        result = _resolve_executable("python3", None)
    assert result == "/usr/bin/python3"


def test_resolve_executable_not_found():
    with patch("lintgate.linters.base.shutil.which", return_value=None):
        result = _resolve_executable("nonexistent_tool_xyz", None)
    assert result is None


# ── BaseLinter.available ────────────────────────────────────────────────


def test_baselinter_available_builtin():
    linter = BaseLinter()
    linter.required_tool = None
    assert linter.available() is True


def test_baselinter_available_missing_tool():
    linter = BaseLinter()
    linter.required_tool = "nonexistent_tool_xyz"
    with patch("lintgate.linters.base.shutil.which", return_value=None):
        assert linter.available() is False


# ── BaseLinter._filter_files ────────────────────────────────────────────


def test_baselinter_filter_files_python_only():
    linter = BaseLinter()
    files = ["/a/b.py", "/c/d.js", "/e/f.py", "/g/h.rs"]
    assert linter._filter_files(files) == ["/a/b.py", "/e/f.py"]


def test_baselinter_filter_files_empty():
    linter = BaseLinter()
    assert linter._filter_files([]) == []


# ── BaseLinter.execute ──────────────────────────────────────────────────


def test_baselinter_execute_skips_unavailable():
    linter = BaseLinter()
    linter.name = "test_linter"
    linter.required_tool = "nonexistent_xyz"
    ctx = LinterContext(files=["foo.py"], project_root="/tmp")
    with patch("lintgate.linters.base.shutil.which", return_value=None):
        result = linter.execute(ctx)
    assert result.status == "skipped"
    assert result.linter_name == "test_linter"
    assert "not installed" in result.error


def test_baselinter_execute_skips_no_files():
    linter = BaseLinter()
    linter.name = "test_linter"
    ctx = LinterContext(files=["foo.js"], project_root="/tmp")
    result = linter.execute(ctx)
    assert result.status == "skipped"
    assert "No applicable files" in result.error


def test_baselinter_execute_ok():
    class TestLinter(BaseLinter):
        name = "ok_linter"
        required_tool = None

        def run(self, ctx):
            return [LintIssue(linter="ok_linter", kind="T001", message="test issue")]

    linter = TestLinter()
    ctx = LinterContext(files=["foo.py"], project_root="/tmp", config={"ok_linter": {}})
    result = linter.execute(ctx)
    assert result.status == "ok"
    assert len(result.issues) == 1
    assert result.issues[0].kind == "T001"
    assert result.duration_ms > 0


def test_baselinter_execute_error():
    class ErrorLinter(BaseLinter):
        name = "err_linter"
        required_tool = None

        def run(self, ctx):
            raise ValueError("boom")

    linter = ErrorLinter()
    ctx = LinterContext(files=["foo.py"], project_root="/tmp", config={"err_linter": {}})
    result = linter.execute(ctx)
    assert result.status == "error"
    assert "ValueError: boom" in result.error


# ── Linter Protocol ────────────────────────────────────────────────────


def test_linter_protocol_isinstance():
    """BaseLinter instances satisfy the Linter protocol."""
    linter = BaseLinter()
    linter.name = "x"
    linter.tier = 0
    linter.timeout_ms = 5000
    linter.required_tool = None
    assert isinstance(linter, Linter)
