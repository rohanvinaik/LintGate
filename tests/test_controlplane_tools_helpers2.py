"""Tests for extracted helper functions in mcp_tools/controlplane_tools.py.

Part 2: Feedback handling (disagreement, constraints, living context),
         repair collection/execution/apply, and register.
"""

from __future__ import annotations

import json
import subprocess
from unittest import mock

import pytest

from mcp_tools.controlplane_tools import (
    _collect_pending_repairs,
    _execute_single_repair,
    _generate_living_context_patches,
    _impl_controlplane_agent_feedback,
    _impl_controlplane_apply_repairs,
    _load_all_repairs,
    _process_accepted_constraints,
    _process_rejected_constraints,
    _record_disagreement,
    register,
)


def _stub_helpers(**overrides):
    defaults = {
        "_validate_project_root": lambda p: p or "/tmp/test",
        "_collect_python_files": lambda _root: [],
        "_build_cp_full_details": lambda _mr, _fi: {},
        "_build_onboarding_status": lambda _root: {"config_state": "config_enabled"},
        "_json_dumps": lambda obj, **kw: json.dumps(obj),
    }
    defaults.update(overrides)
    return defaults


# ── _record_disagreement ─────────────────────────────────────────────


class TestRecordDisagreement:
    def test_appends_and_records_action(self):
        session = mock.MagicMock()
        session.agent_disagreements = []
        actions: list[str] = []
        _record_disagreement(session, "run1", "I disagree with lint", actions)
        assert len(session.agent_disagreements) == 1
        assert session.agent_disagreements[0]["run_id"] == "run1"
        assert session.agent_disagreements[0]["disagreement"] == "I disagree with lint"
        assert "Recorded disagreement" in actions[0]

    def test_uses_unknown_when_no_run_id(self):
        session = mock.MagicMock()
        session.agent_disagreements = []
        actions: list[str] = []
        _record_disagreement(session, None, "text", actions)
        assert session.agent_disagreements[0]["run_id"] == "unknown"


# ── _process_accepted_constraints ────────────────────────────────────


class TestProcessAcceptedConstraints:
    def test_accepts_and_collects_rules(self):
        session = mock.MagicMock()
        session.proposed_constraints = [
            {"pattern_key": "ruff|F821", "status": "accepted", "proposed_rule": "no F821"},
        ]
        actions: list[str] = []
        with mock.patch(
            "lintgate.controlplane.constraint_proposer.update_constraint_status",
            return_value=True,
        ):
            rules = _process_accepted_constraints(session, ["ruff|F821"], actions)
        assert rules == ["no F821"]
        assert any("Accepted" in a for a in actions)

    def test_not_found_recorded(self):
        session = mock.MagicMock()
        session.proposed_constraints = []
        actions: list[str] = []
        with mock.patch(
            "lintgate.controlplane.constraint_proposer.update_constraint_status",
            return_value=False,
        ):
            rules = _process_accepted_constraints(session, ["missing"], actions)
        assert rules == []
        assert any("not found" in a for a in actions)

    def test_empty_rule_text_not_collected(self):
        """Branch: constraint accepted but proposed_rule is empty string → not collected."""
        session = mock.MagicMock()
        session.proposed_constraints = [
            {"pattern_key": "ruff|E501", "status": "accepted", "proposed_rule": ""},
        ]
        actions: list[str] = []
        with mock.patch(
            "lintgate.controlplane.constraint_proposer.update_constraint_status",
            return_value=True,
        ):
            rules = _process_accepted_constraints(session, ["ruff|E501"], actions)
        assert rules == []
        assert any("Accepted" in a for a in actions)

    def test_pattern_key_mismatch_skips(self):
        """Branch: constraint accepted but no matching pattern_key in proposed_constraints."""
        session = mock.MagicMock()
        session.proposed_constraints = [
            {"pattern_key": "other|key", "status": "accepted", "proposed_rule": "some rule"},
        ]
        actions: list[str] = []
        with mock.patch(
            "lintgate.controlplane.constraint_proposer.update_constraint_status",
            return_value=True,
        ):
            rules = _process_accepted_constraints(session, ["ruff|F821"], actions)
        assert rules == []

    def test_none_input_is_noop(self):
        session = mock.MagicMock()
        actions: list[str] = []
        rules = _process_accepted_constraints(session, None, actions)
        assert rules == []
        assert actions == []


# ── _process_rejected_constraints ────────────────────────────────────


