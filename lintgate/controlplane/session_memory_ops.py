"""Session memory operations — mesh recording, repair tracking, test failure analysis.

Split from session_memory.py — contains functions that operate on SessionMemory
instances: mesh run recording, repair tracking, behavior compass helpers,
habit mode helpers, persistent test failure tracking, and session utilities.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from lintgate.types import LintIssue

    from .behavior_compass import BehaviorCompass
    from .types import MeshResult, RepairAction

from .session_memory import _MAX_SNAPSHOTS, SESSION_DIR, SessionMemory, SessionSnapshot

# ── Mesh run recording ────────────────────────────────────────────


def record_mesh_run(
    session: SessionMemory,
    mesh_result: MeshResult,
    finding_index: dict[str, dict[str, Any]] | None = None,
    disposition: str | None = None,
    last_nudge: dict[str, Any] | None = None,
    compliance_outcome: str | None = None,
) -> SessionSnapshot:
    """Record a mesh run and append its snapshot to session memory."""
    coherence = mesh_result.coherence
    run_id = mesh_result.event.event_id if mesh_result.event else uuid.uuid4().hex[:12]

    total_findings = 0
    blocking_count = 0
    all_repair_ids: list[str] = []
    pattern_this_run: dict[str, int] = {}
    repair_catalog: dict[str, dict[str, Any]] = {}

    for cr in mesh_result.channel_results:
        for finding in cr.findings:
            total_findings += 1
            if finding.severity == "blocking":
                blocking_count += 1
            key = f"{finding.linter}|{finding.kind}"
            pattern_this_run[key] = pattern_this_run.get(key, 0) + 1

        for repair in cr.repairs:
            all_repair_ids.append(repair.action_id)
            if repair.action_id not in session.repair_outcomes:
                session.repair_outcomes[repair.action_id] = "pending"
            repair_catalog[repair.action_id] = {
                "channel": repair.channel,
                "kind": repair.kind,
                "summary": repair.summary,
                "safe": str(repair.safe).lower(),
                "payload": dict(repair.payload),
            }

    pattern_alerts: list[dict[str, Any]] = []
    for cr in mesh_result.channel_results:
        if cr.channel == "lint":
            pattern_alerts = cr.metrics.get("pattern_alerts", [])
            break

    snapshot = SessionSnapshot(
        run_id=run_id,
        timestamp=time.time(),
        coherence_state=coherence.state,
        loud_channels=list(coherence.loud_channels),
        silent_channels=list(coherence.silent_channels),
        finding_count=total_findings,
        blocking_count=blocking_count,
        pattern_alerts=pattern_alerts,
        repairs_proposed=all_repair_ids,
        repairs_applied=[],
        repair_catalog=repair_catalog,
        finding_index=finding_index or {},
        disposition=disposition,
        last_nudge=last_nudge,
        compliance_outcome=compliance_outcome,
    )

    session.snapshots.append(snapshot)

    if len(session.snapshots) > _MAX_SNAPSHOTS:
        session.snapshots = session.snapshots[-_MAX_SNAPSHOTS:]

    session.coherence_trajectory.append(coherence.state)

    for key, count in pattern_this_run.items():
        if key not in session.pattern_trend:
            session.pattern_trend[key] = []
        session.pattern_trend[key].append(count)
        if len(session.pattern_trend[key]) > _MAX_SNAPSHOTS:
            session.pattern_trend[key] = session.pattern_trend[key][-_MAX_SNAPSHOTS:]

    session.last_active = time.time()
    return snapshot


def expire_session(session: SessionMemory, max_age_hours: float = 4.0) -> bool:
    """Check if a session has expired due to inactivity."""
    if max_age_hours <= 0:
        return False
    max_age_seconds = max_age_hours * 3600
    elapsed = time.time() - session.last_active
    return elapsed > max_age_seconds


# ── Repair tracking ───────────────────────────────────────────────


def propose_repairs(session: SessionMemory, repairs: list[RepairAction]) -> None:
    """Register newly proposed repairs in the session."""
    for repair in repairs:
        if repair.action_id not in session.repair_outcomes:
            session.repair_outcomes[repair.action_id] = "pending"


def report_repair_outcome(session: SessionMemory, action_id: str, outcome: str) -> None:
    """Record the outcome of a repair action."""
    session.repair_outcomes[action_id] = outcome


def detect_applied_repairs(
    session: SessionMemory,
    current_findings: list[LintIssue],
) -> list[str]:
    """Heuristic repair detection: infer applied repairs from absent findings."""
    if not session.snapshots:
        return []

    current_sigs = set()
    for f in current_findings:
        sig = f"{f.linter}|{f.kind}|{f.file or ''}|{f.line or 0}"
        current_sigs.add(sig)

    applied: list[str] = []
    last_snapshot = session.snapshots[-1]

    if len(session.snapshots) < 2:
        return []

    prev_snapshot = session.snapshots[-2]

    for _action_id, status in session.repair_outcomes.items():
        if status == "pending" and last_snapshot.blocking_count < prev_snapshot.blocking_count:
            pass  # Future: correlate specific findings with specific repairs

    return applied


# ── Behavior compass integration ──────────────────────────────────


def load_behavior_compass(session: SessionMemory) -> BehaviorCompass:
    """Deserialize BehaviorCompass from session's behavior_compass dict."""
    from .behavior_compass import BehaviorCompass

    return BehaviorCompass.from_dict(session.behavior_compass)


