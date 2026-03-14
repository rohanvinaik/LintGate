"""Specification health as transparent vector + scalar.

5-axis health vector with hard veto gates. Scalar from geometric mean
so one near-zero axis cannot hide behind strong neighbors. Replaces
opaque single scores with a decomposable diagnostic.

Axes:
    spec_level        — assertion_count / sigma coverage
    kill_rate         — mutation kill rate (1 - survival_rate)
    convergence       — convergence efficiency from greedy analysis
    composition       — 1 / (1 + gamma), interface composition health
    test_efficiency   — test effectiveness score

Veto gates (any fires → scalar = 0.0):
    discovery_artifact     — auto target has artifact discovery state
    mock_boundary          — mock-boundary tests dominate coverage
    budget_instability     — mutation runs exhaust budget too often
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


class HealthAxis(str, Enum):
    SPEC_LEVEL = "spec_level"
    KILL_RATE = "kill_rate"
    CONVERGENCE = "convergence"
    COMPOSITION = "composition"
    TEST_EFFICIENCY = "test_efficiency"


class VetoGate(str, Enum):
    DISCOVERY_ARTIFACT = "discovery_artifact"
    MOCK_BOUNDARY = "mock_boundary"
    BUDGET_INSTABILITY = "budget_instability"


@dataclass
class SpecificationHealth:
    """Transparent health vector with hard veto gates."""

    axes: dict[str, float]
    vetoes: dict[str, bool]
    scalar: float
    vetoed: bool
    reconciliation_active: bool = False
    axes_measured: dict[str, bool] = field(
        default_factory=lambda: {a.value: True for a in HealthAxis}
    )


def compute_health(
    spec_level: float = 0.0,
    kill_rate: float = 0.0,
    convergence: float = 0.0,
    composition_gamma: float = 0.0,
    test_efficiency: float = 0.0,
    *,
    has_discovery_artifact: bool = False,
    mock_boundary_share: float = 0.0,
    budget_exhausted_share: float = 0.0,
    mock_boundary_threshold: float = 0.5,
    budget_exhausted_threshold: float = 0.3,
    reconciled_spec_level: float | None = None,
    convergence_measured: bool | None = None,
) -> SpecificationHealth:
    """Compute specification health from raw metrics.

    Args:
        spec_level: Mean assertion_count/sigma (0.0–1.0).
        kill_rate: Mean mutation kill rate (0.0–1.0).
        convergence: Mean convergence rate from greedy analysis (0.0–1.0).
        composition_gamma: Mean composition gap (0.0–inf).
            Transformed via 1/(1+gamma): gamma=0→1.0, gamma=1→0.5.
        test_efficiency: Mean test effectiveness score (0.0–1.0).
        has_discovery_artifact: Any auto target has artifact discovery state.
        mock_boundary_share: Fraction of functions with mock-boundary dominance.
        budget_exhausted_share: Fraction of mutation runs that exhausted budget.
        reconciled_spec_level: When not None, use this for the SPEC_LEVEL axis
            instead of the raw spec_level. The raw value is preserved under
            'static_spec_level' in the axes dict.
    """
    reconciliation_active = reconciled_spec_level is not None
    effective_spec = reconciled_spec_level if reconciliation_active else spec_level
    if effective_spec is None:
        effective_spec = 0.0
    composition = 1.0 / (1.0 + composition_gamma)

    axes = {
        HealthAxis.SPEC_LEVEL.value: _clamp(effective_spec),
        HealthAxis.KILL_RATE.value: _clamp(kill_rate),
        HealthAxis.CONVERGENCE.value: _clamp(convergence),
        HealthAxis.COMPOSITION.value: _clamp(composition),
        HealthAxis.TEST_EFFICIENCY.value: _clamp(test_efficiency),
    }

    vetoes = {
        VetoGate.DISCOVERY_ARTIFACT.value: has_discovery_artifact,
        VetoGate.MOCK_BOUNDARY.value: mock_boundary_share > mock_boundary_threshold,
        VetoGate.BUDGET_INSTABILITY.value: (budget_exhausted_share > budget_exhausted_threshold),
    }

    if reconciliation_active:
        axes["static_spec_level"] = _clamp(spec_level)

    vetoed = any(vetoes.values())
    # Geometric mean uses the 5 canonical axes only (exclude static_spec_level)
    canonical = [axes[a.value] for a in HealthAxis]

    measured = [True] * 5
    convergence_is_measured = (
        convergence_measured if convergence_measured is not None else convergence > 0.0
    )
    if not convergence_is_measured:
        measured[2] = False  # CONVERGENCE index

    scalar = _geometric_mean(canonical, measured) if not vetoed else 0.0
    axes_measured = {a.value: m for a, m in zip(HealthAxis, measured)}

    return SpecificationHealth(
        axes=axes,
        vetoes=vetoes,
        scalar=round(scalar, 4),
        vetoed=vetoed,
        reconciliation_active=reconciliation_active,
        axes_measured=axes_measured,
    )


def _geometric_mean(values: list[float], measured: list[bool] | None = None) -> float:
    """Geometric mean of non-negative values, skipping unmeasured axes."""
    if measured is None:
        measured = [True] * len(values)
    active = [v for v, m in zip(values, measured) if m]
    if not active or any(v <= 0.0 for v in active):
        return 0.0
    return math.exp(sum(math.log(v) for v in active) / len(active))


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))
