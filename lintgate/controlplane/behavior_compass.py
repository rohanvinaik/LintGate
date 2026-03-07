"""Behavioral compass — live hypothesis model of agent behavior.

Tracks constraints discovered through tool-use patterns, maintains
confidence-scored hypotheses, and computes coverage metrics for the
constraint_check tool (formerly behavior_precheck).

Design principles (from Grail hidden compass):
- Hypotheses are live experiments, not static facts
- Confidence rises/falls based on outcome evidence
- Wrong hypotheses are productive (they improve uncertainty targeting)
- Co-construction: agent declares its model, tool computes gaps

No LLM calls, no subprocess calls, no file I/O in this module.
All persistence flows through session_memory.
"""

from __future__ import annotations

import re
import time
from typing import Any

from lintgate.orchestration.attribution import SignalSourceDecomposition

# ── Re-exports for backward compatibility ────────────────────────────────
# All types, constants, and command normalization functions are re-exported
# so existing imports from this module continue to work.
from .behavior_types import (  # noqa: F401
    DEFAULT_HYPOTHESIS_CONFIG,
    DEFAULT_THRESHOLDS,
    MAX_ACTION_HISTORY,
    MAX_APPROACHES,
    MAX_ERROR_MEMORY,
    MAX_EVIDENCE_ITEMS,
    ApproachAttempt,
    BehaviorCompass,
    BehaviorHypothesis,
    CoverageMetrics,
    NudgeState,
    Prediction,
    PredictionExpectation,
    PredictionStateContainer,
    SignalState,
    make_hypothesis_id,
    new_compass,
)
from .command_normalization import (  # noqa: F401
    DEFAULT_INTENT_MAP,
    DEFAULT_INTENT_SIG_MAP,
    INTENT_CATEGORIES,
    error_memory_key,
    extract_error_sig,
    normalize_command_sig,
    resolve_intent,
)

# Private alias for internal use
_make_hypothesis_id = make_hypothesis_id


# ── Prediction checking ──────────────────────────────────────────────────

_PREDICTION_EXPIRY_EVENTS = (
    20  # Expire predictions after this many events without check
)


def _evaluate_prediction_match(
    exp: PredictionExpectation,
    exit_code: int | None,
    error_sig: str,
    output_str: str,
) -> bool:
    """Evaluate whether a prediction expectation matches the actual outcome."""
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

    return matched


def _apply_prediction_to_hypothesis(
    compass: BehaviorCompass,
    pred: Prediction,
    cfg: dict[str, Any],
) -> None:
    """Strengthen or weaken the hypothesis linked to a checked prediction."""
    if not pred.linked_hypothesis_id:
        return

    for hyp in compass.hypotheses:
        if hyp.id != pred.linked_hypothesis_id:
            continue
        if pred.status == "confirmed":
            delta = cfg.get("strengthen_delta", 0.15)
            hyp.confidence = min(hyp.confidence + delta, 1.0)

            # Prediction confirmation boosts coherence and outcome
            decomp = SignalSourceDecomposition(
                signal_name=hyp.id, outcome_score=0.6, coherence_score=0.4
            )
            hyp.trust_score = min(1.0, hyp.trust_score + decomp.total_confidence * 0.1)
            hyp.evidence_for.append(f"prediction confirmed: {decomp.to_summary()}")
        elif pred.status == "falsified":
            delta = cfg.get("weaken_delta", 0.1)
            hyp.confidence = max(hyp.confidence - delta, 0.0)
            hyp.evidence_against.append(
                f"prediction falsified at event {compass.event_counter}"
            )
        compass.hypothesis_version += 1
        break


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

        # Skip predictions that don't match this command
        if (
            not pred.declared_sig
            or pred.declared_sig == "unknown:unknown"
            or not command_sig
            or command_sig == "unknown:unknown"
            or pred.declared_sig != command_sig
        ):
            still_pending.append(pred)
            continue

        # Evaluate and record outcome
        matched = _evaluate_prediction_match(
            pred.expected, exit_code, error_sig, output_str
        )
        pred.status = "confirmed" if matched else "falsified"
        pred.checked_at_event = compass.event_counter
        pred.actual_outcome = f"exit={exit_code}, err={error_sig[:50]}"

        compass.prediction_log.append(
            {
                "prediction_id": pred.prediction_id,
                "status": pred.status,
                "event": compass.event_counter,
                "expected_type": pred.expected.type,
                "expected_value": pred.expected.value,
                "actual_outcome": pred.actual_outcome,
            }
        )

        _apply_prediction_to_hypothesis(compass, pred, cfg)

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


