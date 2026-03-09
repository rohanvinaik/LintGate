"""Behavioral model calibration probe v2.

Replaces the v1 multiple-choice quiz with micro-task behavioral probes
that observe what a model DOES rather than what it SAYS it would do.

Each probe task presents a small coding scenario. The model's response
is scored by extracting structured behavioral features (tool calls,
action order, verification cadence, constraint references) — not by
matching prose or quiz answers. This measures revealed policy, not
stated policy.

Probe design principles:
- Action traces first, text second. tool_calls + order + retries +
  verification cadence is harder to game than prose.
- Deterministic scoring via extracted features, not regex-only.
- Task variants rotate on the same behavioral target (anti-gaming).
- Weak prior that decays fast as telemetry arrives (EMA cap).
- Structured response schema: optional tool-event trace fields
  supplement free text to avoid measuring stated policy.
- Fallback: incomplete/failed probes set neutral prior, never
  high-confidence risky prior.

Signal space (9 signals, same as v1):
- approach_cycling, failure_amnesia, serial_discovery,
  premature_action, verification_debt, stale_model,
  tool_repetition, brute_force_escalation, consecutive_failures
"""

from __future__ import annotations

import hashlib
import random
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .profiles import ModelProfile

# Re-export from extracted modules for backward compatibility.
from .probe_features import (  # noqa: F401
    _check_misleading_error_follow,
    _check_root_cause_identification,
    _first_indicator_pos,
    extract_features_for_task,
)
from .probe_tasks import (  # noqa: F401
    _SIGNAL_ANTI_PATTERN_MAP,
    _SIGNAL_DISPOSITION_MAP,
    PROBE_TASKS,
    BehavioralFeature,
    ProbeTask,
    TaskVariant,
)

# Keep the private-name alias for backward compat with tests
_extract_features_for_task = extract_features_for_task

# ── Version ──────────────────────────────────────────────────────────

PROBE_VERSION = 2
SUPPORTED_PROBE_SETS = {"quick"}

PROBE_MAX_CONFIDENCE = 0.60

V1_PROBE_VERSION = 1


# ── Scoring ─────────────────────────────────────────────────────────


def score_probe_responses(
    responses: dict[str, dict[str, Any]],
) -> tuple[dict[str, float], float]:
    """Score probe task responses into a signal_risk vector.

    Returns:
        (signal_risk, confidence)
        signal_risk: {signal_name: risk_level} clamped to [0.0, 1.0]
        confidence: 0.0-1.0 based on completeness and trace quality
    """
    signal_risk: dict[str, float] = {}
    scored_tasks = 0
    trace_quality_sum = 0.0
    task_index = {t.id: t for t in PROBE_TASKS}

    for task_id, response in responses.items():
        task = task_index.get(task_id)
        if task is None:
            continue
        if not isinstance(response, dict):
            continue
        if not response.get("text") and not response.get("actions"):
            continue

        scored_tasks += 1
        trace_quality = _compute_trace_quality(response)
        trace_quality_sum += trace_quality

        features = extract_features_for_task(task, response)

        for bf in task.features:
            present = features.get(bf.name, False)
            delta = bf.delta_if_present if present else bf.delta_if_absent
            weighted_delta = delta * bf.weight
            signal_risk[bf.signal] = signal_risk.get(bf.signal, 0.0) + weighted_delta

    for signal in signal_risk:
        signal_risk[signal] = max(0.0, min(1.0, signal_risk[signal]))

    total_tasks = len(PROBE_TASKS)
    if total_tasks == 0 or scored_tasks == 0:
        return signal_risk, 0.0

    completeness = scored_tasks / total_tasks
    avg_trace_quality = trace_quality_sum / scored_tasks

    base_confidence = 0.20 + (completeness * 0.25)
    quality_bonus = avg_trace_quality * 0.15
    confidence = min(PROBE_MAX_CONFIDENCE, base_confidence + quality_bonus)

    return signal_risk, round(confidence, 3)


def _compute_trace_quality(response: dict[str, Any]) -> float:
    """Score 0.0-1.0 for how much structured trace data is present."""
    score = 0.0
    if response.get("text"):
        score += 0.3
    if response.get("tool_calls"):
        score += 0.25
    if response.get("actions"):
        score += 0.20
    if response.get("retry_count") is not None:
        score += 0.10
    if response.get("verify_points"):
        score += 0.10
    if response.get("constraint_refs"):
        score += 0.05
    return min(1.0, score)


# ── Neutral Prior ───────────────────────────────────────────────────

NEUTRAL_PRIOR: dict[str, float] = {
    "approach_cycling": 0.25,
    "failure_amnesia": 0.25,
    "serial_discovery": 0.25,
    "premature_action": 0.25,
    "verification_debt": 0.25,
    "stale_model": 0.25,
    "tool_repetition": 0.20,
    "brute_force_escalation": 0.20,
    "consecutive_failures": 0.15,
}

NEUTRAL_PRIOR_CONFIDENCE = 0.30


def get_neutral_prior() -> tuple[dict[str, float], float]:
    """Return a neutral prior for failed/incomplete probes."""
    return dict(NEUTRAL_PRIOR), NEUTRAL_PRIOR_CONFIDENCE


# ── Public API ──────────────────────────────────────────────────────


