"""Tests for lintgate/linters/custom_linter.py — custom linter runner."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from lintgate.linters.custom_linter import CustomLinter

# --- __init__ ---


def test_custom_linter_init_defaults():
    linter = CustomLinter(linter_name="my_tool", command="my_tool --check")
    assert linter.name == "my_tool"
    assert linter.tier == 3
    assert linter.timeout_ms == 15000
    assert linter._severity_default == "warning"
    assert linter._parse_mode == "lines"


def test_custom_linter_init_custom_values():
    linter = CustomLinter(
        linter_name="checker",
        command="checker -j",
        tier=1,
        severity_default="blocking",
        parse_mode="jsonl",
        timeout_ms=5000,
    )
    assert linter.name == "checker"
    assert linter.tier == 1
    assert linter.timeout_ms == 5000
    assert linter._severity_default == "blocking"
    assert linter._parse_mode == "jsonl"


# --- available ---


def test_available_python_always_true():
    linter = CustomLinter(linter_name="test", command="python -m mymodule")
    assert linter.available() is True


def test_available_python3_always_true():
    linter = CustomLinter(linter_name="test", command="python3 script.py")
    assert linter.available() is True


def test_available_missing_tool():
    linter = CustomLinter(linter_name="test", command="nonexistent_tool_xyzzy --flag")
    assert linter.available() is False


def test_available_invalid_command():
    linter = CustomLinter(linter_name="test", command="")
    assert linter.available() is False


# --- _parse_jsonl ---


def test_parse_jsonl_valid():
    linter = CustomLinter(linter_name="my_lint", command="cmd")
    line1 = json.dumps({"file": "a.py", "line": 10, "message": "bad code", "kind": "E001"})
    line2 = json.dumps({"file": "b.py", "line": 20, "message": "worse code"})
    output = f"{line1}\n{line2}\n"

    issues = list(linter._parse_jsonl(output))
    assert len(issues) == 2
    assert issues[0].linter == "my_lint"
    assert issues[0].kind == "E001"
    assert issues[0].message == "bad code"
    assert issues[0].file == "a.py"
    assert issues[0].line == 10
    assert issues[0].confidence == 0.8

    assert issues[1].kind == "custom"  # default when not specified
    assert issues[1].file == "b.py"


def test_parse_jsonl_skips_invalid_json():
    linter = CustomLinter(linter_name="test", command="cmd")
    output = "not json\n" + json.dumps({"message": "valid"}) + "\n"
    issues = list(linter._parse_jsonl(output))
    assert len(issues) == 1
    assert issues[0].message == "valid"


def test_parse_jsonl_skips_empty_lines():
    linter = CustomLinter(linter_name="test", command="cmd")
    output = "\n  \n" + json.dumps({"message": "ok"}) + "\n\n"
    issues = list(linter._parse_jsonl(output))
    assert len(issues) == 1


def test_parse_jsonl_uses_severity_default():
    linter = CustomLinter(linter_name="test", command="cmd", severity_default="blocking")
    output = json.dumps({"message": "issue"}) + "\n"
    issues = list(linter._parse_jsonl(output))
    assert issues[0].severity == "blocking"


# --- _parse_lines ---


def test_parse_lines_basic():
    linter = CustomLinter(linter_name="plain", command="cmd")
    output = "Error: missing import\nWarning: unused var\n"
    issues = list(linter._parse_lines(output))
    assert len(issues) == 2
    assert issues[0].linter == "plain"
    assert issues[0].kind == "custom"
    assert issues[0].message == "Error: missing import"
    assert issues[0].confidence == 0.7
    assert issues[1].message == "Warning: unused var"


def test_parse_lines_skips_empty():
    linter = CustomLinter(linter_name="test", command="cmd")
    output = "\n  \nactual line\n\n"
    issues = list(linter._parse_lines(output))
    assert len(issues) == 1
    assert issues[0].message == "actual line"


def test_parse_lines_severity_from_config():
    linter = CustomLinter(linter_name="test", command="cmd", severity_default="informational")
    output = "some finding\n"
    issues = list(linter._parse_lines(output))
    assert issues[0].severity == "informational"


# --- _filter_files ---


def test_filter_files_returns_all():
    linter = CustomLinter(linter_name="test", command="cmd")
    files = ["a.py", "b.js", "c.rs"]
    assert linter._filter_files(files) == files


# --- run ---


def test_run_empty_output():
    linter = CustomLinter(linter_name="test", command="echo ''", parse_mode="lines")
    mock_result = MagicMock()
    mock_result.stdout = ""
    with patch.object(linter, "run_command", return_value=mock_result):
        ctx = MagicMock()
        ctx.project_root = "/tmp"
        issues = list(linter.run(ctx))
    assert issues == []


def test_run_jsonl_mode():
    linter = CustomLinter(linter_name="jlint", command="jlint .", parse_mode="jsonl")
    mock_result = MagicMock()
    mock_result.stdout = json.dumps({"message": "found issue", "kind": "J001"}) + "\n"
    with patch.object(linter, "run_command", return_value=mock_result):
        ctx = MagicMock()
        ctx.project_root = "/tmp"
        issues = list(linter.run(ctx))
    assert len(issues) == 1
    assert issues[0].kind == "J001"


def test_run_lines_mode():
    linter = CustomLinter(linter_name="txt", command="txt .", parse_mode="lines")
    mock_result = MagicMock()
    mock_result.stdout = "line finding 1\nline finding 2\n"
    with patch.object(linter, "run_command", return_value=mock_result):
        ctx = MagicMock()
        ctx.project_root = "/tmp"
        issues = list(linter.run(ctx))
    assert len(issues) == 2
    assert issues[0].message == "line finding 1"
