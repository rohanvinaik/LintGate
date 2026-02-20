"""Behavior channel — supervision for agent behavioral drift.

Detects anti-patterns in tool-use sequences that indicate the agent's
problem-solving strategy is diverging from effective reasoning:

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

v2 additions:
- Intent bias layer: 6-category intent taxonomy (inspect, modify, verify,
  execute, meta, unknown) biases signal confidence via deltas (not hard gating)
- Signal coordinator: per-signal cooldown, precheck nudge dedup, escalation
- Evidence traces: every finding carries intent_counts, bias terms, score_delta

This channel is ADVISORY and read-only for session state:
- Receives compass via event.raw_input["behavior_compass"]
- Returns compass delta in ChannelResult.metrics["behavior_compass_delta"]
- Hook applies delta after mesh completes (avoids race with parallel channels)
"""

from __future__ import annotations

import time
from typing import Any

from lintgate.controlplane.behavior_compass import (
    DEFAULT_THRESHOLDS,
    BehaviorCompass,
)
from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    SupervisionEvent,
)
from lintgate.types import LintIssue

from .behavior_detection import (
    detect_approach_cycling,
    detect_brute_force_escalation,
    detect_consecutive_failures,
    detect_failure_amnesia,
    detect_premature_action,
    detect_serial_discovery,
    detect_stale_model,
    detect_tool_repetition,
    detect_verification_debt,
)

# Re-export from extracted modules for backward compatibility.
# Tests and external callers may import these names from behavior_channel.
from .behavior_scoring import (  # noqa: F401
    _THEORY_CODA_MAX_CHARS,  # noqa: F401
    SIGNAL_THEORY_MAP,
    _ground_finding_in_theory,
)
from .behavior_scoring import (
    IntentBiasScorer as _IntentBiasScorer,
)
from .behavior_scoring import (
    SignalCoordinator as _SignalCoordinator,
)

# ── Config Loading ─────────────────────────────────────────────────────


def _load_execute_config(
    event: SupervisionEvent,
    config: ControlPlaneConfig,
) -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any] | None, dict[str, Any] | None, dict[str, str]
]:
    """Load thresholds, bias weights, global priors, theory profile, and recent codas.

    Returns:
        (thresholds, bias_weights, global_priors, theory_profile, recent_codas)
    """
    channel_config = config.channels.get("behavior", None)
    thresholds = dict(DEFAULT_THRESHOLDS)
    settings = getattr(channel_config, "settings", {}) if channel_config else {}
    if isinstance(settings, dict):
        nested = settings.get("thresholds", {})
        if isinstance(nested, dict):
            for key, value in nested.items():
                if key in DEFAULT_THRESHOLDS:
                    thresholds[key] = value
        for key, value in settings.items():
            if key in DEFAULT_THRESHOLDS:
                thresholds[key] = value

    if "behavior_thresholds" in event.raw_input:
        thresholds.update(event.raw_input["behavior_thresholds"])

    bias_weights = settings.get("bias_weights", {}) if isinstance(settings, dict) else {}
    global_priors = event.raw_input.get("behavior_global_priors")

    theory_profile = (
        event.raw_input.get("theory_profile") if config.inquiry.theory_grounded_signals else None
    )
    compass_data = event.raw_input.get("behavior_compass", {})
    recent_codas = compass_data.get("_theory_recent_codas", {})

    return thresholds, bias_weights, global_priors, theory_profile, recent_codas


def _apply_prediction_modulation(
    findings: list[LintIssue],
    compass: BehaviorCompass,
    config: ControlPlaneConfig,
) -> None:
    """Modulate finding confidence based on prediction accuracy.

    High accuracy (>70%) softens informational signals.
    Low accuracy (<30%) amplifies all signals.
    Only activates with 5+ checked predictions.
    """
    if not config.inquiry.prediction_tracking:
        return

    from lintgate.controlplane.behavior_compass import compute_prediction_accuracy

    pred_accuracy = compute_prediction_accuracy(compass)
    if pred_accuracy is None:
        return

    for finding in findings:
        if pred_accuracy > 0.70 and finding.severity == "informational":
            if finding.confidence is not None:
                finding.confidence = round(max(0.0, finding.confidence - 0.15), 2)
        elif pred_accuracy < 0.30 and finding.confidence is not None:
            finding.confidence = round(min(1.0, finding.confidence + 0.15), 2)


def _compute_nudge_outcomes(
    compass: BehaviorCompass,
    nudge_signals: list[str],
) -> dict[str, str]:
    """Compute nudge outcomes and update compass pending state.

    Returns nudge_outcomes dict for global profile delta.
    """
    nudge_outcomes: dict[str, str] = {}
    if compass.pending_nudge_signals:
        precheck_delta = (
            compass.constraint_check_count_session - compass.pending_nudge_constraint_check_count
        )
        outcome = "accepted" if precheck_delta > 0 else "ignored"
        for sig in compass.pending_nudge_signals:
            nudge_outcomes[sig] = outcome
        compass.nudge_outcomes.update(nudge_outcomes)

    compass.pending_nudge_signals = list(nudge_signals)
    compass.pending_nudge_constraint_check_count = compass.constraint_check_count_session
    return nudge_outcomes


