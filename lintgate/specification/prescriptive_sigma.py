"""Prescriptive sigma estimator — specification complexity from IR before code exists.

Parallel to predictor.py but operates on PrescriptiveSpec rather than AST.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .prescriptive_spec import PrescriptiveSpec


def estimate_prescriptive_sigma(spec: PrescriptiveSpec) -> int:
    """Estimate specification complexity from the IR before code exists.

    σ = |state_vars| × max(|transitions|, 1) + |invariants| + |forbidden|
        + interface_complexity + |algebraic_laws|
    """
    state_factor = len(spec.state_variables) * max(len(spec.allowed_transitions), 1)
    invariant_count = len(spec.invariants)
    forbidden_count = len(spec.forbidden_behaviors)

    # Interface complexity: parameters + return type presence
    interface_complexity = len(spec.parameters)
    if spec.return_type:
        interface_complexity += 1

    algebraic_count = len(spec.algebraic_laws)

    sigma = state_factor + invariant_count + forbidden_count + interface_complexity + algebraic_count

    # Minimum sigma of 1 for any non-empty spec
    return max(sigma, 1) if (invariant_count or forbidden_count or spec.parameters) else 0


def compute_convergence_signal(prescriptive_sigma: int, retrospective_sigma: int) -> dict[str, Any]:
    """Compare pre-code and post-code sigma.

    Returns: {ratio, assessment, delta}
    """
    if prescriptive_sigma == 0:
        return {
            "ratio": 0.0,
            "assessment": "no_prescriptive_spec",
            "delta": retrospective_sigma,
        }

    ratio = retrospective_sigma / prescriptive_sigma if prescriptive_sigma else 0.0

    if 0.5 <= ratio <= 2.0:
        assessment = "converged"
    elif ratio < 0.5:
        assessment = "over_specified"
    else:
        assessment = "under_specified"

    return {
        "ratio": round(ratio, 3),
        "assessment": assessment,
        "delta": retrospective_sigma - prescriptive_sigma,
    }
