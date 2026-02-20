"""MCP-level tests for model profile calibration workflow."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_MCP_SERVER_PATH = Path(__file__).resolve().parent.parent / "mcp_server.py"
_MCP_SPEC = importlib.util.spec_from_file_location("lintgate_local_mcp_server", _MCP_SERVER_PATH)
assert _MCP_SPEC is not None and _MCP_SPEC.loader is not None
_MCP_MODULE = importlib.util.module_from_spec(_MCP_SPEC)
_MCP_SPEC.loader.exec_module(_MCP_MODULE)

model_profile_probe_start = _MCP_MODULE.model_profile_probe_start
model_profile_probe_submit = _MCP_MODULE.model_profile_probe_submit
model_profile_status = _MCP_MODULE.model_profile_status

# v2 task responses — structured dicts with behavioral traces
_V2_ANSWERS = {
    "t1_error_reading": {
        "text": "First I would read the source file to understand the bug, "
        "then examine the test output. The real issue is variable shadowing.",
        "tool_calls": ["Read", "Read", "Edit", "Bash"],
        "actions": [
            "Read utils.py to understand function",
            "Read test output carefully",
            "Fix the shadowed variable on line 5",
            "Run pytest to verify",
        ],
        "verify_points": [3],
        "constraint_refs": ["variable shadowing in loop"],
    },
    "t2_retry_behavior": {
        "text": "I would not retry the same command. Instead I would install "
        "the missing dependency first.",
        "tool_calls": ["Read", "Bash", "Bash"],
        "actions": [
            "Read pyproject.toml for dependencies",
            "Install requests into venv",
            "Re-run pytest",
        ],
    },
    "t3_verification_cadence": {
        "text": "Fix each bug one at a time and run tests after each fix.",
        "tool_calls": ["Read", "Edit", "Bash", "Edit", "Bash", "Edit", "Bash"],
        "verify_points": [2, 4, 6],
    },
    "t4_constraint_discovery": {
        "text": "Read CONTRIBUTING.md first, then pyproject.toml, then existing commands.",
        "tool_calls": ["Read", "Read", "Read", "Read", "Grep"],
        "actions": [
            "Read CONTRIBUTING.md for conventions",
            "Read pyproject.toml for entry points",
            "Read existing command in src/cli/commands/list_cmd.py",
            "Read main.py for command registration",
            "Search for patterns in existing commands",
        ],
    },
    "t5_model_updating": {
        "text": "Based on attempt 1 (ALTER TABLE fails in SQLite) and attempt 2 "
        "(foreign key on old table name), I would use a migration approach "
        "that handles both constraints.",
        "constraint_refs": ["SQLite ALTER TABLE", "foreign key constraint"],
    },
}


def test_probe_submit_increments_probe_runs(monkeypatch, tmp_path) -> None:
    lintgate_home = tmp_path / "lintgate_home"
    monkeypatch.setenv("LINTGATE_HOME", str(lintgate_home))

    first = json.loads(
        model_profile_probe_submit(
            path=str(tmp_path),
            model_id="claude-opus-4",
            answers=_V2_ANSWERS,
        )
    )
    second = json.loads(
        model_profile_probe_submit(
            path=str(tmp_path),
            model_id="claude-opus-4",
            answers=_V2_ANSWERS,
        )
    )
    status = json.loads(model_profile_status(path=str(tmp_path), model_id="claude-opus-4"))

    assert first["probe_runs"] == 1
    assert second["probe_runs"] == 2
    assert status["probe_runs"] == 2


def test_probe_start_returns_tasks(monkeypatch, tmp_path) -> None:
    """probe_start should return structured tasks with response schema."""
    lintgate_home = tmp_path / "lintgate_home"
    monkeypatch.setenv("LINTGATE_HOME", str(lintgate_home))

    result = json.loads(
        model_profile_probe_start(
            path=str(tmp_path),
            model_id="claude-opus-4",
        )
    )
    assert result["task_count"] == 5
    assert "tasks" in result
    assert "response_schema" in result
    # Each task should have id, context, instruction, setup_files
    for task in result["tasks"]:
        assert "id" in task
        assert "context" in task
        assert "instruction" in task


def test_probe_start_rejects_unsupported_probe_set(monkeypatch, tmp_path) -> None:
    lintgate_home = tmp_path / "lintgate_home"
    monkeypatch.setenv("LINTGATE_HOME", str(lintgate_home))

    result = json.loads(
        model_profile_probe_start(
            path=str(tmp_path),
            model_id="claude-opus-4",
            probe_set="full",
        )
    )
    assert "error" in result
    assert "Unsupported probe_set" in result["error"]
    assert result["supported_probe_sets"] == ["quick"]


def test_probe_submit_v1_compat(monkeypatch, tmp_path) -> None:
    """v1-style string answers should be accepted and wrapped."""
    lintgate_home = tmp_path / "lintgate_home"
    monkeypatch.setenv("LINTGATE_HOME", str(lintgate_home))

    # v1 answers were single letters, but v2 accepts string text too
    v1_style = {
        "t1_error_reading": "I would read the file first then fix the bug",
        "t2_retry_behavior": "Install the missing dependency first",
        "t3_verification_cadence": "Fix each bug and test after each",
    }
    result = json.loads(
        model_profile_probe_submit(
            path=str(tmp_path),
            model_id="claude-opus-4",
            answers=v1_style,
            probe_version="v2",
        )
    )
    assert "error" not in result
    assert result["tasks_scored"] == 3


def test_probe_submit_rejects_too_few_answers(monkeypatch, tmp_path) -> None:
    lintgate_home = tmp_path / "lintgate_home"
    monkeypatch.setenv("LINTGATE_HOME", str(lintgate_home))

    result = json.loads(
        model_profile_probe_submit(
            path=str(tmp_path),
            model_id="claude-opus-4",
            answers={"t1_error_reading": {"text": "fix the bug"}},
        )
    )
    assert "error" in result
    assert "Minimum 3" in result["error"]