def _build_channel_result(
    findings: list[LintIssue],
    next_actions: list[dict[str, Any]],
    compass: BehaviorCompass,
    coord: _SignalCoordinator,
    scorer: _IntentBiasScorer,
    nudge_outcomes: dict[str, str],
    intent_delta: dict[str, int],
    elapsed_ms: float,
) -> ChannelResult:
    """Build the final ChannelResult for the behavior channel."""
    status = "fail" if findings else "pass"

    has_hard = any(f.severity == "warning" for f in findings)
    if has_hard:
        severity = "warning"
    elif findings:
        severity = "informational"
    else:
        severity = "none"

    return ChannelResult(
        channel="behavior",
        status=status,
        severity=severity,
        findings=findings,
        repairs=[],
        metrics={
            "alert_count": len(findings),
            "hard_alerts": sum(1 for f in findings if f.severity == "warning"),
            "soft_alerts": sum(1 for f in findings if f.severity == "informational"),
            "next_actions": next_actions,
            "compass_summary": {
                "hypotheses_active": sum(
                    1 for h in compass.hypotheses if h.status in ("active", "confirmed")
                ),
                "approaches_total": len(compass.approaches),
                "prediction_recall": compass.coverage.prediction_recall,
            },
            "behavior_compass_delta": {
                "last_fired": compass.last_fired,
                "signal_fire_counts": compass.signal_fire_counts,
                "early_nudge_emitted": compass.early_nudge_emitted,
                "pending_nudge_signals": compass.pending_nudge_signals,
                "pending_nudge_constraint_check_count": compass.pending_nudge_constraint_check_count,
                "nudge_outcomes": compass.nudge_outcomes,
                "_theory_recent_codas": coord._new_codas,
            },
            "intent_summary": scorer.build_evidence_trace(),
            "global_profile_delta": {
                "signal_fire_counts": dict(coord.run_fire_counts),
                "intent_summary": intent_delta,
                "nudge_outcomes": nudge_outcomes,
            },
        },
        duration_ms=elapsed_ms,
    )


# ── BehaviorChannel ────────────────────────────────────────────────────


class BehaviorChannel:
    """Supervision channel for agent behavioral drift detection.

    Advisory only — behavioral findings are weather-style observations,
    not judgments. Hard signals trigger constraint_check nudges.
    """

    name = "behavior"
    timeout_ms = 500
    blocking_capable = False

    def should_run(self, event: SupervisionEvent, config: ControlPlaneConfig) -> bool:
        """Run when behavior compass data is available in the event."""
        if event.surface == "mcp":
            return True
        return "behavior_compass" in event.raw_input

    def execute(self, event: SupervisionEvent, config: ControlPlaneConfig) -> ChannelResult:
        """Execute behavioral drift detection against compass state."""
        start = time.perf_counter()

        compass = BehaviorCompass.from_dict(event.raw_input.get("behavior_compass", {}))

        thresholds, bias_weights, global_priors, theory_profile, recent_codas = (
            _load_execute_config(event, config)
        )

        scorer = _IntentBiasScorer(compass, bias_weights, global_priors=global_priors)
        coord = _SignalCoordinator(
            compass,
            thresholds,
            theory_profile=theory_profile,
            recent_codas=recent_codas,
        )

        # Run all 9 detection rules
        detect_approach_cycling(compass, thresholds, coord, scorer)
        detect_failure_amnesia(compass, thresholds, coord, scorer)
        detect_brute_force_escalation(compass, thresholds, coord, scorer)
        detect_premature_action(compass, thresholds, coord, scorer)
        detect_serial_discovery(compass, thresholds, coord, scorer)
        detect_tool_repetition(compass, thresholds, coord, scorer)
        detect_consecutive_failures(compass, thresholds, coord, scorer)
        detect_verification_debt(compass, thresholds, coord, scorer)
        detect_stale_model(compass, thresholds, coord, scorer)

        _apply_prediction_modulation(coord.findings, compass, config)

        findings, next_actions, nudge_signals = coord.finalize()
        nudge_outcomes = _compute_nudge_outcomes(compass, nudge_signals)

        # Global profile intent delta: per-run, not rolling-window cumulative
        intent_delta: dict[str, int] = {}
        if event.surface == "hook" and compass.action_history:
            latest_intent = compass.action_history[-1].get("intent")
            if isinstance(latest_intent, str) and latest_intent:
                intent_delta[latest_intent] = 1

        elapsed_ms = (time.perf_counter() - start) * 1000

        return _build_channel_result(
            findings,
            next_actions,
            compass,
            coord,
            scorer,
            nudge_outcomes,
            intent_delta,
            elapsed_ms,
        )
