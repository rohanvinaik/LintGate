"""Tests for the workflow mode system (surgical/refactor/greenfield/explore/debug_spiral).

Covers:
- WorkflowMode enum parsing
- declare_workflow state transitions
- HabitModeState serialization with workflow_mode
- RuntimeState workflow_mode propagation
- PreToolUse snapshot stashing
- PostToolUse surgical delta computation + heartbeat
- lint_files surgical scope split
- ModeSpec guide rendering
- Intent-to-workflow mapping
- verify_mode_specs CI check
"""

from __future__ import annotations

import json
import os
import time

import pytest

from lintgate._habit_types import HabitModeState, WorkflowMode


# ---------------------------------------------------------------------------
# WorkflowMode enum
# ---------------------------------------------------------------------------


class TestWorkflowMode:
    def test_valid_modes(self):
        assert WorkflowMode.from_str("surgical") == WorkflowMode.SURGICAL
        assert WorkflowMode.from_str("refactor") == WorkflowMode.REFACTOR
        assert WorkflowMode.from_str("greenfield") == WorkflowMode.GREENFIELD
        assert WorkflowMode.from_str("explore") == WorkflowMode.EXPLORE
        assert WorkflowMode.from_str("debug_spiral") == WorkflowMode.DEBUG_SPIRAL

    def test_case_insensitive(self):
        assert WorkflowMode.from_str("SURGICAL") == WorkflowMode.SURGICAL
        assert WorkflowMode.from_str("Refactor") == WorkflowMode.REFACTOR

    def test_whitespace_stripped(self):
        assert WorkflowMode.from_str("  surgical  ") == WorkflowMode.SURGICAL

    def test_invalid_returns_none(self):
        assert WorkflowMode.from_str("invalid") is None
        assert WorkflowMode.from_str("") is None
        assert WorkflowMode.from_str(None) is None

    def test_valid_names(self):
        names = WorkflowMode.valid_names()
        assert len(names) == 5
        assert "surgical" in names
        assert "debug_spiral" in names

    def test_str_enum(self):
        assert WorkflowMode.SURGICAL == "surgical"
        assert WorkflowMode.SURGICAL.value == "surgical"


# ---------------------------------------------------------------------------
# declare_workflow
# ---------------------------------------------------------------------------


class TestDeclareWorkflow:
    def test_set_surgical(self):
        from lintgate._habit_signals import declare_workflow

        state = HabitModeState()
        result = declare_workflow(state, "surgical")
        assert result == "surgical"
        assert state.workflow_mode == "surgical"

    def test_set_refactor(self):
        from lintgate._habit_signals import declare_workflow

        state = HabitModeState()
        result = declare_workflow(state, "refactor")
        assert result == "refactor"
        assert state.workflow_mode == "refactor"

    def test_clear_workflow(self):
        from lintgate._habit_signals import declare_workflow

        state = HabitModeState(workflow_mode="surgical")
        result = declare_workflow(state, None)
        assert result == ""
        assert state.workflow_mode == ""

    def test_clear_with_empty_string(self):
        from lintgate._habit_signals import declare_workflow

        state = HabitModeState(workflow_mode="surgical")
        result = declare_workflow(state, "")
        assert result == ""

    def test_invalid_raises(self):
        from lintgate._habit_signals import declare_workflow

        state = HabitModeState()
        with pytest.raises(ValueError, match="Invalid workflow mode"):
            declare_workflow(state, "invalid_mode")

    def test_orthogonal_to_habit(self):
        """Workflow mode doesn't affect habit mode state."""
        from lintgate._habit_signals import declare_workflow

        state = HabitModeState(active=True, habit_score=0.8)
        declare_workflow(state, "surgical")
        assert state.active is True
        assert state.habit_score == 0.8
        assert state.workflow_mode == "surgical"


# ---------------------------------------------------------------------------
# HabitModeState serialization
# ---------------------------------------------------------------------------


class TestHabitModeStateSerialization:
    def test_workflow_mode_in_to_dict(self):
        state = HabitModeState(workflow_mode="surgical")
        d = state.to_dict()
        assert d["workflow_mode"] == "surgical"

    def test_workflow_mode_from_dict(self):
        data = {"workflow_mode": "refactor", "active": True}
        state = HabitModeState.from_dict(data)
        assert state.workflow_mode == "refactor"

    def test_missing_workflow_mode_defaults_empty(self):
        state = HabitModeState.from_dict({})
        assert state.workflow_mode == ""

    def test_roundtrip(self):
        state = HabitModeState(active=True, workflow_mode="greenfield")
        d = state.to_dict()
        restored = HabitModeState.from_dict(d)
        assert restored.workflow_mode == "greenfield"
        assert restored.active is True


