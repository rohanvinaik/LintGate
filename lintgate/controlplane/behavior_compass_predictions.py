"""Prediction tracking for the behavioral compass.

Handles prediction checking against actual outcomes, hypothesis
strengthening/weakening based on prediction results, and accuracy
computation.

Extracted from behavior_compass.py for module size compliance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lintgate.orchestration.attribution import SignalSourceDecomposition

if TYPE_CHECKING:
    from .behavior_types import (
        BehaviorCompass,
        Prediction,
        PredictionExpectation,
    )

_PREDICTION_EXPIRY_EVENTS = 20  # Expire predictions after this many events without check


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
            hyp.evidence_against.append(f"prediction falsified at event {compass.event_counter}")
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
        matched = _evaluate_prediction_match(pred.expected, exit_code, error_sig, output_str)
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
