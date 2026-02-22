"""Comprehensive tests for lintgate/controlplane/session_memory.py.

Covers all public symbols: dataclass round-trips, load/save/get_or_create,
repair tracking, behavior compass helpers, and habit mode detection.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from lintgate.controlplane.session_memory import (
    SESSION_DIR,
    BehaviorEventData,
    SessionMemory,
    SessionSnapshot,
    detect_applied_repairs,
    expire_session,
    get_habit_mode_active,
    get_or_create_session,
    load_behavior_compass,
    load_session,
    propose_repairs,
    record_mesh_run,
    report_repair_outcome,
    save_behavior_compass,
    save_session,
)
from lintgate.controlplane.types import (
    ChannelResult,
    CoherenceResult,
    MeshResult,
    RepairAction,
    SupervisionEvent,
)


# ── Helpers to build minimal mock objects ────────────────────────────────


@dataclass
class _FakeLintIssue:
    """Minimal stand-in for lintgate.types.LintIssue."""

    linter: str = "ruff"
    kind: str = "F821"
    message: str = "undefined name"
    file: str | None = None
    line: int | None = None
    severity: str = "warning"
    confidence: float = 1.0
    fixable: bool = False
    fix_description: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)
    column: int | None = None
    end_line: int | None = None
    end_column: int | None = None


def _make_mesh_result(
    *,
    coherence_state: str = "stable",
    findings: list[_FakeLintIssue] | None = None,
    repairs: list[RepairAction] | None = None,
    event_id: str = "evt001",
    lint_pattern_alerts: list[dict[str, Any]] | None = None,
    loud: list[str] | None = None,
    silent: list[str] | None = None,
) -> MeshResult:
    """Build a minimal MeshResult for testing record_mesh_run."""
    findings = findings or []
    repairs = repairs or []
    channel_results = [
        ChannelResult(
            channel="lint",
            status="fail" if findings else "pass",
            findings=findings,  # type: ignore[arg-type]
            repairs=repairs,
            metrics={"pattern_alerts": lint_pattern_alerts or []},
        ),
    ]
    coherence = CoherenceResult(
        state=coherence_state,
        loud_channels=loud or [],
        silent_channels=silent or [],
    )
    event = SupervisionEvent(event_id=event_id)
    return MeshResult(
        event=event,
        channel_results=channel_results,
        coherence=coherence,
    )


# ── 1. BehaviorEventData round-trip ──────────────────────────────────────


class TestBehaviorEventDataRoundTrip:
    def test_defaults_round_trip(self) -> None:
        bed = BehaviorEventData()
        d = bed.to_dict()
        restored = BehaviorEventData.from_dict(d)
        assert restored.action_type == ""
        assert restored.exit_code is None
        assert restored.behavior_alerts == []
        assert restored.predictions_checked == 0

    def test_populated_round_trip(self) -> None:
        bed = BehaviorEventData(
            action_type="bash",
            command_signature="pytest:run",
            exit_code=1,
            error_signature="AssertionError",
            behavior_alerts=["approach_cycling"],
            prediction_accuracy=0.75,
            predictions_checked=4,
        )
        d = bed.to_dict()
        restored = BehaviorEventData.from_dict(d)
        assert restored.action_type == "bash"
        assert restored.command_signature == "pytest:run"
        assert restored.exit_code == 1
        assert restored.error_signature == "AssertionError"
        assert restored.behavior_alerts == ["approach_cycling"]
        assert restored.prediction_accuracy == 0.75
        assert restored.predictions_checked == 4

    def test_from_dict_missing_keys(self) -> None:
        restored = BehaviorEventData.from_dict({})
        assert restored.action_type == ""
        assert restored.exit_code is None
        assert restored.prediction_accuracy is None


# ── 2. SessionSnapshot round-trip ────────────────────────────────────────


class TestSessionSnapshotRoundTrip:
    def test_defaults_round_trip(self) -> None:
        snap = SessionSnapshot()
        d = snap.to_dict()
        restored = SessionSnapshot.from_dict(d)
        assert restored.run_id == ""
        assert restored.finding_count == 0
        assert restored.behavior.action_type == ""

    def test_populated_round_trip(self) -> None:
        bed = BehaviorEventData(action_type="bash", exit_code=0)
        snap = SessionSnapshot(
            run_id="run123",
            timestamp=1000.0,
            coherence_state="isolated",
            loud_channels=["lint"],
            silent_channels=["test"],
            finding_count=3,
            blocking_count=1,
            pattern_alerts=[{"name": "repeat"}],
            repairs_proposed=["r1"],
            repairs_applied=["r2"],
            repair_catalog={"r1": {"channel": "lint", "kind": "command", "summary": "fix", "safe": "true"}},
            behavior=bed,
            finding_index={"fp1": {"linter": "ruff"}},
        )
        d = snap.to_dict()
        restored = SessionSnapshot.from_dict(d)
        assert restored.run_id == "run123"
        assert restored.coherence_state == "isolated"
        assert restored.loud_channels == ["lint"]
        assert restored.finding_count == 3
        assert restored.blocking_count == 1
        assert restored.repairs_proposed == ["r1"]
        assert restored.repair_catalog["r1"]["channel"] == "lint"
        assert restored.finding_index["fp1"]["linter"] == "ruff"

    def test_to_dict_flattens_behavior(self) -> None:
        """to_dict should inline behavior fields at the top level for backward compat."""
        snap = SessionSnapshot(
            behavior=BehaviorEventData(action_type="write", exit_code=None),
        )
        d = snap.to_dict()
        assert "behavior" not in d
        assert d["action_type"] == "write"
        assert d["exit_code"] is None


# ── 3. SessionSnapshot property accessors ────────────────────────────────


class TestSessionSnapshotPropertyAccessors:
    def test_action_type_property(self) -> None:
        snap = SessionSnapshot(behavior=BehaviorEventData(action_type="edit"))
        assert snap.action_type == "edit"

    def test_command_signature_property(self) -> None:
        snap = SessionSnapshot(behavior=BehaviorEventData(command_signature="git:push"))
        assert snap.command_signature == "git:push"

    def test_exit_code_property(self) -> None:
        snap = SessionSnapshot(behavior=BehaviorEventData(exit_code=42))
        assert snap.exit_code == 42

    def test_error_signature_property(self) -> None:
        snap = SessionSnapshot(behavior=BehaviorEventData(error_signature="ENOENT"))
        assert snap.error_signature == "ENOENT"

    def test_behavior_alerts_property(self) -> None:
        snap = SessionSnapshot(behavior=BehaviorEventData(behavior_alerts=["a", "b"]))
        assert snap.behavior_alerts == ["a", "b"]

    def test_prediction_accuracy_property(self) -> None:
        snap = SessionSnapshot(behavior=BehaviorEventData(prediction_accuracy=0.9))
        assert snap.prediction_accuracy == 0.9

    def test_predictions_checked_property(self) -> None:
        snap = SessionSnapshot(behavior=BehaviorEventData(predictions_checked=7))
        assert snap.predictions_checked == 7


# ── 4. SessionMemory round-trip ──────────────────────────────────────────


class TestSessionMemoryRoundTrip:
    def test_defaults_round_trip(self) -> None:
        sm = SessionMemory()
        d = sm.to_dict()
        restored = SessionMemory.from_dict(d)
        assert restored.project_root == ""
        assert restored.snapshots == []
        assert restored.repair_outcomes == {}
        assert restored.theory_profile_cache is None

    def test_populated_round_trip(self) -> None:
        snap = SessionSnapshot(run_id="s1", finding_count=2)
        sm = SessionMemory(
            session_id="sid123",
            project_root="/tmp/proj",
            started_at=100.0,
            last_active=200.0,
            snapshots=[snap],
            coherence_trajectory=["stable", "isolated"],
            repair_outcomes={"r1": "pending"},
            pattern_trend={"ruff|F821": [1, 2]},
            proposed_constraints=[{"rule": "no-star-imports"}],
            agent_disagreements=[{"note": "disagreed"}],
            behavior_compass={"hypothesis_version": 3},
            pending_patches=[{"section": "rules"}],
        )
        d = sm.to_dict()
        restored = SessionMemory.from_dict(d)
        assert restored.session_id == "sid123"
        assert restored.project_root == "/tmp/proj"
        assert restored.started_at == 100.0
        assert len(restored.snapshots) == 1
        assert restored.snapshots[0].run_id == "s1"
        assert restored.coherence_trajectory == ["stable", "isolated"]
        assert restored.repair_outcomes == {"r1": "pending"}
        assert restored.pattern_trend == {"ruff|F821": [1, 2]}
        assert restored.proposed_constraints == [{"rule": "no-star-imports"}]
        assert restored.agent_disagreements == [{"note": "disagreed"}]
        assert restored.behavior_compass == {"hypothesis_version": 3}
        assert restored.pending_patches == [{"section": "rules"}]

    def test_theory_profile_cache_not_persisted(self) -> None:
        sm = SessionMemory(theory_profile_cache={"some": "data"})
        d = sm.to_dict()
        assert "theory_profile_cache" not in d
        restored = SessionMemory.from_dict(d)
        assert restored.theory_profile_cache is None

    def test_from_dict_missing_keys_uses_defaults(self) -> None:
        restored = SessionMemory.from_dict({})
        assert restored.project_root == ""
        assert restored.snapshots == []
        assert restored.pending_patches == []


# ── 5. load_session ──────────────────────────────────────────────────────


class TestLoadSession:
    def test_load_missing_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path)
        result = load_session("/tmp/nonexistent_project")
        assert result is None

    def test_load_corrupted_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path)
        # Write corrupted JSON
        import hashlib

        h = hashlib.sha256(b"/tmp/bad_project").hexdigest()[:16]
        (tmp_path / f"{h}.json").write_text("{invalid json!!!")
        result = load_session("/tmp/bad_project")
        assert result is None

    def test_load_non_dict_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path)
        import hashlib

        h = hashlib.sha256(b"/tmp/list_project").hexdigest()[:16]
        (tmp_path / f"{h}.json").write_text("[1, 2, 3]")
        result = load_session("/tmp/list_project")
        assert result is None

    def test_load_valid_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path)
        import hashlib

        project = "/tmp/good_project"
        h = hashlib.sha256(project.encode()).hexdigest()[:16]
        data = SessionMemory(project_root=project, session_id="abc123").to_dict()
        (tmp_path / f"{h}.json").write_text(json.dumps(data))
        result = load_session(project)
        assert result is not None
        assert result.session_id == "abc123"
        assert result.project_root == project


# ── 6. save_session ──────────────────────────────────────────────────────


class TestSaveSession:
    def test_save_creates_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path)
        session = SessionMemory(project_root="/tmp/save_test", session_id="save1")
        save_session(session)
        # Verify file exists and can be loaded back
        loaded = load_session("/tmp/save_test")
        assert loaded is not None
        assert loaded.session_id == "save1"

    def test_save_updates_last_active(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path)
        session = SessionMemory(project_root="/tmp/ts_test", last_active=0.0)
        before = time.time()
        save_session(session)
        assert session.last_active >= before

    def test_save_creates_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        nested = tmp_path / "deep" / "nested" / "dir"
        monkeypatch.setattr("lintgate.controlplane.session_memory.SESSION_DIR", nested)
        session = SessionMemory(project_root="/tmp/nested_test")
        save_session(session)
        assert nested.exists()


# ── 7. get_or_create_session ─────────────────────────────────────────────


class TestGetOrCreateSession:
    def test_creates_new_when_none_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path)
        session = get_or_create_session("/tmp/brand_new")
        assert session.project_root == "/tmp/brand_new"
        assert session.snapshots == []

    def test_loads_existing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path)
        original = SessionMemory(project_root="/tmp/existing", session_id="exist1")
        save_session(original)
        loaded = get_or_create_session("/tmp/existing")
        assert loaded.session_id == "exist1"

    def test_replaces_expired(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import hashlib

        monkeypatch.setattr("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path)
        project = "/tmp/expired"
        h = hashlib.sha256(project.encode()).hexdigest()[:16]
        # Write the session file directly (save_session would update last_active)
        old = SessionMemory(
            project_root=project,
            session_id="old1",
            last_active=time.time() - 20000,  # Way past 1-hour max_age
        )
        (tmp_path / f"{h}.json").write_text(json.dumps(old.to_dict()))
        new_session = get_or_create_session(project, max_age_hours=1.0)
        # Should get a fresh session, not the old one
        assert new_session.session_id != "old1"
        assert new_session.project_root == project


# ── 8. expire_session ───────────────────────────────────────────────────


class TestExpireSession:
    def test_fresh_session_not_expired(self) -> None:
        session = SessionMemory(last_active=time.time())
        assert expire_session(session, max_age_hours=4.0) is False

    def test_old_session_expired(self) -> None:
        session = SessionMemory(last_active=time.time() - 20000)
        assert expire_session(session, max_age_hours=1.0) is True

    def test_max_age_zero_never_expires(self) -> None:
        session = SessionMemory(last_active=0.0)
        assert expire_session(session, max_age_hours=0) is False

    def test_negative_max_age_never_expires(self) -> None:
        session = SessionMemory(last_active=0.0)
        assert expire_session(session, max_age_hours=-1.0) is False

    def test_boundary_just_expired(self) -> None:
        # Set last_active to exactly max_age_hours + 1s ago
        session = SessionMemory(last_active=time.time() - 3601)
        assert expire_session(session, max_age_hours=1.0) is True

    def test_boundary_not_yet_expired(self) -> None:
        session = SessionMemory(last_active=time.time() - 3500)
        assert expire_session(session, max_age_hours=1.0) is False


# ── 9. propose_repairs ──────────────────────────────────────────────────


class TestProposeRepairs:
    def test_registers_pending(self) -> None:
        session = SessionMemory()
        repairs = [
            RepairAction(action_id="r1", channel="lint", summary="fix1"),
            RepairAction(action_id="r2", channel="test", summary="fix2"),
        ]
        propose_repairs(session, repairs)
        assert session.repair_outcomes == {"r1": "pending", "r2": "pending"}

    def test_idempotent_does_not_overwrite(self) -> None:
        session = SessionMemory(repair_outcomes={"r1": "applied"})
        repairs = [RepairAction(action_id="r1", channel="lint", summary="fix1")]
        propose_repairs(session, repairs)
        assert session.repair_outcomes["r1"] == "applied"

    def test_empty_repairs(self) -> None:
        session = SessionMemory()
        propose_repairs(session, [])
        assert session.repair_outcomes == {}


# ── 10. report_repair_outcome ────────────────────────────────────────────


class TestReportRepairOutcome:
    def test_updates_status(self) -> None:
        session = SessionMemory(repair_outcomes={"r1": "pending"})
        report_repair_outcome(session, "r1", "applied")
        assert session.repair_outcomes["r1"] == "applied"

    def test_new_action_id(self) -> None:
        session = SessionMemory()
        report_repair_outcome(session, "r_new", "rejected")
        assert session.repair_outcomes["r_new"] == "rejected"

    def test_overwrite_existing(self) -> None:
        session = SessionMemory(repair_outcomes={"r1": "applied"})
        report_repair_outcome(session, "r1", "ignored")
        assert session.repair_outcomes["r1"] == "ignored"


# ── 11. detect_applied_repairs ──────────────────────────────────────────


class TestDetectAppliedRepairs:
    def test_no_snapshots_returns_empty(self) -> None:
        session = SessionMemory()
        result = detect_applied_repairs(session, [])
        assert result == []

    def test_single_snapshot_returns_empty(self) -> None:
        session = SessionMemory(snapshots=[SessionSnapshot(blocking_count=5)])
        result = detect_applied_repairs(session, [])
        assert result == []

    def test_two_snapshots_returns_list(self) -> None:
        session = SessionMemory(
            snapshots=[
                SessionSnapshot(blocking_count=3),
                SessionSnapshot(blocking_count=1),
            ],
            repair_outcomes={"r1": "pending"},
        )
        findings: list[_FakeLintIssue] = []
        result = detect_applied_repairs(session, findings)  # type: ignore[arg-type]
        # Current implementation returns empty (conservative heuristic)
        assert isinstance(result, list)

    def test_with_findings(self) -> None:
        session = SessionMemory(
            snapshots=[
                SessionSnapshot(blocking_count=2),
                SessionSnapshot(blocking_count=2),
            ],
            repair_outcomes={"r1": "pending"},
        )
        findings = [
            _FakeLintIssue(linter="ruff", kind="F821", file="a.py", line=10),
        ]
        result = detect_applied_repairs(session, findings)  # type: ignore[arg-type]
        assert isinstance(result, list)


# ── 12. load_behavior_compass ────────────────────────────────────────────


class TestLoadBehaviorCompass:
    def test_empty_dict_returns_fresh_compass(self) -> None:
        session = SessionMemory(behavior_compass={})
        compass = load_behavior_compass(session)
        assert compass.hypothesis_version == 0
        assert compass.hypotheses == []

    def test_with_compass_data(self) -> None:
        session = SessionMemory(
            behavior_compass={
                "hypothesis_version": 5,
                "uncertainty_zones": ["zone1"],
                "hypotheses": [],
                "approaches": [],
                "coverage": {},
                "action_history": [],
                "error_memory": {},
            },
        )
        compass = load_behavior_compass(session)
        assert compass.hypothesis_version == 5
        assert compass.uncertainty_zones == ["zone1"]


# ── 13. save_behavior_compass ────────────────────────────────────────────


class TestSaveBehaviorCompass:
    def test_writes_compass_dict(self) -> None:
        from lintgate.controlplane.behavior_types import BehaviorCompass

        session = SessionMemory()
        compass = BehaviorCompass(hypothesis_version=10)
        save_behavior_compass(session, compass)
        assert session.behavior_compass["hypothesis_version"] == 10

    def test_round_trip_with_load(self) -> None:
        from lintgate.controlplane.behavior_types import BehaviorCompass

        session = SessionMemory()
        compass = BehaviorCompass(
            hypothesis_version=7,
            uncertainty_zones=["env", "tooling"],
        )
        save_behavior_compass(session, compass)
        restored = load_behavior_compass(session)
        assert restored.hypothesis_version == 7
        assert restored.uncertainty_zones == ["env", "tooling"]


# ── 14. get_habit_mode_active ────────────────────────────────────────────


class TestGetHabitModeActive:
    def test_active_true(self) -> None:
        session = SessionMemory(
            behavior_compass={"habit_mode": {"active": True}},
        )
        assert get_habit_mode_active(session) is True

    def test_active_false(self) -> None:
        session = SessionMemory(
            behavior_compass={"habit_mode": {"active": False}},
        )
        assert get_habit_mode_active(session) is False

    def test_no_habit_mode_key(self) -> None:
        session = SessionMemory(behavior_compass={})
        assert get_habit_mode_active(session) is False

    def test_empty_compass(self) -> None:
        session = SessionMemory()
        assert get_habit_mode_active(session) is False

    def test_habit_mode_missing_active_key(self) -> None:
        session = SessionMemory(
            behavior_compass={"habit_mode": {}},
        )
        assert get_habit_mode_active(session) is False


# ── 15. record_mesh_run ──────────────────────────────────────────────────


class TestRecordMeshRun:
    def test_basic_record(self) -> None:
        session = SessionMemory(project_root="/tmp/proj")
        mesh = _make_mesh_result()
        snapshot = record_mesh_run(session, mesh)
        assert snapshot.run_id == "evt001"
        assert snapshot.coherence_state == "stable"
        assert len(session.snapshots) == 1
        assert session.coherence_trajectory == ["stable"]

    def test_findings_counted(self) -> None:
        session = SessionMemory()
        findings = [
            _FakeLintIssue(severity="blocking"),
            _FakeLintIssue(severity="warning"),
            _FakeLintIssue(severity="blocking"),
        ]
        mesh = _make_mesh_result(findings=findings)
        snapshot = record_mesh_run(session, mesh)
        assert snapshot.finding_count == 3
        assert snapshot.blocking_count == 2

    def test_repairs_tracked(self) -> None:
        session = SessionMemory()
        repairs = [
            RepairAction(action_id="fix1", channel="lint", kind="command", summary="s1", safe=True),
            RepairAction(action_id="fix2", channel="lint", kind="command", summary="s2", safe=False),
        ]
        mesh = _make_mesh_result(repairs=repairs)
        snapshot = record_mesh_run(session, mesh)
        assert "fix1" in snapshot.repairs_proposed
        assert "fix2" in snapshot.repairs_proposed
        assert session.repair_outcomes["fix1"] == "pending"
        assert session.repair_outcomes["fix2"] == "pending"
        assert snapshot.repair_catalog["fix1"]["safe"] == "true"
        assert snapshot.repair_catalog["fix2"]["safe"] == "false"

    def test_pattern_trend_updated(self) -> None:
        session = SessionMemory()
        findings = [
            _FakeLintIssue(linter="ruff", kind="F821"),
            _FakeLintIssue(linter="ruff", kind="F821"),
            _FakeLintIssue(linter="mypy", kind="import-error"),
        ]
        mesh = _make_mesh_result(findings=findings)
        record_mesh_run(session, mesh)
        assert session.pattern_trend["ruff|F821"] == [2]
        assert session.pattern_trend["mypy|import-error"] == [1]

    def test_pattern_alerts_extracted(self) -> None:
        session = SessionMemory()
        alerts = [{"name": "repeat_pattern", "count": 3}]
        mesh = _make_mesh_result(lint_pattern_alerts=alerts)
        snapshot = record_mesh_run(session, mesh)
        assert snapshot.pattern_alerts == alerts

    def test_snapshot_trimming(self) -> None:
        session = SessionMemory()
        # Add 55 snapshots (exceeds _MAX_SNAPSHOTS=50)
        for i in range(55):
            mesh = _make_mesh_result(event_id=f"evt{i:03d}")
            record_mesh_run(session, mesh)
        assert len(session.snapshots) == 50
        # The earliest snapshots should have been trimmed
        assert session.snapshots[0].run_id == "evt005"
        assert session.snapshots[-1].run_id == "evt054"

    def test_finding_index_recorded(self) -> None:
        session = SessionMemory()
        mesh = _make_mesh_result()
        idx = {"fp1": {"linter": "ruff", "kind": "F821"}}
        snapshot = record_mesh_run(session, mesh, finding_index=idx)
        assert snapshot.finding_index == idx

    def test_finding_index_default_empty(self) -> None:
        session = SessionMemory()
        mesh = _make_mesh_result()
        snapshot = record_mesh_run(session, mesh)
        assert snapshot.finding_index == {}

    def test_no_event_uses_uuid(self) -> None:
        session = SessionMemory()
        mesh = MeshResult(
            event=None,  # type: ignore[arg-type]
            channel_results=[],
            coherence=CoherenceResult(state="stable"),
        )
        snapshot = record_mesh_run(session, mesh)
        assert len(snapshot.run_id) == 12  # uuid hex[:12]

    def test_coherence_loud_silent(self) -> None:
        session = SessionMemory()
        mesh = _make_mesh_result(
            coherence_state="isolated",
            loud=["lint"],
            silent=["test", "structure"],
        )
        snapshot = record_mesh_run(session, mesh)
        assert snapshot.loud_channels == ["lint"]
        assert snapshot.silent_channels == ["test", "structure"]

    def test_existing_repair_not_overwritten(self) -> None:
        """record_mesh_run should not overwrite existing repair outcomes."""
        session = SessionMemory(repair_outcomes={"fix1": "applied"})
        repairs = [RepairAction(action_id="fix1", channel="lint", summary="s")]
        mesh = _make_mesh_result(repairs=repairs)
        record_mesh_run(session, mesh)
        assert session.repair_outcomes["fix1"] == "applied"

    def test_last_active_updated(self) -> None:
        session = SessionMemory(last_active=0.0)
        mesh = _make_mesh_result()
        before = time.time()
        record_mesh_run(session, mesh)
        assert session.last_active >= before

    def test_pattern_trend_trimming(self) -> None:
        session = SessionMemory()
        # Pre-fill pattern_trend with 50 entries
        session.pattern_trend["ruff|F821"] = list(range(50))
        findings = [_FakeLintIssue(linter="ruff", kind="F821")]
        mesh = _make_mesh_result(findings=findings)
        record_mesh_run(session, mesh)
        # Should be trimmed to 50
        assert len(session.pattern_trend["ruff|F821"]) == 50

    def test_multiple_channel_results(self) -> None:
        """Findings and repairs across multiple channel results are aggregated."""
        session = SessionMemory()
        cr1 = ChannelResult(
            channel="lint",
            findings=[_FakeLintIssue(severity="blocking")],  # type: ignore[list-item]
            repairs=[RepairAction(action_id="r1", channel="lint", summary="f1")],
            metrics={},
        )
        cr2 = ChannelResult(
            channel="test",
            findings=[_FakeLintIssue(linter="pytest", kind="test_fail", severity="warning")],  # type: ignore[list-item]
            repairs=[RepairAction(action_id="r2", channel="test", summary="f2")],
            metrics={},
        )
        mesh = MeshResult(
            event=SupervisionEvent(event_id="multi"),
            channel_results=[cr1, cr2],
            coherence=CoherenceResult(state="coupled"),
        )
        snapshot = record_mesh_run(session, mesh)
        assert snapshot.finding_count == 2
        assert snapshot.blocking_count == 1
        assert "r1" in snapshot.repairs_proposed
        assert "r2" in snapshot.repairs_proposed


# ── 16. Integration: save then load round-trip ──────────────────────────


class TestSaveLoadIntegration:
    def test_full_round_trip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path)
        session = SessionMemory(
            project_root="/tmp/integration",
            session_id="int1",
            repair_outcomes={"r1": "pending"},
            behavior_compass={"hypothesis_version": 2},
        )
        # Record a mesh run
        mesh = _make_mesh_result(
            findings=[_FakeLintIssue(severity="blocking")],
            repairs=[RepairAction(action_id="r2", channel="lint", summary="fix")],
        )
        record_mesh_run(session, mesh)
        save_session(session)

        loaded = load_session("/tmp/integration")
        assert loaded is not None
        assert loaded.session_id == "int1"
        assert len(loaded.snapshots) == 1
        assert loaded.repair_outcomes["r1"] == "pending"
        assert loaded.repair_outcomes["r2"] == "pending"
        assert loaded.behavior_compass == {"hypothesis_version": 2}