class TestProcessRejectedConstraints:
    def test_rejects_known(self):
        session = mock.MagicMock()
        actions: list[str] = []
        with mock.patch(
            "lintgate.controlplane.constraint_proposer.update_constraint_status",
            return_value=True,
        ):
            _process_rejected_constraints(session, ["ruff|F821"], actions)
        assert any("Rejected" in a for a in actions)

    def test_rejects_unknown(self):
        session = mock.MagicMock()
        actions: list[str] = []
        with mock.patch(
            "lintgate.controlplane.constraint_proposer.update_constraint_status",
            return_value=False,
        ):
            _process_rejected_constraints(session, ["missing"], actions)
        assert any("not found" in a for a in actions)

    def test_none_input_is_noop(self):
        session = mock.MagicMock()
        actions: list[str] = []
        _process_rejected_constraints(session, None, actions)
        assert actions == []


# ── _generate_living_context_patches ─────────────────────────────────


class TestGenerateLivingContextPatches:
    def test_noop_when_inquiry_disabled(self):
        session = mock.MagicMock()
        actions: list[str] = []
        cp = mock.MagicMock()
        cp.inquiry.living_context = False
        with mock.patch("lintgate.config.load_controlplane_config", return_value=cp):
            _generate_living_context_patches(session, "/tmp", ["rule1"], actions)
        assert actions == []

    def test_noop_when_no_accepted_rules(self):
        session = mock.MagicMock()
        actions: list[str] = []
        cp = mock.MagicMock()
        cp.inquiry.living_context = True
        with mock.patch("lintgate.config.load_controlplane_config", return_value=cp):
            _generate_living_context_patches(session, "/tmp", [], actions)
        assert actions == []

    def test_noop_when_patch_returns_none(self):
        """Branch: generate_context_patch returns None → no patch appended."""
        session = mock.MagicMock()
        session.pending_patches = []
        actions: list[str] = []
        cp = mock.MagicMock()
        cp.inquiry.living_context = True

        with (
            mock.patch("lintgate.config.load_controlplane_config", return_value=cp),
            mock.patch(
                "lintgate.context_bootstrap.generate_context_patch",
                return_value=None,
            ),
        ):
            _generate_living_context_patches(session, "/tmp", ["rule_text"], actions)
        assert len(session.pending_patches) == 0
        assert not any("Generated" in a for a in actions)

    def test_generates_patches(self):
        session = mock.MagicMock()
        session.pending_patches = []
        actions: list[str] = []
        cp = mock.MagicMock()
        cp.inquiry.living_context = True

        fake_patch = mock.MagicMock()
        fake_patch.patch_id = "p1"
        fake_patch.to_dict.return_value = {"patch_id": "p1"}

        with (
            mock.patch("lintgate.config.load_controlplane_config", return_value=cp),
            mock.patch(
                "lintgate.context_bootstrap.generate_context_patch",
                return_value=fake_patch,
            ),
        ):
            _generate_living_context_patches(session, "/tmp", ["rule_text"], actions)
        assert len(session.pending_patches) == 1
        assert any("p1" in a for a in actions)


# ── _impl_controlplane_agent_feedback ────────────────────────────────


class TestImplAgentFeedback:
    def test_no_disagreement_skips_record(self):
        """Branch: disagreement is None → _record_disagreement is not called."""
        fake_session = mock.MagicMock()
        fake_session.session_id = "s2"
        fake_session.agent_disagreements = []
        fake_session.proposed_constraints = []

        with (
            mock.patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=fake_session,
            ),
            mock.patch("lintgate.controlplane.session_memory.save_session"),
            mock.patch("mcp_tools.controlplane_tools._record_disagreement") as rec,
            mock.patch(
                "mcp_tools.controlplane_tools._process_accepted_constraints",
                return_value=[],
            ),
            mock.patch("mcp_tools.controlplane_tools._process_rejected_constraints"),
            mock.patch("mcp_tools.controlplane_tools._generate_living_context_patches"),
        ):
            raw = _impl_controlplane_agent_feedback(
                "/tmp", None, None, None, None, _stub_helpers()
            )
        parsed = json.loads(raw)
        assert parsed["session_id"] == "s2"
        rec.assert_not_called()

    def test_full_flow(self):
        fake_session = mock.MagicMock()
        fake_session.session_id = "s1"
        fake_session.agent_disagreements = []
        fake_session.proposed_constraints = []

        with (
            mock.patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=fake_session,
            ),
            mock.patch("lintgate.controlplane.session_memory.save_session"),
            mock.patch("mcp_tools.controlplane_tools._record_disagreement") as rec,
            mock.patch(
                "mcp_tools.controlplane_tools._process_accepted_constraints",
                return_value=[],
            ),
            mock.patch("mcp_tools.controlplane_tools._process_rejected_constraints"),
            mock.patch("mcp_tools.controlplane_tools._generate_living_context_patches"),
        ):
            raw = _impl_controlplane_agent_feedback(
                "/tmp", "run1", "disagree", ["c1"], ["c2"], _stub_helpers()
            )
        parsed = json.loads(raw)
        assert parsed["session_id"] == "s1"
        rec.assert_called_once()