# ── Core update functions ────────────────────────────────────────────────


def _parse_bash_event(
    tool_input: dict[str, Any] | str,
    tool_output: str | dict[str, Any],
) -> tuple[str, int | None, str, str]:
    """Parse command signature, exit code, and error from a Bash event.

    Returns (command_sig, exit_code, error_sig, output_str).
    """
    cmd = ""
    if isinstance(tool_input, dict):
        cmd = tool_input.get("command", "")
    elif isinstance(tool_input, str):
        cmd = tool_input
    command_sig = normalize_command_sig(cmd)

    output_str = tool_output if isinstance(tool_output, str) else str(tool_output)
    exit_match = re.search(
        r"(?:exit[_ ]code|exit[_ ]status|exitstatus)[: =]+(\d+)",
        output_str,
        re.IGNORECASE,
    )

    if exit_match:
        exit_code: int | None = int(exit_match.group(1))
    elif "error" in output_str.lower() or "failed" in output_str.lower():
        exit_code = 1
    else:
        exit_code = 0

    error_sig = extract_error_sig(output_str) if exit_code != 0 else ""

    return command_sig, exit_code, error_sig, output_str


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
        command_sig, exit_code, error_sig, output_str = _parse_bash_event(
            tool_input,
            tool_output,
        )
        if exit_code != 0:
            _update_error_memory(compass, error_sig, now)
    elif tool_name in ("Agent", "Task") and isinstance(tool_input, dict):
        # Capture description for delegation detection
        command_sig = str(tool_input.get("description", ""))[:80]

    # Resolve and track intent
    intent = resolve_intent(tool_name, command_sig)

    # Append to action history (rolling window)
    compass.action_history.append(
        {
            "tool": tool_name,
            "ts": now,
            "sig": command_sig,
            "exit": exit_code,
            "err": error_sig,
            "intent": intent,
        }
    )
    compass.intent_history.append(intent)
    if len(compass.intent_history) > MAX_ACTION_HISTORY:
        compass.intent_history = compass.intent_history[-MAX_ACTION_HISTORY:]
    if len(compass.action_history) > MAX_ACTION_HISTORY:
        compass.action_history = compass.action_history[-MAX_ACTION_HISTORY:]

    # Update approaches (Bash commands only)
    if tool_name == "Bash" and command_sig and exit_code is not None:
        _update_approach(compass, command_sig, exit_code, error_sig, now)

    # Test existing hypotheses (before auto-generating, to avoid double-strengthening)
    if tool_name == "Bash" and command_sig:
        _test_hypotheses(compass, command_sig, exit_code, error_sig, now, cfg)

    # Check pending predictions against actual outcomes (Bash events only)
    _check_predictions(
        compass, tool_name, command_sig, exit_code, error_sig, output_str, cfg
    )

    # Auto-generate hypothesis from Bash failure
    if tool_name == "Bash" and exit_code is not None and exit_code != 0 and error_sig:
        _auto_generate_hypothesis(compass, command_sig, error_sig, now, cfg)

    # Integration verification tracking (Phase 3)
    _update_integration_counters(compass, tool_name, tool_input, command_sig, now)

    # Decay stale hypotheses
    decay_stale(compass, now, cfg)

    # Recompute coverage
    compass.coverage = compute_coverage(compass, cfg)
    compass.uncertainty_zones = compute_uncertainty_zones(compass)

    return []  # Alerts computed by channel, not here