# ---------------------------------------------------------------------------
# RuntimeState workflow_mode field
# ---------------------------------------------------------------------------


class TestRuntimeStateWorkflowMode:
    def test_default_empty(self):
        from lintgate.runtime_state import RuntimeState

        rs = RuntimeState()
        assert rs.workflow_mode == ""

    def test_from_dict(self):
        from lintgate.runtime_state import RuntimeState

        rs = RuntimeState.from_dict({"workflow_mode": "surgical"})
        assert rs.workflow_mode == "surgical"

    def test_to_dict_includes_workflow(self):
        from lintgate.runtime_state import RuntimeState

        rs = RuntimeState(workflow_mode="refactor")
        d = rs.to_dict()
        assert d["workflow_mode"] == "refactor"

    def test_build_runtime_state_propagates(self):
        from lintgate.runtime_state import RuntimeState, build_runtime_state

        habit = HabitModeState(workflow_mode="surgical")
        # build_runtime_state needs a project_root; use a temp path
        # that has no existing state file
        rs = build_runtime_state("/nonexistent/path/12345", habit_state=habit)
        assert rs.workflow_mode == "surgical"


# ---------------------------------------------------------------------------
# PostToolUse surgical delta computation
# ---------------------------------------------------------------------------


class TestSurgicalDelta:
    def test_compute_delta_clean(self):
        from lintgate.hooks.posttooluse_controlplane import _compute_surgical_delta

        pre_stash = {"file": "src/foo.py", "finding_count": 0}

        # Mock mesh_result with no findings
        class MockChannelResult:
            def __init__(self, findings):
                self.channel = "lint"
                self.findings = findings

        class MockMesh:
            channel_results = [MockChannelResult([])]

        delta = _compute_surgical_delta(pre_stash, MockMesh(), "/project")
        assert delta["delta"] == 0
        assert delta["post_count"] == 0
        assert delta["edit_file"] == "src/foo.py"

    def test_compute_delta_regression(self):
        from lintgate.hooks.posttooluse_controlplane import _compute_surgical_delta

        pre_stash = {"file": "src/foo.py", "finding_count": 0}

        class MockFinding:
            file = "src/foo.py"
            line = 10
            code = "E501"
            kind = ""
            message = "line too long"
            severity = "warning"

        class MockChannelResult:
            channel = "lint"
            findings = [MockFinding()]

        class MockMesh:
            channel_results = [MockChannelResult()]

        delta = _compute_surgical_delta(pre_stash, MockMesh(), "/project")
        assert delta["delta"] == 1
        assert delta["post_count"] == 1
        assert len(delta["new_findings"]) == 1

    def test_compute_delta_improvement(self):
        from lintgate.hooks.posttooluse_controlplane import _compute_surgical_delta

        pre_stash = {"file": "src/foo.py", "finding_count": 3}

        class MockChannelResult:
            channel = "lint"
            findings = []

        class MockMesh:
            channel_results = [MockChannelResult()]

        delta = _compute_surgical_delta(pre_stash, MockMesh(), "/project")
        assert delta["delta"] == -3
        assert delta["post_count"] == 0


class TestSurgicalReport:
    def test_clean_silent(self):
        from lintgate.hooks.posttooluse_controlplane import _format_surgical_report

        delta = {"delta": 0, "post_count": 0, "edit_file": "foo.py", "new_findings": []}
        report = _format_surgical_report(delta, heartbeat_count=1)
        assert report == {}

    def test_clean_heartbeat_every_5(self):
        from lintgate.hooks.posttooluse_controlplane import _format_surgical_report

        delta = {"delta": 0, "post_count": 0, "edit_file": "foo.py", "new_findings": []}
        report = _format_surgical_report(delta, heartbeat_count=5)
        assert "systemMessage" in report
        assert "clean" in report["systemMessage"]

    def test_improvement_report(self):
        from lintgate.hooks.posttooluse_controlplane import _format_surgical_report

        delta = {"delta": -2, "post_count": 1, "edit_file": "foo.py", "new_findings": []}
        report = _format_surgical_report(delta, heartbeat_count=1)
        assert "improved" in report["systemMessage"]
        assert "-2" in report["systemMessage"]

    def test_regression_report(self):
        from lintgate.hooks.posttooluse_controlplane import _format_surgical_report

        delta = {
            "delta": 2,
            "post_count": 2,
            "edit_file": "foo.py",
            "new_findings": ["  foo.py:10:E501 (warning) line too long"],
        }
        report = _format_surgical_report(delta, heartbeat_count=1)
        assert "+2" in report["systemMessage"]
        assert "E501" in report["systemMessage"]


