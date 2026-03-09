"""Hard behavioral signal detectors — high-confidence drift indicators.

Hard signals (severity="warning", participate in coherence):
1. approach_cycling: Repeatedly trying failed approaches without updating model
2. failure_amnesia: Repeating the same error without incorporating prior lessons
3. brute_force_escalation: More approaches tried than constraints understood

Extracted from behavior_detection.py for module size compliance.
"""

from __future__ import annotations

import time
from typing import Any

from lintgate.controlplane.behavior_compass import BehaviorCompass, error_memory_key
from lintgate.orchestration.attribution import SignalSourceDecomposition
from lintgate.types import LintIssue

from .behavior_scoring import (
    IntentBiasScorer,
    SignalCoordinator,
    _error_like_match,
    _extract_hypothesis_error_candidates,
)


def detect_approach_cycling(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: SignalCoordinator,
    scorer: IntentBiasScorer,
) -> None:
    """Detect repeated failed approaches within a time window. Hard signal."""
    count_threshold = thresholds.get("approach_cycling_count", 3)
    window_min = thresholds.get("approach_cycling_window_min", 30)

    if not compass.approaches:
        return

    now = compass.action_history[-1]["ts"] if compass.action_history else time.time()
    cutoff = now - (window_min * 60)

    recent_failed = [
        a for a in compass.approaches if a.outcome == "failed" and a.last_event >= cutoff
    ]

    if len(recent_failed) >= count_threshold:
        window_actual = int((now - min(a.started_at for a in recent_failed)) / 60)
        sigs = ", ".join(a.approach_sig for a in recent_failed[:4])

        decomp = SignalSourceDecomposition(
            signal_name="approach_cycling",
            pattern_score=min(1.0, len(recent_failed) / count_threshold),
            outcome_score=1.0,  # All recent failures contribute to outcome evidence
        )

        coord.add_finding(
            "approach_cycling",
            LintIssue(
                linter="behavior_channel",
                kind="approach_cycling",
                message=(
                    f"{len(recent_failed)} approaches attempted in {window_actual}min, "
                    f"all failed. Constraint space may be wider than current model. "
                    f"Approaches: {sigs}"
                ),
                severity="warning",
                evidence=scorer.build_evidence_trace(),
            ),
            is_hard=True,
            precheck_nudge={
                "tool": "constraint_check",
                "reason": "approach_cycling detected — enumerate constraints before next attempt",
            },
            decomposition=decomp,
        )


def detect_failure_amnesia(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: SignalCoordinator,
    scorer: IntentBiasScorer,
) -> None:
    """Detect repeated error signatures — multi-source. Hard signal."""
    lookback = thresholds.get("failure_amnesia_lookback", 30)
    recent = compass.action_history[-lookback:]

    bias, bias_terms = scorer.failure_amnesia_bias()
    evidence = scorer.build_evidence_trace()
    evidence["matched_bias_terms"] = bias_terms
    evidence["score_delta"] = bias

    # Source 1: action_history repeated error_sig
    if _detect_amnesia_from_action_history(recent, evidence, coord):
        return

    latest_err = recent[-1].get("err", "") if recent else ""
    if not latest_err:
        return

    # Source 2: persistent error-memory aggregate
    if _detect_amnesia_from_error_memory(compass, latest_err, evidence, coord):
        return

    # Source 3: hypothesis evidence matching latest error
    _detect_amnesia_from_hypotheses(compass, latest_err, evidence, coord)


def _detect_amnesia_from_action_history(
    recent: list[dict[str, Any]],
    evidence: dict[str, Any],
    coord: SignalCoordinator,
) -> bool:
    """Check for repeated error_sig in action history. Returns True if finding added."""
    error_counts: dict[str, list[dict[str, Any]]] = {}
    for event in recent:
        err = event.get("err", "")
        if err:
            error_counts.setdefault(err, []).append(event)

    for err_sig, events in error_counts.items():
        if len(events) >= 2:
            first_ts = events[0].get("ts", 0)
            last_ts = events[-1].get("ts", 0)
            gap_min = int((last_ts - first_ts) / 60) if last_ts > first_ts else 0

            decomp = SignalSourceDecomposition(
                signal_name="failure_amnesia",
                pattern_score=min(1.0, len(events) / 2.0),
                outcome_score=1.0,
            )
            coord.add_finding(
                "failure_amnesia",
                LintIssue(
                    linter="behavior_channel",
                    kind="failure_amnesia",
                    message=(
                        f"Error signature '{err_sig[:80]}' seen {len(events)} times "
                        f"in action history (first {gap_min}min ago). "
                        "Known constraint may not be incorporated into approach."
                    ),
                    severity="warning",
                    evidence=evidence,
                ),
                is_hard=True,
                precheck_nudge={
                    "tool": "constraint_check",
                    "reason": f"failure_amnesia: '{err_sig[:60]}' repeated — check constraint ledger",
                },
                decomposition=decomp,
            )
            return True
    return False


