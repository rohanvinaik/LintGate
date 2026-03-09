"""Session memory — cross-run state accumulation for ControlPlane.

Persists supervision signals across mesh runs within a single agent session.
Sessions expire after configurable inactivity (default: 4 hours).

Storage: ~/.claude/lintgate/session/<project_hash>.json

This module closes the feedback loop:
- Coherence trajectory tracking (detect regressions / resolutions)
- Repair outcome tracking (proposed → applied/ignored)
- Pattern trend accumulation (for constraint proposer)
- Agent disagreement logging (for future theory refinement)

Design decisions:
- One session per project — keyed by project path hash
- Sessions expire by inactivity, not wall-clock age
- Snapshots are append-only within a session; expiry starts fresh
- Repair detection is heuristic: absence of associated finding → inferred applied
- All operations are fail-safe — corrupted state starts a fresh session
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintgate.types import LintIssue

    from .behavior_compass import BehaviorCompass
    from .types import MeshResult, RepairAction

from lintgate.orchestration.knowledge import KnowledgeManager, SessionKnowledge

SESSION_DIR = Path.home() / ".claude" / "lintgate" / "session"

_MAX_SNAPSHOTS = 50  # Prevent unbounded growth within a session


@dataclass
class BehaviorEventData:
    """Behavioral event data populated by the behavior channel."""

    action_type: str = ""  # "bash" | "write" | "edit" | "read" | "grep" | "glob"
    command_signature: str = ""  # Normalized command family (redacted)
    exit_code: int | None = None
    error_signature: str = ""  # Normalized error output
    behavior_alerts: list[str] = field(default_factory=list)  # Pattern names that fired
    prediction_accuracy: float | None = None
    predictions_checked: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BehaviorEventData:
        return cls(
            action_type=data.get("action_type", ""),
            command_signature=data.get("command_signature", ""),
            exit_code=data.get("exit_code"),
            error_signature=data.get("error_signature", ""),
            behavior_alerts=data.get("behavior_alerts", []),
            prediction_accuracy=data.get("prediction_accuracy"),
            predictions_checked=data.get("predictions_checked", 0),
        )


@dataclass
class SessionSnapshot:
    """A single mesh run snapshot within a session."""

    run_id: str = ""
    timestamp: float = 0.0
    coherence_state: str = ""
    loud_channels: list[str] = field(default_factory=list)
    silent_channels: list[str] = field(default_factory=list)
    finding_count: int = 0
    blocking_count: int = 0
    pattern_alerts: list[dict[str, Any]] = field(default_factory=list)
    repairs_proposed: list[str] = field(default_factory=list)  # action_ids
    repairs_applied: list[str] = field(default_factory=list)  # action_ids confirmed
    repair_catalog: dict[str, dict[str, str]] = field(
        default_factory=dict
    )  # action_id → compact meta
    behavior: BehaviorEventData = field(default_factory=BehaviorEventData)
    finding_index: dict[str, dict[str, Any]] = field(default_factory=dict)  # fingerprint → summary
    delivery_metrics: dict[str, Any] = field(default_factory=dict)  # channel health for this run
    disposition: str | None = None  # Behavioral nudge string from last run
    last_nudge: dict[str, Any] | None = None  # Full nudge object for compliance analysis
    compliance_outcome: str | None = None  # followed | ignored | overridden | uncertain

    # Backward-compatible property accessors for behavior fields
    @property
    def action_type(self) -> str:
        return self.behavior.action_type

    @property
    def command_signature(self) -> str:
        return self.behavior.command_signature

    @property
    def exit_code(self) -> int | None:
        return self.behavior.exit_code

    @property
    def error_signature(self) -> str:
        return self.behavior.error_signature

    @property
    def behavior_alerts(self) -> list[str]:
        return self.behavior.behavior_alerts

    @property
    def prediction_accuracy(self) -> float | None:
        return self.behavior.prediction_accuracy

    @property
    def predictions_checked(self) -> int:
        return self.behavior.predictions_checked

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Flatten behavior fields for backward-compatible serialization
        beh = d.pop("behavior", {})
        d.update(beh)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionSnapshot:
        behavior = BehaviorEventData.from_dict(data)
        return cls(
            run_id=data.get("run_id", ""),
            timestamp=data.get("timestamp", 0.0),
            coherence_state=data.get("coherence_state", ""),
            loud_channels=data.get("loud_channels", []),
            silent_channels=data.get("silent_channels", []),
            finding_count=data.get("finding_count", 0),
            blocking_count=data.get("blocking_count", 0),
            pattern_alerts=data.get("pattern_alerts", []),
            repairs_proposed=data.get("repairs_proposed", []),
            repairs_applied=data.get("repairs_applied", []),
            repair_catalog=data.get("repair_catalog", {}),
            behavior=behavior,
            finding_index=data.get("finding_index", {}),
            delivery_metrics=data.get("delivery_metrics", {}),
            disposition=data.get("disposition"),
            last_nudge=data.get("last_nudge"),
            compliance_outcome=data.get("compliance_outcome"),
        )


@dataclass
class SessionMemory:
    """Cross-run session state for a project.

    Accumulates snapshots from successive mesh runs. Provides
    coherence trajectory, repair outcome tracking, and pattern
    trend data for the constraint proposer and reporter.
    """

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    project_root: str = ""
    started_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    snapshots: list[SessionSnapshot] = field(default_factory=list)
    coherence_trajectory: list[str] = field(default_factory=list)
    repair_outcomes: dict[str, str] = field(default_factory=dict)  # action_id → status
    pattern_trend: dict[str, list[int]] = field(default_factory=dict)  # "linter|kind" → [counts]
    proposed_constraints: list[dict[str, Any]] = field(default_factory=list)
    agent_disagreements: list[dict[str, Any]] = field(default_factory=list)
    behavior_compass: dict[str, Any] = field(default_factory=dict)  # Serialized BehaviorCompass
    # Architecture of Inquiry: cached theory profile for current mesh run (transient, not persisted)
    theory_profile_cache: dict[str, Any] | None = None
    # Architecture of Inquiry: pending context patches awaiting explicit apply
    pending_patches: list[dict[str, Any]] = field(default_factory=list)
    resolution_repertoire: list[dict[str, Any]] = field(default_factory=list)
    active_finding_history: dict[str, Any] = field(default_factory=dict)
    action_history: list[dict[str, Any]] = field(default_factory=list)
    edit_cycle_state: dict[str, Any] = field(default_factory=dict)
    latest_transfer_packet: dict[str, Any] | None = None
    delivery_health_summary: dict[str, Any] = field(default_factory=dict)
    knowledge_meta: dict[str, Any] = field(default_factory=dict)  # Staleness, survival, etc.

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "project_root": self.project_root,
            "started_at": self.started_at,
            "last_active": self.last_active,
            "snapshots": [s.to_dict() for s in self.snapshots],
            "coherence_trajectory": self.coherence_trajectory,
            "repair_outcomes": self.repair_outcomes,
            "pattern_trend": self.pattern_trend,
            "proposed_constraints": self.proposed_constraints,
            "agent_disagreements": self.agent_disagreements,
            "behavior_compass": self.behavior_compass,
            # theory_profile_cache is transient (per-run), not persisted
            "pending_patches": self.pending_patches,
            "resolution_repertoire": self.resolution_repertoire,
            "active_finding_history": self.active_finding_history,
            "action_history": self.action_history,
            "edit_cycle_state": self.edit_cycle_state,
            "latest_transfer_packet": self.latest_transfer_packet,
            "delivery_health_summary": self.delivery_health_summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionMemory:
        snapshots = [SessionSnapshot.from_dict(s) for s in data.get("snapshots", [])]
        return cls(
            session_id=data.get("session_id", uuid.uuid4().hex[:12]),
            project_root=data.get("project_root", ""),
            started_at=data.get("started_at", 0.0),
            last_active=data.get("last_active", 0.0),
            snapshots=snapshots,
            coherence_trajectory=data.get("coherence_trajectory", []),
            repair_outcomes=data.get("repair_outcomes", {}),
            pattern_trend=data.get("pattern_trend", {}),
            proposed_constraints=data.get("proposed_constraints", []),
            agent_disagreements=data.get("agent_disagreements", []),
            behavior_compass=data.get("behavior_compass", {}),
            # theory_profile_cache is transient — always None on load
            theory_profile_cache=None,
            pending_patches=data.get("pending_patches", []),
            resolution_repertoire=data.get("resolution_repertoire", []),
            active_finding_history=data.get("active_finding_history", {}),
            action_history=data.get("action_history", []),
            edit_cycle_state=data.get("edit_cycle_state", {}),
            latest_transfer_packet=data.get("latest_transfer_packet"),
            delivery_health_summary=data.get("delivery_health_summary", {}),
            knowledge_meta=data.get("knowledge_meta", {}),
        )

    def update_knowledge(self, knowledge: SessionKnowledge) -> None:
        """Update knowledge state from current session."""
        knowledge.compass_state = self.behavior_compass
        knowledge.repertoire = self.resolution_repertoire
        # Extract facts from latest snapshots
        if self.snapshots:
            last = self.snapshots[-1]
            knowledge.facts["last_coherence"] = last.coherence_state
            knowledge.facts["last_finding_count"] = last.finding_count
            knowledge.facts["compliance_outcome"] = last.compliance_outcome

    def preload_transfer_packet(self, packet: Any) -> None:
        """Hydrate session from a transfer packet (handoff)."""
        from dataclasses import asdict

        self.latest_transfer_packet = asdict(packet) if hasattr(packet, "to_dict") else packet

        # Hydrate active finding history to maintain coherence across handoff
        active_findings = (
            packet.active_findings
            if hasattr(packet, "active_findings")
            else packet.get("active_findings", [])
        )
        for finding in active_findings:
            fingerprint = finding.get("fingerprint")
            if fingerprint:
                self.active_finding_history[fingerprint] = {
                    "first_seen": finding.get("first_seen", time.time()),
                    "last_seen": time.time(),
                    "status": "active",
                    "severity": finding.get("severity", "warning"),
                }

        # Record the transfer event in snapshots
        self.snapshots.append(
            SessionSnapshot(
                run_id=f"transfer_{uuid.uuid4().hex[:8]}",
                timestamp=time.time(),
                coherence_state="stable",
                disposition="Preloaded from transfer packet",
            )
        )


# ── Public API ────────────────────────────────────────────────────────


def load_session(project_root: str) -> SessionMemory | None:
    """Load a session for a project, or None if no active session exists.

    Returns None if:
    - No session file exists
    - Session file is corrupted
    - Session has expired (caller should check with expire_session)
    """
    session_path = _session_path(project_root)
    if not session_path.exists():
        return None

    try:
        with open(session_path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return SessionMemory.from_dict(data)
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def save_session(session: SessionMemory) -> None:
    """Persist session to disk."""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    session_path = _session_path(session.project_root)
    session.last_active = time.time()

    try:
        with open(session_path, "w") as f:
            json.dump(session.to_dict(), f, indent=2)

        # Also save to unified Knowledge store (#175)
        km = KnowledgeManager(session.project_root)
        knowledge = km.load()
        session.update_knowledge(knowledge)
        km.save(knowledge)
    except OSError:
        pass  # Non-fatal — session memory is observability, not correctness


def get_or_create_session(project_root: str, max_age_hours: float = 4.0) -> SessionMemory:
    """Load existing session or create a new one.

    If the existing session has expired, a fresh one is created.
    This is the recommended entry point for the hook/MCP layer.
    """
    session = load_session(project_root)

    if session is not None and expire_session(session, max_age_hours):
        # Session expired — start fresh
        session = None

    if session is None:
        session = SessionMemory(
            project_root=project_root,
            started_at=time.time(),
            last_active=time.time(),
        )

        # Check for pending transfer packet (#169)
        transfer_path = (
            SESSION_DIR / f"transfer_{hashlib.sha256(project_root.encode()).hexdigest()[:12]}.json"
        )
        if transfer_path.exists():
            try:
                with open(transfer_path) as f:
                    packet_data = json.load(f)
                session.preload_transfer_packet(packet_data)
                # Cleanup transfer packet after preload to avoid re-triggering
                transfer_path.unlink()
            except (OSError, json.JSONDecodeError):
                pass

        km = KnowledgeManager(project_root)
        knowledge = km.load()
        session.knowledge_meta = {
            "staleness_hrs": knowledge.knowledge_staleness_hrs,
            "survival_ratio": knowledge.survival_ratio,
        }
        if knowledge.compass_state:
            session.behavior_compass = knowledge.compass_state
        if knowledge.repertoire:
            session.resolution_repertoire = knowledge.repertoire

    return session


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

    # Collect findings and repairs across all channel results
    total_findings = 0
    blocking_count = 0
    all_repair_ids: list[str] = []
    pattern_this_run: dict[str, int] = {}

    repair_catalog: dict[str, dict[str, str]] = {}

    for cr in mesh_result.channel_results:
        for finding in cr.findings:
            total_findings += 1
            if finding.severity == "blocking":
                blocking_count += 1
            # Track pattern by linter|kind
            key = f"{finding.linter}|{finding.kind}"
            pattern_this_run[key] = pattern_this_run.get(key, 0) + 1

        for repair in cr.repairs:
            all_repair_ids.append(repair.action_id)
            if repair.action_id not in session.repair_outcomes:
                session.repair_outcomes[repair.action_id] = "pending"
            # Compact catalog entry (no full payload — stays on disk)
            repair_catalog[repair.action_id] = {
                "channel": repair.channel,
                "kind": repair.kind,
                "summary": repair.summary,
                "safe": str(repair.safe).lower(),
            }

    # Extract pattern alerts from lint channel metrics
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

    # Trim snapshots if exceeding max
    if len(session.snapshots) > _MAX_SNAPSHOTS:
        session.snapshots = session.snapshots[-_MAX_SNAPSHOTS:]

    # Update coherence trajectory
    session.coherence_trajectory.append(coherence.state)

    # Update pattern trends
    for key, count in pattern_this_run.items():
        if key not in session.pattern_trend:
            session.pattern_trend[key] = []
        session.pattern_trend[key].append(count)
        # Keep bounded
        if len(session.pattern_trend[key]) > _MAX_SNAPSHOTS:
            session.pattern_trend[key] = session.pattern_trend[key][-_MAX_SNAPSHOTS:]

    session.last_active = time.time()
    return snapshot


def expire_session(session: SessionMemory, max_age_hours: float = 4.0) -> bool:
    """Check if a session has expired due to inactivity.

    Args:
        session: Session to check.
        max_age_hours: Maximum inactivity period in hours.

    Returns:
        True if the session has expired and should be replaced.
    """
    if max_age_hours <= 0:
        return False
    max_age_seconds = max_age_hours * 3600
    elapsed = time.time() - session.last_active
    return elapsed > max_age_seconds


# ── Repair Tracking ──────────────────────────────────────────────────


def propose_repairs(session: SessionMemory, repairs: list[RepairAction]) -> None:
    """Register newly proposed repairs in the session.

    Sets their initial status to 'pending'. Does not overwrite
    existing repair outcomes (idempotent for reruns).
    """
    for repair in repairs:
        if repair.action_id not in session.repair_outcomes:
            session.repair_outcomes[repair.action_id] = "pending"


def report_repair_outcome(session: SessionMemory, action_id: str, outcome: str) -> None:
    """Record the outcome of a repair action.

    Args:
        session: Active session memory.
        action_id: The repair action ID.
        outcome: One of 'applied', 'ignored', 'rejected'.
    """
    session.repair_outcomes[action_id] = outcome


def detect_applied_repairs(
    session: SessionMemory,
    current_findings: list[LintIssue],
) -> list[str]:
    """Heuristic repair detection: infer applied repairs from absent findings.

    For each pending repair, check if its associated finding signature
    is no longer present in current findings. If absent → infer 'applied'.

    This is a heuristic — it may produce false positives when issues
    disappear for other reasons. That's acceptable for advisory reporting.

    Args:
        session: Active session memory with pending repairs.
        current_findings: Findings from the current mesh run.

    Returns:
        List of action_ids that were inferred as applied.
    """
    if not session.snapshots:
        return []

    # Build set of current finding signatures
    current_sigs = set()
    for f in current_findings:
        sig = f"{f.linter}|{f.kind}|{f.file or ''}|{f.line or 0}"
        current_sigs.add(sig)

    # Look at the most recent snapshot's proposed repairs
    # and check which associated findings have disappeared
    applied: list[str] = []
    last_snapshot = session.snapshots[-1]

    # We can only detect applied repairs if we have previous findings to compare
    if len(session.snapshots) < 2:
        return []

    prev_snapshot = session.snapshots[-2]

    # For now, we track repairs by their action_id status
    # A more sophisticated version would store finding-repair associations
    for _action_id, status in session.repair_outcomes.items():
        if status == "pending" and last_snapshot.blocking_count < prev_snapshot.blocking_count:
            # Mark as potentially applied (conservative: only if blocking decreased)
            pass  # Future: correlate specific findings with specific repairs

    return applied


# ── Behavior Compass Helpers ─────────────────────────────────────────


def load_behavior_compass(session: SessionMemory) -> BehaviorCompass:
    """Deserialize BehaviorCompass from session's behavior_compass dict.

    Returns a fresh compass if the session has no compass data.
    """
    from .behavior_compass import BehaviorCompass

    return BehaviorCompass.from_dict(session.behavior_compass)


def save_behavior_compass(session: SessionMemory, compass: BehaviorCompass) -> None:
    """Serialize BehaviorCompass into session's behavior_compass dict."""
    session.behavior_compass = compass.to_dict()


