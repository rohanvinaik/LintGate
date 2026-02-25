"""Tests for compass-aware Claude Code hooks."""

from __future__ import annotations

from types import SimpleNamespace

from lintgate.hooks.pre_compact import handle as pre_compact_handle
from lintgate.hooks.pre_tool import handle as pre_tool_handle
from lintgate.hooks.session_end import handle as session_end_handle
from lintgate.hooks.session_start import handle as session_start_handle
from lintgate.hooks.stop_gate import handle as stop_gate_handle
from lintgate.hooks.user_prompt import handle as user_prompt_handle

# ── All hooks return valid dict with continue: True on empty input ──


def test_session_start_empty_input() -> None:
    result = session_start_handle({})
    assert result["continue"] is True
    assert isinstance(result.get("systemMessage", ""), str)


def test_session_end_empty_input() -> None:
    result = session_end_handle({})
    assert result["continue"] is True
    assert isinstance(result.get("systemMessage", ""), str)


def test_pre_tool_empty_input() -> None:
    result = pre_tool_handle({})
    assert result["continue"] is True
    assert isinstance(result.get("systemMessage", ""), str)


def test_pre_compact_empty_input() -> None:
    result = pre_compact_handle({})
    assert result["continue"] is True
    assert isinstance(result.get("systemMessage", ""), str)


def test_stop_gate_empty_input() -> None:
    result = stop_gate_handle({})
    assert result["continue"] is True
    assert isinstance(result.get("systemMessage", ""), str)


def test_user_prompt_empty_input() -> None:
    result = user_prompt_handle({})
    assert result["continue"] is True
    assert isinstance(result.get("systemMessage", ""), str)


# ── session_start with no compass ───────────────────────────────────


def test_session_start_no_compass_returns_advisory(tmp_path: object) -> None:
    """When no compass file exists, session_start should return an advisory message."""
    result = session_start_handle({"cwd": str(tmp_path)})
    assert result["continue"] is True
    msg = result.get("systemMessage", "")
    assert "No compass found" in msg or msg == ""


# ── user_prompt detects theory keywords ─────────────────────────────


def test_user_prompt_theory_keyword_detection() -> None:
    result = user_prompt_handle({"userMessage": "Let me explore the architecture"})
    assert result["continue"] is True
    msg = result.get("systemMessage", "")
    assert "theory-relevant" in msg


def test_user_prompt_no_theory_keyword() -> None:
    result = user_prompt_handle({"userMessage": "Fix the typo in line 42"})
    assert result["continue"] is True
    # No theory-relevant hint, so systemMessage should be empty
    assert result.get("systemMessage", "") == ""


def test_user_prompt_reports_non_normal_mode_without_keyword(monkeypatch) -> None:
    monkeypatch.setattr(
        "lintgate.controlplane.session_memory.get_or_create_session",
        lambda _root: SimpleNamespace(behavior_compass={"mode_state": {"current": "habit"}}),
    )
    result = user_prompt_handle({"cwd": "/tmp", "userMessage": "Fix typo"})
    assert result["continue"] is True
    assert "Mode: habit" in result.get("systemMessage", "")


def test_user_prompt_dict_message_format() -> None:
    """user_prompt should handle dict-format messages too."""
    result = user_prompt_handle({"userMessage": {"content": "understand the theory"}})
    assert result["continue"] is True
    msg = result.get("systemMessage", "")
    assert "theory-relevant" in msg


# ── pre_tool in theory mode ─────────────────────────────────────────


def test_pre_tool_write_tool_no_compass(tmp_path: object) -> None:
    """Without a compass, pre_tool should pass through even for Write."""
    result = pre_tool_handle({"tool_name": "Write", "cwd": str(tmp_path)})
    assert result["continue"] is True


def test_pre_tool_bash_no_compass(tmp_path: object) -> None:
    """Without a compass, pre_tool should pass through for Bash."""
    result = pre_tool_handle(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "cwd": str(tmp_path),
        }
    )
    assert result["continue"] is True


def test_pre_tool_theory_mode_warns_on_write_without_compass(monkeypatch) -> None:
    monkeypatch.setattr(
        "lintgate.controlplane.session_memory.get_or_create_session",
        lambda _root: SimpleNamespace(behavior_compass={"mode_state": {"current": "theory"}}),
    )
    monkeypatch.setattr("lintgate.compass_io.load_compass", lambda _root: None)
    result = pre_tool_handle({"tool_name": "Write", "cwd": "/tmp"})
    assert result["continue"] is True
    assert "Theory mode active" in result.get("systemMessage", "")


# ── stop_gate with no compass ───────────────────────────────────────


def test_stop_gate_no_compass(tmp_path: object) -> None:
    result = stop_gate_handle({"cwd": str(tmp_path)})
    assert result["continue"] is True


# ── pre_compact with no compass ─────────────────────────────────────


def test_pre_compact_no_compass(tmp_path: object) -> None:
    result = pre_compact_handle({"cwd": str(tmp_path)})
    assert result["continue"] is True
    # No hookSpecificOutput when there is no compass
    assert "hookSpecificOutput" not in result


# ── session_end with no compass ─────────────────────────────────────


def test_session_end_no_compass(tmp_path: object) -> None:
    result = session_end_handle({"cwd": str(tmp_path)})
    assert result["continue"] is True
