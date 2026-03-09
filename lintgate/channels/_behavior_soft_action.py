"""Soft behavioral detectors — action/execution pattern signals.

Detectors:
- detect_premature_action: High bash:read ratio with high failure rate
- detect_tool_repetition: Same command signature repeated excessively
- detect_consecutive_failures: 3+ consecutive Bash failures (trigger-only)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