# ── Habit Mode Helpers ────────────────────────────────────────────────


def get_habit_mode_active(session: SessionMemory) -> bool:
    """Quick check if habit mode is active without full deserialization."""
    return bool(session.behavior_compass.get("habit_mode", {}).get("active", False))


# ── Persistent Test Failure Tracking (#205) ─────────────────────────


def _extract_test_failure_keys(snapshot: SessionSnapshot) -> set[str]:
    """Extract test failure fingerprints from a snapshot's finding index."""
    keys: set[str] = set()
    for fp, info in snapshot.finding_index.items():
        kind = info.get("kind", "")
        if kind in ("test_failure", "TEFF009"):
            keys.add(fp)
    return keys


def escalate_persistent_failures(session: SessionMemory) -> list[dict[str, Any]]:
    """TEFF008 — Identify test failures present from session start to now.

    Compares the first snapshot's test failures with the latest snapshot.
    Failures that persist without being addressed get escalated.

    Returns a list of dicts suitable for building LintIssue findings.
    """
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

    # Check which persistent failures were classified via agent feedback
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
    """Return advisory messages for session exit.

    Checks for unresolved test failures that persisted through the
    entire session without investigation or classification.
    This is advisory — it surfaces the gap but does not hard-block.
    """
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
    """Record a structured classification for a test failure.

    Valid classifications:
    - stale_test: Tests reference deleted interfaces
    - known_regression: Code is broken, tracked in an issue
    - flaky: Non-deterministic failure
    - out_of_scope: Explicitly deferred with rationale
    """
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