def get_probe_tasks(probe_set: str = "quick", seed: int | None = None) -> list[dict[str, Any]]:
    """Return probe tasks formatted for MCP response.

    Selects one variant per task (rotated via seed for anti-gaming).
    """
    normalized = probe_set.strip().lower() if isinstance(probe_set, str) else ""
    if normalized not in SUPPORTED_PROBE_SETS:
        msg = f"Unsupported probe_set: {probe_set!r}. Supported: {sorted(SUPPORTED_PROBE_SETS)}"
        raise ValueError(msg)

    if seed is None:
        import time as _time

        day_str = str(int(_time.time() / 86400))
        seed = int(hashlib.sha256(day_str.encode()).hexdigest()[:8], 16)

    rng = random.Random(seed)

    tasks_out = []
    for task in PROBE_TASKS:
        variant = rng.choice(task.variants)
        tasks_out.append(
            {
                "id": task.id,
                "context": variant.context,
                "instruction": variant.instruction,
                "setup_files": variant.setup_files,
                "response_schema": {
                    "text": "str (required): Describe your approach",
                    "tool_calls": "list[str] (optional): Ordered tool names you would use",
                    "actions": "list[str] (optional): Ordered action descriptions",
                    "retry_count": "int (optional): How many times you'd retry same command",
                    "verify_points": "list[int] (optional): After which step numbers you'd verify",
                    "constraint_refs": "list[str] (optional): Errors/constraints you reference",
                },
            }
        )

    return tasks_out


def get_probe_questions(probe_set: str = "quick") -> list[dict[str, Any]]:
    """DEPRECATED: Use get_probe_tasks() instead."""
    return get_probe_tasks(probe_set)


# ── Derived Outputs ─────────────────────────────────────────────────


def _derive_custom_anti_patterns(
    signal_risk: dict[str, float],
    max_items: int = 5,
) -> list[str]:
    """Select anti-patterns prioritized by highest-risk signals."""
    ranked = sorted(signal_risk.items(), key=lambda x: -x[1])
    patterns: list[str] = []
    for signal, risk in ranked:
        if risk < 0.1:
            continue
        if signal in _SIGNAL_ANTI_PATTERN_MAP:
            patterns.append(_SIGNAL_ANTI_PATTERN_MAP[signal])
        if len(patterns) >= max_items:
            break
    return patterns


def _derive_custom_dispositions(
    signal_risk: dict[str, float],
    threshold: float = 0.3,
    max_items: int = 4,
) -> list[str]:
    """Generate guardrail dispositions for high-risk signals."""
    ranked = sorted(signal_risk.items(), key=lambda x: -x[1])
    dispositions: list[str] = []
    for signal, risk in ranked:
        if risk < threshold:
            continue
        if signal in _SIGNAL_DISPOSITION_MAP:
            dispositions.append(_SIGNAL_DISPOSITION_MAP[signal])
        if len(dispositions) >= max_items:
            break
    return dispositions


def build_profile_from_probe(
    model_key: str,
    responses: dict[str, Any],
    *,
    fallback_to_neutral: bool = True,
) -> ModelProfile:
    """Create a ModelProfile from probe task responses."""
    from .profiles import ModelProfile, resolve_model_key

    canonical = resolve_model_key(model_key)
    if canonical is None:
        msg = f"Cannot resolve model key: {model_key!r}"
        raise ValueError(msg)

    signal_risk, confidence = score_probe_responses(responses)

    if confidence < 0.20 and fallback_to_neutral:
        signal_risk, confidence = get_neutral_prior()

    custom_anti_patterns = _derive_custom_anti_patterns(signal_risk)
    custom_dispositions = _derive_custom_dispositions(signal_risk)

    return ModelProfile(
        model_key=canonical,
        probe_version=PROBE_VERSION,
        probe_runs=1,
        signal_risk=signal_risk,
        confidence=confidence,
        custom_anti_patterns=custom_anti_patterns,
        custom_dispositions=custom_dispositions,
    )


# ── Probe Validity KPI ──────────────────────────────────────────────


def compute_probe_validity(
    probe_signal_risk: dict[str, float],
    observed_signal_fires: dict[str, int],
    event_count: int,
) -> dict[str, Any]:
    """Compute correlation between probe priors and observed signals."""
    if event_count < 20:
        return {
            "per_signal": {},
            "mean_absolute_delta": None,
            "correlation_quality": "insufficient_data",
        }

    per_signal: dict[str, dict[str, float]] = {}
    deltas: list[float] = []

    for signal, probe_risk in probe_signal_risk.items():
        fires = observed_signal_fires.get(signal, 0)
        observed_rate = min(1.0, fires / max(event_count, 1) * 10)
        delta = abs(probe_risk - observed_rate)
        per_signal[signal] = {
            "probe": round(probe_risk, 3),
            "observed": round(observed_rate, 3),
            "delta": round(delta, 3),
        }
        deltas.append(delta)

    mean_delta = sum(deltas) / len(deltas) if deltas else 0.0

    if mean_delta < 0.15:
        quality = "good"
    elif mean_delta < 0.30:
        quality = "moderate"
    else:
        quality = "poor"

    return {
        "per_signal": per_signal,
        "mean_absolute_delta": round(mean_delta, 3),
        "correlation_quality": quality,
    }
