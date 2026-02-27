"""Behavioral compass type definitions and constants.

Dataclasses for hypothesis tracking, approach attempts, predictions,
coverage metrics, and the top-level BehaviorCompass container.

Extracted from behavior_compass.py for module size compliance.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

# ── Hypothesis & Approach ────────────────────────────────────────────────


@dataclass
class BehaviorHypothesis:
    """A live constraint hypothesis with confidence tracking.

    Auto-generated from command failures at low confidence (0.3),
    promoted by evidence or agent declaration via constraint_check.
    """

    id: str  # 8-char hash
    claim: str  # Human-readable: "idevicerestore can't send iBSS to iBSS DFU"
    confidence: float  # 0.0-1.0
    evidence_for: list[str] = field(default_factory=list)
    evidence_against: list[str] = field(default_factory=list)
    created_at: float = 0.0
    last_tested: float = 0.0
    last_decay: float = 0.0
    source: str = "command_failure"  # command_failure | precheck_declared | constraint_violation
    status: str = "active"  # active | confirmed | weakened | expired
    applies_to_sigs: list[str] = field(default_factory=list)  # e.g. ["idevicerestore:*"]
    applies_to_tools: list[str] = field(default_factory=list)  # e.g. ["Bash"]
    trust_score: float = 0.5  # Derived from coherence and source evidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "claim": self.claim,
            "confidence": self.confidence,
            "evidence_for": self.evidence_for,
            "evidence_against": self.evidence_against,
            "created_at": self.created_at,
            "last_tested": self.last_tested,
            "last_decay": self.last_decay,
            "source": self.source,
            "status": self.status,
            "applies_to_sigs": self.applies_to_sigs,
            "applies_to_tools": self.applies_to_tools,
            "trust_score": self.trust_score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BehaviorHypothesis:
        return cls(
            id=data.get("id", ""),
            claim=data.get("claim", ""),
            confidence=data.get("confidence", 0.0),
            evidence_for=data.get("evidence_for", []),
            evidence_against=data.get("evidence_against", []),
            created_at=data.get("created_at", 0.0),
            last_tested=data.get("last_tested", 0.0),
            last_decay=data.get("last_decay", data.get("last_tested", 0.0)),
            source=data.get("source", "command_failure"),
            status=data.get("status", "active"),
            applies_to_sigs=data.get("applies_to_sigs", []),
            applies_to_tools=data.get("applies_to_tools", []),
            trust_score=data.get("trust_score", 0.5),
        )


@dataclass
class ApproachAttempt:
    """A normalized attempt at a goal, tracked by command signature."""

    approach_sig: str  # Normalized: "idevicerestore:restore"
    exit_codes: list[int] = field(default_factory=list)
    error_sigs: list[str] = field(default_factory=list)
    started_at: float = 0.0
    last_event: float = 0.0
    outcome: str = "pending"  # pending | success | failed | abandoned
    event_count: int = 0
    hyp_version_at_start: int = 0  # hypothesis_version when approach was first created

    def to_dict(self) -> dict[str, Any]:
        return {
            "approach_sig": self.approach_sig,
            "exit_codes": self.exit_codes,
            "error_sigs": self.error_sigs,
            "started_at": self.started_at,
            "last_event": self.last_event,
            "outcome": self.outcome,
            "event_count": self.event_count,
            "hyp_version_at_start": self.hyp_version_at_start,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApproachAttempt:
        return cls(
            approach_sig=data.get("approach_sig", ""),
            exit_codes=data.get("exit_codes", []),
            error_sigs=data.get("error_sigs", []),
            started_at=data.get("started_at", 0.0),
            last_event=data.get("last_event", 0.0),
            outcome=data.get("outcome", "pending"),
            event_count=data.get("event_count", 0),
            hyp_version_at_start=data.get("hyp_version_at_start", 0),
        )


# ── Prediction Tracking (Architecture of Inquiry) ────────────────────


@dataclass
class PredictionExpectation:
    """Structured expected outcome for deterministic falsification.

    Types:
    - exit_code: Compare against command exit code (int)
    - error_signature: Check if value is substring of error output
    - stdout_contains: Check if value is substring of stdout
    """

    type: str = "exit_code"  # "exit_code" | "error_signature" | "stdout_contains"
    value: str | int = 0  # The expected value
    negate: bool = False  # True means "NOT this outcome"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "value": self.value, "negate": self.negate}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PredictionExpectation:
        return cls(
            type=data.get("type", "exit_code"),
            value=data.get("value", 0),
            negate=data.get("negate", False),
        )


@dataclass
class Prediction:
    """A falsifiable prediction registered via prediction_register.

    Tracks what the agent expected to happen and whether it was confirmed
    or falsified by the actual outcome. Only applicable to execute/Bash flows.
    """

    prediction_id: str = ""
    claim: str = ""  # Free-text: "I expect this command to succeed"
    expected: PredictionExpectation = field(default_factory=PredictionExpectation)
    declared_at_event: int = 0
    declared_sig: str = ""  # Command signature the prediction applies to
    linked_hypothesis_id: str | None = None
    status: str = "pending"  # "pending" | "confirmed" | "falsified" | "expired"
    checked_at_event: int | None = None
    actual_outcome: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "claim": self.claim,
            "expected": self.expected.to_dict(),
            "declared_at_event": self.declared_at_event,
            "declared_sig": self.declared_sig,
            "linked_hypothesis_id": self.linked_hypothesis_id,
            "status": self.status,
            "checked_at_event": self.checked_at_event,
            "actual_outcome": self.actual_outcome,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Prediction:
        exp_data = data.get("expected", {})
        return cls(
            prediction_id=data.get("prediction_id", ""),
            claim=data.get("claim", ""),
            expected=PredictionExpectation.from_dict(exp_data)
            if isinstance(exp_data, dict)
            else PredictionExpectation(),
            declared_at_event=data.get("declared_at_event", 0),
            declared_sig=data.get("declared_sig", ""),
            linked_hypothesis_id=data.get("linked_hypothesis_id"),
            status=data.get("status", "pending"),
            checked_at_event=data.get("checked_at_event"),
            actual_outcome=data.get("actual_outcome"),
        )


# ── Coverage Metrics ─────────────────────────────────────────────────


@dataclass
class CoverageMetrics:
    """Computed behavioral statistics."""

    constraints_verified: int = 0  # Hypotheses at confidence > promote_threshold
    constraints_predicted: int = 0  # Existed BEFORE the event that tested them
    approaches_attempted: int = 0
    approach_success_rate: float = 0.0
    bash_count_recent: int = 0  # In last N tool uses
    read_count_recent: int = 0  # In last N tool uses
    prediction_recall: float = 0.0  # predicted / (predicted + surprise)

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraints_verified": self.constraints_verified,
            "constraints_predicted": self.constraints_predicted,
            "approaches_attempted": self.approaches_attempted,
            "approach_success_rate": round(self.approach_success_rate, 3),
            "bash_count_recent": self.bash_count_recent,
            "read_count_recent": self.read_count_recent,
            "prediction_recall": round(self.prediction_recall, 3),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoverageMetrics:
        return cls(
            constraints_verified=data.get("constraints_verified", 0),
            constraints_predicted=data.get("constraints_predicted", 0),
            approaches_attempted=data.get("approaches_attempted", 0),
            approach_success_rate=data.get("approach_success_rate", 0.0),
            bash_count_recent=data.get("bash_count_recent", 0),
            read_count_recent=data.get("read_count_recent", 0),
            prediction_recall=data.get("prediction_recall", 0.0),
        )


# ── Nested state containers ──────────────────────────────────────────


@dataclass
class SignalState:
    """Event tracking and signal cooldown state (v2 intent bias layer)."""

    event_counter: int = 0  # Monotonic per-event for cooldowns
    last_fired: dict[str, int] = field(default_factory=dict)  # signal → event_counter at fire
    signal_fire_counts: dict[str, int] = field(default_factory=dict)  # signal → session-total
    early_nudge_emitted: bool = False  # One-time serial_discovery stage 1
    constraint_check_count_session: int = 0  # constraint_check invocations this session
    intent_history: list[str] = field(default_factory=list)  # Last 30 intents (rolling)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_counter": self.event_counter,
            "last_fired": self.last_fired,
            "signal_fire_counts": self.signal_fire_counts,
            "early_nudge_emitted": self.early_nudge_emitted,
            "constraint_check_count_session": self.constraint_check_count_session,
            "intent_history": self.intent_history,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SignalState:
        return cls(
            event_counter=data.get("event_counter", 0),
            last_fired=data.get("last_fired", {}),
            signal_fire_counts=data.get("signal_fire_counts", {}),
            early_nudge_emitted=data.get("early_nudge_emitted", False),
            constraint_check_count_session=data.get(
                "constraint_check_count_session",
                data.get("precheck_count_session", 0),  # v1 compat
            ),
            intent_history=data.get("intent_history", []),
        )


@dataclass
class NudgeState:
    """Global behavior profile nudge tracking (v3)."""

    pending_nudge_signals: list[str] = field(default_factory=list)  # Signals with active nudges
    pending_nudge_constraint_check_count: int = (
        0  # constraint_check_count snapshot when pending_nudge_signals was set
    )
    nudge_outcomes: dict[str, str] = field(default_factory=dict)  # signal → "accepted" | "ignored"
    compliance_rate: float = 1.0  # Rolling compliance score (0.0-1.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pending_nudge_signals": self.pending_nudge_signals,
            "pending_nudge_constraint_check_count": self.pending_nudge_constraint_check_count,
            "nudge_outcomes": self.nudge_outcomes,
            "compliance_rate": self.compliance_rate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NudgeState:
        return cls(
            pending_nudge_signals=data.get("pending_nudge_signals", []),
            pending_nudge_constraint_check_count=data.get(
                "pending_nudge_constraint_check_count",
                data.get("pending_nudge_precheck_count", 0),  # v1 compat
            ),
            nudge_outcomes=data.get("nudge_outcomes", {}),
            compliance_rate=data.get("compliance_rate", 1.0),
        )


@dataclass
class PredictionStateContainer:
    """Architecture of Inquiry prediction tracking (v4)."""

    pending_predictions: list[Prediction] = field(default_factory=list)
    prediction_log: list[dict[str, Any]] = field(
        default_factory=list
    )  # confirmed/falsified history

    def to_dict(self) -> dict[str, Any]:
        return {
            "pending_predictions": [p.to_dict() for p in self.pending_predictions],
            "prediction_log": self.prediction_log,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PredictionStateContainer:
        return cls(
            pending_predictions=[
                Prediction.from_dict(p) for p in data.get("pending_predictions", [])
            ],
            prediction_log=data.get("prediction_log", []),
        )


# ── Top-level container ──────────────────────────────────────────────


@dataclass
class BehaviorCompass:
    """Top-level container for all behavioral state.

    Flows through session memory as a serialized dict.
    Channel receives it via event.raw_input, returns deltas via metrics.
    """

    hypotheses: list[BehaviorHypothesis] = field(default_factory=list)
    approaches: list[ApproachAttempt] = field(default_factory=list)
    coverage: CoverageMetrics = field(default_factory=CoverageMetrics)
    uncertainty_zones: list[str] = field(default_factory=list)
    action_history: list[dict[str, Any]] = field(default_factory=list)
    error_memory: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )  # error_hash -> aggregate stats
    hypothesis_version: int = 0  # Monotonic; +1 on any hyp mutation
    signals: SignalState = field(default_factory=SignalState)
    nudges: NudgeState = field(default_factory=NudgeState)
    predictions: PredictionStateContainer = field(default_factory=PredictionStateContainer)

    # ── Backward-compatible property accessors for SignalState ──

    @property
    def event_counter(self) -> int:
        return self.signals.event_counter

    @event_counter.setter
    def event_counter(self, val: int) -> None:
        self.signals.event_counter = val

    @property
    def last_fired(self) -> dict[str, int]:
        return self.signals.last_fired

    @last_fired.setter
    def last_fired(self, val: dict[str, int]) -> None:
        self.signals.last_fired = val

    @property
    def signal_fire_counts(self) -> dict[str, int]:
        return self.signals.signal_fire_counts

    @signal_fire_counts.setter
    def signal_fire_counts(self, val: dict[str, int]) -> None:
        self.signals.signal_fire_counts = val

    @property
    def early_nudge_emitted(self) -> bool:
        return self.signals.early_nudge_emitted

    @early_nudge_emitted.setter
    def early_nudge_emitted(self, val: bool) -> None:
        self.signals.early_nudge_emitted = val

    @property
    def constraint_check_count_session(self) -> int:
        return self.signals.constraint_check_count_session

    @constraint_check_count_session.setter
    def constraint_check_count_session(self, val: int) -> None:
        self.signals.constraint_check_count_session = val

    @property
    def intent_history(self) -> list[str]:
        return self.signals.intent_history

    @intent_history.setter
    def intent_history(self, val: list[str]) -> None:
        self.signals.intent_history = val

    # ── Backward-compatible property accessors for NudgeState ──

    @property
    def pending_nudge_signals(self) -> list[str]:
        return self.nudges.pending_nudge_signals

    @pending_nudge_signals.setter
    def pending_nudge_signals(self, val: list[str]) -> None:
        self.nudges.pending_nudge_signals = val

    @property
    def pending_nudge_constraint_check_count(self) -> int:
        return self.nudges.pending_nudge_constraint_check_count

    @pending_nudge_constraint_check_count.setter
    def pending_nudge_constraint_check_count(self, val: int) -> None:
        self.nudges.pending_nudge_constraint_check_count = val

    @property
    def nudge_outcomes(self) -> dict[str, str]:
        return self.nudges.nudge_outcomes

    @nudge_outcomes.setter
    def nudge_outcomes(self, val: dict[str, str]) -> None:
        self.nudges.nudge_outcomes = val

    @property
    def compliance_rate(self) -> float:
        return self.nudges.compliance_rate

    @compliance_rate.setter
    def compliance_rate(self, val: float) -> None:
        self.nudges.compliance_rate = val

    # ── Backward-compatible property accessors for PredictionState ──

    @property
    def pending_predictions(self) -> list[Prediction]:
        return self.predictions.pending_predictions

    @pending_predictions.setter
    def pending_predictions(self, val: list[Prediction]) -> None:
        self.predictions.pending_predictions = val

    @property
    def prediction_log(self) -> list[dict[str, Any]]:
        return self.predictions.prediction_log

    @prediction_log.setter
    def prediction_log(self, val: list[dict[str, Any]]) -> None:
        self.predictions.prediction_log = val

    # ── Serialization (flattened for backward compat) ──

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "hypotheses": [h.to_dict() for h in self.hypotheses],
            "approaches": [a.to_dict() for a in self.approaches],
            "coverage": self.coverage.to_dict(),
            "uncertainty_zones": self.uncertainty_zones,
            "action_history": self.action_history,
            "error_memory": self.error_memory,
            "hypothesis_version": self.hypothesis_version,
        }
        # Flatten nested state for backward-compatible serialization
        d.update(self.signals.to_dict())
        d.update(self.nudges.to_dict())
        d.update(self.predictions.to_dict())
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BehaviorCompass:
        if not data:
            return cls()
        return cls(
            hypotheses=[BehaviorHypothesis.from_dict(h) for h in data.get("hypotheses", [])],
            approaches=[ApproachAttempt.from_dict(a) for a in data.get("approaches", [])],
            coverage=CoverageMetrics.from_dict(data.get("coverage", {})),
            uncertainty_zones=data.get("uncertainty_zones", []),
            action_history=data.get("action_history", []),
            error_memory=data.get("error_memory", {}),
            hypothesis_version=data.get("hypothesis_version", 0),
            signals=SignalState.from_dict(data),
            nudges=NudgeState.from_dict(data),
            predictions=PredictionStateContainer.from_dict(data),
        )


def new_compass() -> BehaviorCompass:
    """Factory for empty compass."""
    return BehaviorCompass()


# ── Default thresholds (overridden by config) ────────────────────────────

DEFAULT_THRESHOLDS: dict[str, Any] = {
    "approach_cycling_count": 3,
    "approach_cycling_window_min": 30,
    "failure_amnesia_lookback": 30,
    "premature_action_ratio": 3.0,
    "premature_action_failure_rate": 0.5,
    "brute_force_approach_gap": 0,
    "consecutive_bash_failures": 3,
    "tool_repetition_count": 4,
    "tool_repetition_window_min": 30,
    "verification_debt_streak": 8,
    "stale_model_approach_changes": 2,
    "serial_discovery_early_threshold": 1,
    "signal_cooldown": 10,
    "escalation_threshold": 3,
}

DEFAULT_HYPOTHESIS_CONFIG: dict[str, Any] = {
    "max_active": 20,
    "auto_generate_confidence": 0.3,
    "promote_threshold": 0.7,
    "decay_per_hour": 0.05,
    "min_evidence_for_promote": 2,
    "strengthen_delta": 0.15,
    "weaken_delta": 0.1,
}

# ── Limits ───────────────────────────────────────────────────────────────

MAX_ACTION_HISTORY = 30
MAX_APPROACHES = 20
MAX_EVIDENCE_ITEMS = 10  # Per hypothesis, for/against lists
MAX_ERROR_MEMORY = 100


def make_hypothesis_id(claim: str, sig: str) -> str:
    """Generate deterministic 8-char ID from claim + command sig."""
    raw = f"{claim}|{sig}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]
