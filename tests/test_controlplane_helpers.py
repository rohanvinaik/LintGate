"""Tests for pure helper functions in lintgate/hooks/controlplane.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest import mock

from lintgate.hooks.controlplane import (
    _SESSION_TELEMETRY_COUNTER_KEY,
    _SESSION_TELEMETRY_UPDATE_CAP,
    _apply_global_profile_delta,
    _check_session_gate,
    _collect_model_candidates,
    accumulate_session_telemetry,
    can_apply_session_telemetry,
    extract_finding_indexes,
    load_global_priors,
    mark_session_telemetry_applied,
    record_snapshot_behavior,
    save_run_details,
    session_telemetry_updates_used,
    setup_session_and_gate,
)

# ── Lightweight stubs ────────────────────────────────────────────────


@dataclass
class StubSession:
    behavior_compass: dict[str, Any] = field(default_factory=dict)
    snapshots: list = field(default_factory=list)
    theory_profile_cache: dict[str, Any] | None = None
    session_id: str = "test-session"
    pattern_trend: dict[str, list[int]] = field(default_factory=dict)
    proposed_constraints: list[dict] = field(default_factory=list)


@dataclass
class StubSnapshot:
    finding_index: dict[str, Any] = field(default_factory=dict)
    disposition: str | None = None
    last_nudge: dict[str, Any] | None = None


@dataclass
class StubBehaviorEventData:
    action_type: str = ""
    command_signature: str = ""
    exit_code: int | None = None
    error_signature: str = ""
    behavior_alerts: list[str] = field(default_factory=list)


@dataclass
class StubRecordSnapshot:
    behavior: StubBehaviorEventData = field(default_factory=StubBehaviorEventData)


@dataclass
class StubCoherence:
    state: str = "stable"
    summary: str = ""
    recommended_action: str = ""
    silent_channels: list[str] = field(default_factory=list)
    loud_channels: list[str] = field(default_factory=list)


@dataclass
class StubLintIssue:
    linter: str = "ruff"
    kind: str = "F821"
    message: str = "test issue"

    def to_dict(self) -> dict[str, Any]:
        return {"linter": self.linter, "kind": self.kind, "message": self.message}


@dataclass
class StubRepairAction:
    action_id: str = "r1"
    kind: str = "command"
    summary: str = "fix it"
    safe: bool = True
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class StubChannelResult:
    channel: str = "lint"
    status: str = "pass"
    severity: str = "none"
    findings: list = field(default_factory=list)
    repairs: list = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 10.0
    error_message: str | None = None


@dataclass
class StubEvent:
    event_id: str = "evt123"
    raw_input: dict[str, Any] = field(default_factory=dict)


@dataclass
class StubMeshResult:
    event: StubEvent = field(default_factory=StubEvent)
    channel_results: list = field(default_factory=list)
    coherence: StubCoherence = field(default_factory=StubCoherence)
    duration_ms: float = 50.0
    incomplete_channels: list[str] = field(default_factory=list)
    partial: bool = False


@dataclass
class StubInquiryConfig:
    session_gate: bool = False

    def any_enabled(self) -> bool:
        return self.session_gate


@dataclass
class StubCpConfig:
    global_memory_enabled: bool = False
    global_memory_alpha: float = 0.6
    global_memory_decay_horizon: int = 50
    global_memory_ttl_days: int = 90
    session_memory: bool = False
    session_max_age_hours: float = 4.0
    constraint_proposal_threshold: int = 5
    inquiry: StubInquiryConfig = field(default_factory=StubInquiryConfig)
    habit_mode_enabled: bool = False
    _enabled_channels: dict[str, bool] = field(default_factory=dict)

    def channel_enabled(self, name: str) -> bool:
        return self._enabled_channels.get(name, True)


# ── can_apply_session_telemetry ──────────────────────────────────────


class TestCanApplySessionTelemetry:
    def test_returns_true_for_fresh_session(self):
        session = StubSession()
        assert can_apply_session_telemetry(session) is True

    def test_returns_true_when_under_cap(self):
        session = StubSession(behavior_compass={_SESSION_TELEMETRY_COUNTER_KEY: 5})
        assert can_apply_session_telemetry(session) is True

    def test_returns_false_at_cap(self):
        session = StubSession(
            behavior_compass={_SESSION_TELEMETRY_COUNTER_KEY: _SESSION_TELEMETRY_UPDATE_CAP}
        )
        assert can_apply_session_telemetry(session) is False

    def test_returns_false_above_cap(self):
        session = StubSession(behavior_compass={_SESSION_TELEMETRY_COUNTER_KEY: 99})
        assert can_apply_session_telemetry(session) is False

    def test_returns_true_for_none_session(self):
        assert can_apply_session_telemetry(None) is True

    def test_returns_true_when_no_behavior_compass_attr(self):
        obj = object()
        assert can_apply_session_telemetry(obj) is True


# ── session_telemetry_updates_used ───────────────────────────────────


class TestSessionTelemetryUpdatesUsed:
    def test_returns_zero_for_none(self):
        assert session_telemetry_updates_used(None) == 0

    def test_returns_zero_for_no_behavior_compass_attr(self):
        assert session_telemetry_updates_used(object()) == 0

    def test_returns_zero_for_non_dict_behavior_compass(self):
        session = StubSession()
        session.behavior_compass = "not a dict"
        assert session_telemetry_updates_used(session) == 0

    def test_returns_zero_when_key_missing(self):
        session = StubSession(behavior_compass={})
        assert session_telemetry_updates_used(session) == 0

    def test_returns_stored_value(self):
        session = StubSession(behavior_compass={_SESSION_TELEMETRY_COUNTER_KEY: 7})
        assert session_telemetry_updates_used(session) == 7

    def test_returns_zero_for_negative_value(self):
        session = StubSession(behavior_compass={_SESSION_TELEMETRY_COUNTER_KEY: -3})
        assert session_telemetry_updates_used(session) == 0

    def test_returns_zero_for_non_int_value(self):
        session = StubSession(behavior_compass={_SESSION_TELEMETRY_COUNTER_KEY: "five"})
        assert session_telemetry_updates_used(session) == 0

    def test_returns_zero_for_float_value(self):
        session = StubSession(behavior_compass={_SESSION_TELEMETRY_COUNTER_KEY: 3.5})
        assert session_telemetry_updates_used(session) == 0


# ── mark_session_telemetry_applied ───────────────────────────────────


class TestMarkSessionTelemetryApplied:
    def test_increments_from_zero(self):
        session = StubSession(behavior_compass={})
        mark_session_telemetry_applied(session)
        assert session.behavior_compass[_SESSION_TELEMETRY_COUNTER_KEY] == 1

    def test_increments_existing_value(self):
        session = StubSession(behavior_compass={_SESSION_TELEMETRY_COUNTER_KEY: 4})
        mark_session_telemetry_applied(session)
        assert session.behavior_compass[_SESSION_TELEMETRY_COUNTER_KEY] == 5

    def test_noop_for_none_session(self):
        mark_session_telemetry_applied(None)

    def test_noop_for_no_behavior_compass_attr(self):
        mark_session_telemetry_applied(object())

    def test_noop_for_non_dict_behavior_compass(self):
        session = StubSession()
        session.behavior_compass = "not a dict"
        mark_session_telemetry_applied(session)
        assert session.behavior_compass == "not a dict"

    def test_multiple_increments(self):
        session = StubSession(behavior_compass={})
        for _ in range(3):
            mark_session_telemetry_applied(session)
        assert session.behavior_compass[_SESSION_TELEMETRY_COUNTER_KEY] == 3


# ── _apply_global_profile_delta ──────────────────────────────────────


class TestApplyGlobalProfileDelta:
    def test_noop_when_global_memory_disabled(self):
        session = StubSession()
        cr = StubChannelResult(metrics={"global_profile_delta": {"some": "data"}})
        config = StubCpConfig(global_memory_enabled=False)
        _apply_global_profile_delta(session, cr, config)

    def test_noop_when_no_delta_in_metrics(self):
        session = StubSession()
        cr = StubChannelResult(metrics={})
        config = StubCpConfig(global_memory_enabled=True)
        _apply_global_profile_delta(session, cr, config)

    def test_calls_apply_when_enabled_and_delta_present(self):
        session = StubSession()
        cr = StubChannelResult(
            metrics={"global_profile_delta": {"signal_counts": {"approach_cycling": 2}}}
        )
        config = StubCpConfig(global_memory_enabled=True)
        with (
            mock.patch(
                "lintgate.controlplane.global_behavior_profile.load_global_profile"
            ) as mock_load,
            mock.patch(
                "lintgate.controlplane.global_behavior_profile.apply_session_delta"
            ) as mock_apply,
            mock.patch(
                "lintgate.controlplane.global_behavior_profile.save_global_profile"
            ) as mock_save,
        ):
            mock_gp = mock.MagicMock()
            mock_load.return_value = mock_gp
            _apply_global_profile_delta(session, cr, config)
            mock_load.assert_called_once_with(ttl_days=90)
            mock_apply.assert_called_once_with(
                mock_gp,
                {"signal_counts": {"approach_cycling": 2}},
                session_id="test-session",
            )
            mock_save.assert_called_once_with(mock_gp)


# ── extract_finding_indexes ──────────────────────────────────────────


class TestExtractFindingIndexes:
    def test_returns_defaults_for_none_session(self):
        result = extract_finding_indexes(None)
        assert result == (None, None, 0, None, None)

    def test_returns_defaults_for_empty_snapshots(self):
        session = StubSession(snapshots=[])
        result = extract_finding_indexes(session)
        assert result == (None, None, 0, None, None)

    def test_extracts_from_single_snapshot(self):
        snap = StubSnapshot(
            finding_index={"fp1": {"code": "F821"}},
            disposition="investigate",
            last_nudge={"type": "slow_down"},
        )
        session = StubSession(snapshots=[snap])
        prev, baseline, count, disp, nudge = extract_finding_indexes(session)
        assert prev == {"fp1": {"code": "F821"}}
        assert baseline == {"fp1": {"code": "F821"}}
        assert count == 1
        assert disp == "investigate"
        assert nudge == {"type": "slow_down"}

    def test_extracts_from_multiple_snapshots(self):
        snap1 = StubSnapshot(
            finding_index={"fp1": {"code": "F821"}},
            disposition="pass",
            last_nudge=None,
        )
        snap2 = StubSnapshot(
            finding_index={"fp2": {"code": "E402"}},
            disposition="investigate",
            last_nudge={"type": "stop"},
        )
        session = StubSession(snapshots=[snap1, snap2])
        prev, baseline, count, disp, nudge = extract_finding_indexes(session)
        assert prev == {"fp2": {"code": "E402"}}
        assert baseline == {"fp1": {"code": "F821"}}
        assert count == 2
        assert disp == "investigate"
        assert nudge == {"type": "stop"}

    def test_returns_tuple_of_five(self):
        result = extract_finding_indexes(None)
        assert len(result) == 5

    def test_snapshot_with_none_values(self):
        snap = StubSnapshot(
            finding_index={},
            disposition=None,
            last_nudge=None,
        )
        session = StubSession(snapshots=[snap])
        prev, baseline, count, disp, nudge = extract_finding_indexes(session)
        assert prev == {}
        assert baseline == {}
        assert count == 1
        assert disp is None
        assert nudge is None


# ── load_global_priors ───────────────────────────────────────────────


class TestLoadGlobalPriors:
    def test_returns_none_when_global_memory_disabled(self):
        config = StubCpConfig(global_memory_enabled=False)
        assert load_global_priors(config) is None

    def test_returns_none_when_behavior_channel_disabled(self):
        config = StubCpConfig(
            global_memory_enabled=True,
            _enabled_channels={"behavior": False},
        )
        assert load_global_priors(config) is None

    def test_returns_none_when_insufficient_sessions(self):
        config = StubCpConfig(global_memory_enabled=True)
        mock_gp = mock.MagicMock()
        mock_gp.session_count = 0
        with (
            mock.patch(
                "lintgate.controlplane.global_behavior_profile.load_global_profile",
                return_value=mock_gp,
            ),
            mock.patch("lintgate.controlplane.global_behavior_profile.MIN_SAMPLE_SIZE", 3),
        ):
            assert load_global_priors(config) is None

    def test_returns_priors_when_sufficient(self):
        config = StubCpConfig(
            global_memory_enabled=True,
            global_memory_alpha=0.7,
            global_memory_decay_horizon=30,
            global_memory_ttl_days=60,
        )
        mock_gp = mock.MagicMock()
        mock_gp.session_count = 10
        mock_gp.computed_bias_adjustments = {"approach_cycling": 0.1}
        with (
            mock.patch(
                "lintgate.controlplane.global_behavior_profile.load_global_profile",
                return_value=mock_gp,
            ),
            mock.patch("lintgate.controlplane.global_behavior_profile.MIN_SAMPLE_SIZE", 3),
        ):
            result = load_global_priors(config)
            assert result == {
                "enabled": True,
                "alpha": 0.7,
                "decay_horizon": 30,
                "computed_bias_adjustments": {"approach_cycling": 0.1},
            }

    def test_returns_none_on_import_error(self):
        config = StubCpConfig(global_memory_enabled=True)
        with mock.patch.dict(
            "sys.modules", {"lintgate.controlplane.global_behavior_profile": None}
        ):
            assert load_global_priors(config) is None


# ── accumulate_session_telemetry ─────────────────────────────────────


class TestAccumulateSessionTelemetry:
    def test_noop_when_report_is_none(self):
        session = StubSession(behavior_compass={})
        accumulate_session_telemetry(None, session)
        assert "telemetry_counters" not in session.behavior_compass

    def test_noop_when_session_is_none(self):
        accumulate_session_telemetry({"_telemetry": {"runs": 1}}, None)

    def test_noop_when_no_telemetry_key(self):
        session = StubSession(behavior_compass={})
        accumulate_session_telemetry({"other": "data"}, session)
        assert "telemetry_counters" not in session.behavior_compass

    def test_noop_when_telemetry_empty(self):
        session = StubSession(behavior_compass={})
        accumulate_session_telemetry({"_telemetry": {}}, session)
        assert "telemetry_counters" not in session.behavior_compass

    def test_accumulates_new_counters(self):
        session = StubSession(behavior_compass={})
        report = {"_telemetry": {"lint_runs": 3, "test_runs": 1}}
        with mock.patch("lintgate.controlplane.session_memory.save_session"):
            accumulate_session_telemetry(report, session)
        assert session.behavior_compass["telemetry_counters"] == {
            "lint_runs": 3,
            "test_runs": 1,
        }

    def test_accumulates_to_existing_counters(self):
        session = StubSession(behavior_compass={"telemetry_counters": {"lint_runs": 2}})
        report = {"_telemetry": {"lint_runs": 3, "test_runs": 1}}
        with mock.patch("lintgate.controlplane.session_memory.save_session"):
            accumulate_session_telemetry(report, session)
        assert session.behavior_compass["telemetry_counters"] == {
            "lint_runs": 5,
            "test_runs": 1,
        }

    def test_replaces_non_dict_existing_counters(self):
        session = StubSession(behavior_compass={"telemetry_counters": "corrupted"})
        report = {"_telemetry": {"runs": 1}}
        with mock.patch("lintgate.controlplane.session_memory.save_session"):
            accumulate_session_telemetry(report, session)
        assert session.behavior_compass["telemetry_counters"] == {"runs": 1}


# ── _check_session_gate ──────────────────────────────────────────────


class TestCheckSessionGate:
    def test_returns_none_for_none_session(self):
        config = StubCpConfig(inquiry=StubInquiryConfig(session_gate=True))
        result = _check_session_gate(None, config, "/tmp", "Edit", [])
        assert result is None

    def test_returns_none_when_session_gate_disabled(self):
        config = StubCpConfig(inquiry=StubInquiryConfig(session_gate=False))
        session = StubSession()
        result = _check_session_gate(session, config, "/tmp", "Edit", [])
        assert result is None

    def test_returns_none_for_non_write_tools(self):
        config = StubCpConfig(inquiry=StubInquiryConfig(session_gate=True))
        session = StubSession()
        for tool in ("Bash", "Read", "Grep", "Glob"):
            result = _check_session_gate(session, config, "/tmp", tool, [])
            assert result is None

    def test_returns_none_when_already_ready(self):
        config = StubCpConfig(inquiry=StubInquiryConfig(session_gate=True))
        session = StubSession(behavior_compass={"_session_ready": True})
        result = _check_session_gate(session, config, "/tmp", "Edit", [])
        assert result is None

    def test_triggers_for_write_tool(self):
        config = StubCpConfig(inquiry=StubInquiryConfig(session_gate=True))
        session = StubSession(behavior_compass={})

        mock_readiness = mock.MagicMock()
        mock_readiness.ready = False
        mock_readiness.missing = ["theory_profile"]
        mock_readiness.recommendation = "Run build_theory_pack"

        channels = [mock.MagicMock(name="behavior"), mock.MagicMock(name="lint")]
        channels[0].name = "behavior"
        channels[1].name = "lint"

        with mock.patch(
            "lintgate.context_auditor.check_session_readiness",
            return_value=mock_readiness,
        ):
            result = _check_session_gate(session, config, "/tmp", "Write", channels)

        assert result is not None
        assert "Context not ready" in result
        assert "theory_profile" in result
        assert len(channels) == 1
        assert channels[0].name == "lint"

    def test_triggers_for_edit_tool(self):
        config = StubCpConfig(inquiry=StubInquiryConfig(session_gate=True))
        session = StubSession(behavior_compass={})

        mock_readiness = mock.MagicMock()
        mock_readiness.ready = False
        mock_readiness.missing = ["rules"]
        mock_readiness.recommendation = "Bootstrap context"

        with mock.patch(
            "lintgate.context_auditor.check_session_readiness",
            return_value=mock_readiness,
        ):
            result = _check_session_gate(session, config, "/tmp", "Edit", [])

        assert result is not None
        assert "rules" in result

    def test_triggers_for_multiedit_tool(self):
        config = StubCpConfig(inquiry=StubInquiryConfig(session_gate=True))
        session = StubSession(behavior_compass={})

        mock_readiness = mock.MagicMock()
        mock_readiness.ready = False
        mock_readiness.missing = ["config"]
        mock_readiness.recommendation = "Check config"

        with mock.patch(
            "lintgate.context_auditor.check_session_readiness",
            return_value=mock_readiness,
        ):
            result = _check_session_gate(session, config, "/tmp", "MultiEdit", [])

        assert result is not None

    def test_marks_ready_on_pass(self):
        config = StubCpConfig(inquiry=StubInquiryConfig(session_gate=True))
        session = StubSession(behavior_compass={})

        mock_readiness = mock.MagicMock()
        mock_readiness.ready = True

        with mock.patch(
            "lintgate.context_auditor.check_session_readiness",
            return_value=mock_readiness,
        ):
            result = _check_session_gate(session, config, "/tmp", "Edit", [])

        assert result is None
        assert session.behavior_compass["_session_ready"] is True


# ── setup_session_and_gate ───────────────────────────────────────────


class TestSetupSessionAndGate:
    def test_returns_none_session_when_memory_disabled(self):
        config = StubCpConfig(session_memory=False)
        event = StubEvent()
        session, advisory = setup_session_and_gate(config, "/tmp", "Bash", event, [], None)
        assert session is None
        assert advisory is None

    def test_returns_session_when_memory_enabled(self):
        config = StubCpConfig(session_memory=True)
        event = StubEvent()
        mock_session = StubSession()
        with mock.patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            return_value=mock_session,
        ):
            session, advisory = setup_session_and_gate(config, "/tmp", "Bash", event, [], None)
        assert session is mock_session

    def test_injects_behavior_compass(self):
        config = StubCpConfig(session_memory=True)
        event = StubEvent(raw_input={})
        mock_session = StubSession(behavior_compass={"key": "val"})
        with mock.patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            return_value=mock_session,
        ):
            session, advisory = setup_session_and_gate(config, "/tmp", "Bash", event, [], None)
        assert event.raw_input.get("behavior_compass") == {"key": "val"}

    def test_injects_global_priors(self):
        config = StubCpConfig(session_memory=False)
        event = StubEvent(raw_input={})
        priors = {"enabled": True, "alpha": 0.6}
        session, advisory = setup_session_and_gate(config, "/tmp", "Bash", event, [], priors)
        assert event.raw_input["behavior_global_priors"] == priors


# ── record_snapshot_behavior ─────────────────────────────────────────


class TestRecordSnapshotBehavior:
    def test_sets_action_type_for_non_bash(self):
        snapshot = StubRecordSnapshot()
        record_snapshot_behavior(snapshot, "Write", {}, "")
        assert snapshot.behavior.action_type == "write"

    def test_sets_action_type_for_read(self):
        snapshot = StubRecordSnapshot()
        record_snapshot_behavior(snapshot, "Read", {}, "")
        assert snapshot.behavior.action_type == "read"

    def test_bash_extracts_command_signature(self):
        snapshot = StubRecordSnapshot()
        record_snapshot_behavior(snapshot, "Bash", {"command": "git status"}, "on branch main")
        assert snapshot.behavior.action_type == "bash"
        assert snapshot.behavior.command_signature != ""

    def test_bash_extracts_exit_code_from_output(self):
        snapshot = StubRecordSnapshot()
        record_snapshot_behavior(snapshot, "Bash", {"command": "ls"}, "exit_code: 0")
        assert snapshot.behavior.exit_code == 0

    def test_bash_extracts_nonzero_exit_code(self):
        snapshot = StubRecordSnapshot()
        record_snapshot_behavior(snapshot, "Bash", {"command": "false"}, "exit code: 1")
        assert snapshot.behavior.exit_code == 1

    def test_bash_infers_error_exit_code(self):
        snapshot = StubRecordSnapshot()
        record_snapshot_behavior(snapshot, "Bash", {"command": "ls"}, "error: file not found")
        assert snapshot.behavior.exit_code == 1

    def test_bash_infers_failed_exit_code(self):
        snapshot = StubRecordSnapshot()
        record_snapshot_behavior(snapshot, "Bash", {"command": "make"}, "build failed")
        assert snapshot.behavior.exit_code == 1

    def test_bash_defaults_to_zero_exit_code(self):
        snapshot = StubRecordSnapshot()
        record_snapshot_behavior(snapshot, "Bash", {"command": "echo hello"}, "hello")
        assert snapshot.behavior.exit_code == 0

    def test_bash_handles_string_tool_input(self):
        snapshot = StubRecordSnapshot()
        record_snapshot_behavior(snapshot, "Bash", "git status", "on branch main")
        assert snapshot.behavior.action_type == "bash"
        assert snapshot.behavior.command_signature != ""

    def test_bash_handles_non_string_non_dict_input(self):
        snapshot = StubRecordSnapshot()
        record_snapshot_behavior(snapshot, "Bash", 12345, "output")
        assert snapshot.behavior.action_type == "bash"
        assert snapshot.behavior.command_signature != ""

    def test_bash_handles_non_string_output(self):
        snapshot = StubRecordSnapshot()
        record_snapshot_behavior(snapshot, "Bash", {"command": "ls"}, None)
        assert snapshot.behavior.exit_code == 0

    def test_bash_exit_status_variant(self):
        snapshot = StubRecordSnapshot()
        record_snapshot_behavior(snapshot, "Bash", {"command": "ls"}, "exitstatus: 2")
        assert snapshot.behavior.exit_code == 2

    def test_bash_exit_code_case_insensitive(self):
        snapshot = StubRecordSnapshot()
        record_snapshot_behavior(snapshot, "Bash", {"command": "ls"}, "EXIT_CODE=42")
        assert snapshot.behavior.exit_code == 42


# ── save_run_details ─────────────────────────────────────────────────


class TestSaveRunDetails:
    def test_noop_when_finding_index_empty(self):
        mesh = StubMeshResult()
        with mock.patch("lintgate.state.save_controlplane_run") as mock_save:
            save_run_details(mesh, {})
        mock_save.assert_not_called()

    def test_noop_when_finding_index_none(self):
        mesh = StubMeshResult()
        with mock.patch("lintgate.state.save_controlplane_run") as mock_save:
            save_run_details(mesh, None)
        mock_save.assert_not_called()

    def test_saves_with_valid_finding_index(self):
        finding = StubLintIssue()
        cr = StubChannelResult(
            channel="lint",
            status="fail",
            severity="warning",
            findings=[finding],
            repairs=[StubRepairAction()],
            metrics={"pattern_alerts": []},
            duration_ms=15.5,
        )
        mesh = StubMeshResult(
            event=StubEvent(event_id="run42"),
            channel_results=[cr],
            coherence=StubCoherence(
                state="isolated",
                summary="lint only",
                recommended_action="fix lint",
                silent_channels=["tests"],
                loud_channels=["lint"],
            ),
            duration_ms=100.0,
            partial=False,
            incomplete_channels=[],
        )
        fi = {"fp1": {"code": "F821", "file": "a.py"}}

        with mock.patch("lintgate.state.save_controlplane_run") as mock_save:
            save_run_details(mesh, fi, compliance_outcome="followed")

        mock_save.assert_called_once()
        run_id, details = mock_save.call_args[0]
        assert run_id == "run42"
        assert details["compliance_outcome"] == "followed"
        assert details["coherence"]["state"] == "isolated"
        assert details["coherence"]["silent_channels"] == ["tests"]
        assert details["coherence"]["loud_channels"] == ["lint"]
        assert details["duration_ms"] == 100.0
        assert details["partial"] is False
        assert details["finding_index"] == fi
        assert "lint" in details["channels"]
        lint_ch = details["channels"]["lint"]
        assert lint_ch["status"] == "fail"
        assert lint_ch["severity"] == "warning"
        assert lint_ch["duration_ms"] == 15.5
        assert len(lint_ch["findings"]) == 1
        assert len(lint_ch["repairs"]) == 1
        assert lint_ch["repairs"][0]["action_id"] == "r1"

    def test_skips_channels_with_skip_status(self):
        cr_skip = StubChannelResult(channel="tests", status="skip")
        cr_lint = StubChannelResult(channel="lint", status="pass")
        mesh = StubMeshResult(
            event=StubEvent(event_id="run43"),
            channel_results=[cr_skip, cr_lint],
        )
        fi = {"fp1": {"code": "ok"}}

        with mock.patch("lintgate.state.save_controlplane_run") as mock_save:
            save_run_details(mesh, fi)

        _, details = mock_save.call_args[0]
        assert "tests" not in details["channels"]
        assert "lint" in details["channels"]

    def test_noop_when_event_id_empty(self):
        mesh = StubMeshResult(event=StubEvent(event_id=""))
        fi = {"fp1": {"code": "ok"}}

        with mock.patch("lintgate.state.save_controlplane_run") as mock_save:
            save_run_details(mesh, fi)

        mock_save.assert_not_called()

    def test_noop_when_event_is_none(self):
        mesh = StubMeshResult()
        mesh.event = None
        fi = {"fp1": {"code": "ok"}}

        with mock.patch("lintgate.state.save_controlplane_run") as mock_save:
            save_run_details(mesh, fi)

        mock_save.assert_not_called()


# ─── _collect_model_candidates (sigma=18) ─────────────────────────────


class TestCollectModelCandidates:
    def test_top_level_model_field(self):
        result = _collect_model_candidates({"model": "claude-opus-4"})
        assert "claude-opus-4" in result

    def test_top_level_model_id_field(self):
        result = _collect_model_candidates({"model_id": "gpt-4o"})
        assert "gpt-4o" in result

    def test_top_level_model_name_field(self):
        result = _collect_model_candidates({"model_name": "gemini-pro"})
        assert "gemini-pro" in result

    def test_assistant_model_field(self):
        result = _collect_model_candidates({"assistant_model": "claude-sonnet-4"})
        assert "claude-sonnet-4" in result

    def test_metadata_model_fields(self):
        data = {"metadata": {"model": "m1", "model_id": "m2", "model_name": "m3"}}
        result = _collect_model_candidates(data)
        assert "m1" in result
        assert "m2" in result
        assert "m3" in result

    def test_session_model_fields(self):
        data = {"session": {"model": "s1", "model_id": "s2", "model_name": "s3"}}
        result = _collect_model_candidates(data)
        assert "s1" in result
        assert "s2" in result
        assert "s3" in result

    def test_tool_input_model_fields(self):
        data = {"tool_input": {"model": "t1", "model_id": "t2"}}
        result = _collect_model_candidates(data)
        assert "t1" in result
        assert "t2" in result

    def test_env_vars_included(self, monkeypatch):
        monkeypatch.setenv("LINTGATE_MODEL_ID", "env-model")
        monkeypatch.delenv("CLAUDE_MODEL", raising=False)
        monkeypatch.delenv("OPENAI_MODEL", raising=False)
        monkeypatch.delenv("MODEL", raising=False)
        result = _collect_model_candidates({})
        assert "env-model" in result

    def test_empty_input_returns_list(self, monkeypatch):
        for k in ("LINTGATE_MODEL_ID", "CLAUDE_MODEL", "OPENAI_MODEL", "MODEL"):
            monkeypatch.delenv(k, raising=False)
        result = _collect_model_candidates({})
        assert isinstance(result, list)
        # All entries should be None
        assert all(v is None for v in result)

    def test_non_dict_metadata_skipped(self):
        data = {"metadata": "not-a-dict", "model": "ok"}
        result = _collect_model_candidates(data)
        assert "ok" in result

    def test_non_dict_session_skipped(self):
        data = {"session": [1, 2, 3], "model": "ok"}
        result = _collect_model_candidates(data)
        assert "ok" in result

    def test_non_dict_tool_input_skipped(self):
        data = {"tool_input": 42, "model": "ok"}
        result = _collect_model_candidates(data)
        assert "ok" in result

    def test_returns_list_type(self):
        assert isinstance(_collect_model_candidates({}), list)

    def test_all_sources_combined(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_MODEL", "env-claude")
        monkeypatch.delenv("LINTGATE_MODEL_ID", raising=False)
        monkeypatch.delenv("OPENAI_MODEL", raising=False)
        monkeypatch.delenv("MODEL", raising=False)
        data = {
            "model": "top",
            "metadata": {"model": "meta"},
            "session": {"model": "sess"},
            "tool_input": {"model": "tool"},
        }
        result = _collect_model_candidates(data)
        assert "top" in result
        assert "meta" in result
        assert "sess" in result
        assert "tool" in result
        assert "env-claude" in result