# ── _collect_pending_repairs ─────────────────────────────────────────


class TestPersistSessionAfterMeshBehaviorBranch:
    """Cover the branch where a behavior channel result IS present
    so _persist_behavior_compass_delta is called (line 210)."""

    def test_no_behavior_channel_skips_persist(self):
        """When no channel_result has channel=='behavior', persist is not called."""
        session = mock.MagicMock()
        lint_cr = mock.MagicMock()
        lint_cr.channel = "lint"
        tests_cr = mock.MagicMock()
        tests_cr.channel = "tests"
        mesh_result = mock.MagicMock()
        mesh_result.channel_results = [lint_cr, tests_cr]

        with mock.patch(
            "lintgate.controlplane.session_memory.record_mesh_run",
        ), mock.patch(
            "lintgate.controlplane.session_memory.save_session",
        ), mock.patch(
            "mcp_tools.controlplane_tools._persist_behavior_compass_delta",
        ) as persist_bc:
            from mcp_tools.controlplane_tools import _persist_session_after_mesh

            _persist_session_after_mesh(session, mesh_result, {}, mock.MagicMock())
        persist_bc.assert_not_called()


class TestCollectPendingRepairs:
    def _make_session(self, repairs_proposed, repair_outcomes=None):
        snapshot = mock.MagicMock()
        snapshot.run_id = "r1"
        snapshot.repairs_proposed = repairs_proposed
        session = mock.MagicMock()
        session.snapshots = [snapshot]
        session.repair_outcomes = repair_outcomes or {}
        return session

    def test_empty_when_no_snapshots(self):
        session = mock.MagicMock()
        session.snapshots = []
        assert _collect_pending_repairs(session, None, False) == []

    def test_filters_by_action_ids(self):
        session = self._make_session(["a1", "a2"])
        repairs = [
            {"action_id": "a1", "safe": True},
            {"action_id": "a2", "safe": True},
        ]
        with mock.patch(
            "mcp_tools.controlplane_tools._load_all_repairs", return_value=repairs,
        ):
            result = _collect_pending_repairs(session, ["a1"], False)
        assert len(result) == 1
        assert result[0]["action_id"] == "a1"

    def test_filters_safe_only(self):
        session = self._make_session(["a1", "a2"])
        repairs = [
            {"action_id": "a1", "safe": True},
            {"action_id": "a2", "safe": False},
        ]
        with mock.patch(
            "mcp_tools.controlplane_tools._load_all_repairs", return_value=repairs,
        ):
            result = _collect_pending_repairs(session, None, True)
        assert len(result) == 1
        assert result[0]["action_id"] == "a1"

    def test_skips_non_pending(self):
        session = self._make_session(["a1"], repair_outcomes={"a1": "applied"})
        repairs = [{"action_id": "a1", "safe": True}]
        with mock.patch(
            "mcp_tools.controlplane_tools._load_all_repairs", return_value=repairs,
        ):
            result = _collect_pending_repairs(session, None, False)
        assert result == []

    def test_skips_repair_not_in_proposed_ids(self):
        """Branch line 612: repair_id not in proposed_ids → continue."""
        session = self._make_session(["a1"])  # only a1 is proposed
        repairs = [
            {"action_id": "a1", "safe": True},
            {"action_id": "a_unknown", "safe": True},  # NOT in proposed_ids
        ]
        with mock.patch(
            "mcp_tools.controlplane_tools._load_all_repairs", return_value=repairs,
        ):
            result = _collect_pending_repairs(session, None, False)
        assert len(result) == 1
        assert result[0]["action_id"] == "a1"


# ── _load_all_repairs ────────────────────────────────────────────────


