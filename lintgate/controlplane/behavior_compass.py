"""Behavioral compass — live hypothesis model of agent behavior.

Tracks constraints discovered through tool-use patterns, maintains
confidence-scored hypotheses, and computes coverage metrics for the
behavior_precheck tool.

Design principles (from Grail hidden compass):
- Hypotheses are live experiments, not static facts
- Confidence rises/falls based on outcome evidence
- Wrong hypotheses are productive (they improve uncertainty targeting)
- Co-construction: agent declares its model, tool computes gaps

No LLM calls, no subprocess calls, no file I/O in this module.
All persistence flows through session_memory.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import time
from dataclasses import dataclass, field
from typing import Any

# ── Dataclasses ──────────────────────────────────────────────────────────


@dataclass
class BehaviorHypothesis:
    """A live constraint hypothesis with confidence tracking.

    Auto-generated from command failures at low confidence (0.3),
    promoted by evidence or agent declaration via precheck.
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
    """A falsifiable prediction registered via behavior_precheck.

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
    # ── v2: intent bias layer ──
    intent_history: list[str] = field(default_factory=list)  # Last 30 intents (rolling)
    hypothesis_version: int = 0  # Monotonic; +1 on any hyp mutation
    precheck_count_session: int = 0  # Precheck invocations this session
    event_counter: int = 0  # Monotonic per-event for cooldowns
    last_fired: dict[str, int] = field(default_factory=dict)  # signal → event_counter at last fire
    signal_fire_counts: dict[str, int] = field(
        default_factory=dict
    )  # signal → session-total firings
    early_nudge_emitted: bool = False  # One-time serial_discovery stage 1
    # ── v3: global behavior profile ──
    pending_nudge_signals: list[str] = field(
        default_factory=list
    )  # Signals with active nudges awaiting outcome
    pending_nudge_precheck_count: int = (
        0  # precheck_count snapshot when pending_nudge_signals was set
    )
    nudge_outcomes: dict[str, str] = field(
        default_factory=dict
    )  # signal → "accepted" | "ignored" (per session)
    # ── v4: Architecture of Inquiry — prediction tracking ──
    pending_predictions: list[Prediction] = field(default_factory=list)
    prediction_log: list[dict[str, Any]] = field(
        default_factory=list
    )  # confirmed/falsified history

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypotheses": [h.to_dict() for h in self.hypotheses],
            "approaches": [a.to_dict() for a in self.approaches],
            "coverage": self.coverage.to_dict(),
            "uncertainty_zones": self.uncertainty_zones,
            "action_history": self.action_history,
            "error_memory": self.error_memory,
            "intent_history": self.intent_history,
            "hypothesis_version": self.hypothesis_version,
            "precheck_count_session": self.precheck_count_session,
            "event_counter": self.event_counter,
            "last_fired": self.last_fired,
            "signal_fire_counts": self.signal_fire_counts,
            "early_nudge_emitted": self.early_nudge_emitted,
            "pending_nudge_signals": self.pending_nudge_signals,
            "pending_nudge_precheck_count": self.pending_nudge_precheck_count,
            "nudge_outcomes": self.nudge_outcomes,
            "pending_predictions": [p.to_dict() for p in self.pending_predictions],
            "prediction_log": self.prediction_log,
        }

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
            intent_history=data.get("intent_history", []),
            hypothesis_version=data.get("hypothesis_version", 0),
            precheck_count_session=data.get("precheck_count_session", 0),
            event_counter=data.get("event_counter", 0),
            last_fired=data.get("last_fired", {}),
            signal_fire_counts=data.get("signal_fire_counts", {}),
            early_nudge_emitted=data.get("early_nudge_emitted", False),
            pending_nudge_signals=data.get("pending_nudge_signals", []),
            pending_nudge_precheck_count=data.get("pending_nudge_precheck_count", 0),
            nudge_outcomes=data.get("nudge_outcomes", {}),
            pending_predictions=[
                Prediction.from_dict(p) for p in data.get("pending_predictions", [])
            ],
            prediction_log=data.get("prediction_log", []),
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

# ── Intent taxonomy ─────────────────────────────────────────────────────
# 6 categories: enough structure to reduce ambiguity without NLP.

INTENT_CATEGORIES = frozenset({"inspect", "modify", "verify", "execute", "meta", "unknown"})

# Deterministic mapping chain:
# 1. Explicit tool type (Read, Write, Edit, Bash → fallback if not in sig map)
# 2. command_sig exact match (binary:subcommand)
# 3. Binary wildcard match (binary from binary:arg)
# 4. Fallback: Bash → "execute", other → "unknown"

_TOOL_TYPE_DEFAULTS: dict[str, str] = {
    "Read": "inspect",
    "Grep": "inspect",
    "Glob": "inspect",
    "Write": "modify",
    "Edit": "modify",
    "MultiEdit": "modify",
    "NotebookEdit": "modify",
    "WebFetch": "inspect",
    "WebSearch": "inspect",
    "Task": "meta",
    "TodoWrite": "meta",
    "AskUserQuestion": "meta",
}

# command_sig wildcard map for Bash: binary → intent
DEFAULT_INTENT_MAP: dict[str, str] = {
    # verify
    "pytest": "verify",
    "python": "execute",
    "ruff": "verify",
    "mypy": "verify",
    "flake8": "verify",
    "black": "modify",
    "isort": "modify",
    "cat": "inspect",
    "ls": "inspect",
    "head": "inspect",
    "tail": "inspect",
    "wc": "inspect",
    "file": "inspect",
    "stat": "inspect",
    "diff": "verify",
    "md5sum": "verify",
    "sha256sum": "verify",
    "xxd": "inspect",
    "hexdump": "inspect",
    "strings": "inspect",
    "find": "inspect",
    "du": "inspect",
    "readlink": "inspect",
    "which": "inspect",
    "type": "inspect",
    "command": "inspect",
    "test": "verify",
    "echo": "inspect",
    # modify
    "mkdir": "modify",
    "cp": "modify",
    "mv": "modify",
    "rm": "modify",
    "touch": "modify",
    "chmod": "modify",
    "chown": "modify",
    "sed": "modify",
    "awk": "modify",
    "tee": "modify",
    # execute
    "pip": "execute",
    "uv": "execute",
    "npm": "execute",
    "yarn": "execute",
    "make": "execute",
    "docker": "execute",
    "curl": "execute",
    "wget": "execute",
    # git default
    "git": "meta",
}

# More specific: "binary:subcommand" → intent (checked before binary-only)
DEFAULT_INTENT_SIG_MAP: dict[str, str] = {
    "git:status": "inspect",
    "git:log": "inspect",
    "git:diff": "verify",
    "git:show": "inspect",
    "git:branch": "inspect",
    "git:add": "modify",
    "git:commit": "modify",
    "git:push": "execute",
    "git:pull": "execute",
    "git:checkout": "modify",
    "git:merge": "execute",
    "git:rebase": "execute",
    "python:test": "verify",
    "python:pytest": "verify",
    # iOS tooling frequently uses hfsplus for inspection of extracted images.
    "hfsplus:ls": "inspect",
    "hfsplus:cat": "inspect",
    "hfsplus:rootfs": "inspect",
}


def resolve_intent(
    tool_name: str,
    command_sig: str,
    intent_map: dict[str, str] | None = None,
    intent_sig_map: dict[str, str] | None = None,
) -> str:
    """Resolve tool-use intent via deterministic mapping chain.

    Order: explicit tool type → command_sig exact match →
           binary wildcard match → fallback → unknown.
    """
    sig_map = intent_sig_map if intent_sig_map is not None else DEFAULT_INTENT_SIG_MAP
    bin_map = intent_map if intent_map is not None else DEFAULT_INTENT_MAP

    # 1. Non-Bash tools: explicit tool type lookup
    if tool_name != "Bash":
        return _TOOL_TYPE_DEFAULTS.get(tool_name, "unknown")

    # 2. Bash: try exact sig match first (e.g. "git:status")
    if command_sig and command_sig in sig_map:
        return sig_map[command_sig]

    # 3. Binary-only match (e.g. "pytest" from "pytest:tests")
    if command_sig:
        binary = command_sig.split(":")[0] if ":" in command_sig else command_sig
        if binary in bin_map:
            return bin_map[binary]

    # 4. Fallback: Bash default is "execute"
    return "execute"


# ── Command normalization ────────────────────────────────────────────────

_WRAPPER_PREFIXES = [
    ("uv", "run"),
    ("python", "-m"),
    ("python3", "-m"),
    ("env",),
    ("sudo",),
    ("nohup",),
    ("time",),
    ("nice",),
]

# Patterns that look like secrets — redact these
_SECRET_PATTERN = re.compile(
    r"(?:"
    r"[A-Za-z0-9+/]{40,}"  # Base64-ish long strings
    r"|[0-9a-f]{32,}"  # Long hex strings
    r"|(?:sk|pk|token|key|secret|password|auth)[_-]?\w{8,}"  # Named secrets
    r")",
    re.IGNORECASE,
)

# Patterns that look like absolute paths — strip to basename
_ABS_PATH_PATTERN = re.compile(r"/(?:[\w.-]+/){2,}([\w.-]+)")
_EXIT_CODE_LINE = re.compile(
    r"^(?:exit(?:[_ ]?(?:code|status))?|status)\s*[:=]?\s*\d+\s*$",
    re.IGNORECASE,
)


def normalize_command_sig(cmd: str) -> str:
    """Extract normalized command signature from a shell command.

    Strips wrapper prefixes, flags, paths, and secrets.
    Groups related commands under the same signature.

    Examples:
        "uv run python -m pytest tests/test_foo.py -v" → "pytest:tests"
        "idevicerestore -e custom.ipsw" → "idevicerestore:restore"
        "git status" → "git:status"
        "hfsplus rootfs.dec ls /Applications/" → "hfsplus:ls"
    """
    if not cmd or not cmd.strip():
        return "unknown:unknown"

    try:
        tokens = shlex.split(cmd)
    except ValueError:
        # Malformed shell command — split naively
        tokens = cmd.split()

    if not tokens:
        return "unknown:unknown"

    # Strip wrapper prefixes
    i = 0
    while i < len(tokens):
        matched = False
        for prefix in _WRAPPER_PREFIXES:
            prefix_len = len(prefix)
            if i + prefix_len <= len(tokens) and all(
                tokens[i + j] == prefix[j] for j in range(prefix_len)
            ):
                i += prefix_len
                # For "env" wrapper, also skip VAR=val tokens
                if prefix == ("env",):
                    while i < len(tokens) and "=" in tokens[i]:
                        i += 1
                matched = True
                break
        if not matched:
            break

    if i >= len(tokens):
        return "unknown:unknown"

    # Binary is the first non-wrapper token
    binary = tokens[i]
    # Strip path from binary if it's an absolute path
    if "/" in binary:
        binary = binary.rsplit("/", 1)[-1]

    # Find first positional argument (non-flag, non-secret)
    first_arg = ""
    for token in tokens[i + 1 :]:
        if token.startswith("-"):
            continue
        if _SECRET_PATTERN.search(token):
            continue
        # Strip absolute paths to basename-ish
        cleaned = _ABS_PATH_PATTERN.sub(r"\1", token)
        # Strip file extensions
        if "." in cleaned:
            cleaned = cleaned.rsplit(".", 1)[0]
        if cleaned:
            first_arg = cleaned
            break

    if not first_arg:
        first_arg = "default"

    # Truncate to reasonable length
    binary = binary[:30]
    first_arg = first_arg[:30]

    return f"{binary}:{first_arg}"


def extract_error_sig(stderr: str) -> str:
    """Extract normalized error signature from stderr output.

    Takes the last non-empty line, strips absolute paths and timestamps.
    Used as constraint key for deduplication.
    """
    if not stderr or not stderr.strip():
        return ""

    lines = stderr.strip().splitlines()

    # Walk backwards to find a meaningful line
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # Skip lines that are just separator characters
        if all(c in "-=_*#" for c in stripped):
            continue
        # Skip status-only lines (not useful failure signatures)
        if _EXIT_CODE_LINE.match(stripped):
            continue

        # Strip absolute paths
        cleaned = _ABS_PATH_PATTERN.sub(r"\1", stripped)
        # Strip timestamps (common patterns: ISO, syslog, bracketed)
        cleaned = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.\d]*\s*", "", cleaned)
        cleaned = re.sub(r"\[\d+[:.]\d+\]\s*", "", cleaned)

        # Truncate to reasonable length
        return cleaned[:200]

    return ""


def error_memory_key(error_sig: str) -> str:
    """Build a stable hash key for error-memory aggregation."""
    normalized = re.sub(r"\s+", " ", (error_sig or "").strip().lower())
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


# ── Hypothesis ID generation ─────────────────────────────────────────────


def _make_hypothesis_id(claim: str, sig: str) -> str:
    """Generate deterministic 8-char ID from claim + command sig."""
    raw = f"{claim}|{sig}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


# ── Core update functions ────────────────────────────────────────────────


_PREDICTION_EXPIRY_EVENTS = 20  # Expire predictions after this many events without check


def _check_predictions(
    compass: BehaviorCompass,
    tool_name: str,
    command_sig: str,
    exit_code: int | None,
    error_sig: str,
    output_str: str,
    cfg: dict[str, Any],
) -> None:
    """Check pending predictions against actual outcomes.

    Only checks for Bash/execute events. Updates prediction status,
    strengthens/weakens linked hypotheses, and logs outcomes.
    """
    # Only check predictions for Bash/execute events
    if tool_name != "Bash":
        return

    still_pending: list[Prediction] = []

    for pred in compass.pending_predictions:
        # Check expiry
        if compass.event_counter - pred.declared_at_event > _PREDICTION_EXPIRY_EVENTS:
            pred.status = "expired"
            compass.prediction_log.append(
                {
                    "prediction_id": pred.prediction_id,
                    "status": "expired",
                    "event": compass.event_counter,
                }
            )
            continue

        # Check if this prediction applies to the current command
        # Require full signature match (e.g. "git:status" only matches "git:status")
        # Skip predictions with empty/unknown sigs — they can't be meaningfully checked
        if not pred.declared_sig or pred.declared_sig == "unknown:unknown":
            still_pending.append(pred)
            continue
        if not command_sig or command_sig == "unknown:unknown":
            still_pending.append(pred)
            continue
        if pred.declared_sig != command_sig:
            still_pending.append(pred)
            continue

        # Evaluate the prediction
        exp = pred.expected
        matched = False

        if exp.type == "exit_code":
            expected_code = int(exp.value) if isinstance(exp.value, (int, str)) else 0
            if exit_code is not None:
                matched = exit_code == expected_code
        elif exp.type == "error_signature":
            matched = str(exp.value).lower() in error_sig.lower() if error_sig else False
        elif exp.type == "stdout_contains":
            matched = str(exp.value).lower() in output_str.lower() if output_str else False

        # Handle negation
        if exp.negate:
            matched = not matched

        if matched:
            pred.status = "confirmed"
        else:
            pred.status = "falsified"

        pred.checked_at_event = compass.event_counter
        pred.actual_outcome = f"exit={exit_code}, err={error_sig[:50]}"

        compass.prediction_log.append(
            {
                "prediction_id": pred.prediction_id,
                "status": pred.status,
                "event": compass.event_counter,
                "expected_type": exp.type,
                "expected_value": exp.value,
                "actual_outcome": pred.actual_outcome,
            }
        )

        # Strengthen/weaken linked hypothesis
        if pred.linked_hypothesis_id:
            for hyp in compass.hypotheses:
                if hyp.id == pred.linked_hypothesis_id:
                    if pred.status == "confirmed":
                        delta = cfg.get("strengthen_delta", 0.15)
                        hyp.confidence = min(hyp.confidence + delta, 1.0)
                        hyp.evidence_for.append(
                            f"prediction confirmed at event {compass.event_counter}"
                        )
                    elif pred.status == "falsified":
                        delta = cfg.get("weaken_delta", 0.1)
                        hyp.confidence = max(hyp.confidence - delta, 0.0)
                        hyp.evidence_against.append(
                            f"prediction falsified at event {compass.event_counter}"
                        )
                    compass.hypothesis_version += 1
                    break

    compass.pending_predictions = still_pending

    # Keep prediction_log bounded
    if len(compass.prediction_log) > 100:
        compass.prediction_log = compass.prediction_log[-100:]


def compute_prediction_accuracy(compass: BehaviorCompass) -> float | None:
    """Compute prediction accuracy from the prediction log.

    Returns None if fewer than 5 predictions have been checked
    (insufficient data for meaningful accuracy).
    """
    checked = [
        entry
        for entry in compass.prediction_log
        if entry.get("status") in ("confirmed", "falsified")
    ]
    if len(checked) < 5:
        return None
    confirmed = sum(1 for e in checked if e["status"] == "confirmed")
    return confirmed / len(checked)


def record_tool_event(
    compass: BehaviorCompass,
    tool_name: str,
    tool_input: dict[str, Any] | str,
    tool_output: str | dict[str, Any],
    *,
    now: float | None = None,
    hyp_config: dict[str, Any] | None = None,
) -> list[str]:
    """Record a tool-use event and update compass state.

    This is the core update function called on every PostToolUse event.
    Auto-generates low-confidence hypotheses from Bash failures.
    Tests existing hypotheses against outcome.
    Applies decay to stale hypotheses.

    Returns list of alert names triggered (for detection rules to check).
    """
    if now is None:
        now = time.time()
    cfg = {**DEFAULT_HYPOTHESIS_CONFIG, **(hyp_config or {})}

    compass.event_counter += 1

    # Extract structured data from the event
    command_sig = ""
    exit_code: int | None = None
    error_sig = ""
    output_str = ""

    if tool_name == "Bash":
        cmd = ""
        if isinstance(tool_input, dict):
            cmd = tool_input.get("command", "")
        elif isinstance(tool_input, str):
            cmd = tool_input
        command_sig = normalize_command_sig(cmd)

        # Parse exit code and error from output
        output_str = tool_output if isinstance(tool_output, str) else str(tool_output)
        # Common patterns for exit code in tool output
        exit_match = re.search(
            r"(?:exit[_ ]code|exit[_ ]status|exitstatus)[: =]+(\d+)",
            output_str,
            re.IGNORECASE,
        )
        if exit_match:
            exit_code = int(exit_match.group(1))
        elif "error" in output_str.lower() or "failed" in output_str.lower():
            exit_code = 1  # Infer failure
        else:
            exit_code = 0  # Assume success

        if exit_code != 0:
            error_sig = extract_error_sig(output_str)
            _update_error_memory(compass, error_sig, now)

    # v2: Resolve and track intent
    intent = resolve_intent(tool_name, command_sig)

    # Append to action history (rolling window)
    event_record = {
        "tool": tool_name,
        "ts": now,
        "sig": command_sig,
        "exit": exit_code,
        "err": error_sig,
        "intent": intent,
    }
    compass.action_history.append(event_record)
    compass.intent_history.append(intent)
    if len(compass.intent_history) > MAX_ACTION_HISTORY:
        compass.intent_history = compass.intent_history[-MAX_ACTION_HISTORY:]
    if len(compass.action_history) > MAX_ACTION_HISTORY:
        compass.action_history = compass.action_history[-MAX_ACTION_HISTORY:]

    # Update approaches (Bash commands only)
    if tool_name == "Bash" and command_sig and exit_code is not None:
        _update_approach(compass, command_sig, exit_code, error_sig, now)

    # Test existing hypotheses against this event (before auto-generating new ones,
    # so newly created hypotheses don't get double-strengthened by their own event)
    if tool_name == "Bash" and command_sig:
        _test_hypotheses(compass, command_sig, exit_code, error_sig, now, cfg)

    # Check pending predictions against actual outcomes (Bash events only)
    _check_predictions(compass, tool_name, command_sig, exit_code, error_sig, output_str, cfg)

    # Auto-generate hypothesis from Bash failure
    if tool_name == "Bash" and exit_code is not None and exit_code != 0 and error_sig:
        _auto_generate_hypothesis(compass, command_sig, error_sig, now, cfg)

    # Decay stale hypotheses
    decay_stale(compass, now, cfg)

    # Recompute coverage
    compass.coverage = compute_coverage(compass, cfg)
    compass.uncertainty_zones = compute_uncertainty_zones(compass)

    return []  # Alerts computed by channel, not here


def _update_error_memory(
    compass: BehaviorCompass,
    error_sig: str,
    now: float,
) -> None:
    """Update rolling cross-window error memory for amnesia detection."""
    key = error_memory_key(error_sig)
    if not key:
        return

    current = compass.error_memory.get(key)
    if current is None:
        compass.error_memory[key] = {
            "count": 1,
            "first_seen": now,
            "last_seen": now,
            "last_sig": error_sig[:120],
        }
    else:
        current["count"] = int(current.get("count", 0)) + 1
        current["last_seen"] = now
        current.setdefault("first_seen", now)
        current["last_sig"] = error_sig[:120]

    if len(compass.error_memory) > MAX_ERROR_MEMORY:
        ordered = sorted(
            compass.error_memory.items(),
            key=lambda kv: float(kv[1].get("last_seen", 0.0)),
        )
        overflow = len(compass.error_memory) - MAX_ERROR_MEMORY
        for key_to_remove, _ in ordered[:overflow]:
            compass.error_memory.pop(key_to_remove, None)


def _update_approach(
    compass: BehaviorCompass,
    sig: str,
    exit_code: int,
    error_sig: str,
    now: float,
) -> None:
    """Update or create an approach attempt for this command signature."""
    # Find existing approach with same sig
    existing = None
    for approach in compass.approaches:
        if approach.approach_sig == sig:
            existing = approach
            break

    if existing is not None:
        existing.exit_codes.append(exit_code)
        existing.error_sigs.append(error_sig)
        existing.last_event = now
        existing.event_count += 1
        # Update outcome
        if exit_code == 0:
            existing.outcome = "success"
        elif existing.outcome != "success":
            existing.outcome = "failed"
        # Cap list sizes
        if len(existing.exit_codes) > MAX_EVIDENCE_ITEMS:
            existing.exit_codes = existing.exit_codes[-MAX_EVIDENCE_ITEMS:]
        if len(existing.error_sigs) > MAX_EVIDENCE_ITEMS:
            existing.error_sigs = existing.error_sigs[-MAX_EVIDENCE_ITEMS:]
    else:
        new_approach = ApproachAttempt(
            approach_sig=sig,
            exit_codes=[exit_code],
            error_sigs=[error_sig] if error_sig else [],
            started_at=now,
            last_event=now,
            outcome="success" if exit_code == 0 else "failed",
            event_count=1,
            hyp_version_at_start=compass.hypothesis_version,
        )
        compass.approaches.append(new_approach)
        # Enforce rolling window
        if len(compass.approaches) > MAX_APPROACHES:
            compass.approaches = compass.approaches[-MAX_APPROACHES:]


def _auto_generate_hypothesis(
    compass: BehaviorCompass,
    command_sig: str,
    error_sig: str,
    now: float,
    cfg: dict[str, Any],
) -> None:
    """Auto-generate a low-confidence hypothesis from a Bash failure.

    Only creates if no existing hypothesis has the same error signature.
    """
    # Check for existing hypothesis with same error sig
    for hyp in compass.hypotheses:
        if hyp.status in ("active", "confirmed"):
            for ev in hyp.evidence_for:
                if error_sig in ev:
                    # Already tracked — strengthen instead
                    update_hypothesis(
                        compass,
                        hyp.id,
                        "strengthen",
                        f"Re-observed: {error_sig}",
                        now=now,
                        cfg=cfg,
                    )
                    return

    # Generate new hypothesis
    hyp_id = _make_hypothesis_id(error_sig, command_sig)

    # Check for duplicate ID
    if any(h.id == hyp_id for h in compass.hypotheses):
        return

    binary = command_sig.split(":")[0] if ":" in command_sig else command_sig

    hypothesis = BehaviorHypothesis(
        id=hyp_id,
        claim=f"{binary} failed: {error_sig[:100]}",
        confidence=cfg["auto_generate_confidence"],
        evidence_for=[f"exit!=0 with: {error_sig[:100]}"],
        created_at=now,
        last_tested=now,
        last_decay=now,
        source="command_failure",
        applies_to_sigs=[f"{binary}:*"],
        applies_to_tools=["Bash"],
    )

    compass.hypotheses.append(hypothesis)
    compass.hypothesis_version += 1
    evict_overflow(compass, cfg)


def _test_hypotheses(
    compass: BehaviorCompass,
    command_sig: str,
    exit_code: int | None,
    error_sig: str,
    now: float,
    cfg: dict[str, Any],
) -> None:
    """Test existing hypotheses against this event outcome."""
    binary = command_sig.split(":")[0] if ":" in command_sig else command_sig

    for hyp in compass.hypotheses:
        if hyp.status not in ("active", "confirmed"):
            continue

        # Check if this hypothesis applies to this command
        relevant = False
        for sig_pattern in hyp.applies_to_sigs:
            if sig_pattern.endswith(":*"):
                if binary == sig_pattern[:-2]:
                    relevant = True
                    break
            elif command_sig == sig_pattern:
                relevant = True
                break

        if not relevant:
            continue

        hyp.last_tested = now

        if exit_code != 0 and error_sig:
            # Failure — does it match this hypothesis?
            # Check if the error signature overlaps
            hyp_error_keywords = set(hyp.claim.lower().split())
            event_error_keywords = set(error_sig.lower().split())
            overlap = hyp_error_keywords & event_error_keywords
            if len(overlap) >= 2:  # At least 2 words in common
                update_hypothesis(
                    compass,
                    hyp.id,
                    "strengthen",
                    f"Confirmed by: {error_sig[:80]}",
                    now=now,
                    cfg=cfg,
                )
        elif exit_code == 0:
            # Success on a command this hypothesis said would fail
            update_hypothesis(
                compass,
                hyp.id,
                "weaken",
                f"Succeeded: {command_sig}",
                now=now,
                cfg=cfg,
            )


def update_hypothesis(
    compass: BehaviorCompass,
    hyp_id: str,
    direction: str,
    evidence: str,
    *,
    now: float | None = None,
    cfg: dict[str, Any] | None = None,
) -> None:
    """Strengthen or weaken a hypothesis based on new evidence.

    direction: "strengthen" (+delta) or "weaken" (-delta)
    """
    if now is None:
        now = time.time()
    if cfg is None:
        cfg = DEFAULT_HYPOTHESIS_CONFIG

    for hyp in compass.hypotheses:
        if hyp.id == hyp_id:
            hyp.last_tested = now
            hyp.last_decay = now

            if direction == "strengthen":
                hyp.confidence = min(1.0, hyp.confidence + cfg["strengthen_delta"])
                hyp.evidence_for.append(evidence)
                if len(hyp.evidence_for) > MAX_EVIDENCE_ITEMS:
                    hyp.evidence_for = hyp.evidence_for[-MAX_EVIDENCE_ITEMS:]
                # Promote to confirmed if enough evidence
                if (
                    hyp.confidence >= cfg["promote_threshold"]
                    and len(hyp.evidence_for) >= cfg["min_evidence_for_promote"]
                ):
                    hyp.status = "confirmed"
            elif direction == "weaken":
                hyp.confidence = max(0.0, hyp.confidence - cfg["weaken_delta"])
                hyp.evidence_against.append(evidence)
                if len(hyp.evidence_against) > MAX_EVIDENCE_ITEMS:
                    hyp.evidence_against = hyp.evidence_against[-MAX_EVIDENCE_ITEMS:]
                if hyp.confidence <= 0.0:
                    hyp.status = "expired"
                elif hyp.confidence < 0.3:
                    hyp.status = "weakened"
            compass.hypothesis_version += 1
            break


def decay_stale(
    compass: BehaviorCompass,
    now: float,
    cfg: dict[str, Any] | None = None,
) -> None:
    """Apply confidence decay to hypotheses not tested recently."""
    if cfg is None:
        cfg = DEFAULT_HYPOTHESIS_CONFIG
    decay_rate = cfg["decay_per_hour"]

    for hyp in compass.hypotheses:
        if hyp.status in ("expired",):
            continue
        decay_anchor = max(hyp.last_tested, hyp.last_decay)
        if decay_anchor <= 0.0:
            decay_anchor = hyp.last_tested
        hours_stale = (now - decay_anchor) / 3600.0
        if hours_stale <= 0.0:
            continue
        decay = decay_rate * hours_stale
        hyp.confidence = max(0.0, hyp.confidence - decay)
        hyp.last_decay = now
        if hyp.confidence <= 0.0:
            hyp.status = "expired"
            compass.hypothesis_version += 1


def evict_overflow(
    compass: BehaviorCompass,
    cfg: dict[str, Any] | None = None,
) -> None:
    """Evict lowest-confidence + oldest hypotheses when over cap."""
    if cfg is None:
        cfg = DEFAULT_HYPOTHESIS_CONFIG
    max_active = cfg["max_active"]

    # Remove expired first
    compass.hypotheses = [h for h in compass.hypotheses if h.status != "expired"]

    if len(compass.hypotheses) <= max_active:
        return

    # Sort by (confidence ASC, created_at ASC) — lowest confidence + oldest first
    compass.hypotheses.sort(key=lambda h: (h.confidence, h.created_at))

    # Keep only the top N
    compass.hypotheses = compass.hypotheses[-(max_active):]


# ── Coverage and uncertainty ─────────────────────────────────────────────


def compute_coverage(
    compass: BehaviorCompass,
    cfg: dict[str, Any] | None = None,
) -> CoverageMetrics:
    """Recompute coverage metrics from current compass state."""
    if cfg is None:
        cfg = DEFAULT_HYPOTHESIS_CONFIG
    promote_threshold = cfg["promote_threshold"]

    active_hyps = [h for h in compass.hypotheses if h.status in ("active", "confirmed")]
    verified = sum(1 for h in active_hyps if h.confidence >= promote_threshold)

    # Count hypotheses that were predicted (existed before the failure that confirmed them)
    predicted = sum(
        1
        for h in active_hyps
        if h.source == "precheck_declared" and h.confidence >= promote_threshold
    )

    # Approach stats
    total_approaches = len(compass.approaches)
    successful = sum(1 for a in compass.approaches if a.outcome == "success")
    success_rate = successful / total_approaches if total_approaches > 0 else 0.0

    # Action ratio from recent history
    recent = compass.action_history[-10:]
    bash_count = sum(1 for e in recent if e.get("tool") == "Bash")
    read_count = sum(1 for e in recent if e.get("tool") in ("Read", "Grep", "Glob"))

    # Prediction recall
    surprise = sum(1 for h in active_hyps if h.source == "command_failure" and h.confidence >= 0.5)
    total_discovered = predicted + surprise
    recall = predicted / total_discovered if total_discovered > 0 else 0.0

    return CoverageMetrics(
        constraints_verified=verified,
        constraints_predicted=predicted,
        approaches_attempted=total_approaches,
        approach_success_rate=success_rate,
        bash_count_recent=bash_count,
        read_count_recent=read_count,
        prediction_recall=recall,
    )


def compute_uncertainty_zones(compass: BehaviorCompass) -> list[str]:
    """Identify areas with lowest confidence or missing coverage.

    Returns human-readable descriptions of uncertainty.
    """
    zones: list[str] = []

    # 1. Approaches that failed but have no hypothesis explaining why
    for approach in compass.approaches:
        if approach.outcome != "failed":
            continue
        # Check if any hypothesis covers this approach
        binary = approach.approach_sig.split(":")[0]
        covered = False
        for hyp in compass.hypotheses:
            if hyp.status in ("expired",):
                continue
            for sig in hyp.applies_to_sigs:
                if sig.endswith(":*") and binary == sig[:-2]:
                    covered = True
                    break
                if approach.approach_sig == sig:
                    covered = True
                    break
            if covered:
                break
        if not covered and approach.error_sigs:
            last_err = approach.error_sigs[-1] if approach.error_sigs else "unknown"
            zones.append(
                f"Failed approach '{approach.approach_sig}' has no constraint hypothesis "
                f"(last error: {last_err[:60]})"
            )

    # 2. Low-confidence hypotheses (< 0.4)
    for hyp in compass.hypotheses:
        if hyp.status in ("expired",):
            continue
        if 0.0 < hyp.confidence < 0.4:
            zones.append(
                f"Low-confidence hypothesis: {hyp.claim[:80]} (confidence: {hyp.confidence:.2f})"
            )

    # 3. Hypotheses with conflicting evidence
    for hyp in compass.hypotheses:
        if hyp.status in ("expired",):
            continue
        if hyp.evidence_for and hyp.evidence_against:
            zones.append(
                f"Conflicting evidence for: {hyp.claim[:80]} "
                f"({len(hyp.evidence_for)} for, {len(hyp.evidence_against)} against)"
            )

    return zones[:5]  # Cap at 5 for token efficiency


# ── Precheck support ─────────────────────────────────────────────────────


def find_relevant_hypotheses(
    compass: BehaviorCompass,
    command_sig: str | None = None,
    tool: str | None = None,
) -> list[BehaviorHypothesis]:
    """Filter hypotheses by applicability for precheck recall computation.

    Returns only active/confirmed hypotheses that match the given
    command signature or tool type.
    """
    results = []
    binary = ""
    if command_sig and ":" in command_sig:
        binary = command_sig.split(":")[0]

    for hyp in compass.hypotheses:
        if hyp.status in ("expired",):
            continue

        # If no filters, return all active
        if not command_sig and not tool:
            results.append(hyp)
            continue

        matched = False

        # Check command signature match
        if command_sig:
            for sig_pattern in hyp.applies_to_sigs:
                if sig_pattern.endswith(":*") and binary == sig_pattern[:-2]:
                    matched = True
                    break
                if command_sig == sig_pattern:
                    matched = True
                    break

        # Check tool match
        if not matched and tool and tool in hyp.applies_to_tools:
            matched = True

        if matched:
            results.append(hyp)

    return results


def add_declared_hypothesis(
    compass: BehaviorCompass,
    claim: str,
    command_sig: str | None = None,
    *,
    now: float | None = None,
    cfg: dict[str, Any] | None = None,
) -> BehaviorHypothesis:
    """Add a hypothesis declared by the agent via precheck.

    These start at confidence 0.5 (higher than auto-generated 0.3)
    and have source="precheck_declared".
    """
    if now is None:
        now = time.time()
    if cfg is None:
        cfg = DEFAULT_HYPOTHESIS_CONFIG

    sig = command_sig or "unknown:unknown"
    hyp_id = _make_hypothesis_id(claim, sig)

    # Check for existing
    for existing in compass.hypotheses:
        if existing.id == hyp_id:
            # Strengthen existing — agent re-declared it
            update_hypothesis(
                compass, hyp_id, "strengthen", "Re-declared via precheck", now=now, cfg=cfg
            )
            return existing

    binary = sig.split(":")[0] if ":" in sig else sig

    hypothesis = BehaviorHypothesis(
        id=hyp_id,
        claim=claim,
        confidence=0.5,
        evidence_for=["Declared via behavior_precheck"],
        created_at=now,
        last_tested=now,
        last_decay=now,
        source="precheck_declared",
        applies_to_sigs=[f"{binary}:*"] if binary != "unknown" else [],
        applies_to_tools=["Bash"],
    )

    compass.hypotheses.append(hypothesis)
    evict_overflow(compass, cfg)
    return hypothesis
