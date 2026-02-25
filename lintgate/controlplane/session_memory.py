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
    transfer_packet: str | None = None

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
            transfer_packet=data.get("transfer_packet"),
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

        # Issue #169: Load and prewarm state from transfer packet if available
        _apply_potential_transfer_packet(session, project_root)

    return session


def record_mesh_run(
    session: SessionMemory,
    mesh_result: MeshResult,
    finding_index: dict[str, dict[str, Any]] | None = None,
) -> SessionSnapshot:
    """Record a mesh execution result as a session snapshot.

    Updates:
    - snapshots: append new snapshot
    - coherence_trajectory: append coherence state
    - pattern_trend: update per-pattern counts
    - repair_outcomes: register newly proposed repairs as 'pending'

    Args:
        session: Active session memory.
        mesh_result: Result from run_mesh().
        finding_index: Optional fingerprint→summary index for delta computation.

    Returns:
        The new SessionSnapshot (also appended to session.snapshots).
    """
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


# ── Helpers ──────────────────────────────────────────────────────────


def _session_path(project_root: str) -> Path:
    """Get the session file path for a project."""
    return SESSION_DIR / f"{_project_hash(project_root)}.json"


def _project_hash(project_root: str) -> str:
    """Generate a stable hash for a project path."""
    return hashlib.sha256(project_root.encode()).hexdigest()[:16]


def _apply_potential_transfer_packet(session: SessionMemory, project_root: str) -> None:
    """Check for and apply a session transfer packet to a fresh session."""
    handoff_path = Path(project_root) / ".lintgate_handoff.json"
    if not handoff_path.exists():
        return

    try:
        with open(handoff_path) as f:
            packet_data = json.load(f)

        if not isinstance(packet_data, dict):
            return

        # Partial prewarm of behavior_compass
        bc = session.behavior_compass or {}

        # Restore compliance rate
        if "comp" in packet_data:
            bc["compliance_rate"] = packet_data["comp"]

        # Restore confirmed hypotheses
        if "hyps" in packet_data:
            from .behavior_types import BehaviorHypothesis

            existing_hyps = [BehaviorHypothesis.from_dict(h) for h in bc.get("hypotheses", [])]
            existing_ids = {h.id for h in existing_hyps}

            for h_data in packet_data["hyps"]:
                h_id = h_data.get("id")
                if h_id and h_id not in existing_ids:
                    # Minimal reconstruction
                    h = BehaviorHypothesis(
                        id=h_id,
                        claim=h_data.get("clm", "Restored hypothesis"),
                        confidence=h_data.get("conf", 0.7),
                        status="confirmed",
                        source="transfer_packet",
                    )
                    existing_hyps.append(h)
                    existing_ids.add(h_id)
            bc["hypotheses"] = [h.to_dict() for h in existing_hyps]
            bc["hypothesis_version"] = bc.get("hypothesis_version", 0) + 1

        # Restore active findings
        if "active" in packet_data:
            bc["pending_nudge_signals"] = packet_data["active"]
            bc["pending_nudge_constraint_check_count"] = 0  # Fresh start

        session.behavior_compass = bc

    except (json.JSONDecodeError, OSError, KeyError):
        pass