class TestSurgicalHeartbeat:
    def test_increment(self):
        from lintgate.hooks.posttooluse_controlplane import _get_surgical_heartbeat_count

        class MockSession:
            behavior_compass = {}

        session = MockSession()
        assert _get_surgical_heartbeat_count(session) == 1
        assert _get_surgical_heartbeat_count(session) == 2
        assert _get_surgical_heartbeat_count(session) == 3

    def test_none_session(self):
        from lintgate.hooks.posttooluse_controlplane import _get_surgical_heartbeat_count

        assert _get_surgical_heartbeat_count(None) == 0


# ---------------------------------------------------------------------------
# PreToolUse snapshot stash
# ---------------------------------------------------------------------------


class TestPreEditStash:
    def test_stash_created_in_surgical_mode(self, tmp_path):
        """Stash is created when workflow_mode is surgical."""
        from lintgate.hooks.pretooluse import _stash_pre_edit_snapshot

        # Set up runtime state with surgical mode
        lintgate_dir = tmp_path / ".lintgate"
        lintgate_dir.mkdir()
        state_data = {"workflow_mode": "surgical", "generation": 1, "timestamp": time.time()}
        (lintgate_dir / "runtime_state.json").write_text(json.dumps(state_data))

        tool_input = {"file_path": str(tmp_path / "src" / "foo.py")}
        _stash_pre_edit_snapshot("Edit", tool_input, str(tmp_path))

        stash_path = lintgate_dir / "pre_edit_snapshot.json"
        assert stash_path.exists()
        stash = json.loads(stash_path.read_text())
        assert "file" in stash
        assert "timestamp" in stash

    def test_no_stash_in_non_surgical(self, tmp_path):
        """No stash when not in surgical mode."""
        from lintgate.hooks.pretooluse import _stash_pre_edit_snapshot

        lintgate_dir = tmp_path / ".lintgate"
        lintgate_dir.mkdir()
        state_data = {"workflow_mode": "refactor", "generation": 1, "timestamp": time.time()}
        (lintgate_dir / "runtime_state.json").write_text(json.dumps(state_data))

        tool_input = {"file_path": str(tmp_path / "src" / "foo.py")}
        _stash_pre_edit_snapshot("Edit", tool_input, str(tmp_path))

        stash_path = lintgate_dir / "pre_edit_snapshot.json"
        assert not stash_path.exists()

    def test_no_stash_for_read(self, tmp_path):
        """No stash for non-edit tools."""
        from lintgate.hooks.pretooluse import _stash_pre_edit_snapshot

        _stash_pre_edit_snapshot("Read", {"file_path": "foo.py"}, str(tmp_path))
        assert not (tmp_path / ".lintgate" / "pre_edit_snapshot.json").exists()


# ---------------------------------------------------------------------------
# lint_files surgical scope
# ---------------------------------------------------------------------------


class TestLintFilesSurgicalScope:
    def test_build_surgical_result_clean(self):
        from scripts.lint_run import _build_surgical_result

        result = {
            "issue_count": 0,
            "blocking_count": 0,
            "run_id": "abc123",
            "next_actions": [],
            "issues": [],
        }
        out = _build_surgical_result(
            result, ["/project/src/foo.py"], "/project", 0, 0
        )
        assert out["edit_scope"]["verdict"] == "clean"
        assert out["edit_scope"]["issue_count"] == 0
        assert out["edit_scope"]["files"] == ["src/foo.py"]
        assert "baseline" in out
        assert out["run_id"] == "abc123"

    def test_build_surgical_result_with_findings(self):
        from scripts.lint_run import _build_surgical_result

        result = {
            "issue_count": 3,
            "blocking_count": 1,
            "run_id": "def456",
            "next_actions": ["lint_fix"],
            "issues": [{"code": "E501"}],
        }
        out = _build_surgical_result(
            result, ["/project/src/foo.py"], "/project", 1, 3
        )
        assert out["edit_scope"]["verdict"] == "findings"
        assert out["edit_scope"]["issue_count"] == 3
        assert out["edit_scope"]["blocking_count"] == 1

    def test_baseline_unknown_when_no_prior_run(self):
        from scripts.lint_run import _load_project_baseline

        baseline = _load_project_baseline("/nonexistent/project/path/99999")
        assert baseline["state"] == "unknown"
        assert baseline["last_full_run"] is None


