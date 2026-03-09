"""Soft behavioral signal detectors — informational drift indicators.

Soft signals (severity="informational", coherence-neutral):
4. premature_action: Acting faster than understanding (high bash:read ratio)
5. serial_discovery: All constraints discovered reactively, none predicted
6. tool_repetition: Same command signature repeated excessively
7. verification_debt: Long execute/modify streak with no verify/inspect
8. stale_model: Approach changes without hypothesis model updates
9. mass_delegation: 3+ Agent/Task spawns in short window during refactoring
10. redundant_planning: EnterPlanMode after controlplane_run with findings
11. integration_verification_debt: Channel code edited without integration testing

Trigger-only (produces nudge but not finding):
12. consecutive_failures: 3+ consecutive Bash failures

Extracted from behavior_detection.py for module size compliance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lintgate.orchestration.attribution import SignalSourceDecomposition
from lintgate.types import LintIssue

if TYPE_CHECKING:
    from lintgate.controlplane.behavior_compass import BehaviorCompass

    from .behavior_scoring import (
        IntentBiasScorer,
        SignalCoordinator,
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


# ── Agent Anti-Pattern Detectors (#191) ──────────────────────────────

_REFACTORING_KEYWORDS = {
    "fix",
    "refactor",
    "complexity",
    "extract",
    "split",
    "simplify",
    "clean",
}


def detect_mass_delegation(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: SignalCoordinator,
    scorer: IntentBiasScorer,
) -> None:
    """Detect mass sub-agent spawning for refactoring work. Soft signal.

    Fires when 3+ Agent/Task tool calls appear in action_history within
    a short window, especially with refactoring-related descriptions.
    Parallel refactoring agents lack project context and produce
    inconsistent results.
    """
    count_threshold = thresholds.get("mass_delegation_count", 3)
    window_min = thresholds.get("mass_delegation_window_min", 10)

    if not compass.action_history:
        return

    now = compass.action_history[-1]["ts"]
    cutoff = now - (window_min * 60)

    recent_delegations = [
        e
        for e in compass.action_history
        if e.get("ts", 0) >= cutoff and e.get("tool") in ("Agent", "Task")
    ]

    if len(recent_delegations) < count_threshold:
        return

    # Check for refactoring-related keywords in descriptions
    refactor_count = 0
    for event in recent_delegations:
        sig = str(event.get("sig", "")).lower()
        if any(kw in sig for kw in _REFACTORING_KEYWORDS):
            refactor_count += 1

    # Fire if enough delegations exist (with or without refactoring keywords)
    decomp = SignalSourceDecomposition(
        signal_name="mass_delegation",
        pattern_score=min(1.0, len(recent_delegations) / count_threshold),
        outcome_score=0.5 if refactor_count == 0 else 1.0,
    )

    detail = ""
    if refactor_count > 0:
        detail = f" ({refactor_count} with refactoring-related descriptions)"

    coord.add_finding(
        "mass_delegation",
        LintIssue(
            linter="behavior_channel",
            kind="mass_delegation",
            message=(
                f"{len(recent_delegations)} sub-agent spawns in {window_min}min"
                f"{detail}. "
                "Parallel refactoring agents produce inconsistent results. "
                "Consider sequential file-by-file work with cumulative context."
            ),
            severity="informational",
            evidence=scorer.build_evidence_trace(),
        ),
        is_hard=False,
        precheck_nudge={
            "tool": "controlplane_get_details",
            "reason": "mass_delegation — use guided work queue instead of parallel agents",
        },
        decomposition=decomp,
    )


def detect_redundant_planning(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: SignalCoordinator,
    scorer: IntentBiasScorer,
) -> None:
    """Detect EnterPlanMode after controlplane_run has provided findings. Soft signal.

    ControlPlane findings ARE the plan — re-articulating them in markdown
    adds zero information and consumes context window. Fires when
    EnterPlanMode appears after controlplane_run with findings.
    """
    if not compass.action_history:
        return

    # Walk action history to find the pattern:
    # controlplane_run occurred, then EnterPlanMode occurred after it
    cp_run_seen = False
    plan_after_cp = False

    for event in compass.action_history:
        tool = event.get("tool", "")
        if tool == "controlplane_run":
            cp_run_seen = True
        elif tool == "EnterPlanMode" and cp_run_seen:
            plan_after_cp = True

    if not plan_after_cp:
        return

    decomp = SignalSourceDecomposition(
        signal_name="redundant_planning",
        pattern_score=1.0,
        outcome_score=0.7,
    )

    coord.add_finding(
        "redundant_planning",
        LintIssue(
            linter="behavior_channel",
            kind="redundant_planning",
            message=(
                "EnterPlanMode called after controlplane_run. "
                "ControlPlane findings are your plan. Execute sequentially "
                "(controlplane_get_details → fix → lint_files → repeat) "
                "rather than re-planning."
            ),
            severity="informational",
            evidence=scorer.build_evidence_trace(),
        ),
        is_hard=False,
        precheck_nudge={
            "tool": "controlplane_get_details",
            "reason": "redundant_planning — drill into existing findings instead of re-planning",
        },
        decomposition=decomp,
    )


# ── Integration Verification Debt (#Phase3) ──────────────────────────

INTEGRATION_PATHS = {
    "lintgate/channels/",
    "lintgate/controlplane/",
    "lintgate/convergence/",
    "mcp_tools/",
    "lintgate/specification/",
}

INTEGRATION_VERIFY_TOOLS = {
    "controlplane_run",
    "controlplane_get_details",
    "controlplane_status",
}

INTEGRATION_VERIFY_BASH_PATTERNS = [
    r"pytest.*test_integration",
    r"pytest.*test_metric_schemas",
    r"pytest.*test_.*channel",
    r"pytest.*-k.*integration",
]


def detect_integration_verification_debt(
    compass: BehaviorCompass,
    thresholds: dict[str, Any],
    coord: SignalCoordinator,
    scorer: IntentBiasScorer,
) -> None:
    """Detect channel/integration code edited without integration testing.

    Fires when:
    - 5+ edits to INTEGRATION_PATHS without verification, OR
    - git commit attempted with any unverified integration edits
    """

    edit_threshold = thresholds.get("integration_verification_debt_edits", 5)
    edits = compass.integration_edits_since_verify

    # Check for commit with unverified edits (any count > 0)
    commit_with_unverified = False
    if edits > 0 and compass.action_history:
        last = compass.action_history[-1]
        sig = last.get("sig", "")
        if last.get("tool") == "Bash" and "commit" in sig:
            commit_with_unverified = True

    if edits < edit_threshold and not commit_with_unverified:
        return

    trigger = "commit with unverified edits" if commit_with_unverified else f"{edits} edits"

    decomp = SignalSourceDecomposition(
        signal_name="integration_verification_debt",
        pattern_score=min(1.0, edits / edit_threshold),
        outcome_score=0.8 if commit_with_unverified else 0.5,
    )

    coord.add_finding(
        "integration_verification_debt",
        LintIssue(
            linter="behavior_channel",
            kind="integration_verification_debt",
            message=(
                f"Channel/integration code modified ({trigger}) without "
                "integration verification. Unit tests verify modules; "
                "integration tests verify composition (sheaf condition). "
                "Run controlplane_run or pytest test_integration* to verify wiring."
            ),
            severity="warning",
            evidence=scorer.build_evidence_trace(),
        ),
        is_hard=True,
        precheck_nudge={
            "tool": "controlplane_run",
            "reason": "integration_verification_debt — verify channel wiring after edits",
        },
        decomposition=decomp,
    )
