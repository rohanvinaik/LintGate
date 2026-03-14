"""Tests for session memory — cross-run state accumulation."""

from __future__ import annotations

import json
import time
from typing import Literal
from unittest.mock import patch

from lintgate.controlplane.session_memory import (
    BehaviorEventData,
    SessionMemory,
    SessionSnapshot,
    _extract_test_failure_keys,
    _project_hash,
    _session_path,
    check_session_exit_gate,
    detect_applied_repairs,
    escalate_persistent_failures,
    expire_session,
    get_habit_mode_active,
    get_or_create_session,
    load_behavior_compass,
    load_session,
    propose_repairs,
    record_mesh_run,
    record_test_failure_classification,
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
from lintgate.types import LintIssue

# ── Helpers ──────────────────────────────────────────────────────────


def _make_mesh_result(
    coherence_state: Literal["stable", "isolated", "coupled", "systemic", "degraded"] = "stable",
    loud: list[str] | None = None,
    silent: list[str] | None = None,
    findings: list[LintIssue] | None = None,
    repairs: list[RepairAction] | None = None,
    event_id: str = "test123",
) -> MeshResult:
    """Build a MeshResult for testing."""
    loud = loud or []
    silent = silent or []
    findings = findings or []
    repairs = repairs or []

    channel_results = []
    for name in loud:
        cr = ChannelResult(
            channel=name,
            status="fail",
            severity="warning",
            findings=[f for f in findings if f.linter == name] or findings[:1],
        )
        channel_results.append(cr)
    for name in silent:
        channel_results.append(ChannelResult(channel=name, status="pass"))

    if repairs:
        if channel_results:
            channel_results[0].repairs = repairs
        else:
            channel_results.append(
                ChannelResult(
                    channel="lint",
                    status="fail",
                    repairs=repairs,
                    findings=findings[:1] if findings else [],
                )
            )

    coherence = CoherenceResult(
        state=coherence_state,
        loud_channels=loud,
        silent_channels=silent,
        summary=f"State: {coherence_state}",
    )

    event = SupervisionEvent(event_id=event_id, project_root="/test/project")

    return MeshResult(
        event=event,
        channel_results=channel_results,
        coherence=coherence,
        duration_ms=42.0,
    )


def _make_mesh_with_lint_alerts(
    pattern_alerts: list[dict],
    coherence_state: Literal["stable", "isolated", "coupled", "systemic", "degraded"] = "stable",
    event_id: str = "alert_test",
) -> MeshResult:
    """Build a MeshResult with lint channel pattern alerts."""
    cr = ChannelResult(
        channel="lint",
        status="pass",
        metrics={"pattern_alerts": pattern_alerts},
    )
    coherence = CoherenceResult(state=coherence_state)
    event = SupervisionEvent(event_id=event_id, project_root="/test/project")
    return MeshResult(
        event=event,
        channel_results=[cr],
        coherence=coherence,
    )


# ── BehaviorEventData ────────────────────────────────────────────────


class TestBehaviorEventData:
    def test_populated_round_trip(self) -> None:
        bed = BehaviorEventData(
            action_type="bash",
            command_signature="pytest:run",
            exit_code=1,
            error_signature="ImportError",
            behavior_alerts=["approach_cycling"],
            prediction_accuracy=0.75,
            predictions_checked=4,
        )
        d = bed.to_dict()
        restored = BehaviorEventData.from_dict(d)
        assert restored.action_type == "bash"
        assert restored.command_signature == "pytest:run"
        assert restored.exit_code == 1
        assert restored.error_signature == "ImportError"
        assert restored.behavior_alerts == ["approach_cycling"]
        assert restored.prediction_accuracy == 0.75
        assert restored.predictions_checked == 4

    def test_defaults(self) -> None:
        bed = BehaviorEventData()
        assert bed.action_type == ""
        assert bed.command_signature == ""
        assert bed.exit_code is None
        assert bed.error_signature == ""
        assert bed.behavior_alerts == []
        assert bed.prediction_accuracy is None
        assert bed.predictions_checked == 0

    def test_from_dict_empty(self) -> None:
        bed = BehaviorEventData.from_dict({})
        assert bed.action_type == ""
        assert bed.exit_code is None
        assert bed.behavior_alerts == []

    def test_from_dict_partial(self) -> None:
        bed = BehaviorEventData.from_dict({"action_type": "edit", "exit_code": 0})
        assert bed.action_type == "edit"
        assert bed.exit_code == 0
        assert bed.command_signature == ""

    def test_to_dict_includes_all_fields(self) -> None:
        bed = BehaviorEventData(action_type="glob", exit_code=0)
        d = bed.to_dict()
        assert "action_type" in d
        assert "command_signature" in d
        assert "exit_code" in d
        assert "error_signature" in d
        assert "behavior_alerts" in d
        assert "prediction_accuracy" in d
        assert "predictions_checked" in d


# ── Snapshot Serialization ───────────────────────────────────────────


class TestSnapshotSerialization:
    def test_roundtrip(self):
        snap = SessionSnapshot(
            run_id="run1",
            timestamp=1000.0,
            coherence_state="isolated",
            loud_channels=["lint"],
            silent_channels=["tests", "deps"],
            finding_count=3,
            blocking_count=1,
            pattern_alerts=[{"linter": "ruff", "kind": "F821"}],
            repairs_proposed=["repair_a"],
            repairs_applied=[],
        )
        data = snap.to_dict()
        restored = SessionSnapshot.from_dict(data)

        assert restored.run_id == "run1"
        assert restored.coherence_state == "isolated"
        assert restored.loud_channels == ["lint"]
        assert restored.silent_channels == ["tests", "deps"]
        assert restored.finding_count == 3
        assert restored.blocking_count == 1
        assert len(restored.pattern_alerts) == 1
        assert restored.repairs_proposed == ["repair_a"]

    def test_from_dict_missing_fields(self):
        """Graceful handling of partial data."""
        snap = SessionSnapshot.from_dict({"run_id": "partial"})
        assert snap.run_id == "partial"
        assert snap.coherence_state == ""
        assert snap.loud_channels == []
        assert snap.finding_count == 0

    def test_from_dict_empty(self):
        snap = SessionSnapshot.from_dict({})
        assert snap.run_id == ""
        assert snap.timestamp == 0.0

    def test_finding_index_roundtrip(self):
        """finding_index field serializes and deserializes correctly."""
        snap = SessionSnapshot(
            run_id="run_idx",
            finding_index={
                "fp_abc123": {
                    "channel": "lint",
                    "kind": "F821",
                    "severity": "blocking",
                    "message": "Undefined 'x'",
                },
                "fp_def456": {
                    "channel": "tests",
                    "kind": "test_fail",
                    "severity": "warning",
                    "message": "test failed",
                },
            },
        )
        data = snap.to_dict()
        restored = SessionSnapshot.from_dict(data)
        assert len(restored.finding_index) == 2
        assert restored.finding_index["fp_abc123"]["channel"] == "lint"
        assert restored.finding_index["fp_def456"]["severity"] == "warning"

    def test_finding_index_defaults_empty(self):
        """Snapshots without finding_index get empty dict default."""
        snap = SessionSnapshot.from_dict({"run_id": "old_snap"})
        assert snap.finding_index == {}

    def test_behavior_backward_compat_properties(self):
        """Backward-compat property accessors delegate to behavior field."""
        bed = BehaviorEventData(
            action_type="bash",
            command_signature="git:status",
            exit_code=0,
            error_signature="",
            behavior_alerts=["alert1"],
            prediction_accuracy=0.9,
            predictions_checked=5,
        )
        snap = SessionSnapshot(run_id="compat_test", behavior=bed)
        assert snap.action_type == "bash"
        assert snap.command_signature == "git:status"
        assert snap.exit_code == 0
        assert snap.error_signature == ""
        assert snap.behavior_alerts == ["alert1"]
        assert snap.prediction_accuracy == 0.9
        assert snap.predictions_checked == 5

    def test_to_dict_flattens_behavior(self):
        """to_dict flattens behavior fields to top level for backward compat."""
        bed = BehaviorEventData(action_type="edit", exit_code=1)
        snap = SessionSnapshot(run_id="flat_test", behavior=bed)
        d = snap.to_dict()
        # Behavior fields are at top level, not nested
        assert d["action_type"] == "edit"
        assert d["exit_code"] == 1
        assert "behavior" not in d

    def test_disposition_nudge_compliance_roundtrip(self):
        """Disposition, last_nudge, and compliance_outcome serialize correctly."""
        snap = SessionSnapshot(
            run_id="disp_test",
            disposition="Run lint before next edit",
            last_nudge={"type": "edit_without_lint", "count": 2},
            compliance_outcome="followed",
        )
        d = snap.to_dict()
        restored = SessionSnapshot.from_dict(d)
        assert restored.disposition == "Run lint before next edit"
        assert restored.last_nudge == {"type": "edit_without_lint", "count": 2}
        assert restored.compliance_outcome == "followed"

    def test_disposition_nudge_defaults_none(self):
        """Disposition, last_nudge, and compliance_outcome default to None."""
        snap = SessionSnapshot.from_dict({})
        assert snap.disposition is None
        assert snap.last_nudge is None
        assert snap.compliance_outcome is None

    def test_delivery_metrics_roundtrip(self):
        """delivery_metrics field serializes and deserializes correctly."""
        snap = SessionSnapshot(
            run_id="metrics_test",
            delivery_metrics={"lint_ms": 120, "tests_ms": 300},
        )
        d = snap.to_dict()
        restored = SessionSnapshot.from_dict(d)
        assert restored.delivery_metrics == {"lint_ms": 120, "tests_ms": 300}

    def test_repair_catalog_roundtrip(self):
        """repair_catalog serializes and deserializes correctly."""
        snap = SessionSnapshot(
            run_id="catalog_test",
            repair_catalog={
                "fix_a": {"channel": "lint", "kind": "command", "summary": "Fix A", "safe": "true"},
            },
        )
        d = snap.to_dict()
        restored = SessionSnapshot.from_dict(d)
        assert "fix_a" in restored.repair_catalog
        assert restored.repair_catalog["fix_a"]["channel"] == "lint"


# ── Finding Index in record_mesh_run ─────────────────────────────────


class TestRecordMeshRunFindingIndex:
    def test_stores_finding_index_on_snapshot(self):
        """record_mesh_run stores provided finding_index on the snapshot."""
        session = SessionMemory(project_root="/test/proj")
        mesh = _make_mesh_result(coherence_state="stable", event_id="idx_test")
        finding_idx = {
            "fp_001": {
                "channel": "lint",
                "kind": "F821",
                "severity": "blocking",
                "message": "error",
            },
        }
        snapshot = record_mesh_run(session, mesh, finding_index=finding_idx)
        assert snapshot.finding_index == finding_idx
        # Also stored in session
        assert session.snapshots[-1].finding_index == finding_idx

    def test_empty_finding_index_when_not_provided(self):
        """record_mesh_run without finding_index gets empty dict."""
        session = SessionMemory(project_root="/test/proj")
        mesh = _make_mesh_result(coherence_state="stable", event_id="no_idx")
        snapshot = record_mesh_run(session, mesh)
        assert snapshot.finding_index == {}


# ── Session Serialization ────────────────────────────────────────────


class TestSessionSerialization:
    def test_roundtrip(self):
        session = SessionMemory(
            session_id="sess1",
            project_root="/test/proj",
            started_at=1000.0,
            last_active=2000.0,
            coherence_trajectory=["stable", "isolated", "stable"],
            repair_outcomes={"fix_a": "applied", "fix_b": "pending"},
            pattern_trend={"ruff|F821": [1, 0, 2]},
        )
        session.snapshots.append(
            SessionSnapshot(
                run_id="run1",
                coherence_state="stable",
            )
        )

        data = session.to_dict()
        restored = SessionMemory.from_dict(data)

        assert restored.session_id == "sess1"
        assert restored.project_root == "/test/proj"
        assert restored.started_at == 1000.0
        assert len(restored.snapshots) == 1
        assert restored.snapshots[0].run_id == "run1"
        assert restored.coherence_trajectory == ["stable", "isolated", "stable"]
        assert restored.repair_outcomes["fix_a"] == "applied"
        assert restored.pattern_trend["ruff|F821"] == [1, 0, 2]

    def test_from_dict_empty(self):
        session = SessionMemory.from_dict({})
        assert session.project_root == ""
        assert session.snapshots == []
        assert session.coherence_trajectory == []

    def test_theory_profile_cache_not_persisted(self):
        """theory_profile_cache is transient and not included in to_dict."""
        session = SessionMemory(session_id="transient_test")
        session.theory_profile_cache = {"facet": "core_theory", "claims": ["c1"]}
        data = session.to_dict()
        assert "theory_profile_cache" not in data

    def test_theory_profile_cache_none_on_load(self):
        """theory_profile_cache is always None after from_dict (transient)."""
        data = {
            "session_id": "load_test",
            "theory_profile_cache": {"facet": "something"},
        }
        session = SessionMemory.from_dict(data)
        assert session.theory_profile_cache is None

    def test_pending_patches_roundtrip(self):
        """pending_patches serialize and deserialize correctly."""
        session = SessionMemory(session_id="patches_test")
        session.pending_patches = [
            {"section": "machine_rules", "diff": "+new rule"},
        ]
        data = session.to_dict()
        restored = SessionMemory.from_dict(data)
        assert len(restored.pending_patches) == 1
        assert restored.pending_patches[0]["section"] == "machine_rules"

    def test_resolution_repertoire_roundtrip(self):
        """resolution_repertoire survives serialization."""
        session = SessionMemory(session_id="repertoire_test")
        session.resolution_repertoire = [{"pattern": "fix_import", "count": 3}]
        data = session.to_dict()
        restored = SessionMemory.from_dict(data)
        assert len(restored.resolution_repertoire) == 1

    def test_active_finding_history_roundtrip(self):
        """active_finding_history serializes correctly."""
        session = SessionMemory(session_id="afh_test")
        session.active_finding_history = {
            "fp_123": {"first_seen": 1000.0, "status": "active"},
        }
        data = session.to_dict()
        restored = SessionMemory.from_dict(data)
        assert "fp_123" in restored.active_finding_history

    def test_action_history_roundtrip(self):
        """action_history serializes correctly."""
        session = SessionMemory(session_id="ah_test")
        session.action_history = [{"action": "bash", "ts": 1000.0}]
        data = session.to_dict()
        restored = SessionMemory.from_dict(data)
        assert len(restored.action_history) == 1

    def test_edit_cycle_state_roundtrip(self):
        """edit_cycle_state serializes correctly."""
        session = SessionMemory(session_id="ecs_test")
        session.edit_cycle_state = {"edits_since_lint": 2}
        data = session.to_dict()
        restored = SessionMemory.from_dict(data)
        assert restored.edit_cycle_state["edits_since_lint"] == 2

    def test_delivery_health_summary_roundtrip(self):
        """delivery_health_summary serializes correctly."""
        session = SessionMemory(session_id="dhs_test")
        session.delivery_health_summary = {"lint_p50_ms": 45}
        data = session.to_dict()
        restored = SessionMemory.from_dict(data)
        assert restored.delivery_health_summary["lint_p50_ms"] == 45

    def test_latest_transfer_packet_roundtrip(self):
        """latest_transfer_packet persists through serialization."""
        session = SessionMemory(session_id="ltp_test")
        session.latest_transfer_packet = {"source_agent_id": "agent_a"}
        data = session.to_dict()
        restored = SessionMemory.from_dict(data)
        assert restored.latest_transfer_packet is not None
        assert restored.latest_transfer_packet["source_agent_id"] == "agent_a"

    def test_latest_transfer_packet_none_default(self):
        """latest_transfer_packet defaults to None."""
        session = SessionMemory.from_dict({})
        assert session.latest_transfer_packet is None

    def test_knowledge_meta_roundtrip(self):
        """knowledge_meta survives from_dict even though not in to_dict."""
        data = {"knowledge_meta": {"staleness_hrs": 2.5, "survival_ratio": 0.8}}
        session = SessionMemory.from_dict(data)
        assert session.knowledge_meta["staleness_hrs"] == 2.5
        assert session.knowledge_meta["survival_ratio"] == 0.8


# ── Persistence (load/save) ──────────────────────────────────────────


class TestPersistence:
    def test_save_and_load(self, tmp_path):
        with patch("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path):
            session = SessionMemory(
                session_id="persist_test",
                project_root="/test/persist",
            )
            session.snapshots.append(SessionSnapshot(run_id="r1"))
            save_session(session)

            loaded = load_session("/test/persist")
            assert loaded is not None
            assert loaded.session_id == "persist_test"
            assert len(loaded.snapshots) == 1
            assert loaded.snapshots[0].run_id == "r1"

    def test_load_nonexistent(self, tmp_path):
        with patch("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path):
            assert load_session("/nonexistent") is None

    def test_load_corrupted(self, tmp_path):
        with patch("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path):
            session_path = tmp_path / f"{_project_hash('/bad')}.json"
            session_path.write_text("not json!!!")
            assert load_session("/bad") is None

    def test_load_non_dict_json(self, tmp_path):
        """A JSON file that is not a dict returns None."""
        with patch("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path):
            session_path = tmp_path / f"{_project_hash('/array')}.json"
            session_path.write_text("[1, 2, 3]")
            assert load_session("/array") is None

    def test_save_updates_last_active(self, tmp_path):
        """save_session updates last_active to current time."""
        with patch("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path):
            session = SessionMemory(
                session_id="ts_test",
                project_root="/test/ts",
                last_active=1000.0,
            )
            before = time.time()
            save_session(session)
            after = time.time()
            assert before <= session.last_active <= after

    def test_save_creates_directory(self, tmp_path):
        """save_session creates SESSION_DIR if it doesn't exist."""
        nested = tmp_path / "deep" / "nested"
        with patch("lintgate.controlplane.session_memory.SESSION_DIR", nested):
            session = SessionMemory(
                session_id="mkdir_test",
                project_root="/test/mkdir",
            )
            save_session(session)
            assert nested.exists()


# ── get_or_create_session ────────────────────────────────────────────


class TestGetOrCreate:
    def test_creates_new_session(self, tmp_path):
        with patch("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path):
            session = get_or_create_session("/new/project")
            assert session.project_root == "/new/project"
            assert session.session_id  # auto-generated
            assert len(session.snapshots) == 0

    def test_loads_existing(self, tmp_path):
        with patch("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path):
            # Create and save a session
            original = SessionMemory(
                session_id="existing",
                project_root="/existing/project",
                last_active=time.time(),
            )
            original.coherence_trajectory = ["stable", "isolated"]
            save_session(original)

            # Load it back
            loaded = get_or_create_session("/existing/project")
            assert loaded.session_id == "existing"
            assert loaded.coherence_trajectory == ["stable", "isolated"]

    def test_replaces_expired(self, tmp_path):
        with patch("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path):
            old = SessionMemory(
                session_id="old_session",
                project_root="/expired/project",
                last_active=time.time() - 5 * 3600,  # 5 hours ago
            )
            # Write directly to disk to avoid save_session updating last_active
            session_path = tmp_path / f"{_project_hash('/expired/project')}.json"
            with open(session_path, "w") as f:
                json.dump(old.to_dict(), f)

            fresh = get_or_create_session("/expired/project", max_age_hours=4.0)
            assert fresh.session_id != "old_session"
            assert len(fresh.snapshots) == 0

    def test_loads_transfer_packet_on_new_session(self, tmp_path):
        """When no session exists but a transfer packet is on disk, preload it."""
        import hashlib

        project_root = "/transfer/project"
        transfer_hash = hashlib.sha256(project_root.encode()).hexdigest()[:12]
        transfer_path = tmp_path / f"transfer_{transfer_hash}.json"
        packet = {
            "source_agent_id": "agent_a",
            "active_findings": [
                {"fingerprint": "fp_1", "first_seen": 1000.0, "severity": "warning"},
            ],
        }
        transfer_path.write_text(json.dumps(packet))

        with patch("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path):
            session = get_or_create_session(project_root)
            # Transfer packet should be preloaded
            assert session.latest_transfer_packet is not None
            # Transfer creates a snapshot
            assert len(session.snapshots) == 1
            assert session.snapshots[0].disposition == "Preloaded from transfer packet"
            # Finding history should be populated
            assert "fp_1" in session.active_finding_history
            # Transfer file should be cleaned up
            assert not transfer_path.exists()

    def test_transfer_packet_corrupt_json_ignored(self, tmp_path):
        """Corrupt transfer packet is silently ignored."""
        import hashlib

        project_root = "/bad_transfer/project"
        transfer_hash = hashlib.sha256(project_root.encode()).hexdigest()[:12]
        transfer_path = tmp_path / f"transfer_{transfer_hash}.json"
        transfer_path.write_text("not valid json!!")

        with patch("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path):
            session = get_or_create_session(project_root)
            assert session.latest_transfer_packet is None
            assert len(session.snapshots) == 0


# ── Expiry ───────────────────────────────────────────────────────────


class TestExpiry:
    def test_not_expired(self):
        session = SessionMemory(last_active=time.time())
        assert expire_session(session, max_age_hours=4.0) is False

    def test_expired(self):
        session = SessionMemory(last_active=time.time() - 5 * 3600)
        assert expire_session(session, max_age_hours=4.0) is True

    def test_zero_max_age_never_expires(self):
        session = SessionMemory(last_active=time.time() - 100 * 3600)
        assert expire_session(session, max_age_hours=0.0) is False

    def test_negative_max_age_never_expires(self):
        session = SessionMemory(last_active=time.time() - 100 * 3600)
        assert expire_session(session, max_age_hours=-1.0) is False

    def test_boundary(self):
        """Just under boundary should not expire, just over should."""
        now = time.time()
        # 3h59m -- not expired
        session = SessionMemory(last_active=now - 3 * 3600 - 59 * 60)
        assert expire_session(session, max_age_hours=4.0) is False

        # 4h1m -- expired
        session2 = SessionMemory(last_active=now - 4 * 3600 - 60)
        assert expire_session(session2, max_age_hours=4.0) is True

    def test_very_small_max_age(self):
        """A very small (but positive) max_age should expire quickly."""
        session = SessionMemory(last_active=time.time() - 10)
        assert expire_session(session, max_age_hours=0.001) is True


# ── Record Mesh Run ──────────────────────────────────────────────────


class TestRecordMeshRun:
    def test_appends_snapshot(self):
        session = SessionMemory(project_root="/test")
        mesh = _make_mesh_result(
            coherence_state="isolated",
            loud=["lint"],
            silent=["tests", "deps"],
        )
        snap = record_mesh_run(session, mesh)

        assert len(session.snapshots) == 1
        assert snap.coherence_state == "isolated"
        assert snap.loud_channels == ["lint"]
        assert snap.silent_channels == ["tests", "deps"]

    def test_updates_coherence_trajectory(self):
        session = SessionMemory(project_root="/test")

        mesh1 = _make_mesh_result(coherence_state="stable")
        record_mesh_run(session, mesh1)

        mesh2 = _make_mesh_result(coherence_state="isolated")
        record_mesh_run(session, mesh2)

        mesh3 = _make_mesh_result(coherence_state="stable")
        record_mesh_run(session, mesh3)

        assert session.coherence_trajectory == ["stable", "isolated", "stable"]

    def test_counts_findings(self):
        session = SessionMemory(project_root="/test")
        findings = [
            LintIssue(linter="ruff", kind="F821", severity="blocking", message="undef"),
            LintIssue(linter="ruff", kind="F401", severity="warning", message="unused"),
        ]
        mesh = _make_mesh_result(
            coherence_state="isolated",
            loud=["lint"],
            findings=findings,
        )
        # Ensure findings are on the channel result
        mesh.channel_results[0].findings = findings

        snap = record_mesh_run(session, mesh)
        assert snap.finding_count == 2
        assert snap.blocking_count == 1

    def test_tracks_pattern_trend(self):
        session = SessionMemory(project_root="/test")

        for count in [2, 0, 3]:
            findings = [
                LintIssue(linter="ruff", kind="F821", severity="warning", message=f"issue {i}")
                for i in range(count)
            ]
            mesh = _make_mesh_result(coherence_state="stable", loud=["lint"] if count else [])
            if count:
                mesh.channel_results[0].findings = findings
            record_mesh_run(session, mesh)

        assert session.pattern_trend.get("ruff|F821") == [
            2,
            3,
        ]  # 0-count runs aren't tracked

    def test_registers_pending_repairs(self):
        session = SessionMemory(project_root="/test")
        repairs = [
            RepairAction(action_id="fix_a", summary="Fix A"),
            RepairAction(action_id="fix_b", summary="Fix B"),
        ]
        mesh = _make_mesh_result(coherence_state="isolated", repairs=repairs)

        snap = record_mesh_run(session, mesh)
        assert "fix_a" in session.repair_outcomes
        assert session.repair_outcomes["fix_a"] == "pending"
        assert "fix_b" in session.repair_outcomes
        assert snap.repairs_proposed == ["fix_a", "fix_b"]

    def test_snapshot_trimming(self):
        session = SessionMemory(project_root="/test")
        # Add 55 snapshots -- should trim to 50
        for i in range(55):
            mesh = _make_mesh_result(coherence_state="stable", event_id=f"evt_{i}")
            record_mesh_run(session, mesh)

        assert len(session.snapshots) <= 50

    def test_snapshot_trimming_preserves_latest(self):
        """After trimming, the most recent snapshot should be the last one added."""
        session = SessionMemory(project_root="/test")
        for i in range(55):
            mesh = _make_mesh_result(coherence_state="stable", event_id=f"evt_{i}")
            record_mesh_run(session, mesh)

        assert session.snapshots[-1].run_id == "evt_54"
        # Oldest should be evt_5 (55-50=5)
        assert session.snapshots[0].run_id == "evt_5"

    def test_does_not_overwrite_existing_repair_outcome(self):
        """If a repair is already marked applied, record_mesh_run won't reset to pending."""
        session = SessionMemory(project_root="/test")
        session.repair_outcomes["fix_a"] = "applied"
        repairs = [RepairAction(action_id="fix_a", summary="Fix A")]
        mesh = _make_mesh_result(coherence_state="stable", repairs=repairs)
        record_mesh_run(session, mesh)
        assert session.repair_outcomes["fix_a"] == "applied"

    def test_repair_catalog_populated(self):
        """record_mesh_run populates repair_catalog on snapshot."""
        session = SessionMemory(project_root="/test")
        repairs = [
            RepairAction(
                action_id="fix_c",
                channel="lint",
                kind="command",
                summary="Auto fix",
                safe=True,
            ),
        ]
        mesh = _make_mesh_result(coherence_state="stable", repairs=repairs)
        snap = record_mesh_run(session, mesh)
        assert "fix_c" in snap.repair_catalog
        assert snap.repair_catalog["fix_c"]["channel"] == "lint"
        assert snap.repair_catalog["fix_c"]["safe"] == "true"

    def test_pattern_alerts_from_lint_channel(self):
        """Pattern alerts are extracted from the lint channel metrics."""
        session = SessionMemory(project_root="/test")
        alerts = [{"linter": "ruff", "kind": "F821", "count": 3}]
        mesh = _make_mesh_with_lint_alerts(alerts)
        snap = record_mesh_run(session, mesh)
        assert snap.pattern_alerts == alerts

    def test_disposition_and_nudge_stored(self):
        """record_mesh_run stores disposition, last_nudge, compliance_outcome."""
        session = SessionMemory(project_root="/test")
        mesh = _make_mesh_result(coherence_state="stable")
        nudge = {"type": "edit_without_lint", "count": 1}
        snap = record_mesh_run(
            session,
            mesh,
            disposition="Run lint",
            last_nudge=nudge,
            compliance_outcome="followed",
        )
        assert snap.disposition == "Run lint"
        assert snap.last_nudge == nudge
        assert snap.compliance_outcome == "followed"

    def test_default_event_uses_event_id(self):
        """When mesh_result uses default event, event_id is used as run_id."""
        session = SessionMemory(project_root="/test")
        mesh = MeshResult(
            channel_results=[],
            coherence=CoherenceResult(state="stable"),
        )
        snap = record_mesh_run(session, mesh)
        assert snap.run_id  # Non-empty

    def test_pattern_trend_bounded(self):
        """Pattern trend lists are bounded to _MAX_SNAPSHOTS."""
        session = SessionMemory(project_root="/test")
        for i in range(55):
            findings = [
                LintIssue(linter="ruff", kind="E501", severity="warning", message=f"line {i}")
            ]
            mesh = _make_mesh_result(coherence_state="stable", loud=["lint"])
            mesh.channel_results[0].findings = findings
            record_mesh_run(session, mesh)
        assert len(session.pattern_trend["ruff|E501"]) <= 50

    def test_updates_last_active(self):
        """record_mesh_run updates session.last_active."""
        session = SessionMemory(project_root="/test", last_active=1000.0)
        mesh = _make_mesh_result(coherence_state="stable")
        before = time.time()
        record_mesh_run(session, mesh)
        assert session.last_active >= before


# ── Repair Tracking ──────────────────────────────────────────────────


class TestRepairTracking:
    def test_propose_repairs(self):
        session = SessionMemory(project_root="/test")
        repairs = [
            RepairAction(action_id="r1", summary="Repair 1"),
            RepairAction(action_id="r2", summary="Repair 2"),
        ]
        propose_repairs(session, repairs)

        assert session.repair_outcomes["r1"] == "pending"
        assert session.repair_outcomes["r2"] == "pending"

    def test_propose_idempotent(self):
        session = SessionMemory(project_root="/test")
        session.repair_outcomes["r1"] = "applied"

        repairs = [RepairAction(action_id="r1", summary="Repair 1")]
        propose_repairs(session, repairs)

        # Should NOT overwrite existing "applied" status
        assert session.repair_outcomes["r1"] == "applied"

    def test_report_outcome(self):
        session = SessionMemory(project_root="/test")
        session.repair_outcomes["r1"] = "pending"

        report_repair_outcome(session, "r1", "applied")
        assert session.repair_outcomes["r1"] == "applied"

        report_repair_outcome(session, "r1", "ignored")
        assert session.repair_outcomes["r1"] == "ignored"

    def test_report_outcome_new_action_id(self):
        """report_repair_outcome works even for action_ids not yet in outcomes."""
        session = SessionMemory(project_root="/test")
        report_repair_outcome(session, "new_action", "rejected")
        assert session.repair_outcomes["new_action"] == "rejected"

    def test_propose_empty_list(self):
        """propose_repairs with empty list is a no-op."""
        session = SessionMemory(project_root="/test")
        propose_repairs(session, [])
        assert session.repair_outcomes == {}


# ── Detect Applied Repairs ───────────────────────────────────────────


class TestDetectAppliedRepairs:
    def test_no_snapshots_returns_empty(self) -> None:
        session = SessionMemory()
        assert detect_applied_repairs(session, []) == []

    def test_single_snapshot_returns_empty(self):
        """With only one snapshot, no comparison is possible."""
        session = SessionMemory()
        session.snapshots.append(SessionSnapshot(run_id="only"))
        assert detect_applied_repairs(session, []) == []

    def test_two_snapshots_returns_empty_for_now(self):
        """Current implementation returns empty (heuristic stub)."""
        session = SessionMemory()
        session.snapshots.append(SessionSnapshot(run_id="first", blocking_count=2))
        session.snapshots.append(SessionSnapshot(run_id="second", blocking_count=0))
        session.repair_outcomes["fix_1"] = "pending"
        result = detect_applied_repairs(session, [])
        # Current implementation is conservative - returns empty list
        assert isinstance(result, list)


# ── Behavior Compass Helpers ─────────────────────────────────────────


class TestLoadSaveBehaviorCompass:
    def test_round_trip(self) -> None:
        from lintgate.controlplane.behavior_types import BehaviorCompass

        session = SessionMemory()
        compass = BehaviorCompass(hypothesis_version=7, uncertainty_zones=["env"])
        save_behavior_compass(session, compass)
        restored = load_behavior_compass(session)
        assert restored.hypothesis_version == 7
        assert restored.uncertainty_zones == ["env"]

    def test_load_empty_compass(self) -> None:
        """Loading from empty behavior_compass dict returns fresh compass."""
        session = SessionMemory()
        compass = load_behavior_compass(session)
        assert compass is not None
        # Should be a valid BehaviorCompass with defaults
        assert isinstance(compass.hypothesis_version, int)

    def test_save_replaces_compass(self) -> None:
        """Saving overwrites existing compass data."""
        from lintgate.controlplane.behavior_types import BehaviorCompass

        session = SessionMemory()
        c1 = BehaviorCompass(hypothesis_version=1)
        c2 = BehaviorCompass(hypothesis_version=2)
        save_behavior_compass(session, c1)
        save_behavior_compass(session, c2)
        restored = load_behavior_compass(session)
        assert restored.hypothesis_version == 2


# ── Habit Mode ───────────────────────────────────────────────────────


class TestGetHabitModeActive:
    def test_active_true(self) -> None:
        session = SessionMemory(behavior_compass={"habit_mode": {"active": True}})
        assert get_habit_mode_active(session) is True

    def test_active_false(self) -> None:
        session = SessionMemory(behavior_compass={"habit_mode": {"active": False}})
        assert get_habit_mode_active(session) is False

    def test_missing_habit_mode(self) -> None:
        session = SessionMemory(behavior_compass={})
        assert get_habit_mode_active(session) is False

    def test_missing_active_key(self) -> None:
        session = SessionMemory(behavior_compass={"habit_mode": {}})
        assert get_habit_mode_active(session) is False

    def test_empty_compass(self) -> None:
        session = SessionMemory()
        assert get_habit_mode_active(session) is False


# ── Persistent Test Failure Tracking ─────────────────────────────────


class TestExtractTestFailureKeys:
    def test_extracts_test_failure_kind(self):
        snap = SessionSnapshot(
            run_id="extract",
            finding_index={
                "fp_1": {"kind": "test_failure", "message": "test broke"},
                "fp_2": {"kind": "F821", "message": "undef"},
            },
        )
        keys = _extract_test_failure_keys(snap)
        assert keys == {"fp_1"}

    def test_extracts_teff009_kind(self):
        snap = SessionSnapshot(
            run_id="teff",
            finding_index={
                "fp_1": {"kind": "TEFF009", "message": "teff finding"},
            },
        )
        keys = _extract_test_failure_keys(snap)
        assert keys == {"fp_1"}

    def test_empty_finding_index(self):
        snap = SessionSnapshot(run_id="empty")
        keys = _extract_test_failure_keys(snap)
        assert keys == set()

    def test_no_matching_kinds(self):
        snap = SessionSnapshot(
            run_id="nomatch",
            finding_index={
                "fp_1": {"kind": "F821", "message": "undef"},
                "fp_2": {"kind": "complexity", "message": "too complex"},
            },
        )
        keys = _extract_test_failure_keys(snap)
        assert keys == set()

    def test_missing_kind_key(self):
        snap = SessionSnapshot(
            run_id="nokey",
            finding_index={
                "fp_1": {"message": "no kind field"},
            },
        )
        keys = _extract_test_failure_keys(snap)
        assert keys == set()

    def test_multiple_test_failures(self):
        snap = SessionSnapshot(
            run_id="multi",
            finding_index={
                "fp_1": {"kind": "test_failure"},
                "fp_2": {"kind": "TEFF009"},
                "fp_3": {"kind": "test_failure"},
                "fp_4": {"kind": "F401"},
            },
        )
        keys = _extract_test_failure_keys(snap)
        assert keys == {"fp_1", "fp_2", "fp_3"}


class TestEscalatePersistentFailures:
    def test_fewer_than_two_snapshots_returns_empty(self):
        session = SessionMemory()
        assert escalate_persistent_failures(session) == []

        session.snapshots.append(SessionSnapshot(run_id="only"))
        assert escalate_persistent_failures(session) == []

    def test_no_initial_failures_returns_empty(self):
        """If the first snapshot has no test failures, nothing to escalate."""
        session = SessionMemory()
        session.snapshots.append(
            SessionSnapshot(
                run_id="first",
                finding_index={"fp_1": {"kind": "F821"}},
            )
        )
        session.snapshots.append(
            SessionSnapshot(
                run_id="latest",
                finding_index={"fp_1": {"kind": "test_failure"}},
            )
        )
        assert escalate_persistent_failures(session) == []

    def test_persistent_failure_escalated(self):
        """A test failure present in first and latest snapshot is escalated."""
        session = SessionMemory()
        session.snapshots.append(
            SessionSnapshot(
                run_id="first",
                finding_index={
                    "fp_1": {
                        "kind": "test_failure",
                        "message": "test broke",
                        "file": "test_a.py",
                        "line": 10,
                    },
                },
            )
        )
        session.snapshots.append(
            SessionSnapshot(
                run_id="latest",
                finding_index={
                    "fp_1": {
                        "kind": "test_failure",
                        "message": "test broke",
                        "file": "test_a.py",
                        "line": 10,
                    },
                },
            )
        )
        findings = escalate_persistent_failures(session)
        assert len(findings) == 1
        assert findings[0]["fingerprint"] == "fp_1"
        assert findings[0]["kind"] == "test_failure"
        assert findings[0]["message"] == "test broke"
        assert findings[0]["file"] == "test_a.py"
        assert findings[0]["line"] == 10
        assert findings[0]["snapshots_present"] == 2

    def test_resolved_failure_not_escalated(self):
        """A test failure in first but not latest snapshot is not escalated."""
        session = SessionMemory()
        session.snapshots.append(
            SessionSnapshot(
                run_id="first",
                finding_index={"fp_1": {"kind": "test_failure"}},
            )
        )
        session.snapshots.append(
            SessionSnapshot(
                run_id="latest",
                finding_index={"fp_2": {"kind": "F821"}},
            )
        )
        assert escalate_persistent_failures(session) == []

    def test_classified_failure_not_escalated(self):
        """A persistent failure that was classified via agent feedback is excluded."""
        session = SessionMemory()
        session.snapshots.append(
            SessionSnapshot(
                run_id="first",
                finding_index={"fp_1": {"kind": "test_failure", "message": "flaky"}},
            )
        )
        session.snapshots.append(
            SessionSnapshot(
                run_id="latest",
                finding_index={"fp_1": {"kind": "test_failure", "message": "flaky"}},
            )
        )
        session.agent_disagreements.append(
            {
                "type": "test_failure_classification",
                "fingerprint": "fp_1",
                "classification": "flaky",
            }
        )
        assert escalate_persistent_failures(session) == []

    def test_partially_classified_returns_uninvestigated(self):
        """Only uninvestigated persistent failures are returned."""
        session = SessionMemory()
        session.snapshots.append(
            SessionSnapshot(
                run_id="first",
                finding_index={
                    "fp_1": {"kind": "test_failure"},
                    "fp_2": {"kind": "test_failure"},
                },
            )
        )
        session.snapshots.append(
            SessionSnapshot(
                run_id="latest",
                finding_index={
                    "fp_1": {"kind": "test_failure"},
                    "fp_2": {"kind": "test_failure"},
                },
            )
        )
        session.agent_disagreements.append(
            {
                "type": "test_failure_classification",
                "fingerprint": "fp_1",
                "classification": "stale_test",
            }
        )
        findings = escalate_persistent_failures(session)
        assert len(findings) == 1
        assert findings[0]["fingerprint"] == "fp_2"

    def test_findings_sorted_by_fingerprint(self):
        """Escalated findings are returned sorted by fingerprint."""
        session = SessionMemory()
        session.snapshots.append(
            SessionSnapshot(
                run_id="first",
                finding_index={
                    "fp_z": {"kind": "test_failure"},
                    "fp_a": {"kind": "test_failure"},
                    "fp_m": {"kind": "test_failure"},
                },
            )
        )
        session.snapshots.append(
            SessionSnapshot(
                run_id="latest",
                finding_index={
                    "fp_z": {"kind": "test_failure"},
                    "fp_a": {"kind": "test_failure"},
                    "fp_m": {"kind": "test_failure"},
                },
            )
        )
        findings = escalate_persistent_failures(session)
        fingerprints = [f["fingerprint"] for f in findings]
        assert fingerprints == ["fp_a", "fp_m", "fp_z"]


class TestCheckSessionExitGate:
    def test_no_persistent_failures_returns_empty(self):
        session = SessionMemory()
        assert check_session_exit_gate(session) == []

    def test_one_persistent_failure_singular(self):
        """Advisory message uses singular form for 1 failure."""
        session = SessionMemory()
        session.snapshots.append(
            SessionSnapshot(
                run_id="first",
                finding_index={"fp_1": {"kind": "test_failure"}},
            )
        )
        session.snapshots.append(
            SessionSnapshot(
                run_id="latest",
                finding_index={"fp_1": {"kind": "test_failure"}},
            )
        )
        advisories = check_session_exit_gate(session)
        assert len(advisories) == 1
        assert "1 test failure" in advisories[0]
        # Singular (no "s")
        assert "1 test failures" not in advisories[0]

    def test_multiple_persistent_failures_plural(self):
        """Advisory message uses plural form for >1 failures."""
        session = SessionMemory()
        session.snapshots.append(
            SessionSnapshot(
                run_id="first",
                finding_index={
                    "fp_1": {"kind": "test_failure"},
                    "fp_2": {"kind": "TEFF009"},
                },
            )
        )
        session.snapshots.append(
            SessionSnapshot(
                run_id="latest",
                finding_index={
                    "fp_1": {"kind": "test_failure"},
                    "fp_2": {"kind": "TEFF009"},
                },
            )
        )
        advisories = check_session_exit_gate(session)
        assert len(advisories) == 1
        assert "2 test failures" in advisories[0]

    def test_advisory_mentions_controlplane_agent_feedback(self):
        """Advisory message tells user to classify via controlplane_agent_feedback."""
        session = SessionMemory()
        session.snapshots.append(
            SessionSnapshot(
                run_id="first",
                finding_index={"fp_1": {"kind": "test_failure"}},
            )
        )
        session.snapshots.append(
            SessionSnapshot(
                run_id="latest",
                finding_index={"fp_1": {"kind": "test_failure"}},
            )
        )
        advisories = check_session_exit_gate(session)
        assert "controlplane_agent_feedback" in advisories[0]


class TestRecordTestFailureClassification:
    def test_valid_classification_recorded(self):
        session = SessionMemory()
        record_test_failure_classification(session, "fp_1", "stale_test", "Interface was deleted")
        assert len(session.agent_disagreements) == 1
        entry = session.agent_disagreements[0]
        assert entry["type"] == "test_failure_classification"
        assert entry["fingerprint"] == "fp_1"
        assert entry["classification"] == "stale_test"
        assert entry["rationale"] == "Interface was deleted"
        assert "timestamp" in entry

    def test_all_valid_classifications(self):
        """All four valid classifications are accepted."""
        session = SessionMemory()
        for cls_name in ("stale_test", "known_regression", "flaky", "out_of_scope"):
            record_test_failure_classification(session, f"fp_{cls_name}", cls_name)
        assert len(session.agent_disagreements) == 4

    def test_invalid_classification_rejected(self):
        """Invalid classification is silently ignored."""
        session = SessionMemory()
        record_test_failure_classification(session, "fp_1", "invalid_type")
        assert len(session.agent_disagreements) == 0

    def test_empty_rationale_allowed(self):
        """Empty rationale is valid (default)."""
        session = SessionMemory()
        record_test_failure_classification(session, "fp_1", "flaky")
        assert session.agent_disagreements[0]["rationale"] == ""

    def test_multiple_classifications_appended(self):
        """Multiple classifications for different fingerprints are all recorded."""
        session = SessionMemory()
        record_test_failure_classification(session, "fp_1", "flaky", "sometimes fails")
        record_test_failure_classification(session, "fp_2", "stale_test", "old interface")
        assert len(session.agent_disagreements) == 2
        assert session.agent_disagreements[0]["fingerprint"] == "fp_1"
        assert session.agent_disagreements[1]["fingerprint"] == "fp_2"


# ── SessionMemory.update_knowledge ───────────────────────────────────


class TestUpdateKnowledge:
    def test_updates_compass_state(self):
        from lintgate.orchestration.knowledge import SessionKnowledge

        session = SessionMemory()
        session.behavior_compass = {"hypothesis_version": 5}
        knowledge = SessionKnowledge()
        session.update_knowledge(knowledge)
        assert knowledge.compass_state == {"hypothesis_version": 5}

    def test_updates_repertoire(self):
        from lintgate.orchestration.knowledge import SessionKnowledge

        session = SessionMemory()
        session.resolution_repertoire = [{"pattern": "fix_import"}]
        knowledge = SessionKnowledge()
        session.update_knowledge(knowledge)
        assert knowledge.repertoire == [{"pattern": "fix_import"}]

    def test_updates_facts_from_latest_snapshot(self):
        from lintgate.orchestration.knowledge import SessionKnowledge

        session = SessionMemory()
        session.snapshots.append(
            SessionSnapshot(
                run_id="latest",
                coherence_state="isolated",
                finding_count=5,
                compliance_outcome="followed",
            )
        )
        knowledge = SessionKnowledge()
        session.update_knowledge(knowledge)
        assert knowledge.facts["last_coherence"] == "isolated"
        assert knowledge.facts["last_finding_count"] == 5
        assert knowledge.facts["compliance_outcome"] == "followed"

    def test_no_snapshots_skips_facts(self):
        from lintgate.orchestration.knowledge import SessionKnowledge

        session = SessionMemory()
        knowledge = SessionKnowledge()
        session.update_knowledge(knowledge)
        assert "last_coherence" not in knowledge.facts


# ── SessionMemory.preload_transfer_packet ────────────────────────────


class TestPreloadTransferPacket:
    def test_preload_dict_packet(self):
        session = SessionMemory()
        packet = {
            "source_agent_id": "agent_a",
            "active_findings": [
                {"fingerprint": "fp_1", "first_seen": 1000.0, "severity": "blocking"},
                {"fingerprint": "fp_2", "severity": "warning"},
            ],
        }
        session.preload_transfer_packet(packet)
        assert session.latest_transfer_packet == packet
        assert "fp_1" in session.active_finding_history
        assert session.active_finding_history["fp_1"]["status"] == "active"
        assert session.active_finding_history["fp_1"]["severity"] == "blocking"
        assert "fp_2" in session.active_finding_history
        # Creates a transfer snapshot
        assert len(session.snapshots) == 1
        assert session.snapshots[0].run_id.startswith("transfer_")
        assert session.snapshots[0].coherence_state == "stable"

    def test_preload_empty_active_findings(self):
        session = SessionMemory()
        packet = {"source_agent_id": "agent_b", "active_findings": []}
        session.preload_transfer_packet(packet)
        assert session.active_finding_history == {}
        assert len(session.snapshots) == 1

    def test_preload_no_fingerprint_skips_finding(self):
        """Findings without fingerprint are skipped in history."""
        session = SessionMemory()
        packet = {
            "active_findings": [
                {"severity": "warning"},  # No fingerprint
            ],
        }
        session.preload_transfer_packet(packet)
        assert session.active_finding_history == {}

    def test_preload_missing_active_findings_key(self):
        """Packet without active_findings key uses empty list."""
        session = SessionMemory()
        packet = {"source_agent_id": "agent_c"}
        session.preload_transfer_packet(packet)
        assert session.active_finding_history == {}
        assert len(session.snapshots) == 1


# ── Helpers ──────────────────────────────────────────────────────────


class TestHelpers:
    def test_project_hash_stable(self):
        h1 = _project_hash("/test/project")
        h2 = _project_hash("/test/project")
        assert h1 == h2
        assert len(h1) == 16

    def test_project_hash_different(self):
        h1 = _project_hash("/project/a")
        h2 = _project_hash("/project/b")
        assert h1 != h2

    def test_project_hash_empty_string(self):
        h = _project_hash("")
        assert len(h) == 16
        assert isinstance(h, str)

    def test_session_path(self, tmp_path):
        with patch("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path):
            path = _session_path("/test/proj")
            assert str(path).startswith(str(tmp_path))
            assert path.suffix == ".json"

    def test_session_path_uses_project_hash(self, tmp_path):
        with patch("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path):
            path = _session_path("/test/proj")
            expected_name = f"{_project_hash('/test/proj')}.json"
            assert path.name == expected_name


# ── Session transfer packet I/O ───────────────────────────────────────


class TestSessionTransfer:
    def test_session_transfer_packet_io(self, tmp_path):
        from lintgate.controlplane.session_transfer import (
            read_transfer_packet,
            write_transfer_packet,
        )
        from lintgate.controlplane.types import SessionTransferPacket

        packet_path = tmp_path / "packet.json"

        packet = SessionTransferPacket(
            source_agent_id="claude",
            target_agent_id="aider",
            transfer_reason="complex_refactoring",
            active_findings=[{"id": 1, "msg": "test"}],
            context_summary="We hit a block on circular dependencies.",
        )

        write_transfer_packet(packet_path, packet)
        assert packet_path.exists()

        restored = read_transfer_packet(packet_path)
        assert restored is not None
        assert restored.source_agent_id == "claude"
        assert restored.target_agent_id == "aider"
        assert restored.transfer_reason == "complex_refactoring"
        assert len(restored.active_findings) == 1
        assert restored.context_summary == "We hit a block on circular dependencies."

    def test_session_transfer_missing(self):
        from pathlib import Path as StdPath

        from lintgate.controlplane.session_transfer import read_transfer_packet

        packet = read_transfer_packet(StdPath("/non/existent/path.json"))
        assert packet is None