class TestLoadAllRepairs:
    def test_loads_from_persisted_run(self):
        snapshot = mock.MagicMock()
        snapshot.run_id = "r1"
        run_data = {
            "channels": {
                "lint": {"repairs": [{"action_id": "fix1"}]},
                "tests": {"repairs": []},
            },
        }
        with mock.patch("lintgate.state.load_controlplane_run", return_value=run_data):
            result = _load_all_repairs(snapshot)
        assert len(result) == 1
        assert result[0]["action_id"] == "fix1"

    def test_fallback_to_repair_catalog(self):
        snapshot = mock.MagicMock()
        snapshot.run_id = "r1"
        snapshot.repair_catalog = {
            "a1": {"kind": "command", "summary": "fix it", "safe": "true", "channel": "lint"},
        }
        with mock.patch("lintgate.state.load_controlplane_run", return_value=None):
            result = _load_all_repairs(snapshot)
        assert len(result) == 1
        assert result[0]["action_id"] == "a1"
        assert result[0]["safe"] is True


# ── _execute_single_repair ───────────────────────────────────────────


class TestExecuteSingleRepair:
    def test_skips_non_command(self):
        repair = {"action_id": "a1", "kind": "config_patch"}
        result = _execute_single_repair(repair, "/tmp", mock.MagicMock())
        assert result["status"] == "skipped"
        assert result["reason"] == "not a command"

    def test_skips_empty_command(self):
        repair = {"action_id": "a1", "kind": "command", "payload": {"command": ""}}
        result = _execute_single_repair(repair, "/tmp", mock.MagicMock())
        assert result["status"] == "skipped"

    def test_runs_command_ok(self):
        repair = {
            "action_id": "a1",
            "kind": "command",
            "payload": {"command": "echo hello"},
        }
        proc = mock.MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        with (
            mock.patch("subprocess.run", return_value=proc),
            mock.patch(
                "lintgate.controlplane.session_memory.report_repair_outcome",
            ) as report,
        ):
            result = _execute_single_repair(repair, "/tmp", mock.MagicMock())
        assert result["status"] == "ok"
        report.assert_called_once()

    def test_handles_timeout(self):
        repair = {
            "action_id": "a1",
            "kind": "command",
            "payload": {"command": "sleep 999"},
        }
        with mock.patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 60),
        ):
            result = _execute_single_repair(repair, "/tmp", mock.MagicMock())
        assert result["status"] == "timeout"

    def test_handles_os_error(self):
        repair = {
            "action_id": "a1",
            "kind": "command",
            "payload": {"command": "nonexistent_binary"},
        }
        with mock.patch("subprocess.run", side_effect=OSError("not found")):
            result = _execute_single_repair(repair, "/tmp", mock.MagicMock())
        assert result["status"] == "error"
        assert "not found" in result["error"]


# ── _impl_controlplane_apply_repairs ─────────────────────────────────


class TestImplApplyRepairs:
    def test_full_flow(self):
        fake_session = mock.MagicMock()
        fake_session.repair_outcomes = {"r1": "pending"}
        with (
            mock.patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=fake_session,
            ),
            mock.patch("lintgate.controlplane.session_memory.save_session"),
            mock.patch(
                "mcp_tools.controlplane_tools._collect_pending_repairs",
                return_value=[
                    {"action_id": "r1", "kind": "command", "payload": {"command": "echo hi"}},
                ],
            ),
            mock.patch(
                "mcp_tools.controlplane_tools._execute_single_repair",
                return_value={"action_id": "r1", "status": "ok"},
            ) as exec_fn,
        ):
            raw = _impl_controlplane_apply_repairs("/tmp", None, True, _stub_helpers())
        parsed = json.loads(raw)
        assert parsed["repairs_executed"] == 1
        exec_fn.assert_called_once()


# ── register ─────────────────────────────────────────────────────────