def _detect_amnesia_from_error_memory(
    compass: BehaviorCompass,
    latest_err: str,
    evidence: dict[str, Any],
    coord: SignalCoordinator,
) -> bool:
    """Check persistent error-memory for repeated errors. Returns True if finding added."""
    key = error_memory_key(latest_err)
    if not key:
        return False

    mem = compass.error_memory.get(key)
    if not mem or int(mem.get("count", 0)) < 2:
        return False

    first_ts = float(mem.get("first_seen", 0.0))
    last_ts = float(mem.get("last_seen", 0.0))
    gap_min = int((last_ts - first_ts) / 60) if last_ts > first_ts else 0
    seen = int(mem.get("count", 0))
    decomp = SignalSourceDecomposition(
        signal_name="failure_amnesia",
        pattern_score=min(1.0, seen / 3.0),
        outcome_score=1.0,
    )
    coord.add_finding(
        "failure_amnesia",
        LintIssue(
            linter="behavior_channel",
            kind="failure_amnesia",
            message=(
                f"Error signature '{latest_err[:80]}' seen {seen} times "
                f"across session memory (first {gap_min}min ago). "
                "Known constraint may not be incorporated into approach."
            ),
            severity="warning",
            evidence=evidence,
        ),
        is_hard=True,
        precheck_nudge={
            "tool": "constraint_check",
            "reason": f"failure_amnesia: '{latest_err[:60]}' repeated across session",
        },
        decomposition=decomp,
    )
    return True


def _detect_amnesia_from_hypotheses(
    compass: BehaviorCompass,
    latest_err: str,
    evidence: dict[str, Any],
    coord: SignalCoordinator,
) -> None:
    """Check hypothesis evidence matching latest error."""
    for hyp in compass.hypotheses:
        if hyp.status == "expired":
            continue
        for candidate_err in _extract_hypothesis_error_candidates(hyp.evidence_for):
            if _error_like_match(candidate_err, latest_err):
                decomp = SignalSourceDecomposition(
                    signal_name="failure_amnesia",
                    theory_score=hyp.confidence,
                    outcome_score=1.0,
                )
                coord.add_finding(
                    "failure_amnesia",
                    LintIssue(
                        linter="behavior_channel",
                        kind="failure_amnesia",
                        message=(
                            f"Latest error matches existing hypothesis "
                            f"'{hyp.claim[:60]}' (confidence {hyp.confidence:.2f}). "
                            "Known constraint may not be incorporated."
                        ),
                        severity="warning",
                        evidence=evidence,
                    ),
                    is_hard=True,
                    precheck_nudge={
                        "tool": "constraint_check",
                        "reason": f"failure_amnesia: error matches hypothesis '{hyp.id}'",
                    },
                    decomposition=decomp,
                )
                return


def detect_brute_force_escalation(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: SignalCoordinator,
    scorer: IntentBiasScorer,
) -> None:
    """Detect more approaches than constraints understood. Hard signal."""
    gap_threshold = thresholds.get("brute_force_approach_gap", 0)
    approaches = compass.coverage.approaches_attempted
    constraints = compass.coverage.constraints_verified
    gap = approaches - constraints

    if approaches > 0 and gap > gap_threshold:
        decomp = SignalSourceDecomposition(
            signal_name="brute_force_escalation",
            pattern_score=min(1.0, gap / 2.0),
            outcome_score=min(1.0, approaches / 10.0),
        )
        coord.add_finding(
            "brute_force_escalation",
            LintIssue(
                linter="behavior_channel",
                kind="brute_force_escalation",
                message=(
                    f"{approaches} approaches tried, only {constraints} constraints verified. "
                    f"Approach-to-constraint gap: {gap}. "
                    "Strategy may be brute-forcing rather than understanding."
                ),
                severity="warning",
                evidence=scorer.build_evidence_trace(),
            ),
            is_hard=True,
            precheck_nudge={
                "tool": "constraint_check",
                "reason": "brute_force_escalation — approaches outpacing constraint understanding",
            },
            decomposition=decomp,
        )
