"""Behavioral drift detection rules — 9 signal detectors.

Hard signals (severity="warning", participate in coherence):
1. approach_cycling: Repeatedly trying failed approaches without updating model
2. failure_amnesia: Repeating the same error without incorporating prior lessons
3. brute_force_escalation: More approaches tried than constraints understood

Soft signals (severity="informational", coherence-neutral):
4. premature_action: Acting faster than understanding (high bash:read ratio)
5. serial_discovery: All constraints discovered reactively, none predicted
6. tool_repetition: Same command signature repeated excessively
7. verification_debt: Long execute/modify streak with no verify/inspect
8. stale_model: Approach changes without hypothesis model updates

Trigger-only (produces nudge but not finding):
9. consecutive_failures: 3+ consecutive Bash failures

Extracted from behavior_channel.py for module size compliance.
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


def detect_premature_action(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: SignalCoordinator,
    scorer: IntentBiasScorer,
) -> None:
    """Detect high action-to-research ratio with high failure rate. Soft signal."""
    ratio_threshold = thresholds.get("premature_action_ratio", 3.0)
    failure_threshold = thresholds.get("premature_action_failure_rate", 0.5)

    bash_count = compass.coverage.bash_count_recent
    read_count = compass.coverage.read_count_recent
    if bash_count == 0:
        return

    ratio = bash_count / max(read_count, 1)
    recent = compass.action_history[-10:]
    bash_events = [e for e in recent if e.get("tool") == "Bash"]
    if not bash_events:
        return

    failures = sum(1 for e in bash_events if (e.get("exit") or 0) != 0)
    failure_rate = failures / len(bash_events)

    if ratio > ratio_threshold and failure_rate > failure_threshold:
        nudge = None
        if ratio > 5.0:
            nudge = {
                "tool": "constraint_check",
                "reason": f"extreme premature_action: {ratio:.1f}:1 bash:read ratio",
            }
        coord.add_finding(
            "premature_action",
            LintIssue(
                linter="behavior_channel",
                kind="premature_action",
                message=(
                    f"{bash_count} of last {len(recent)} actions were Bash commands "
                    f"({failures} failed, {failure_rate:.0%} failure rate). "
                    "Research-to-action ratio suggests acting ahead of understanding."
                ),
                severity="informational",
                evidence=scorer.build_evidence_trace(),
            ),
            is_hard=False,
            precheck_nudge=nudge,
        )


def detect_serial_discovery(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: SignalCoordinator,
    scorer: IntentBiasScorer,
) -> None:
    """Detect constraints discovered reactively — two-stage. Soft signal."""
    active_hyps = [h for h in compass.hypotheses if h.status in ("active", "confirmed")]
    failure_sourced = sum(1 for h in active_hyps if h.source == "command_failure")
    precheck_sourced = sum(1 for h in active_hyps if h.source == "precheck_declared")

    # Stage 1: Early nudge (one-time)
    if (
        failure_sourced >= 1
        and compass.constraint_check_count_session == 0
        and not compass.early_nudge_emitted
    ):
        bias, bias_terms = scorer.serial_discovery_bias()
        evidence = scorer.build_evidence_trace()
        evidence["matched_bias_terms"] = bias_terms
        evidence["score_delta"] = bias
        evidence["stage"] = 1

        decomp = SignalSourceDecomposition(
            signal_name="serial_discovery",
            pattern_score=min(1.0, failure_sourced / 2.0),
            theory_score=0.3,  # Indirect indicator of missing theory
        )

        coord.add_finding(
            "serial_discovery_early",
            LintIssue(
                linter="behavior_channel",
                kind="serial_discovery",
                message=(
                    f"{failure_sourced} constraint(s) discovered through failure, "
                    "0 predicted via constraint_check. Consider proactive constraint enumeration "
                    "with constraint_check."
                ),
                severity="informational",
                confidence=round(min(1.0, max(0.0, bias)), 2),
                evidence=evidence,
            ),
            is_hard=False,
            precheck_nudge={
                "tool": "constraint_check",
                "reason": "serial_discovery_early — first failure-sourced constraint, no constraint_check used",
            },
            decomposition=decomp,
        )
        compass.early_nudge_emitted = True

    # Stage 2: 3+ failure-sourced, 0 precheck
    if failure_sourced >= 3 and precheck_sourced == 0:
        evidence = scorer.build_evidence_trace()
        evidence["stage"] = 2
        decomp = SignalSourceDecomposition(
            signal_name="serial_discovery",
            pattern_score=min(1.0, failure_sourced / 5.0),
            outcome_score=0.8,
        )
        coord.add_finding(
            "serial_discovery",
            LintIssue(
                linter="behavior_channel",
                kind="serial_discovery",
                message=(
                    f"{failure_sourced} constraints discovered through failure, "
                    "0 predicted via constraint_check. All learning is reactive — "
                    "consider proactive constraint enumeration."
                ),
                severity="informational",
                evidence=evidence,
            ),
            is_hard=False,
            decomposition=decomp,
        )


def detect_tool_repetition(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: SignalCoordinator,
    scorer: IntentBiasScorer,
) -> None:
    """Detect same command signature repeated excessively. Soft signal."""
    count_threshold = thresholds.get("tool_repetition_count", 4)
    window_min = thresholds.get("tool_repetition_window_min", 30)

    if not compass.action_history:
        return

    now = compass.action_history[-1]["ts"]
    cutoff = now - (window_min * 60)

    sig_counts: dict[str, int] = {}
    for event in compass.action_history:
        if event.get("ts", 0) < cutoff:
            continue
        sig = event.get("sig", "")
        if sig:
            sig_counts[sig] = sig_counts.get(sig, 0) + 1

    for sig, count in sig_counts.items():
        if count >= count_threshold:
            coord.add_finding(
                "tool_repetition",
                LintIssue(
                    linter="behavior_channel",
                    kind="tool_repetition",
                    message=(
                        f"Command '{sig}' executed {count} times in "
                        f"{window_min}min window. Repeated tool use without "
                        "progress may indicate stuck approach."
                    ),
                    severity="informational",
                    evidence=scorer.build_evidence_trace(),
                ),
                is_hard=False,
            )
            break


def detect_consecutive_failures(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: SignalCoordinator,
    scorer: IntentBiasScorer,
) -> None:
    """Detect consecutive Bash failures as precheck trigger."""
    threshold = thresholds.get("consecutive_bash_failures", 3)
    if not compass.action_history:
        return

    consecutive = 0
    for event in reversed(compass.action_history):
        if event.get("tool") != "Bash":
            continue
        if (event.get("exit") or 0) != 0:
            consecutive += 1
        else:
            break

    if consecutive >= threshold:
        coord.register_nudge_only(
            "consecutive_failures",
            {
                "tool": "constraint_check",
                "reason": f"{consecutive} consecutive Bash failures — pause and check constraints",
            },
        )


def detect_verification_debt(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: SignalCoordinator,
    scorer: IntentBiasScorer,
) -> None:
    """Detect long execute/modify streak without verification. Soft signal."""
    bias, terms = scorer.verification_debt_bias()

    streak = 0
    for intent in reversed(compass.intent_history):
        if intent in ("execute", "modify"):
            streak += 1
        else:
            break

    threshold = thresholds.get("verification_debt_streak", 8)
    if streak < threshold:
        return

    score = min(1.0, max(0.0, bias))
    evidence = scorer.build_evidence_trace()
    evidence["matched_bias_terms"] = terms
    evidence["score_delta"] = bias
    evidence["execute_streak"] = streak

    coord.add_finding(
        "verification_debt",
        LintIssue(
            linter="behavior_channel",
            kind="verification_debt",
            message=(
                f"{streak} execute/modify actions without verification checkpoint. "
                "Consider verifying downstream acceptance."
            ),
            severity="informational",
            confidence=round(score, 2),
            evidence=evidence,
        ),
        is_hard=False,
        precheck_nudge={
            "tool": "constraint_check",
            "reason": f"verification_debt: {streak} actions without verification",
        },
    )


def detect_stale_model(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: SignalCoordinator,
    scorer: IntentBiasScorer,
) -> None:
    """Detect approach changes without hypothesis model updates. Soft signal."""
    bias, terms = scorer.stale_model_bias()
    if not terms:
        return

    sorted_approaches = sorted(compass.approaches, key=lambda a: a.started_at)
    max_streak = 1
    current = 1
    last_v = sorted_approaches[0].hyp_version_at_start
    for a in sorted_approaches[1:]:
        if a.hyp_version_at_start == last_v:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 1
            last_v = a.hyp_version_at_start

    evidence = scorer.build_evidence_trace()
    evidence["matched_bias_terms"] = terms
    evidence["score_delta"] = bias
    evidence["approach_streak_at_same_version"] = max_streak

    coord.add_finding(
        "stale_model",
        LintIssue(
            linter="behavior_channel",
            kind="stale_model",
            message=(
                f"{max_streak} approach changes without constraint model updates. "
                "Hypothesis set unchanged. Consider using constraint_check."
            ),
            severity="informational",
            confidence=round(min(1.0, max(0.0, bias)), 2),
            evidence=evidence,
        ),
        is_hard=False,
    )