def save_behavior_compass(session: SessionMemory, compass: BehaviorCompass) -> None:
    """Serialize BehaviorCompass into session's behavior_compass dict."""
    session.behavior_compass = compass.to_dict()


def get_habit_mode_active(session: SessionMemory) -> bool:
    """Quick check if habit mode is active without full deserialization."""
    return bool(session.behavior_compass.get("habit_mode", {}).get("active", False))


# ── Test failure analysis ─────────────────────────────────────────


def _extract_test_failure_keys(snapshot: SessionSnapshot) -> set[str]:
    """Extract test failure fingerprints from a snapshot's finding index."""
    keys: set[str] = set()
    for fp, info in snapshot.finding_index.items():
        kind = info.get("kind", "")
        if kind in ("test_failure", "TEFF009"):
            keys.add(fp)
    return keys


def escalate_persistent_failures(session: SessionMemory) -> list[dict[str, Any]]:
    """TEFF008 — Identify test failures present from session start to now."""
    if len(session.snapshots) < 2:
        return []

    initial = session.snapshots[0]
    latest = session.snapshots[-1]

    initial_failures = _extract_test_failure_keys(initial)
    latest_failures = _extract_test_failure_keys(latest)

    if not initial_failures:
        return []

    persistent = initial_failures & latest_failures
    if not persistent:
        return []

    classified = set()
    for disagreement in session.agent_disagreements:
        if disagreement.get("type") == "test_failure_classification":
            classified.add(disagreement.get("fingerprint", ""))

    uninvestigated = persistent - classified
    if not uninvestigated:
        return []

    findings: list[dict[str, Any]] = []
    for fp in sorted(uninvestigated):
        info = latest.finding_index.get(fp, {})
        findings.append(
            {
                "fingerprint": fp,
                "kind": info.get("kind", "test_failure"),
                "message": info.get("message", ""),
                "file": info.get("file"),
                "line": info.get("line"),
                "snapshots_present": len(session.snapshots),
            }
        )

    return findings


def check_session_exit_gate(session: SessionMemory) -> list[str]:
    """Return advisory messages for session exit."""
    advisories: list[str] = []

    persistent = escalate_persistent_failures(session)
    if persistent:
        advisories.append(
            f"{len(persistent)} test failure{'s' if len(persistent) != 1 else ''} "
            f"present at session start and not addressed. "
            f"Classify each as stale/regression/flaky via "
            f"controlplane_agent_feedback before completing session."
        )

    return advisories


def record_test_failure_classification(
    session: SessionMemory,
    fingerprint: str,
    classification: str,
    rationale: str = "",
) -> None:
    """Record a structured classification for a test failure."""
    valid = {"stale_test", "known_regression", "flaky", "out_of_scope"}
    if classification not in valid:
        return

    session.agent_disagreements.append(
        {
            "type": "test_failure_classification",
            "fingerprint": fingerprint,
            "classification": classification,
            "rationale": rationale,
            "timestamp": time.time(),
        }
    )


# ── Helpers ──────────────────────────────────────────────────────────


def _session_path(project_root: str) -> Path:
    """Get the session file path for a project."""
    return SESSION_DIR / f"{_project_hash(project_root)}.json"


def _project_hash(project_root: str) -> str:
    """Generate a stable hash for a project path."""
    return hashlib.sha256(project_root.encode()).hexdigest()[:16]
