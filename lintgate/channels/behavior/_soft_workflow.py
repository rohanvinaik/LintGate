"""Soft behavioral detectors — workflow anti-pattern signals.

Detectors:
- detect_stale_model: Approach changes without hypothesis model updates
- detect_mass_delegation: 3+ Agent/Task spawns in short window during refactoring
- detect_redundant_planning: EnterPlanMode after controlplane_run with findings
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lintgate.orchestration.attribution import SignalSourceDecomposition
from lintgate.types import LintIssue

if TYPE_CHECKING:
    from lintgate.controlplane.behavior_types import BehaviorCompass

    from .scoring import (
        IntentBiasScorer,
        SignalCoordinator,
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
