"""Tests for bandit_linter."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from lintgate.linters.bandit_linter import (
    BanditLinter,
    _is_test_or_docs_context,
)
from lintgate.types import LinterContext


# ── _is_test_or_docs_context ─────────────────────────────────────────


def test_test_dir(tmp_path):
    assert _is_test_or_docs_context(str(tmp_path / "tests" / "test_foo.py"), str(tmp_path))


def test_docs_dir(tmp_path):
    assert _is_test_or_docs_context(str(tmp_path / "docs" / "conf.py"), str(tmp_path))


def test_src_dir_not_test(tmp_path):
    assert not _is_test_or_docs_context(str(tmp_path / "src" / "app.py"), str(tmp_path))


def test_nested_test_dir(tmp_path):
    assert _is_test_or_docs_context(str(tmp_path / "src" / "tests" / "t.py"), str(tmp_path))


# ── BanditLinter ─────────────────────────────────────────────────────


def _make_ctx(tmp_path, files=None, config=None):
    return LinterContext(
        files=files or [],
        project_root=str(tmp_path),
        strictness="normal",
        config=config or {},
    )


def _bandit_json(results):
    return json.dumps({"results": results})


def test_basic_finding(tmp_path):
    ctx = _make_ctx(tmp_path, files=["app.py"])
    linter = BanditLinter()
    data = _bandit_json([{
        "test_id": "B101",
        "test_name": "assert_used",
        "issue_text": "Use of assert detected.",
        "issue_severity": "LOW",
        "issue_confidence": "HIGH",
        "filename": "app.py",
        "line_number": 10,
        "issue_cwe": {"id": 703},
        "more_info": "https://bandit.readthedocs.io",
    }])
    mock_result = MagicMock(stdout=data)
    with patch.object(linter, "run_command", return_value=mock_result):
        issues = list(linter.run(ctx))
    assert len(issues) == 1
    assert issues[0].kind == "B101/assert_used"
    assert issues[0].severity == "informational"
    assert issues[0].confidence == 1.0


def test_high_severity_is_blocking(tmp_path):
    ctx = _make_ctx(tmp_path, files=["app.py"])
    linter = BanditLinter()
    data = _bandit_json([{
        "test_id": "B602",
        "test_name": "subprocess_popen_with_shell_equals_true",
        "issue_text": "subprocess with shell=True",
        "issue_severity": "HIGH",
        "issue_confidence": "MEDIUM",
        "filename": "app.py",
        "line_number": 5,
    }])
    mock_result = MagicMock(stdout=data)
    with patch.object(linter, "run_command", return_value=mock_result):
        issues = list(linter.run(ctx))
    assert len(issues) == 1
    assert issues[0].severity == "blocking"
    assert issues[0].confidence == 0.8


def test_b105_suppressed_in_test_dir(tmp_path):
    ctx = _make_ctx(tmp_path, files=["tests/test_auth.py"])
    linter = BanditLinter()
    data = _bandit_json([{
        "test_id": "B105",
        "test_name": "hardcoded_password_string",
        "issue_text": "Possible hardcoded password",
        "issue_severity": "LOW",
        "issue_confidence": "MEDIUM",
        "filename": str(tmp_path / "tests" / "test_auth.py"),
        "line_number": 3,
    }])
    mock_result = MagicMock(stdout=data)
    with patch.object(linter, "run_command", return_value=mock_result):
        issues = list(linter.run(ctx))
    assert issues == []


def test_b105_not_suppressed_in_src(tmp_path):
    ctx = _make_ctx(tmp_path, files=["src/auth.py"])
    linter = BanditLinter()
    data = _bandit_json([{
        "test_id": "B105",
        "test_name": "hardcoded_password_string",
        "issue_text": "Possible hardcoded password",
        "issue_severity": "LOW",
        "issue_confidence": "MEDIUM",
        "filename": str(tmp_path / "src" / "auth.py"),
        "line_number": 3,
    }])
    mock_result = MagicMock(stdout=data)
    with patch.object(linter, "run_command", return_value=mock_result):
        issues = list(linter.run(ctx))
    assert len(issues) == 1


def test_no_output(tmp_path):
    ctx = _make_ctx(tmp_path)
    linter = BanditLinter()
    mock_result = MagicMock(stdout="")
    with patch.object(linter, "run_command", return_value=mock_result):
        issues = list(linter.run(ctx))
    assert issues == []


def test_bad_json(tmp_path):
    ctx = _make_ctx(tmp_path)
    linter = BanditLinter()
    mock_result = MagicMock(stdout="not json")
    with patch.object(linter, "run_command", return_value=mock_result):
        issues = list(linter.run(ctx))
    assert issues == []


def test_extra_args(tmp_path):
    ctx = _make_ctx(tmp_path, files=["a.py"], config={"extra_args": ["-c", "bandit.yaml"]})
    linter = BanditLinter()
    mock_result = MagicMock(stdout=_bandit_json([]))
    with patch.object(linter, "run_command", return_value=mock_result) as mock_cmd:
        list(linter.run(ctx))
    cmd = mock_cmd.call_args[0][0]
    assert "-c" in cmd
    assert "bandit.yaml" in cmd


def test_medium_severity_is_warning(tmp_path):
    ctx = _make_ctx(tmp_path, files=["a.py"])
    linter = BanditLinter()
    data = _bandit_json([{
        "test_id": "B301",
        "test_name": "pickle",
        "issue_text": "Pickle usage",
        "issue_severity": "MEDIUM",
        "issue_confidence": "LOW",
        "filename": "a.py",
        "line_number": 1,
    }])
    mock_result = MagicMock(stdout=data)
    with patch.object(linter, "run_command", return_value=mock_result):
        issues = list(linter.run(ctx))
    assert issues[0].severity == "warning"
    assert issues[0].confidence == 0.6


def test_no_test_id_uses_test_name(tmp_path):
    ctx = _make_ctx(tmp_path, files=["a.py"])
    linter = BanditLinter()
    data = _bandit_json([{
        "test_id": "",
        "test_name": "custom_check",
        "issue_text": "Something",
        "issue_severity": "LOW",
        "issue_confidence": "LOW",
        "filename": "a.py",
        "line_number": 1,
    }])
    mock_result = MagicMock(stdout=data)
    with patch.object(linter, "run_command", return_value=mock_result):
        issues = list(linter.run(ctx))
    assert issues[0].kind == "custom_check"
