"""Tests for session memory — cross-run state accumulation."""

from __future__ import annotations

import json
import time
from unittest.mock import patch

from lintgate.controlplane.session_memory import (
    SessionMemory,
    SessionSnapshot,
    _project_hash,
    _session_path,
    expire_session,
    get_or_create_session,
    load_session,
    propose_repairs,
    record_mesh_run,
    report_repair_outcome,
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
    coherence_state: str = "stable",
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

    def test_boundary(self):
        """Just under boundary should not expire, just over should."""
        now = time.time()
        # 3h59m — not expired
        session = SessionMemory(last_active=now - 3 * 3600 - 59 * 60)
        assert expire_session(session, max_age_hours=4.0) is False

        # 4h1m — expired
        session2 = SessionMemory(last_active=now - 4 * 3600 - 60)
        assert expire_session(session2, max_age_hours=4.0) is True


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

        assert session.pattern_trend.get("ruff|F821") == [2, 3]  # 0-count runs aren't tracked

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
        # Add 55 snapshots — should trim to 50
        for i in range(55):
            mesh = _make_mesh_result(coherence_state="stable", event_id=f"evt_{i}")
            record_mesh_run(session, mesh)

        assert len(session.snapshots) <= 50


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

    def test_session_path(self, tmp_path):
        with patch("lintgate.controlplane.session_memory.SESSION_DIR", tmp_path):
            path = _session_path("/test/proj")
            assert str(path).startswith(str(tmp_path))
            assert path.suffix == ".json"
