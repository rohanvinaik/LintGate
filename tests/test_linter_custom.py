"""Tests for custom_linter."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from lintgate.linters.custom_linter import CustomLinter
from lintgate.types import LinterContext


def _make_ctx(tmp_path, config=None):
    return LinterContext(
        files=[],
        project_root=str(tmp_path),
        strictness="normal",
        config=config or {},
    )


# ── CustomLinter init ────────────────────────────────────────────────


def test_init_sets_fields():
    c = CustomLinter("my_lint", "python -m my_lint", tier=2, severity_default="blocking")
    assert c.name == "my_lint"
    assert c.tier == 2
    assert c._severity_default == "blocking"
    assert c._parse_mode == "lines"


# ── available ────────────────────────────────────────────────────────


def test_available_python():
    c = CustomLinter("x", "python -m foo")
    assert c.available() is True


def test_available_python3():
    c = CustomLinter("x", "python3 -m foo")
    assert c.available() is True


def test_available_executable_found():
    c = CustomLinter("x", "ruff check .")
    with patch("shutil.which", return_value="/usr/bin/ruff"):
        assert c.available() is True


def test_available_executable_missing():
    c = CustomLinter("x", "nonexistent_tool_xyz check")
    with patch("shutil.which", return_value=None):
        assert c.available() is False


def test_available_empty_command():
    c = CustomLinter("x", "")
    assert c.available() is False


# ── run / parse_lines ────────────────────────────────────────────────


def test_run_lines_mode(tmp_path):
    ctx = _make_ctx(tmp_path)
    c = CustomLinter("my_lint", "my_lint run", parse_mode="lines")
    mock_result = MagicMock(stdout="issue one\nissue two\n")
    with patch.object(c, "run_command", return_value=mock_result):
        issues = list(c.run(ctx))
    assert len(issues) == 2
    assert issues[0].message == "issue one"
    assert issues[0].kind == "custom"
    assert issues[0].confidence == 0.7


def test_run_empty_output(tmp_path):
    ctx = _make_ctx(tmp_path)
    c = CustomLinter("x", "x")
    mock_result = MagicMock(stdout="")
    with patch.object(c, "run_command", return_value=mock_result):
        issues = list(c.run(ctx))
    assert issues == []


def test_run_none_output(tmp_path):
    ctx = _make_ctx(tmp_path)
    c = CustomLinter("x", "x")
    mock_result = MagicMock(stdout=None)
    with patch.object(c, "run_command", return_value=mock_result):
        issues = list(c.run(ctx))
    assert issues == []


def test_parse_lines_skips_blank():
    c = CustomLinter("x", "x", parse_mode="lines")
    issues = list(c._parse_lines("line1\n\nline2\n"))
    assert len(issues) == 2


# ── run / parse_jsonl ────────────────────────────────────────────────


def test_run_jsonl_mode(tmp_path):
    ctx = _make_ctx(tmp_path)
    c = CustomLinter("my_lint", "my_lint run", parse_mode="jsonl")
    output = json.dumps({"kind": "error", "message": "bad thing", "file": "a.py", "line": 5})
    mock_result = MagicMock(stdout=output + "\n")
    with patch.object(c, "run_command", return_value=mock_result):
        issues = list(c.run(ctx))
    assert len(issues) == 1
    assert issues[0].kind == "error"
    assert issues[0].file == "a.py"
    assert issues[0].line == 5


def test_parse_jsonl_bad_json():
    c = CustomLinter("x", "x", parse_mode="jsonl")
    issues = list(c._parse_jsonl("not json\n"))
    assert issues == []


def test_parse_jsonl_defaults():
    c = CustomLinter("x", "x", parse_mode="jsonl", severity_default="warning")
    issues = list(c._parse_jsonl(json.dumps({"message": "hi"}) + "\n"))
    assert len(issues) == 1
    assert issues[0].kind == "custom"
    assert issues[0].severity == "warning"
    assert issues[0].confidence == 0.8


def test_parse_jsonl_skips_blank():
    c = CustomLinter("x", "x", parse_mode="jsonl")
    row = json.dumps({"message": "a"})
    issues = list(c._parse_jsonl(f"{row}\n\n{row}\n"))
    assert len(issues) == 2


def test_parse_jsonl_with_all_fields():
    c = CustomLinter("x", "x", parse_mode="jsonl")
    row = json.dumps(
        {
            "kind": "err",
            "message": "msg",
            "file": "f.py",
            "line": 1,
            "column": 5,
            "severity": "blocking",
            "confidence": 0.95,
            "suggestions": ["fix it"],
        }
    )
    issues = list(c._parse_jsonl(row + "\n"))
    assert issues[0].severity == "blocking"
    assert issues[0].confidence == 0.95
    assert issues[0].suggestions == ["fix it"]