def _update_integration_counters(
    compass: BehaviorCompass,
    tool_name: str,
    tool_input: dict[str, Any] | str,
    command_sig: str,
    now: float,
) -> None:
    """Update integration verification tracking counters.

    Increments on Edit/Write to integration paths.
    Resets on verification tools or integration test bash commands.
    """
    from lintgate.channels.behavior_detection import (
        INTEGRATION_PATHS,
        INTEGRATION_VERIFY_BASH_PATTERNS,
        INTEGRATION_VERIFY_TOOLS,
    )

    # Check for verification events (reset counter)
    if tool_name in INTEGRATION_VERIFY_TOOLS:
        compass.integration_edits_since_verify = 0
        compass.last_integration_verify_ts = now
        return

    if tool_name == "Bash" and command_sig:
        for pattern in INTEGRATION_VERIFY_BASH_PATTERNS:
            if re.search(pattern, command_sig):
                compass.integration_edits_since_verify = 0
                compass.last_integration_verify_ts = now
                return

    # Check for edits to integration paths (increment counter)
    if tool_name in ("Edit", "Write", "NotebookEdit"):
        file_path = ""
        if isinstance(tool_input, dict):
            file_path = tool_input.get("file_path", "")
        elif isinstance(tool_input, str):
            file_path = tool_input
        # Normalize to forward slashes for matching
        file_path = file_path.replace("\\", "/")
        for integration_path in INTEGRATION_PATHS:
            if integration_path in file_path:
                compass.integration_edits_since_verify += 1
                return


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
        if exit_code == 0:
            existing.outcome = "success"
        elif existing.outcome != "success":
            existing.outcome = "failed"
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
        if len(compass.approaches) > MAX_APPROACHES:
            compass.approaches = compass.approaches[-MAX_APPROACHES:]


def _auto_generate_hypothesis(
    compass: BehaviorCompass,
    command_sig: str,
    error_sig: str,
    now: float,
    cfg: dict[str, Any],
) -> None:
    """Auto-generate a low-confidence hypothesis from a Bash failure."""
    # Check for existing hypothesis with same error sig
    for hyp in compass.hypotheses:
        if hyp.status not in ("active", "confirmed"):
            continue
        for ev in hyp.evidence_for:
            if error_sig in ev:
                update_hypothesis(
                    compass,
                    hyp.id,
                    "strengthen",
                    f"Re-observed: {error_sig}",
                    now=now,
                    cfg=cfg,
                )
                return

    hyp_id = _make_hypothesis_id(error_sig, command_sig)
    if any(h.id == hyp_id for h in compass.hypotheses):
        return

    binary = command_sig.split(":")[0] if ":" in command_sig else command_sig
    hypothesis = BehaviorHypothesis(
        id=hyp_id,
        claim=f"{binary} failed: {error_sig[:100]}",
        confidence=cfg["auto_generate_confidence"],
        evidence_for=["exit!=0 (outcome attribution)"],
        created_at=now,
        last_tested=now,
        last_decay=now,
        source="command_failure",
        applies_to_sigs=[f"{binary}:*"],
        applies_to_tools=["Bash"],
        trust_score=0.3,  # Command failures are purely reactive (outcome-only)
    )
    compass.hypotheses.append(hypothesis)
    compass.hypothesis_version += 1
    evict_overflow(compass, cfg)


