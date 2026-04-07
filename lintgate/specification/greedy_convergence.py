"""Greedy convergence verification — validate Theorem 3.2.

Each test should add ≥1/σ specification coverage. This module analyzes
test suite convergence against the greedy bound, detects redundant tests,
and computes convergence efficiency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from Wesker.engine import ProfilingResult

_FLOAT_ZERO_EPS = 1e-12


@dataclass
class ConvergenceStep:
    """One step in the greedy convergence analysis."""

    test_name: str
    new_kills: int
    delta_spec: float
    cumulative_spec: float
    meets_bound: bool


@dataclass
class ConvergenceResult:
    """Full convergence analysis for a function."""

    function_key: str = ""
    sigma: int = 0
    steps: list[ConvergenceStep] = field(default_factory=list)
    redundant_tests: list[str] = field(default_factory=list)
    convergence_efficiency: float = 0.0
    greedy_bound_violations: int = 0
    is_fully_specified: bool = False
    is_error_state: bool = False
    error_reason: str = ""

    def to_dict(self) -> dict:
        result = {
            "function_key": self.function_key,
            "sigma": self.sigma,
            "total_steps": len(self.steps),
            "redundant_tests": self.redundant_tests,
            "convergence_efficiency": round(self.convergence_efficiency, 3),
            "greedy_bound_violations": self.greedy_bound_violations,
            "is_fully_specified": self.is_fully_specified,
            "is_error_state": self.is_error_state,
            "error_reason": self.error_reason,
            "steps": [
                {
                    "test_name": s.test_name,
                    "new_kills": s.new_kills,
                    "delta_spec": round(s.delta_spec, 4),
                    "cumulative_spec": round(s.cumulative_spec, 4),
                    "meets_bound": s.meets_bound,
                }
                for s in self.steps
            ],
        }
        return result


def analyze_convergence(
    profiling_result: ProfilingResult,
    sigma: int,
    test_ordering: list[str] | None = None,
) -> ConvergenceResult:
    """Analyze test suite convergence against the greedy bound (Thm 3.2).

    If test_ordering is None, tests are ordered by kill count (greedy-optimal).
    This gives the best-case convergence. Passing the actual test execution
    order reveals how far the real suite deviates from optimal.
    """
    if sigma == 0 and profiling_result.total_mutants == 0:
        # Truly trivial: no complexity and no mutants → trivially specified
        return ConvergenceResult(
            function_key=profiling_result.function_key,
            sigma=sigma,
            is_fully_specified=True,
            convergence_efficiency=0.0,
        )

    if sigma <= 0 and profiling_result.total_mutants > 0:
        # Error state: sigma is zero or negative but mutants exist.
        # This indicates an incorrectly computed sigma — do NOT claim fully specified.
        return ConvergenceResult(
            function_key=profiling_result.function_key,
            sigma=sigma,
            is_fully_specified=False,
            convergence_efficiency=0.0,
            is_error_state=True,
            error_reason=(
                f"sigma={sigma} but total_mutants={profiling_result.total_mutants}; "
                "sigma must be positive when mutants exist"
            ),
        )

    if profiling_result.total_mutants == 0:
        # No mutants generated (sigma > 0 but nothing to test) → trivially specified
        return ConvergenceResult(
            function_key=profiling_result.function_key,
            sigma=sigma,
            is_fully_specified=True,
            convergence_efficiency=0.0,
        )

    # Build test → set of mutants killed mapping
    test_kills = _build_test_kill_map(profiling_result)

    if not test_kills:
        return ConvergenceResult(
            function_key=profiling_result.function_key,
            sigma=sigma,
            is_fully_specified=profiling_result.survival_rate <= _FLOAT_ZERO_EPS,
        )

    # Determine test order
    if test_ordering is not None:
        ordered_tests = [t for t in test_ordering if t in test_kills]
        # Add any tests not in the ordering at the end
        for t in test_kills:
            if t not in ordered_tests:
                ordered_tests.append(t)
    else:
        # Greedy-optimal: sort by kill count descending
        ordered_tests = sorted(test_kills, key=lambda t: len(test_kills[t]), reverse=True)

    # Walk through tests, tracking which mutants are still surviving
    total_mutants = profiling_result.total_mutants
    surviving = set(_all_mutant_ids(profiling_result))
    bound = 1.0 / sigma if sigma > 0 else 0.0
    cumulative = 0.0
    steps: list[ConvergenceStep] = []
    redundant: list[str] = []
    violations = 0
    steps_to_full = 0

    for test_name in ordered_tests:
        kills = test_kills[test_name]
        new_kills = kills & surviving
        surviving -= new_kills

        new_kill_count = len(new_kills)
        delta = new_kill_count / total_mutants if total_mutants > 0 else 0.0
        cumulative += delta
        meets = delta >= bound or new_kill_count == 0

        if new_kill_count == 0:
            redundant.append(test_name)
        else:
            steps_to_full += 1
            if not meets:
                violations += 1

        steps.append(
            ConvergenceStep(
                test_name=test_name,
                new_kills=new_kill_count,
                delta_spec=delta,
                cumulative_spec=cumulative,
                meets_bound=meets,
            )
        )

    fully_specified = cumulative >= 1.0 - 1e-9
    efficiency = steps_to_full / sigma if sigma > 0 and steps_to_full > 0 else 0.0

    return ConvergenceResult(
        function_key=profiling_result.function_key,
        sigma=sigma,
        steps=steps,
        redundant_tests=redundant,
        convergence_efficiency=efficiency,
        greedy_bound_violations=violations,
        is_fully_specified=fully_specified,
    )


def _build_test_kill_map(profiling_result: ProfilingResult) -> dict[str, set[str]]:
    """Build mapping from test name → set of mutant descriptions it kills."""
    test_kills: dict[str, set[str]] = {}
    for mutant_desc, test_names in profiling_result.kill_matrix.items():
        for test_name in test_names:
            test_kills.setdefault(test_name, set()).add(mutant_desc)
    return test_kills


def _all_mutant_ids(profiling_result: ProfilingResult) -> set[str]:
    """Get all mutant descriptions from kill matrix + survived mutants."""
    killed = set()
    for mutant_desc in profiling_result.kill_matrix:
        killed.add(mutant_desc)
    # For survived mutants, we need to reconstruct from category results
    # The kill_matrix only has killed mutants, so survived ones aren't tracked by ID
    # We synthesize IDs for counting purposes
    survived_count = profiling_result.total_survived
    all_ids = set(killed)
    for i in range(survived_count):
        all_ids.add(f"__survived_{i}")
    return all_ids