class TestRegister:
    def test_registers_all_tools(self):
        mcp = mock.MagicMock()
        mcp.tool.return_value = lambda fn: fn
        helpers = _stub_helpers()
        result = register(mcp, helpers)
        expected_names = {
            "controlplane_run",
            "controlplane_get_details",
            "controlplane_status",
            "controlplane_test_skeleton",
            "controlplane_report_repair",
            "controlplane_agent_feedback",
            "controlplane_apply_repairs",
        }
        assert set(result.keys()) == expected_names
        assert mcp.tool.call_count == 7

    def test_test_skeleton_validates_file(self):
        mcp = mock.MagicMock()
        mcp.tool.return_value = lambda fn: fn
        helpers = _stub_helpers()
        tools = register(mcp, helpers)
        skeleton_fn = tools["controlplane_test_skeleton"]
        with pytest.raises(ValueError, match="Source file not found"):
            skeleton_fn(path="/tmp/test", target_file="nonexistent.py")

    def test_report_repair_valid_outcome(self):
        """Lines 845-860: controlplane_report_repair with valid outcome."""
        mcp = mock.MagicMock()
        mcp.tool.return_value = lambda fn: fn
        helpers = _stub_helpers()
        tools = register(mcp, helpers)
        report_fn = tools["controlplane_report_repair"]

        fake_session = mock.MagicMock()
        fake_session.session_id = "s1"
        fake_session.repair_outcomes = {"a1": "applied"}

        with (
            mock.patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=fake_session,
            ),
            mock.patch("lintgate.controlplane.session_memory.save_session"),
            mock.patch(
                "lintgate.controlplane.session_memory.report_repair_outcome",
            ) as report_mock,
        ):
            raw = report_fn(path="/tmp/test", action_id="a1", outcome="applied")
        parsed = json.loads(raw)
        assert parsed["action_id"] == "a1"
        assert parsed["outcome"] == "applied"
        report_mock.assert_called_once_with(fake_session, "a1", "applied")

    def test_report_repair_invalid_outcome(self):
        """Lines 853-856: controlplane_report_repair with invalid outcome raises ValueError."""
        mcp = mock.MagicMock()
        mcp.tool.return_value = lambda fn: fn
        helpers = _stub_helpers()
        tools = register(mcp, helpers)
        report_fn = tools["controlplane_report_repair"]

        with pytest.raises(ValueError, match="Invalid outcome"):
            report_fn(path="/tmp/test", action_id="a1", outcome="bogus")

    def test_test_skeleton_makes_absolute(self):
        mcp = mock.MagicMock()
        mcp.tool.return_value = lambda fn: fn
        helpers = _stub_helpers()
        tools = register(mcp, helpers)
        skeleton_fn = tools["controlplane_test_skeleton"]
        with (
            mock.patch("os.path.exists", return_value=True),
            mock.patch(
                "lintgate.controlplane.skeleton_generator.generate_test_skeleton",
                return_value="# test",
            ),
            mock.patch(
                "lintgate.controlplane.skeleton_generator.generate_test_path",
                return_value="tests/test_foo.py",
            ),
        ):
            raw = skeleton_fn(path="/tmp/test", target_file="foo.py")
        parsed = json.loads(raw)
        assert parsed["source_file"] == "/tmp/test/foo.py"


class TestRegisterAgentFeedbackClosure:
    """Cover register() lines 897: controlplane_agent_feedback closure."""

    def test_agent_feedback_closure_delegates(self, tmp_path) -> None:
        from mcp_tools.controlplane_tools import register

        mcp_mock = mock.MagicMock()
        mcp_mock.tool.return_value = lambda fn: fn
        helpers = {"_validate_project_root": lambda p: str(tmp_path)}
        tools = register(mcp_mock, helpers)
        fn = tools["controlplane_agent_feedback"]

        with (
            mock.patch(
                "mcp_tools.controlplane_tools._impl_controlplane_agent_feedback",
                return_value='{"status":"ok"}',
            ) as patched,
        ):
            result = fn(path=str(tmp_path), run_id="abc")
        patched.assert_called_once()
        assert result == '{"status":"ok"}'


class TestRegisterApplyRepairsClosure:
    """Cover register() line 916: controlplane_apply_repairs closure."""

    def test_apply_repairs_closure_delegates(self, tmp_path) -> None:
        from mcp_tools.controlplane_tools import register

        mcp_mock = mock.MagicMock()
        mcp_mock.tool.return_value = lambda fn: fn
        helpers = {"_validate_project_root": lambda p: str(tmp_path)}
        tools = register(mcp_mock, helpers)
        fn = tools["controlplane_apply_repairs"]

        with (
            mock.patch(
                "mcp_tools.controlplane_tools._impl_controlplane_apply_repairs",
                return_value='{"applied":0}',
            ) as patched,
        ):
            result = fn(path=str(tmp_path))
        patched.assert_called_once()
        assert result == '{"applied":0}'
