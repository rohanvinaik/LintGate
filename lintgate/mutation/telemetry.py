"""Quantitative acceptance metrics and theory-to-system contracts.

Provides a stable, deterministic comparison of filtered mutation execution
against a baseline run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintgate.mutation.policy import MutationTelemetry


@dataclass(frozen=True)
class TelemetryTargets:
    """Acceptance thresholds for filtered-vs-baseline mutation runs."""

    min_runtime_reduction_ratio: float = 0.50
    max_score_degradation_abs: float = 3.0
    min_filtered_score: float = 80.0


def _coerce_float(value: Any, default: float = 0.0) -> float:
    """Best-effort float conversion for telemetry payloads."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_filtered_runtime_s(
    filtered_telemetry: MutationTelemetry,
    filtered_stats: dict[str, Any],
) -> float:
    """Resolve filtered runtime from explicit stats, falling back to telemetry clock."""
    stats_time = _coerce_float(filtered_stats.get("total_time_s"), default=-1.0)
    if stats_time >= 0.0:
        return stats_time

    measured = filtered_telemetry.end_time - filtered_telemetry.start_time
    return measured if measured > 0.0 else 0.0


def evaluate_telemetry_against_targets(
    baseline_stats: dict[str, Any],
    filtered_telemetry: MutationTelemetry,
    filtered_stats: dict[str, Any],
    targets: TelemetryTargets | None = None,
) -> dict[str, Any]:
    """Validate a filtered run against baseline using explicit quantitative targets."""
    active_targets = targets or TelemetryTargets()

    baseline_time = _coerce_float(baseline_stats.get("total_time_s"))
    filtered_time = _resolve_filtered_runtime_s(filtered_telemetry, filtered_stats)
    if baseline_time > 0.0 and filtered_time > 0.0:
        runtime_reduction_ratio = (baseline_time - filtered_time) / baseline_time
    else:
        runtime_reduction_ratio = 0.0

    baseline_score = _coerce_float(baseline_stats.get("score"))
    filtered_score = _coerce_float(filtered_stats.get("score"))
    score_degradation_abs = max(0.0, baseline_score - filtered_score)

    runtime_reduction_met = runtime_reduction_ratio >= active_targets.min_runtime_reduction_ratio
    score_degradation_met = score_degradation_abs <= active_targets.max_score_degradation_abs
    score_floor_met = filtered_score >= active_targets.min_filtered_score
    passed_all = runtime_reduction_met and score_degradation_met and score_floor_met

    return {
        "passed_all": passed_all,
        "runtime_reduction_met": runtime_reduction_met,
        "score_degradation_met": score_degradation_met,
        "score_floor_met": score_floor_met,
        "metrics": {
            "runtime_reduction_ratio": round(runtime_reduction_ratio, 3),
            "score_degradation_abs": round(score_degradation_abs, 3),
            "filtered_score": round(filtered_score, 3),
            "baseline_score": round(baseline_score, 3),
        },
    }
