"""Tests for mcp_tools/_controlplane_impl_feedback.py.

Covers all 13 functions with exact-value assertions and minimal mocking.
Lazy imports inside function bodies are patched at the source module.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

from mcp_tools._controlplane_impl_feedback import (
    _build_feedback_result,
    _collect_pending_repairs,
    _execute_safe_delete,
    _execute_single_repair,
    _generate_living_context_patches,
    _impl_controlplane_agent_feedback,
    _impl_controlplane_apply_repairs,
    _load_all_repairs,
    _process_accepted_constraints,
    _process_rejected_constraints,
    _process_test_failure_classifications,
    _process_tuned_findings,
    _record_disagreement,
)


def _load_tool_result(json_str):
    import json as _j
    import os as _os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and _os.path.isfile(r.get("file","")):
        with open(r["file"]) as f:

            return _j.loads(f.read())
    return r


# ── Lightweight session stand-in ─────────────────────────────────────────


@dataclass
class _FakeSnapshot:
    run_id: str = "run-1"
    repairs_proposed: list[str] = field(default_factory=list)
    repair_catalog: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class _FakeSession:
    session_id: str = "sess-abc"
    agent_disagreements: list[dict[str, Any]] = field(default_factory=list)
    proposed_constraints: list[dict[str, Any]] = field(default_factory=list)
    pending_patches: list[dict[str, Any]] = field(default_factory=list)
    snapshots: list[_FakeSnapshot] = field(default_factory=list)
    repair_outcomes: dict[str, str] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# 1. _record_disagreement
# ═══════════════════════════════════════════════════════════════════════════


class TestRecordDisagreement:
    def test_appends_disagreement_to_session(self):
        session = _FakeSession()
        actions: list[str] = []
        _record_disagreement(session, "run-42", "lint is wrong", actions)

        assert len(session.agent_disagreements) == 1
        entry = session.agent_disagreements[0]
        assert entry["run_id"] == "run-42"
        assert entry["disagreement"] == "lint is wrong"
        assert isinstance(entry["timestamp"], float)

    def test_uses_unknown_when_run_id_is_none(self):
        session = _FakeSession()
        actions: list[str] = []
        _record_disagreement(session, None, "bad finding", actions)
        assert session.agent_disagreements[0]["run_id"] == "unknown"

    def test_action_truncates_at_100_chars(self):
        session = _FakeSession()
        actions: list[str] = []
        long_msg = "x" * 200
        _record_disagreement(session, "r1", long_msg, actions)
        assert len(actions) == 1
        # The action message includes prefix "Recorded disagreement: " + first 100 chars
        assert actions[0] == f"Recorded disagreement: {'x' * 100}"

    def test_multiple_calls_accumulate(self):
        session = _FakeSession()
        actions: list[str] = []
        _record_disagreement(session, "r1", "first", actions)
        _record_disagreement(session, "r2", "second", actions)
        assert len(session.agent_disagreements) == 2
        assert len(actions) == 2


# ═══════════════════════════════════════════════════════════════════════════
# 2. _process_accepted_constraints
# ═══════════════════════════════════════════════════════════════════════════


class TestProcessAcceptedConstraints:
    @patch("lintgate.controlplane.constraint_proposer.update_constraint_status")
    def test_returns_accepted_rules(self, mock_update):
        mock_update.return_value = True
        session = _FakeSession(
            proposed_constraints=[
                {"pattern_key": "ruff|F821", "status": "accepted", "proposed_rule": "no F821"},
            ]
        )
        actions: list[str] = []
        rules = _process_accepted_constraints(session, ["ruff|F821"], actions)
        assert rules == ["no F821"]
        assert "Accepted constraint: ruff|F821" in actions

    @patch("lintgate.controlplane.constraint_proposer.update_constraint_status")
    def test_not_found_constraint(self, mock_update):
        mock_update.return_value = False
        session = _FakeSession()
        actions: list[str] = []
        rules = _process_accepted_constraints(session, ["ruff|FAKE"], actions)
        assert rules == []
        assert "Constraint not found: ruff|FAKE" in actions

    @patch("lintgate.controlplane.constraint_proposer.update_constraint_status")
    def test_none_accepted_constraints(self, mock_update):
        session = _FakeSession()
        actions: list[str] = []
        rules = _process_accepted_constraints(session, None, actions)
        assert rules == []
        mock_update.assert_not_called()

    @patch("lintgate.controlplane.constraint_proposer.update_constraint_status")
    def test_accepted_but_no_rule_text(self, mock_update):
        mock_update.return_value = True
        session = _FakeSession(
            proposed_constraints=[
                {"pattern_key": "mypy|E111", "status": "accepted", "proposed_rule": ""},
            ]
        )
        actions: list[str] = []
        rules = _process_accepted_constraints(session, ["mypy|E111"], actions)
        # Empty rule_text should not be appended
        assert rules == []


# ═══════════════════════════════════════════════════════════════════════════
# 3. _process_rejected_constraints
# ═══════════════════════════════════════════════════════════════════════════


class TestProcessRejectedConstraints:
    @patch("lintgate.controlplane.constraint_proposer.update_constraint_status")
    def test_rejects_constraints(self, mock_update):
        mock_update.return_value = True
        session = _FakeSession()
        actions: list[str] = []
        _process_rejected_constraints(session, ["ruff|F821"], actions)
        assert actions == ["Rejected constraint: ruff|F821"]

    @patch("lintgate.controlplane.constraint_proposer.update_constraint_status")
    def test_not_found_rejection(self, mock_update):
        mock_update.return_value = False
        session = _FakeSession()
        actions: list[str] = []
        _process_rejected_constraints(session, ["nope"], actions)
        assert actions == ["Constraint not found: nope"]

    @patch("lintgate.controlplane.constraint_proposer.update_constraint_status")
    def test_none_rejected(self, mock_update):
        session = _FakeSession()
        actions: list[str] = []
        _process_rejected_constraints(session, None, actions)
        assert actions == []
        mock_update.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# 4. _generate_living_context_patches
# ═══════════════════════════════════════════════════════════════════════════


class TestGenerateLivingContextPatches:
    @patch("lintgate.context_bootstrap.generate_context_patch")
    @patch("lintgate.config.load_controlplane_config")
    def test_generates_patches_when_enabled(self, mock_config, mock_gen_patch):
        cp = MagicMock()
        cp.inquiry.living_context = True
        mock_config.return_value = cp

        fake_patch = MagicMock()
        fake_patch.patch_id = "patch-001"
        fake_patch.to_dict.return_value = {"id": "patch-001"}
        mock_gen_patch.return_value = fake_patch

        session = _FakeSession()
        actions: list[str] = []
        _generate_living_context_patches(session, "/proj", ["rule-text"], actions)

        assert len(session.pending_patches) == 1
        assert session.pending_patches[0] == {"id": "patch-001"}
        assert "Generated context patch: patch-001" in actions

    @patch("lintgate.config.load_controlplane_config")
    def test_skips_when_living_context_disabled(self, mock_config):
        cp = MagicMock()
        cp.inquiry.living_context = False
        mock_config.return_value = cp

        session = _FakeSession()
        actions: list[str] = []
        _generate_living_context_patches(session, "/proj", ["rule"], actions)
        assert session.pending_patches == []
        assert actions == []

    @patch("lintgate.config.load_controlplane_config")
    def test_skips_when_no_accepted_rules(self, mock_config):
        cp = MagicMock()
        cp.inquiry.living_context = True
        mock_config.return_value = cp

        session = _FakeSession()
        actions: list[str] = []
        _generate_living_context_patches(session, "/proj", [], actions)
        assert session.pending_patches == []

    @patch("lintgate.context_bootstrap.generate_context_patch")
    @patch("lintgate.config.load_controlplane_config")
    def test_skips_when_patch_is_none(self, mock_config, mock_gen_patch):
        cp = MagicMock()
        cp.inquiry.living_context = True
        mock_config.return_value = cp
        mock_gen_patch.return_value = None

        session = _FakeSession()
        actions: list[str] = []
        _generate_living_context_patches(session, "/proj", ["rule"], actions)
        assert session.pending_patches == []
        assert actions == []


# ═══════════════════════════════════════════════════════════════════════════
# 5. _build_feedback_result
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildFeedbackResult:
    def test_basic_result(self):
        session = _FakeSession(
            session_id="abc",
            agent_disagreements=[{"x": 1}],
            proposed_constraints=[
                {"status": "proposed"},
                {"status": "accepted"},
                {"status": "proposed"},
            ],
        )
        result = _build_feedback_result(session, ["action1"], [], [])
        assert result["session_id"] == "abc"
        assert result["actions_taken"] == ["action1"]
        assert result["total_disagreements"] == 1
        assert result["proposed_constraints"] == 3
        assert result["active_proposals"] == 2
        assert "tuned" not in result
        assert "rejected_tunings" not in result

    def test_includes_tuned_when_present(self):
        session = _FakeSession()
        result = _build_feedback_result(session, [], ["sig1"], [])
        assert result["tuned"] == ["sig1"]

    def test_includes_rejected_tunings_when_present(self):
        session = _FakeSession()
        rejected = [{"signature": "s", "reason": "bad"}]
        result = _build_feedback_result(session, [], [], rejected)
        assert result["rejected_tunings"] == rejected

    def test_empty_session(self):
        session = _FakeSession()
        result = _build_feedback_result(session, [], [], [])
        assert result["total_disagreements"] == 0
        assert result["proposed_constraints"] == 0
        assert result["active_proposals"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# 6. _process_tuned_findings
# ═══════════════════════════════════════════════════════════════════════════


class TestProcessTunedFindings:
    @patch("lintgate.signal_tunings.apply_tuning")
    @patch("lintgate.signal_tunings.VALID_ACTIONS", {"suppress", "downgrade", "reset"})
    def test_successful_tuning(self, mock_apply):
        mock_apply.return_value = {}
        actions: list[str] = []
        tuned, rejected = _process_tuned_findings(
            [{"signature": "SIG1", "action": "suppress", "rationale": "noisy"}],
            "/proj",
            actions,
        )
        assert tuned == ["SIG1"]
        assert rejected == []
        assert "Tuned finding: SIG1 (suppress)" in actions

    @patch("lintgate.signal_tunings.VALID_ACTIONS", {"suppress", "downgrade", "reset"})
    def test_missing_signature_rejected(self):
        actions: list[str] = []
        tuned, rejected = _process_tuned_findings(
            [{"signature": "", "action": "suppress", "rationale": "x"}],
            "/proj",
            actions,
        )
        assert tuned == []
        assert len(rejected) == 1
        assert rejected[0]["reason"] == "missing signature"

    @patch("lintgate.signal_tunings.VALID_ACTIONS", {"suppress", "downgrade", "reset"})
    def test_invalid_action_rejected(self):
        actions: list[str] = []
        tuned, rejected = _process_tuned_findings(
            [{"signature": "SIG1", "action": "nuke", "rationale": "x"}],
            "/proj",
            actions,
        )
        assert tuned == []
        assert rejected[0]["reason"] == "invalid action: nuke"

    @patch("lintgate.signal_tunings.VALID_ACTIONS", {"suppress", "downgrade", "reset"})
    def test_rationale_required_for_non_reset(self):
        actions: list[str] = []
        tuned, rejected = _process_tuned_findings(
            [{"signature": "SIG1", "action": "suppress", "rationale": ""}],
            "/proj",
            actions,
        )
        assert rejected[0]["reason"] == "rationale required for tuning"

    @patch("lintgate.signal_tunings.apply_tuning")
    @patch("lintgate.signal_tunings.VALID_ACTIONS", {"suppress", "downgrade", "reset"})
    def test_reset_without_rationale_succeeds(self, mock_apply):
        mock_apply.return_value = {}
        actions: list[str] = []
        tuned, rejected = _process_tuned_findings(
            [{"signature": "SIG1", "action": "reset", "rationale": ""}],
            "/proj",
            actions,
        )
        assert tuned == ["SIG1"]
        assert rejected == []

    @patch("lintgate.signal_tunings.apply_tuning")
    @patch("lintgate.signal_tunings.VALID_ACTIONS", {"suppress", "downgrade", "reset"})
    def test_apply_error_rejected(self, mock_apply):
        mock_apply.return_value = {"error": "boom"}
        actions: list[str] = []
        tuned, rejected = _process_tuned_findings(
            [{"signature": "SIG1", "action": "suppress", "rationale": "reason"}],
            "/proj",
            actions,
        )
        assert tuned == []
        assert rejected[0]["reason"] == "boom"


# ═══════════════════════════════════════════════════════════════════════════
# 7. _process_test_failure_classifications
# ═══════════════════════════════════════════════════════════════════════════


class TestProcessTestFailureClassifications:
    @patch("lintgate.controlplane.session_memory.record_test_failure_classification")
    def test_valid_classification(self, mock_record):
        session = _FakeSession()
        actions: list[str] = []
        _process_test_failure_classifications(
            [{"fingerprint": "fp1", "classification": "flaky", "rationale": "intermittent"}],
            session,
            actions,
        )
        mock_record.assert_called_once_with(session, "fp1", "flaky", "intermittent")
        assert actions == ["Classified test failure fp1 as flaky"]

    @patch("lintgate.controlplane.session_memory.record_test_failure_classification")
    def test_invalid_classification_rejected(self, mock_record):
        session = _FakeSession()
        actions: list[str] = []
        _process_test_failure_classifications(
            [{"fingerprint": "fp1", "classification": "invalid_type", "rationale": "x"}],
            session,
            actions,
        )
        mock_record.assert_not_called()
        assert "Rejected classification for fp1: invalid type 'invalid_type'" in actions[0]

    @patch("lintgate.controlplane.session_memory.record_test_failure_classification")
    def test_empty_fingerprint_skipped(self, mock_record):
        session = _FakeSession()
        actions: list[str] = []
        _process_test_failure_classifications(
            [{"fingerprint": "", "classification": "flaky", "rationale": "x"}],
            session,
            actions,
        )
        mock_record.assert_not_called()
        assert actions == []

    @patch("lintgate.controlplane.session_memory.record_test_failure_classification")
    def test_all_valid_types(self, mock_record):
        session = _FakeSession()
        actions: list[str] = []
        valid_types = ["stale_test", "known_regression", "flaky", "out_of_scope"]
        entries = [
            {"fingerprint": f"fp-{t}", "classification": t, "rationale": ""} for t in valid_types
        ]
        _process_test_failure_classifications(entries, session, actions)
        assert mock_record.call_count == 4
        assert len(actions) == 4


# ═══════════════════════════════════════════════════════════════════════════
# 8. _load_all_repairs
# ═══════════════════════════════════════════════════════════════════════════


class TestLoadAllRepairs:
    @patch("lintgate.state.load_controlplane_run")
    def test_loads_from_run_details(self, mock_load):
        mock_load.return_value = {
            "channels": {
                "lint": {"repairs": [{"action_id": "a1"}]},
                "test": {"repairs": [{"action_id": "a2"}]},
            }
        }
        snapshot = _FakeSnapshot(run_id="run-1")
        repairs = _load_all_repairs(snapshot)
        assert len(repairs) == 2
        assert repairs[0]["action_id"] == "a1"
        assert repairs[1]["action_id"] == "a2"

    @patch("lintgate.state.load_controlplane_run")
    def test_fallback_to_repair_catalog(self, mock_load):
        mock_load.return_value = None
        snapshot = _FakeSnapshot(
            run_id="run-1",
            repair_catalog={
                "aid-1": {
                    "kind": "command",
                    "summary": "fix it",
                    "safe": "true",
                    "channel": "lint",
                },
            },
        )
        repairs = _load_all_repairs(snapshot)
        assert len(repairs) == 1
        assert repairs[0]["action_id"] == "aid-1"
        assert repairs[0]["kind"] == "command"
        assert repairs[0]["safe"] is True

    @patch("lintgate.state.load_controlplane_run")
    def test_fallback_catalog_preserves_payload(self, mock_load):
        mock_load.return_value = None
        snapshot = _FakeSnapshot(
            run_id="run-1",
            repair_catalog={
                "aid-1": {
                    "kind": "command",
                    "summary": "fix it",
                    "safe": "true",
                    "channel": "lint",
                    "payload": {"command": "ruff check --fix"},
                },
            },
        )
        repairs = _load_all_repairs(snapshot)
        assert repairs[0]["payload"] == {"command": "ruff check --fix"}

    @patch("lintgate.state.load_controlplane_run")
    def test_empty_run_id(self, mock_load):
        snapshot = _FakeSnapshot(run_id="")
        repairs = _load_all_repairs(snapshot)
        assert repairs == []
        mock_load.assert_not_called()

    @patch("lintgate.state.load_controlplane_run")
    def test_catalog_safe_false(self, mock_load):
        mock_load.return_value = None
        snapshot = _FakeSnapshot(
            run_id="run-1",
            repair_catalog={
                "aid-1": {"kind": "command", "summary": "risky", "safe": "false"},
            },
        )
        repairs = _load_all_repairs(snapshot)
        assert repairs[0]["safe"] is False


# ═══════════════════════════════════════════════════════════════════════════
# 9. _collect_pending_repairs
# ═══════════════════════════════════════════════════════════════════════════


class TestCollectPendingRepairs:
    def test_no_snapshots(self):
        session = _FakeSession()
        pending, skipped = _collect_pending_repairs(session, None, False)
        assert pending == []
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "no_snapshots"

    @patch(
        "mcp_tools._controlplane_impl_feedback._load_all_repairs",
        return_value=[],
    )
    def test_no_repairs_in_run(self, mock_load):
        session = _FakeSession(snapshots=[_FakeSnapshot(repairs_proposed=["a1"])])
        pending, skipped = _collect_pending_repairs(session, None, False)
        assert pending == []
        assert skipped[0]["reason"] == "no_repairs_in_run"

    @patch(
        "mcp_tools._controlplane_impl_feedback._load_all_repairs",
        return_value=[{"action_id": "a1"}],
    )
    def test_no_proposed_repairs(self, mock_load):
        session = _FakeSession(snapshots=[_FakeSnapshot(repairs_proposed=[])])
        pending, skipped = _collect_pending_repairs(session, None, False)
        assert pending == []
        assert skipped[0]["reason"] == "no_proposed_repairs"

    @patch(
        "mcp_tools._controlplane_impl_feedback._load_all_repairs",
        return_value=[{"action_id": "a1"}],
    )
    def test_already_executed_skipped(self, mock_load):
        session = _FakeSession(
            snapshots=[_FakeSnapshot(repairs_proposed=["a1"])],
            repair_outcomes={"a1": "applied"},
        )
        pending, skipped = _collect_pending_repairs(session, None, False)
        assert pending == []
        assert skipped[0]["reason"] == "already_executed"
        assert "applied" in skipped[0]["detail"]

    @patch(
        "mcp_tools._controlplane_impl_feedback._load_all_repairs",
        return_value=[{"action_id": "a1"}, {"action_id": "a2"}],
    )
    def test_action_ids_filter(self, mock_load):
        session = _FakeSession(snapshots=[_FakeSnapshot(repairs_proposed=["a1", "a2"])])
        pending, skipped = _collect_pending_repairs(session, ["a1"], False)
        assert len(pending) == 1
        assert pending[0]["action_id"] == "a1"
        assert any(s["reason"] == "not_in_action_ids" for s in skipped)

    @patch(
        "mcp_tools._controlplane_impl_feedback._load_all_repairs",
        return_value=[{"action_id": "a1", "safe": False, "kind": "dangerous"}],
    )
    def test_safe_only_filter(self, mock_load):
        session = _FakeSession(snapshots=[_FakeSnapshot(repairs_proposed=["a1"])])
        pending, skipped = _collect_pending_repairs(session, None, True)
        assert pending == []
        assert skipped[0]["reason"] == "safe_only_filter"
        assert "dangerous" in skipped[0]["detail"]

    @patch(
        "mcp_tools._controlplane_impl_feedback._load_all_repairs",
        return_value=[{"action_id": "a1", "safe": True}],
    )
    def test_collects_pending_repair(self, mock_load):
        session = _FakeSession(snapshots=[_FakeSnapshot(repairs_proposed=["a1"])])
        pending, skipped = _collect_pending_repairs(session, None, False)
        assert len(pending) == 1
        assert pending[0]["action_id"] == "a1"
        assert skipped == []

    @patch("lintgate.state.load_controlplane_run")
    def test_run_id_uses_persisted_run_when_snapshot_missing(self, mock_load):
        mock_load.return_value = {
            "channels": {
                "lint": {
                    "repairs": [{"action_id": "a1", "safe": True, "kind": "command"}],
                }
            }
        }
        session = _FakeSession()
        pending, skipped = _collect_pending_repairs(session, None, False, run_id="run-9")
        assert skipped == []
        assert len(pending) == 1
        assert pending[0]["action_id"] == "a1"


# ═══════════════════════════════════════════════════════════════════════════
# 10. _execute_single_repair
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteSingleRepair:
    @patch("mcp_tools._controlplane_impl_feedback._execute_safe_delete")
    def test_dispatches_safe_delete(self, mock_delete):
        mock_delete.return_value = {"action_id": "a1", "status": "ok"}
        repair = {"action_id": "a1", "kind": "safe_delete"}
        result = _execute_single_repair(repair, "/proj", _FakeSession())
        assert result["status"] == "ok"
        mock_delete.assert_called_once()

    def test_skips_non_command_kind(self):
        repair = {"action_id": "a1", "kind": "manual"}
        result = _execute_single_repair(repair, "/proj", _FakeSession())
        assert result["status"] == "skipped"
        assert result["reason"] == "not a command"

    def test_skips_empty_command(self):
        repair = {"action_id": "a1", "kind": "command", "payload": {"command": ""}}
        result = _execute_single_repair(repair, "/proj", _FakeSession())
        assert result["status"] == "skipped"
        assert result["reason"] == "empty command"

    @patch("lintgate.controlplane.session_memory.report_repair_outcome")
    @patch("subprocess.run")
    def test_successful_command(self, mock_run, mock_report):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        repair = {
            "action_id": "a1",
            "kind": "command",
            "payload": {"command": "echo hello"},
        }
        session = _FakeSession()
        result = _execute_single_repair(repair, "/proj", session)
        assert result["status"] == "ok"
        assert result["returncode"] == 0
        assert result["command"] == "echo hello"
        mock_report.assert_called_once_with(session, "a1", "applied")

    @patch("lintgate.controlplane.session_memory.report_repair_outcome")
    @patch("subprocess.run")
    def test_failed_command(self, mock_run, mock_report):
        mock_run.return_value = MagicMock(returncode=1, stderr="fail msg")
        repair = {
            "action_id": "a1",
            "kind": "command",
            "payload": {"command": "false"},
        }
        session = _FakeSession()
        result = _execute_single_repair(repair, "/proj", session)
        assert result["status"] == "error"
        assert result["returncode"] == 1
        mock_report.assert_called_once_with(session, "a1", "ignored")

    @patch("subprocess.run", side_effect=OSError("no such file"))
    def test_oserror(self, mock_run):
        repair = {
            "action_id": "a1",
            "kind": "command",
            "payload": {"command": "nonexistent"},
        }
        result = _execute_single_repair(repair, "/proj", _FakeSession())
        assert result["status"] == "error"
        assert result["error"] == "no such file"

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 60))
    def test_timeout(self, mock_run):
        repair = {
            "action_id": "a1",
            "kind": "command",
            "payload": {"command": "sleep 999"},
        }
        result = _execute_single_repair(repair, "/proj", _FakeSession())
        assert result["status"] == "timeout"
        assert result["command"] == "sleep 999"


# ═══════════════════════════════════════════════════════════════════════════
# 11. _execute_safe_delete
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteSafeDelete:
    def test_no_target_path(self):
        repair = {"action_id": "a1", "payload": {}}
        result = _execute_safe_delete(repair, "/proj", _FakeSession())
        assert result["status"] == "skipped"
        assert result["reason"] == "no target_path"

    def test_outside_project_root(self):
        repair = {"action_id": "a1", "payload": {"target_path": "/etc/passwd"}}
        result = _execute_safe_delete(repair, "/proj", _FakeSession())
        assert result["status"] == "blocked"
        assert result["reason"] == "target outside project root"

    def test_not_in_test_directory(self, tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        target = src_dir / "module.py"
        target.write_text("x = 1")

        repair = {"action_id": "a1", "payload": {"target_path": str(target)}}
        result = _execute_safe_delete(repair, str(tmp_path), _FakeSession())
        assert result["status"] == "blocked"
        assert result["reason"] == "target not in test directory"

    def test_file_already_deleted(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        target = tests_dir / "test_gone.py"
        # Do NOT create the file

        repair = {"action_id": "a1", "payload": {"target_path": str(target)}}
        result = _execute_safe_delete(repair, str(tmp_path), _FakeSession())
        assert result["status"] == "skipped"
        assert result["reason"] == "file already deleted"

    @patch("lintgate.controlplane.session_memory.report_repair_outcome")
    def test_successful_delete(self, mock_report, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        target = tests_dir / "test_old.py"
        target.write_text("# old test")

        session = _FakeSession()
        repair = {"action_id": "a1", "payload": {"target_path": str(target)}}
        result = _execute_safe_delete(repair, str(tmp_path), session)
        assert result["status"] == "ok"
        assert result["deleted"] == os.path.join("tests", "test_old.py")
        assert not target.exists()
        mock_report.assert_called_once_with(session, "a1", "applied")


# ═══════════════════════════════════════════════════════════════════════════
# 12. _impl_controlplane_agent_feedback
# ═══════════════════════════════════════════════════════════════════════════


class TestImplControlplaneAgentFeedback:
    @patch("lintgate.controlplane.session_memory.save_session")
    @patch("lintgate.controlplane.session_memory.get_or_create_session")
    def test_basic_feedback_flow(self, mock_get_session, mock_save):
        session = _FakeSession()
        mock_get_session.return_value = session
        helpers = {"_validate_project_root": lambda p: "/proj"}

        result_str = _impl_controlplane_agent_feedback(
            path="/proj",
            run_id="run-1",
            disagreement="bad lint",
            accepted_constraints=None,
            rejected_constraints=None,
            helpers=helpers,
        )
        result = _load_tool_result(result_str)
        assert result["session_id"] == "sess-abc"
        assert result["total_disagreements"] == 1
        assert any("bad lint" in a for a in result["actions_taken"])
        mock_save.assert_called_once_with(session)

    @patch("mcp_tools._controlplane_impl_feedback._process_tuned_findings")
    @patch("lintgate.controlplane.session_memory.save_session")
    @patch("lintgate.controlplane.session_memory.get_or_create_session")
    def test_with_tuned_findings(self, mock_get_session, mock_save, mock_tune):
        session = _FakeSession()
        mock_get_session.return_value = session
        mock_tune.return_value = (["SIG1"], [])
        helpers = {"_validate_project_root": lambda p: "/proj"}

        result_str = _impl_controlplane_agent_feedback(
            path="/proj",
            run_id=None,
            disagreement=None,
            accepted_constraints=None,
            rejected_constraints=None,
            helpers=helpers,
            tuned_findings=[{"signature": "SIG1", "action": "suppress", "rationale": "noisy"}],
        )
        result = _load_tool_result(result_str)
        assert result["tuned"] == ["SIG1"]

    @patch("mcp_tools._controlplane_impl_feedback._process_test_failure_classifications")
    @patch("lintgate.controlplane.session_memory.save_session")
    @patch("lintgate.controlplane.session_memory.get_or_create_session")
    def test_with_test_classifications(self, mock_get_session, mock_save, mock_classify):
        session = _FakeSession()
        mock_get_session.return_value = session
        helpers = {"_validate_project_root": lambda p: "/proj"}

        _impl_controlplane_agent_feedback(
            path="/proj",
            run_id=None,
            disagreement=None,
            accepted_constraints=None,
            rejected_constraints=None,
            helpers=helpers,
            test_failure_classifications=[{"fingerprint": "fp1", "classification": "flaky"}],
        )
        mock_classify.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
# 13. _impl_controlplane_apply_repairs
# ═══════════════════════════════════════════════════════════════════════════


class TestImplControlplaneApplyRepairs:
    @patch("lintgate.controlplane.session_memory.save_session")
    @patch("mcp_tools._controlplane_impl_feedback._execute_single_repair")
    @patch("mcp_tools._controlplane_impl_feedback._collect_pending_repairs")
    @patch("lintgate.controlplane.session_memory.get_or_create_session")
    def test_no_pending_repairs(self, mock_get_session, mock_collect, mock_exec, mock_save):
        session = _FakeSession()
        mock_get_session.return_value = session
        mock_collect.return_value = (
            [],
            [{"reason": "no_snapshots", "detail": "No snapshots"}],
        )
        helpers = {"_validate_project_root": lambda p: "/proj"}

        result_str = _impl_controlplane_apply_repairs("/proj", None, False, helpers)
        result = _load_tool_result(result_str)
        assert result["summary"]["collected"] == 0
        assert result["summary"]["succeeded"] == 0
        assert result["summary"]["skipped"] == 1
        assert result["repairs_executed"] == 0
        assert result["skipped"][0]["reason"] == "no_snapshots"
        mock_exec.assert_not_called()

    @patch("lintgate.controlplane.session_memory.save_session")
    @patch("mcp_tools._controlplane_impl_feedback._execute_single_repair")
    @patch("mcp_tools._controlplane_impl_feedback._collect_pending_repairs")
    @patch("lintgate.controlplane.session_memory.get_or_create_session")
    def test_successful_repair(self, mock_get_session, mock_collect, mock_exec, mock_save):
        session = _FakeSession()
        mock_get_session.return_value = session
        mock_collect.return_value = ([{"action_id": "a1"}], [])
        mock_exec.return_value = {"action_id": "a1", "status": "ok"}
        helpers = {"_validate_project_root": lambda p: "/proj"}

        result_str = _impl_controlplane_apply_repairs("/proj", None, False, helpers)
        result = _load_tool_result(result_str)
        assert result["summary"]["succeeded"] == 1
        assert result["summary"]["failed"] == 0
        assert result["summary"]["collected"] == 1
        assert result["repairs_executed"] == 1
        assert result["results"][0]["status"] == "ok"

    @patch("lintgate.controlplane.session_memory.save_session")
    @patch("mcp_tools._controlplane_impl_feedback._execute_single_repair")
    @patch("mcp_tools._controlplane_impl_feedback._collect_pending_repairs")
    @patch("lintgate.controlplane.session_memory.get_or_create_session")
    def test_mixed_results(self, mock_get_session, mock_collect, mock_exec, mock_save):
        session = _FakeSession()
        mock_get_session.return_value = session
        mock_collect.return_value = (
            [{"action_id": "a1"}, {"action_id": "a2"}, {"action_id": "a3"}],
            [{"reason": "already_executed", "action_id": "a0"}],
        )
        mock_exec.side_effect = [
            {"action_id": "a1", "status": "ok"},
            {"action_id": "a2", "status": "error"},
            {"action_id": "a3", "status": "skipped", "reason": "not a command"},
        ]
        helpers = {"_validate_project_root": lambda p: "/proj"}

        result_str = _impl_controlplane_apply_repairs("/proj", None, False, helpers)
        result = _load_tool_result(result_str)
        assert result["summary"]["total_proposed"] == 4
        assert result["summary"]["succeeded"] == 1
        assert result["summary"]["failed"] == 1
        assert result["summary"]["skipped"] == 2
        assert "skipped_by_reason" in result["summary"]
        assert result["summary"]["skipped_by_reason"]["already_executed"] == 1
        assert result["summary"]["skipped_by_reason"]["not a command"] == 1

    @patch("lintgate.controlplane.session_memory.save_session")
    @patch("mcp_tools._controlplane_impl_feedback._execute_single_repair")
    @patch("mcp_tools._controlplane_impl_feedback._collect_pending_repairs")
    @patch("lintgate.controlplane.session_memory.get_or_create_session")
    def test_pending_remaining_count(self, mock_get_session, mock_collect, mock_exec, mock_save):
        session = _FakeSession(repair_outcomes={"a1": "pending", "a2": "applied", "a3": "pending"})
        mock_get_session.return_value = session
        mock_collect.return_value = ([], [])
        helpers = {"_validate_project_root": lambda p: "/proj"}

        result_str = _impl_controlplane_apply_repairs("/proj", None, False, helpers)
        result = _load_tool_result(result_str)
        assert result["pending_remaining"] == 2

    @patch("lintgate.controlplane.session_memory.save_session")
    @patch("mcp_tools._controlplane_impl_feedback._execute_single_repair")
    @patch("mcp_tools._controlplane_impl_feedback._collect_pending_repairs")
    @patch("lintgate.controlplane.session_memory.get_or_create_session")
    def test_passes_run_id_to_collection(
        self, mock_get_session, mock_collect, mock_exec, mock_save
    ):
        session = _FakeSession()
        mock_get_session.return_value = session
        mock_collect.return_value = ([], [])
        helpers = {"_validate_project_root": lambda p: "/proj"}

        _impl_controlplane_apply_repairs("/proj", None, False, helpers, run_id="run-7")
        mock_collect.assert_called_once_with(session, None, False, run_id="run-7")
