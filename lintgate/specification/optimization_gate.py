"""Optimization gate — specification-backed hint validation with stop criteria.

Validates that optimization hints (cacheable, parallelizable, etc.) are
backed by sufficient specification evidence. Audit mode only (Phase 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import FunctionSpecification

GATE_THRESHOLDS: dict[str, float] = {
    "cacheable": 0.6,
    "cache-without-invalidation": 0.8,
    "parallelizable": 0.7,
    "map-reduce-compatible": 0.8,
    "foldable": 0.5,
}


@dataclass
class GateResult:
    """Result of an optimization gate check for a function."""

    function_key: str = ""
    passed: bool = False
    stop_criteria_met: bool = False
    spec_level: float = 0.0
    required_threshold: float = 0.0
    delta: float = 0.0
    estimated_tests_remaining: int = 0
    gated_hints: list[str] | None = None
    passed_hints: list[str] | None = None


def check_gate(func_spec: FunctionSpecification) -> GateResult:
    """Check optimization gate for a function's hints.

    Returns GateResult indicating whether all hints pass their
    specification level thresholds and the stop criteria contract.
    """
    hints = func_spec.optimization_hints
    spec_level = func_spec.core.specification_level
    sigma = func_spec.core.estimated_sigma

    if not hints:
        return GateResult(
            function_key=func_spec.function_key,
            passed=True,
            stop_criteria_met=True,
            spec_level=spec_level,
        )

    gated: list[str] = []
    passed_hints: list[str] = []

    for hint in hints:
        threshold = GATE_THRESHOLDS.get(hint, 0.0)
        if spec_level >= threshold:
            passed_hints.append(hint)
        elif threshold > 0:
            gated.append(hint)

    required = max(
        (GATE_THRESHOLDS.get(h, 0.0) for h in hints),
        default=0.0,
    )
    delta = max(required - spec_level, 0.0)
    tests_remaining = _estimate_tests_remaining(sigma, spec_level, required)
    stop_met = len(gated) == 0 and required > 0

    return GateResult(
        function_key=func_spec.function_key,
        passed=len(gated) == 0,
        stop_criteria_met=stop_met,
        spec_level=spec_level,
        required_threshold=required,
        delta=round(delta, 3),
        estimated_tests_remaining=tests_remaining,
        gated_hints=gated if gated else None,
        passed_hints=passed_hints if passed_hints else None,
    )


def _estimate_tests_remaining(sigma: int, current_level: float, target_level: float) -> int:
    """Estimate additional tests needed to reach target spec level."""
    if sigma <= 0 or current_level >= target_level:
        return 0
    current_covered = int(current_level * sigma)
    target_covered = int(target_level * sigma)
    return max(target_covered - current_covered, 0)
