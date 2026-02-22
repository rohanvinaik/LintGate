"""Tests for ruff_linter."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from lintgate.linters.ruff_linter import (
    RuffFormatLinter,
    RuffLinter,
    _classify_severity,
)
from lintgate.types import LinterContext


def _make_ctx(tmp_path, files=None, config=None, strictness="normal"):
    return LinterContext(
        files=files or [],
        project_root=str(tmp_path),
        strictness=strictness,
        config=config or {},
    )


# ── _classify_severity ───────────────────────────────────────────────


def test_blocking_code():
    assert _classify_severity("F821", "normal") == "blocking"
    assert _classify_severity("E999", "normal") == "blocking"


def test_informational_code():
    assert _classify_severity("E501", "normal") == "informational"
    assert _classify_severity("D100", "normal") == "informational"


def test_warning_default():
    assert _classify_severity("F401", "normal") == "warning"


def test_strict_mode_unused():
    assert _classify_severity("F401", "strict") == "warning"
    assert _classify_severity("F811", "strict") == "blocking"


# ── RuffLinter ───────────────────────────────────────────────────────


def test_ruff_basic(tmp_path):
    ctx = _make_ctx(tmp_path, files=["a.py"])
    linter = RuffLinter()
    data = json.dumps([{
        "code": "F401",
        "message": "os imported but unused",
        "filename": "a.py",
        "location": {"row": 1, "column": 1},
        "end_location": {"row": 1, "column": 10},
        "fix": {"message": "Remove unused import"},
    }])
    mock_result = MagicMock(stdout=data)
    with patch.object(linter, "run_command", return_value=mock_result):
        issues = list(linter.run(ctx))
    assert len(issues) == 1
    assert issues[0].kind == "F401"
    assert issues[0].fixable is True
    assert issues[0].fix_description == "Remove unused import"


def test_ruff_no_fix(tmp_path):
    ctx = _make_ctx(tmp_path, files=["a.py"])
    linter = RuffLinter()
    data = json.dumps([{
        "code": "E501",
        "message": "Line too long",
        "filename": "a.py",
        "location": {"row": 1, "column": 1},
        "end_location": {"row": 1, "column": 100},
        "fix": None,
    }])
    mock_result = MagicMock(stdout=data)
    with patch.object(linter, "run_command", return_value=mock_result):
        issues = list(linter.run(ctx))
    assert len(issues) == 1
    assert issues[0].fixable is False
    assert issues[0].fix_description is None


def test_ruff_no_output(tmp_path):
    ctx = _make_ctx(tmp_path)
    linter = RuffLinter()
    mock_result = MagicMock(stdout="")
    with patch.object(linter, "run_command", return_value=mock_result):
        issues = list(linter.run(ctx))
    assert issues == []


def test_ruff_bad_json(tmp_path):
    ctx = _make_ctx(tmp_path, files=["a.py"])
    linter = RuffLinter()
    mock_result = MagicMock(stdout="not json")
    with patch.object(linter, "run_command", return_value=mock_result):
        issues = list(linter.run(ctx))
    assert issues == []


def test_ruff_extra_args(tmp_path):
    ctx = _make_ctx(tmp_path, files=["a.py"], config={"extra_args": ["--select", "F"]})
    linter = RuffLinter()
    mock_result = MagicMock(stdout="[]")
    with patch.object(linter, "run_command", return_value=mock_result) as mock_cmd:
        list(linter.run(ctx))
    cmd = mock_cmd.call_args[0][0]
    assert "--select" in cmd


# ── RuffFormatLinter ─────────────────────────────────────────────────


def test_format_needs_formatting(tmp_path):
    ctx = _make_ctx(tmp_path, files=["a.py"])
    linter = RuffFormatLinter()
    diff = "+++ a.py\t2024-01-01\n-old\n+new\n"
    mock_result = MagicMock(returncode=1, stdout=diff)
    with patch.object(linter, "run_command", return_value=mock_result):
        issues = list(linter.run(ctx))
    assert len(issues) == 1
    assert issues[0].kind == "format"
    assert issues[0].fixable is True


def test_format_clean(tmp_path):
    ctx = _make_ctx(tmp_path, files=["a.py"])
    linter = RuffFormatLinter()
    mock_result = MagicMock(returncode=0, stdout="")
    with patch.object(linter, "run_command", return_value=mock_result):
        issues = list(linter.run(ctx))
    assert issues == []


def test_format_dedup_files(tmp_path):
    ctx = _make_ctx(tmp_path, files=["a.py"])
    linter = RuffFormatLinter()
    diff = "+++ a.py\t2024\n-x\n+y\n+++ a.py\t2024\n-a\n+b\n"
    mock_result = MagicMock(returncode=1, stdout=diff)
    with patch.object(linter, "run_command", return_value=mock_result):
        issues = list(linter.run(ctx))
    assert len(issues) == 1


def test_format_skips_dev_null(tmp_path):
    ctx = _make_ctx(tmp_path, files=["a.py"])
    linter = RuffFormatLinter()
    diff = "+++ /dev/null\n"
    mock_result = MagicMock(returncode=1, stdout=diff)
    with patch.object(linter, "run_command", return_value=mock_result):
        issues = list(linter.run(ctx))
    assert issues == []