def _hypothesis_matches_sig(
    hyp: BehaviorHypothesis, command_sig: str, binary: str
) -> bool:
    """Check if a hypothesis applies to a given command signature."""
    for sig_pattern in hyp.applies_to_sigs:
        if sig_pattern.endswith(":*") and binary == sig_pattern[:-2]:
            return True
        if command_sig == sig_pattern:
            return True
    return False


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
        if not _hypothesis_matches_sig(hyp, command_sig, binary):
            continue

        hyp.last_tested = now

        if exit_code != 0 and error_sig:
            hyp_keywords = set(hyp.claim.lower().split())
            event_keywords = set(error_sig.lower().split())
            if len(hyp_keywords & event_keywords) >= 2:
                # Command failure is outcome attribution
                decomp = SignalSourceDecomposition(
                    signal_name=hyp.id, outcome_score=1.0
                )
                update_hypothesis(
                    compass,
                    hyp.id,
                    "strengthen",
                    f"Confirmed by failure: {decomp.to_summary()}",
                    now=now,
                    cfg=cfg,
                )
                hyp.trust_score = min(1.0, hyp.trust_score + 0.05)
        elif exit_code == 0:
            update_hypothesis(
                compass,
                hyp.id,
                "weaken",
                f"Succeeded: {command_sig}",
                now=now,
                cfg=cfg,
            )


# ── Hypothesis management ────────────────────────────────────────────────


def _strengthen_hypothesis(
    hyp: BehaviorHypothesis,
    evidence: str,
    cfg: dict[str, Any],
) -> None:
    """Apply strengthening evidence to a hypothesis."""
    hyp.confidence = min(1.0, hyp.confidence + cfg["strengthen_delta"])
    hyp.evidence_for.append(evidence)
    if len(hyp.evidence_for) > MAX_EVIDENCE_ITEMS:
        hyp.evidence_for = hyp.evidence_for[-MAX_EVIDENCE_ITEMS:]
    if (
        hyp.confidence >= cfg["promote_threshold"]
        and len(hyp.evidence_for) >= cfg["min_evidence_for_promote"]
    ):
        hyp.status = "confirmed"


def _weaken_hypothesis(
    hyp: BehaviorHypothesis,
    evidence: str,
    cfg: dict[str, Any],
) -> None:
    """Apply weakening evidence to a hypothesis."""
    hyp.confidence = max(0.0, hyp.confidence - cfg["weaken_delta"])
    hyp.evidence_against.append(evidence)
    if len(hyp.evidence_against) > MAX_EVIDENCE_ITEMS:
        hyp.evidence_against = hyp.evidence_against[-MAX_EVIDENCE_ITEMS:]
    if hyp.confidence <= 0.0:
        hyp.status = "expired"
    elif hyp.confidence < 0.3:
        hyp.status = "weakened"


def update_hypothesis(
    compass: BehaviorCompass,
    hyp_id: str,
    direction: str,
    evidence: str,
    *,
    now: float | None = None,
    cfg: dict[str, Any] | None = None,
) -> None:
    """Strengthen or weaken a hypothesis based on new evidence."""
    if now is None:
        now = time.time()
    if cfg is None:
        cfg = DEFAULT_HYPOTHESIS_CONFIG

    for hyp in compass.hypotheses:
        if hyp.id != hyp_id:
            continue
        hyp.last_tested = now
        hyp.last_decay = now

        if direction == "strengthen":
            _strengthen_hypothesis(hyp, evidence, cfg)
        elif direction == "weaken":
            _weaken_hypothesis(hyp, evidence, cfg)
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
        if hyp.status == "expired":
            continue
        decay_anchor = max(hyp.last_tested, hyp.last_decay)
        if decay_anchor <= 0.0:
            decay_anchor = hyp.last_tested
        hours_stale = (now - decay_anchor) / 3600.0
        if hours_stale <= 0.0:
            continue
        hyp.confidence = max(0.0, hyp.confidence - decay_rate * hours_stale)
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

    compass.hypotheses = [h for h in compass.hypotheses if h.status != "expired"]
    if len(compass.hypotheses) <= max_active:
        return

    compass.hypotheses.sort(key=lambda h: (h.confidence, h.created_at))
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
    predicted = sum(
        1
        for h in active_hyps
        if h.source == "precheck_declared" and h.confidence >= promote_threshold
    )

    total_approaches = len(compass.approaches)
    successful = sum(1 for a in compass.approaches if a.outcome == "success")
    success_rate = successful / total_approaches if total_approaches > 0 else 0.0

    recent = compass.action_history[-10:]
    bash_count = sum(1 for e in recent if e.get("tool") == "Bash")
    read_count = sum(1 for e in recent if e.get("tool") in ("Read", "Grep", "Glob"))

    surprise = sum(
        1 for h in active_hyps if h.source == "command_failure" and h.confidence >= 0.5
    )
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


