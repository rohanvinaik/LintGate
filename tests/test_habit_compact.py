"""Tests for lintgate/_habit_compact.py.

Covers compaction snapshot building, section builders,
truncation enforcement, and tool injection logic.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from lintgate._habit_compact import (
    _SECTION_BUDGETS,
    _TRUNCATION_ORDER,
    _build_active_context,
    _build_behavioral_section,
    _build_lint_section,
    _build_session_history,
    _build_token_section,
    _build_tool_injections,
    _enforce_snapshot_cap,
    build_compaction_snapshot,
)
from lintgate._habit_types import SNAPSHOT_MAX_CHARS, HabitModeState


def _make_state(**overrides) -> HabitModeState:
    defaults = {
        "active": True,
        "habit_score": 0.75,
        "declared": False,
        "compaction_count": 0,
        "active_files": ["src/main.py", "src/utils.py"],
        "last_test_status": "passed",
    }
    defaults.update(overrides)
    state = MagicMock(spec=HabitModeState)
    for k, v in defaults.items():
        setattr(state, k, v)
    # signals sub-object for tool injections
    if "signals" not in overrides:
        signals = MagicMock()
        signals.edit_streak = 0
        signals.test_in_last_n = True
        state.signals = signals
    return state


# ── Section Budgets & Truncation Order ──────────────────────────


class TestConstants:
    def test_section_budgets_all_positive(self):
        for key, budget in _SECTION_BUDGETS.items():
            assert budget > 0, f"{key} has non-positive budget"

    def test_truncation_order_subset_of_budgets(self):
        for key in _TRUNCATION_ORDER:
            assert key in _SECTION_BUDGETS, f"{key} not in budgets"


# ── _build_active_context ────────────────────────────────────────


class TestBuildActiveContext:
    def test_basic(self):
        state = _make_state()
        ctx = _build_active_context(state)
        assert ctx["files"] == ["src/main.py", "src/utils.py"]
        assert ctx["last_test_status"] == "passed"

    def test_caps_at_10_files(self):
        files = [f"file_{i}.py" for i in range(20)]
        state = _make_state(active_files=files)
        ctx = _build_active_context(state)
        assert len(ctx["files"]) == 10

    def test_shortens_long_paths(self):
        # Each path ~50 chars * 10 = ~500 > 400 threshold
        files = [f"very/long/path/to/deeply/nested/module/file_{i}.py" for i in range(10)]
        state = _make_state(active_files=files)
        ctx = _build_active_context(state)
        # When total length > 400, paths are shortened to basename
        for f in ctx["files"]:
            assert "/" not in f

    def test_empty_files(self):
        state = _make_state(active_files=[])
        ctx = _build_active_context(state)
        assert ctx["files"] == []


# ── _build_lint_section ──────────────────────────────────────────


class TestBuildLintSection:
    def test_none_input(self):
        assert _build_lint_section(None) is None

    def test_empty_dict_returns_none(self):
        # Empty dict is falsy → returns None
        assert _build_lint_section({}) is None

    def test_basic_run(self):
        result = _build_lint_section({"blocking_count": 1, "warning_count": 2, "issues": []})
        assert result is not None
        assert result["blocking_count"] == 1
        assert result["warning_count"] == 2
        assert result["issues"] == []

    def test_caps_issues_at_5(self):
        issues = [{"file": f"f{i}.py", "kind": "E001", "message": "err"} for i in range(10)]
        result = _build_lint_section({"issues": issues, "blocking_count": 10})
        assert result is not None
        assert len(result["issues"]) == 5
        assert result["blocking_count"] == 10

    def test_truncates_long_messages(self):
        issues = [{"message": "x" * 200, "file": "a.py", "kind": "E1"}]
        result = _build_lint_section({"issues": issues})
        assert result is not None
        assert len(result["issues"][0]["message"]) <= 80


# ── _build_behavioral_section ────────────────────────────────────


class TestBuildBehavioralSection:
    def test_none_input(self):
        assert _build_behavioral_section(None) is None

    def test_empty_dict_returns_none(self):
        # Empty dict is falsy → returns None
        assert _build_behavioral_section({}) is None

    def test_minimal_compass(self):
        result = _build_behavioral_section({"hypotheses": [], "error_memory": {}})
        assert result is not None
        assert result["top_constraints"] == []
        assert result["top_errors"] == []
        assert result["prediction_recall"] == 0.0

    def test_sorts_hypotheses_by_confidence(self):
        compass = {
            "hypotheses": [
                {"claim": "low", "confidence": 0.2},
                {"claim": "high", "confidence": 0.9},
                {"claim": "mid", "confidence": 0.5},
                {"claim": "top", "confidence": 0.95},
            ]
        }
        result = _build_behavioral_section(compass)
        assert result is not None
        assert len(result["top_constraints"]) == 3
        confs = [h["confidence"] for h in result["top_constraints"]]
        assert confs == sorted(confs, reverse=True)

    def test_error_memory_top_2(self):
        compass = {
            "error_memory": {
                "err_a": {"count": 5},
                "err_b": {"count": 1},
                "err_c": {"count": 10},
            }
        }
        result = _build_behavioral_section(compass)
        assert result is not None
        assert len(result["top_errors"]) == 2
        assert result["top_errors"][0]["count"] == 10


# ── _build_session_history ───────────────────────────────────────


class TestBuildSessionHistory:
    def test_none_input(self):
        assert _build_session_history(None) is None

    def test_empty_snapshots(self):
        result = _build_session_history({"snapshots": []})
        assert result == []

    def test_caps_at_2(self):
        snapshots = [
            {"coherence_state": "clean", "blocking_count": 0, "finding_count": 1},
            {"coherence_state": "coupled", "blocking_count": 2, "finding_count": 5},
            {"coherence_state": "systemic", "blocking_count": 4, "finding_count": 10},
        ]
        result = _build_session_history({"snapshots": snapshots})
        assert result is not None
        assert len(result) == 2
        assert result[0]["coherence"] == "coupled"
        assert result[1]["coherence"] == "systemic"


# ── _build_token_section ─────────────────────────────────────────


class TestBuildTokenSection:
    def test_none_input(self):
        assert _build_token_section(None) is None

    def test_basic(self):
        result = _build_token_section(
            {
                "estimated_tokens_used": 50000,
                "tool_call_count": 120,
                "lines_written": 300,
            }
        )
        assert result is not None
        assert result["estimated_used"] == 50000
        assert result["tool_calls"] == 120
        assert result["lines_written"] == 300

    def test_empty_dict_returns_none(self):
        assert _build_token_section({}) is None

    def test_missing_keys_default_to_zero(self):
        result = _build_token_section({"some_key": 1})
        assert result is not None
        assert result["estimated_used"] == 0
        assert result["tool_calls"] == 0


# ── _build_tool_injections ───────────────────────────────────────


class TestBuildToolInjections:
    def test_always_includes_habit_status(self):
        state = _make_state()
        result = _build_tool_injections(state, None, None, None)
        tools = [inj["tool"] for inj in result]
        assert "habit_status" in tools

    def test_edit_streak_triggers_prediction(self):
        state = _make_state()
        state.signals.edit_streak = 6
        state.signals.test_in_last_n = False
        result = _build_tool_injections(state, None, None, None)
        tools = [inj["tool"] for inj in result]
        assert "prediction_register" in tools

    def test_blocking_lint_triggers_lint_fix(self):
        state = _make_state()
        lint_run = {"blocking_count": 5}
        result = _build_tool_injections(state, None, lint_run, None)
        tools = [inj["tool"] for inj in result]
        assert "lint_fix" in tools

    def test_systemic_coherence_triggers_controlplane(self):
        state = _make_state()
        session_memory = {"coherence_trajectory": ["clean", "systemic"]}
        result = _build_tool_injections(state, None, None, session_memory)
        tools = [inj["tool"] for inj in result]
        assert "controlplane_run" in tools

    def test_failed_approaches_trigger_constraint_check(self):
        state = _make_state()
        compass = {
            "approaches": [
                {"outcome": "failed"},
                {"outcome": "failed"},
                {"outcome": "success"},
            ]
        }
        result = _build_tool_injections(state, compass, None, None)
        tools = [inj["tool"] for inj in result]
        assert "constraint_check" in tools

    def test_caps_at_4(self):
        state = _make_state()
        state.signals.edit_streak = 10
        state.signals.test_in_last_n = False
        compass = {"approaches": [{"outcome": "failed"}] * 5}
        lint_run = {"blocking_count": 10}
        session = {"coherence_trajectory": ["systemic"]}
        result = _build_tool_injections(state, compass, lint_run, session)
        assert len(result) <= 4

    def test_sorted_by_priority(self):
        state = _make_state()
        state.signals.edit_streak = 10
        state.signals.test_in_last_n = False
        result = _build_tool_injections(state, None, None, None)
        priorities = [inj["priority"] for inj in result]
        assert priorities == sorted(priorities)


# ── _enforce_snapshot_cap ────────────────────────────────────────


class TestEnforceSnapshotCap:
    def test_small_snapshot_unchanged(self):
        snapshot = {"mode": {"active": True}, "lint_state": {"issues": []}}
        original = dict(snapshot)
        _enforce_snapshot_cap(snapshot)
        assert snapshot == original

    def test_large_snapshot_truncates(self):
        snapshot = {
            "mode": {"active": True},
            "session_history": ["x" * 1000] * 50,
            "recurring_issues": ["y" * 1000] * 50,
            "behavioral_trajectory": {"data": "z" * 5000},
            "lint_state": {"issues": ["a" * 500] * 50},
            "coherence_trajectory": ["b" * 500] * 50,
        }
        _enforce_snapshot_cap(snapshot)
        serialized = json.dumps(snapshot, separators=(",", ":"))
        assert len(serialized) <= SNAPSHOT_MAX_CHARS

    def test_truncation_follows_priority_order(self):
        # session_history is first in truncation order
        snapshot = {
            "mode": {"active": True},
            "session_history": "x" * (SNAPSHOT_MAX_CHARS + 1000),
            "recurring_issues": "still here",
        }
        _enforce_snapshot_cap(snapshot)
        assert snapshot["session_history"] is None


# ── build_compaction_snapshot ────────────────────────────────────


class TestBuildCompactionSnapshot:
    def test_basic_structure(self):
        state = _make_state()
        result = build_compaction_snapshot(state, "/project")
        assert "mode" in result
        assert "active_context" in result
        assert "focus_directive" in result
        assert result["mode"]["active"] is True
        assert result["mode"]["habit_score"] == 0.75
        assert result["mode"]["compaction_number"] == 1

    def test_with_all_inputs(self):
        state = _make_state()
        result = build_compaction_snapshot(
            state,
            "/project",
            session_memory={"snapshots": [], "coherence_trajectory": ["clean"]},
            compass={"hypotheses": [], "error_memory": {}},
            last_lint_run={"blocking_count": 0, "warning_count": 0, "issues": []},
            theory_pack={"facets": {}},
            issue_memory={"recurrent_issues": ["iss1"]},
            token_estimate={"estimated_tokens_used": 100},
        )
        assert result["theory_digest"] == {"facets": {}}
        assert result["token_state"]["estimated_used"] == 100
        assert result["recurring_issues"] == ["iss1"]

    def test_none_optionals(self):
        state = _make_state()
        result = build_compaction_snapshot(state, "/project")
        assert result["theory_digest"] is None
        assert result["lint_state"] is None
        assert result["behavioral_trajectory"] is None

    def test_focus_directive_includes_files(self):
        state = _make_state(active_files=["a.py", "b.py"])
        result = build_compaction_snapshot(state, "/project")
        assert "a.py" in result["focus_directive"]
        assert "b.py" in result["focus_directive"]

    def test_focus_directive_no_files(self):
        state = _make_state(active_files=[])
        result = build_compaction_snapshot(state, "/project")
        assert "none" in result["focus_directive"]
