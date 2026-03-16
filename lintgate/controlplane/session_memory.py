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
from typing import Any

from lintgate.orchestration.knowledge import KnowledgeManager, SessionKnowledge

SESSION_DIR = Path.home() / ".claude" / "lintgate" / "session"

_MAX_SNAPSHOTS = 50  # Prevent unbounded growth within a session


# ── Data classes ──────────────────────────────────────────────────


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
    repair_catalog: dict[str, dict[str, Any]] = field(
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


# ── Session persistence ───────────────────────────────────────────


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
    session_path = _session_path(session.project_root)
    session_path.parent.mkdir(parents=True, exist_ok=True)
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
            _session_path(project_root).parent
            / f"transfer_{hashlib.sha256(project_root.encode()).hexdigest()[:12]}.json"
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


from .session_memory_ops import (  # noqa: F401, E402
    _extract_test_failure_keys,
    _project_hash,
    _session_path,
    check_session_exit_gate,
    detect_applied_repairs,
    escalate_persistent_failures,
    expire_session,
    get_habit_mode_active,
    load_behavior_compass,
    propose_repairs,
    record_mesh_run,
    record_test_failure_classification,
    report_repair_outcome,
    save_behavior_compass,
)