def _find_uncovered_approaches(compass: BehaviorCompass) -> list[str]:
    """Find failed approaches with no explaining hypothesis."""
    zones: list[str] = []
    for approach in compass.approaches:
        if approach.outcome != "failed":
            continue
        binary = approach.approach_sig.split(":")[0]
        covered = any(
            _hypothesis_matches_sig(hyp, approach.approach_sig, binary)
            for hyp in compass.hypotheses
            if hyp.status != "expired"
        )
        if not covered and approach.error_sigs:
            last_err = approach.error_sigs[-1]
            zones.append(
                f"Failed approach '{approach.approach_sig}' has no constraint hypothesis "
                f"(last error: {last_err[:60]})"
            )
    return zones


def _find_low_confidence_hypotheses(compass: BehaviorCompass) -> list[str]:
    """Find hypotheses with confidence below 0.4."""
    return [
        f"Low-confidence hypothesis: {hyp.claim[:80]} (confidence: {hyp.confidence:.2f})"
        for hyp in compass.hypotheses
        if hyp.status != "expired" and 0.0 < hyp.confidence < 0.4
    ]


def _find_conflicting_hypotheses(compass: BehaviorCompass) -> list[str]:
    """Find hypotheses with both supporting and contradicting evidence."""
    return [
        f"Conflicting evidence for: {hyp.claim[:80]} "
        f"({len(hyp.evidence_for)} for, {len(hyp.evidence_against)} against)"
        for hyp in compass.hypotheses
        if hyp.status != "expired" and hyp.evidence_for and hyp.evidence_against
    ]


def compute_uncertainty_zones(compass: BehaviorCompass) -> list[str]:
    """Identify areas with lowest confidence or missing coverage."""
    zones: list[str] = []
    zones.extend(_find_uncovered_approaches(compass))
    zones.extend(_find_low_confidence_hypotheses(compass))
    zones.extend(_find_conflicting_hypotheses(compass))
    return zones[:5]  # Cap at 5 for token efficiency


# ── Precheck support ─────────────────────────────────────────────────────


def find_relevant_hypotheses(
    compass: BehaviorCompass,
    command_sig: str | None = None,
    tool: str | None = None,
) -> list[BehaviorHypothesis]:
    """Filter hypotheses by applicability for precheck recall computation."""
    results = []
    binary = ""
    if command_sig and ":" in command_sig:
        binary = command_sig.split(":")[0]

    for hyp in compass.hypotheses:
        if hyp.status == "expired":
            continue

        if not command_sig and not tool:
            results.append(hyp)
            continue

        matched = False
        if command_sig:
            matched = _hypothesis_matches_sig(hyp, command_sig, binary)
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

    for existing in compass.hypotheses:
        if existing.id == hyp_id:
            update_hypothesis(
                compass,
                hyp_id,
                "strengthen",
                "Re-declared via precheck",
                now=now,
                cfg=cfg,
            )
            return existing

    binary = sig.split(":")[0] if ":" in sig else sig
    # Agent declaration boosts theory and pattern
    decomp = SignalSourceDecomposition(
        signal_name=claim, theory_score=0.8, pattern_score=0.2
    )
    hypothesis = BehaviorHypothesis(
        id=hyp_id,
        claim=claim,
        confidence=0.5,
        evidence_for=[f"Declared via precheck: {decomp.to_summary()}"],
        created_at=now,
        last_tested=now,
        last_decay=now,
        source="precheck_declared",
        applies_to_sigs=[f"{binary}:*"] if binary != "unknown" else [],
        applies_to_tools=["Bash"],
        trust_score=0.7,  # Declarations start with higher trust
    )
    compass.hypotheses.append(hypothesis)
    evict_overflow(compass, cfg)
    return hypothesis
