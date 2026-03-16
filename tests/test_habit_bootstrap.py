"""Tests for habit mode bootstrap — historical session data parsing."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from lintgate._habit_bootstrap import (
    HabitBootstrapper,
    _accumulate_error_memory,
    _collect_actions,
    _quick_intent,
    _tool_call_to_action,
    load_error_bootstrap,
)


def _tc(
    tool_name: str = "Bash",
    input_data: dict | None = None,
    timestamp: float = 1.0,
    exit_code: int | None = None,
    error_text: str = "",
) -> SimpleNamespace:
    """Create a mock ToolCall."""
    return SimpleNamespace(
        tool_name=tool_name,
        input_data=input_data or {},
        timestamp=timestamp,
        exit_code=exit_code,
        error_text=error_text,
    )


def _exchange(tool_calls=None, user_text=""):
    return SimpleNamespace(tool_calls=tool_calls or [], user_text=user_text)


def _session(exchanges=None, project_path="/proj", cwd="/proj", token_usage=None):
    return SimpleNamespace(
        exchanges=exchanges or [],
        project_path=project_path,
        cwd=cwd,
        total_token_usage=token_usage or {"input": 0, "output": 0},
    )


# ── _quick_intent ────────────────────────────────────────────────


class TestQuickIntent:
    def test_inspect_tools(self):
        for tool in ("Read", "Grep", "Glob", "WebFetch", "WebSearch", "LSP"):
            assert _quick_intent(tool, {}) == "inspect"

    def test_modify_tools(self):
        for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            assert _quick_intent(tool, {}) == "modify"

    def test_meta_tools(self):
        for tool in ("Task", "TaskCreate", "TaskUpdate", "TodoWrite", "Agent"):
            assert _quick_intent(tool, {}) == "meta"

    def test_bash_verify(self):
        assert _quick_intent("Bash", {"command": "pytest tests/"}) == "verify"
        assert _quick_intent("Bash", {"command": "ruff check ."}) == "verify"
        assert _quick_intent("Bash", {"command": "mypy src/"}) == "verify"

    def test_bash_execute(self):
        assert _quick_intent("Bash", {"command": "python main.py"}) == "execute"
        assert _quick_intent("Bash", {"command": "git status"}) == "execute"

    def test_bash_empty_command(self):
        assert _quick_intent("Bash", {}) == "execute"

    def test_unknown_tool(self):
        assert _quick_intent("SomeTool", {}) == "unknown"

    def test_verify_case_insensitive(self):
        assert _quick_intent("Bash", {"command": "PYTEST -v"}) == "verify"
        assert _quick_intent("Bash", {"command": "Ruff Check"}) == "verify"


# ── _tool_call_to_action ─────────────────────────────────────────


class TestToolCallToAction:
    def test_basic_bash(self):
        tc = _tc(tool_name="Bash", input_data={"command": "ls -la"}, timestamp=10.0)
        result = _tool_call_to_action(tc)
        assert result["tool"] == "Bash"
        assert result["ts"] == 10.0
        assert result["sig"] == "ls -la"
        assert result["intent"] == "execute"

    def test_file_path_preferred_as_sig(self):
        tc = _tc(
            tool_name="Read",
            input_data={"file_path": "/src/main.py", "command": "ignored"},
        )
        result = _tool_call_to_action(tc)
        assert result["sig"] == "/src/main.py"

    def test_sig_truncated_to_80(self):
        tc = _tc(input_data={"command": "x" * 200})
        result = _tool_call_to_action(tc)
        assert len(result["sig"]) == 80

    def test_exit_code_included(self):
        tc = _tc(exit_code=1)
        result = _tool_call_to_action(tc)
        assert result["exit"] == 1

    def test_exit_code_zero_included(self):
        tc = _tc(exit_code=0)
        result = _tool_call_to_action(tc)
        assert result["exit"] == 0

    def test_exit_code_none_excluded(self):
        tc = _tc(exit_code=None)
        result = _tool_call_to_action(tc)
        assert "exit" not in result

    def test_error_text_included(self):
        tc = _tc(error_text="ModuleNotFoundError: foo")
        result = _tool_call_to_action(tc)
        assert result["err"] == "ModuleNotFoundError: foo"

    def test_error_text_truncated_to_200(self):
        tc = _tc(error_text="e" * 500)
        result = _tool_call_to_action(tc)
        assert len(result["err"]) == 200

    def test_empty_error_text_excluded(self):
        tc = _tc(error_text="")
        result = _tool_call_to_action(tc)
        assert "err" not in result

    def test_empty_input_data(self):
        tc = _tc(input_data={})
        result = _tool_call_to_action(tc)
        assert result["sig"] == ""


# ── _accumulate_error_memory ──────────────────────────────────────


class TestAccumulateErrorMemory:
    def test_non_bash_ignored(self):
        mem: dict = {}
        tc = _tc(tool_name="Read", exit_code=1, error_text="some error")
        _accumulate_error_memory(mem, tc)
        assert len(mem) == 0

    def test_success_ignored(self):
        mem: dict = {}
        tc = _tc(exit_code=0, error_text="")
        _accumulate_error_memory(mem, tc)
        assert len(mem) == 0

    def test_none_exit_no_error_ignored(self):
        mem: dict = {}
        tc = _tc(exit_code=None, error_text="")
        _accumulate_error_memory(mem, tc)
        assert len(mem) == 0

    def test_nonzero_exit_with_error(self):
        mem: dict = {}
        tc = _tc(exit_code=1, error_text="ModuleNotFoundError: foo\nfull traceback", timestamp=5.0)
        _accumulate_error_memory(mem, tc)
        assert "ModuleNotFoundError: foo" in mem
        assert mem["ModuleNotFoundError: foo"]["count"] == 1
        assert mem["ModuleNotFoundError: foo"]["first_seen"] == 5.0

    def test_error_text_only(self):
        """exit_code=None but error_text present should still record."""
        mem: dict = {}
        tc = _tc(exit_code=None, error_text="timeout error")
        _accumulate_error_memory(mem, tc)
        assert "timeout error" in mem

    def test_count_increments(self):
        mem: dict = {}
        tc1 = _tc(exit_code=1, error_text="err\nstack", timestamp=1.0)
        tc2 = _tc(exit_code=1, error_text="err\nstack2", timestamp=3.0)
        _accumulate_error_memory(mem, tc1)
        _accumulate_error_memory(mem, tc2)
        assert mem["err"]["count"] == 2
        assert mem["err"]["first_seen"] == 1.0
        assert mem["err"]["last_seen"] == 3.0

    def test_last_seen_uses_max(self):
        mem: dict = {}
        tc1 = _tc(exit_code=1, error_text="err", timestamp=5.0)
        tc2 = _tc(exit_code=1, error_text="err", timestamp=2.0)
        _accumulate_error_memory(mem, tc1)
        _accumulate_error_memory(mem, tc2)
        assert mem["err"]["last_seen"] == 5.0

    def test_empty_error_sig_skipped(self):
        mem: dict = {}
        tc = _tc(exit_code=1, error_text="\n\n")
        _accumulate_error_memory(mem, tc)
        assert len(mem) == 0

    def test_error_sig_truncated_to_120(self):
        mem: dict = {}
        tc = _tc(exit_code=1, error_text="a" * 200)
        _accumulate_error_memory(mem, tc)
        key = list(mem.keys())[0]
        assert len(key) == 120


# ── _collect_actions ──────────────────────────────────────────────


class TestCollectActions:
    def test_empty_sessions(self):
        actions, errors, tokens, chars = _collect_actions([])
        assert actions == []
        assert errors == {}
        assert tokens == 0
        assert chars == 0

    def test_single_session(self):
        tc = _tc(tool_name="Read", input_data={"file_path": "a.py"}, timestamp=1.0)
        session = _session(
            exchanges=[_exchange(tool_calls=[tc], user_text="hello")],
            token_usage={"input": 100, "output": 50},
        )
        actions, errors, tokens, chars = _collect_actions([session])
        assert len(actions) == 1
        assert actions[0]["tool"] == "Read"
        assert tokens == 150
        assert chars == 5

    def test_error_collection(self):
        tc = _tc(exit_code=1, error_text="fail", timestamp=2.0)
        session = _session(exchanges=[_exchange(tool_calls=[tc])])
        _, err_mem, _, _ = _collect_actions([session])
        assert "fail" in err_mem

    def test_multiple_sessions(self):
        s1 = _session(
            exchanges=[_exchange(tool_calls=[_tc(timestamp=1.0)])],
            token_usage={"input": 10, "output": 5},
        )
        s2 = _session(
            exchanges=[_exchange(tool_calls=[_tc(timestamp=2.0)])],
            token_usage={"input": 20, "output": 10},
        )
        actions, _, tokens, _ = _collect_actions([s1, s2])
        assert len(actions) == 2
        assert tokens == 45


# ── HabitBootstrapper.bootstrap_project ───────────────────────────


class TestBootstrapProject:
    def test_empty_sessions(self):
        b = HabitBootstrapper()
        result = b.bootstrap_project([])
        assert result == {"error": "no sessions"}

    def test_no_project_path(self):
        b = HabitBootstrapper()
        s = _session(project_path="", cwd="")
        result = b.bootstrap_project([s])
        assert result == {"error": "no project path"}

    @patch("lintgate._habit_bootstrap.save_habit_state_standalone")
    def test_valid_session(self, _mock_save):
        b = HabitBootstrapper()
        tc = _tc(tool_name="Read", input_data={"file_path": "f.py"}, timestamp=1.0)
        s = _session(
            exchanges=[_exchange(tool_calls=[tc], user_text="do stuff")],
            project_path="/proj",
            token_usage={"input": 100, "output": 50},
        )
        result = b.bootstrap_project([s])
        assert result["project_path"] == "/proj"
        assert result["sessions_count"] == 1
        assert result["total_actions"] == 1
        assert result["total_tokens"] == 150
        assert "habit_score" in result
        assert "signals" in result
        _mock_save.assert_called_once()

    @patch("lintgate._habit_bootstrap.save_habit_state_standalone")
    def test_action_ring_capped_at_30(self, _mock_save):
        b = HabitBootstrapper()
        tcs = [
            _tc(tool_name="Read", input_data={"file_path": "f.py"}, timestamp=float(i))
            for i in range(50)
        ]
        s = _session(exchanges=[_exchange(tool_calls=tcs)])
        result = b.bootstrap_project([s])
        assert result["total_actions"] == 50
        assert result["action_ring_size"] == 30

    @patch("lintgate._habit_bootstrap.save_habit_state_standalone")
    def test_calibration_factor(self, _mock_save):
        b = HabitBootstrapper()
        s = _session(
            exchanges=[_exchange(user_text="x" * 100)],
            token_usage={"input": 500, "output": 500},
        )
        result = b.bootstrap_project([s])
        assert result["calibration_factor"] == round(1000 / 100, 4)

    @patch("lintgate._habit_bootstrap.save_habit_state_standalone")
    def test_zero_chars_calibration(self, _mock_save):
        b = HabitBootstrapper()
        s = _session(exchanges=[], token_usage={"input": 0, "output": 0})
        result = b.bootstrap_project([s])
        assert result["calibration_factor"] == 0.25


# ── load_error_bootstrap ──────────────────────────────────────────


class TestLoadErrorBootstrap:
    def test_missing_file_returns_empty(self, tmp_path):
        with (
            patch("lintgate._habit_bootstrap._HABIT_STATE_DIR", tmp_path),
            patch("lintgate._habit_bootstrap._project_hash", return_value="abc"),
        ):
            result = load_error_bootstrap("/proj")
            assert result == {}

    def test_valid_file_loaded(self, tmp_path):
        with (
            patch("lintgate._habit_bootstrap._HABIT_STATE_DIR", tmp_path),
            patch("lintgate._habit_bootstrap._project_hash", return_value="abc"),
        ):
            (tmp_path / "abc_errors.json").write_text(json.dumps({"err1": {"count": 2}}))
            result = load_error_bootstrap("/proj")
            assert result == {"err1": {"count": 2}}

    def test_corrupted_json_returns_empty(self, tmp_path):
        with (
            patch("lintgate._habit_bootstrap._HABIT_STATE_DIR", tmp_path),
            patch("lintgate._habit_bootstrap._project_hash", return_value="abc"),
        ):
            (tmp_path / "abc_errors.json").write_text("{broken json")
            result = load_error_bootstrap("/proj")
            assert result == {}

    def test_non_dict_returns_empty(self, tmp_path):
        with (
            patch("lintgate._habit_bootstrap._HABIT_STATE_DIR", tmp_path),
            patch("lintgate._habit_bootstrap._project_hash", return_value="abc"),
        ):
            (tmp_path / "abc_errors.json").write_text("[1,2,3]")
            result = load_error_bootstrap("/proj")
            assert result == {}


# ── HabitBootstrapper._save_error_memory ──────────────────────────


class TestSaveErrorMemory:
    def test_saves_error_file(self, tmp_path):
        with (
            patch("lintgate._habit_bootstrap._HABIT_STATE_DIR", tmp_path),
            patch("lintgate._habit_bootstrap._project_hash", return_value="xyz"),
        ):
            HabitBootstrapper._save_error_memory("/proj", {"err": {"count": 1}})
            saved = json.loads((tmp_path / "xyz_errors.json").read_text())
            assert saved == {"err": {"count": 1}}

    def test_os_error_silenced(self, tmp_path):
        with (
            patch("lintgate._habit_bootstrap._HABIT_STATE_DIR", tmp_path / "no" / "such"),
            patch("lintgate._habit_bootstrap._project_hash", return_value="abc"),
        ):
            # _HABIT_STATE_DIR.mkdir should handle this, but if parent is
            # unwritable, OSError is silenced
            HabitBootstrapper._save_error_memory("/proj", {"e": {"c": 1}})
