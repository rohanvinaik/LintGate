"""Trajectory analysis — empirical phase detection and teaching-set bounds.

Extends greedy convergence with:
- Teaching-set upper bound (minimum non-redundant tests in greedy order)
- Tail onset detection (where marginal kill gain drops below threshold)
- Phase classification (bulk/transition/tail/complete)
- Trajectory summary for operator decision-making

All quantities are explicitly upper bounds unless stated otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .greedy_convergence import ConvergenceResult


@dataclass
class TrajectoryResult:
    """Trajectory analysis for a function's test suite."""

    function_key: str = ""
    sigma_upper_bound: int = 0
    teaching_set_upper_bound: int = 0
    phase: str = "unknown"  # bulk | transition | tail | complete
    tail_onset_step: int | None = None
    redundant_test_count: int = 0
    high_value_next_tests: list[str] = field(default_factory=list)
    trajectory_summary: str = ""

    def to_dict(self) -> dict:
        d = {
            "function_key": self.function_key,
            "sigma_upper_bound": self.sigma_upper_bound,
            "teaching_set_upper_bound": self.teaching_set_upper_bound,
            "phase": self.phase,
            "redundant_test_count": self.redundant_test_count,
            "trajectory_summary": self.trajectory_summary,
        }
        if self.tail_onset_step is not None:
            d["tail_onset_step"] = self.tail_onset_step
        if self.high_value_next_tests:
            d["high_value_next_tests"] = self.high_value_next_tests
        return d


def analyze_trajectory(
    convergence: ConvergenceResult,
    total_mutants: int,
    survival_rate: float,
) -> TrajectoryResult:
    """Analyze the trajectory of a test suite's mutation kill progress.

    Args:
        convergence: Result from greedy convergence analysis.
        total_mutants: Total mutants generated (empirical sigma upper bound).
        survival_rate: Current survival rate from profiling.
    """
    sigma_ub = total_mutants

    # Teaching-set upper bound: count non-redundant steps in greedy order
    non_redundant = [s for s in convergence.steps if s.new_kills > 0]
    teaching_set_ub = len(non_redundant)

    # Tail onset: first step where delta_spec drops below 1/sigma threshold
    tail_onset = None
    threshold = 1.0 / sigma_ub if sigma_ub > 0 else 0.0
    for i, step in enumerate(non_redundant):
        if step.delta_spec < threshold and i > 0:
            tail_onset = i
            break

    # Phase classification
    phase = _classify_phase(
        survival_rate=survival_rate,
        convergence_efficiency=convergence.convergence_efficiency,
        is_fully_specified=convergence.is_fully_specified,
        tail_onset=tail_onset,
        total_steps=len(non_redundant),
    )

    # High-value next tests: first few non-redundant tests by kill count
    high_value = [s.test_name for s in non_redundant[:3]]

    # Trajectory summary
    summary = _build_summary(
        phase=phase,
        teaching_set_ub=teaching_set_ub,
        redundant_count=len(convergence.redundant_tests),
        survival_rate=survival_rate,
        sigma_ub=sigma_ub,
        tail_onset=tail_onset,
    )

    return TrajectoryResult(
        function_key=convergence.function_key,
        sigma_upper_bound=sigma_ub,
        teaching_set_upper_bound=teaching_set_ub,
        phase=phase,
        tail_onset_step=tail_onset,
        redundant_test_count=len(convergence.redundant_tests),
        high_value_next_tests=high_value,
        trajectory_summary=summary,
    )


def _classify_phase(
    survival_rate: float,
    convergence_efficiency: float,
    is_fully_specified: bool,
    tail_onset: int | None,
    total_steps: int,
) -> str:
    """Classify the current specification phase."""
    if is_fully_specified or survival_rate < 0.01:
        return "complete"
    if tail_onset is not None and tail_onset <= total_steps // 2:
        return "tail"
    if survival_rate < 0.3:
        return "transition"
    return "bulk"


def _build_summary(
    phase: str,
    teaching_set_ub: int,
    redundant_count: int,
    survival_rate: float,
    sigma_ub: int,
    tail_onset: int | None,
) -> str:
    """Build a human-readable trajectory summary."""
    parts = [f"Phase: {phase}."]

    if phase == "complete":
        parts.append("All mutants killed — specification is complete for generated mutant set.")
    elif phase == "tail":
        parts.append(
            f"Diminishing returns: tail onset at step {tail_onset}. "
            "Consider decomposition before writing more tests."
        )
    elif phase == "transition":
        parts.append(
            f"Survival rate {survival_rate:.0%} — approaching well-specified. "
            f"Teaching set upper bound: {teaching_set_ub} tests."
        )
    else:
        parts.append(
            f"Survival rate {survival_rate:.0%} — significant specification gaps remain. "
            f"Sigma upper bound: {sigma_ub} mutants."
        )

    if redundant_count > 0:
        parts.append(f"{redundant_count} redundant test(s) detected.")

    return " ".join(parts)
