"""Soft behavioral detectors — verification debt signals.

Detectors:
- detect_serial_discovery: All constraints discovered reactively, none predicted
- detect_verification_debt: Long execute/modify streak with no verify/inspect
- detect_integration_verification_debt: Channel code edited without integration testing
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lintgate.orchestration.attribution import SignalSourceDecomposition
from lintgate.types import LintIssue

if TYPE_CHECKING:
    from lintgate.controlplane.behavior_types import BehaviorCompass

    from .behavior_scoring import (
        IntentBiasScorer,
        SignalCoordinator,
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