# ---------------------------------------------------------------------------
# Workflow guides
# ---------------------------------------------------------------------------


class TestWorkflowGuides:
    def test_all_modes_have_specs(self):
        from lintgate.workflow_guides import MODE_SPECS

        assert len(MODE_SPECS) == 5
        assert set(MODE_SPECS.keys()) == {
            "surgical", "refactor", "greenfield", "explore", "debug_spiral"
        }

    def test_render_guide_surgical(self):
        from lintgate.workflow_guides import MODE_SPECS, render_guide

        guide = render_guide(MODE_SPECS["surgical"])
        assert "MODE: surgical" in guide
        assert "LOOP:" in guide
        assert "HOOK POLICY:" in guide
        assert "ESCALATE IF:" in guide
        assert "lint_files" in guide

    def test_render_all_guides_summary(self):
        from lintgate.workflow_guides import render_all_guides_summary

        summary = render_all_guides_summary()
        assert "WORKFLOW MODES" in summary
        assert "surgical" in summary
        assert "refactor" in summary
        assert "greenfield" in summary
        assert "declare_workflow" in summary

    def test_verify_mode_specs_with_valid_tools(self):
        from lintgate.workflow_guides import MODE_SPECS, verify_mode_specs

        # Collect all tool names referenced in tools_to_ignore
        all_tools = set()
        for spec in MODE_SPECS.values():
            all_tools.update(spec.tools_to_ignore)
        errors = verify_mode_specs(all_tools)
        assert errors == []

    def test_verify_mode_specs_catches_missing(self):
        from lintgate.workflow_guides import verify_mode_specs

        errors = verify_mode_specs({"lint_files"})  # Deliberately incomplete
        assert len(errors) > 0
        assert any("unknown tool" in e for e in errors)


# ---------------------------------------------------------------------------
# Intent to workflow mapping
# ---------------------------------------------------------------------------


class TestIntentMapping:
    def test_fix_bug_maps_to_surgical(self):
        from lintgate.orchestration.workflows import intent_to_workflow_mode

        assert intent_to_workflow_mode("fix_bug") == "surgical"

    def test_refactor_maps_to_refactor(self):
        from lintgate.orchestration.workflows import intent_to_workflow_mode

        assert intent_to_workflow_mode("refactor") == "refactor"

    def test_new_code_maps_to_greenfield(self):
        from lintgate.orchestration.workflows import intent_to_workflow_mode

        assert intent_to_workflow_mode("new_code") == "greenfield"

    def test_audit_maps_to_explore(self):
        from lintgate.orchestration.workflows import intent_to_workflow_mode

        assert intent_to_workflow_mode("audit") == "explore"

    def test_debug_maps_to_debug_spiral(self):
        from lintgate.orchestration.workflows import intent_to_workflow_mode

        assert intent_to_workflow_mode("debug") == "debug_spiral"

    def test_unknown_returns_none(self):
        from lintgate.orchestration.workflows import intent_to_workflow_mode

        assert intent_to_workflow_mode("unknown_intent") is None

    def test_none_returns_none(self):
        from lintgate.orchestration.workflows import intent_to_workflow_mode

        assert intent_to_workflow_mode(None) is None

    def test_case_insensitive(self):
        from lintgate.orchestration.workflows import intent_to_workflow_mode

        assert intent_to_workflow_mode("FIX_BUG") == "surgical"


# ---------------------------------------------------------------------------
# Session rendering
# ---------------------------------------------------------------------------


class TestSessionRendering:
    def test_workflow_mode_in_session_content(self):
        from lintgate.renderers.dynamic import render_session_content
        from lintgate.runtime_state import RuntimeState

        runtime = RuntimeState(mode="habit", habit_score=0.7, workflow_mode="surgical")
        content = render_session_content(runtime)
        assert "Workflow: surgical" in content
        assert "Mode: habit" in content

    def test_no_workflow_line_when_empty(self):
        from lintgate.renderers.dynamic import render_session_content
        from lintgate.runtime_state import RuntimeState

        runtime = RuntimeState(mode="normal", workflow_mode="")
        content = render_session_content(runtime)
        assert "Workflow:" not in content
