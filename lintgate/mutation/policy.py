"""Mutation testing policy, operator relevance matrix, and runtime budget tracking.

Defines the rules for selecting which mutations to apply to which functions based on
structural properties, and tracks execution against defined runtime budgets.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MutationOperatorCategory(str, Enum):
    """Broad categories of mutation operators."""

    ARITHMETIC = "arithmetic"  # +, -, *, /, //, %
    CONDITIONAL = "conditional"  # if, and, or, ==, <, >
    STRING = "string"  # "foo" -> "XXfooXX"
    NUMBER = "number"  # 1 -> 2
    KEYWORD = "keyword"  # break -> continue, True -> False
    DECORATOR = "decorator"


@dataclass(frozen=True)
class RuntimeBudget:
    """Hard constraints for mutation execution."""

    max_inline_ms_per_function: int = (
        5000  # 5 seconds per function max for inline profiling
    )
    max_mutants_per_function_inline: int = 15  # Sample limit
    max_mutants_per_function_background: int = 100  # Deep sweep limit
    max_workers: int = 4
    enabled: bool = True


@dataclass
class MutationTelemetry:
    """Tracks metrics and budget adherence across a mutation run."""

    run_id: str
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0

    total_mutants_evaluated: int = 0
    mutants_executed: int = 0
    mutants_skipped_budget: int = 0
    mutants_skipped_policy: int = 0
    mutants_skipped_equivalent: int = 0

    inline_functions_profiled: int = 0
    inline_time_ms_spent: float = 0.0
    background_functions_profiled: int = 0

    def finish(self) -> None:
        self.end_time = time.time()

    def add_inline_time(self, ms: float) -> None:
        self.inline_time_ms_spent += ms
        self.inline_functions_profiled += 1


@dataclass
class CalibratedPolicy:
    """Dynamic thresholds based on repository mutation history."""

    base_blocking_threshold: float = 0.60
    base_warning_threshold: float = 0.30

    # Penalty for uncertain equivalent mutants (extreme survival often implies equivalence)
    equivalent_mutant_penalty: float = 0.15

    def get_thresholds(
        self, state: Any, all_states: dict[str, Any]
    ) -> tuple[float, float]:
        """Returns (warning_threshold, blocking_threshold)."""
        valid_states = [s for s in all_states.values() if getattr(s, "total", 0) > 0]
        if len(valid_states) > 5:
            avg_survival = sum(s.survival_rate for s in valid_states) / len(
                valid_states
            )
            # Block aggressively if way worse than average, warn if worse than average
            warning = max(0.15, min(avg_survival + 0.10, 0.50))
            blocking = max(warning + 0.20, min(avg_survival + 0.30, 0.80))
        else:
            warning = self.base_warning_threshold
            blocking = self.base_blocking_threshold

        return warning, blocking

    def get_confidence(self, state: Any) -> float:
        """Adjust confidence based on depth and equivalent mutant uncertainty."""
        from lintgate.mutation.state import CoverageDepth

        base_confidence = (
            0.8
            if getattr(state, "depth", CoverageDepth.NONE) == CoverageDepth.PROFILED
            else 0.5
        )

        rate = getattr(state, "survival_rate", 0.0)
        if rate > 0.8:
            base_confidence = max(0.1, base_confidence - self.equivalent_mutant_penalty)

        return base_confidence


class OperatorRelevanceMatrix:
    """Maps function characteristics to prioritized mutation categories.

    This acts as a filter prior to mutant execution, saving budget by avoiding
    mutations that are unlikely to be meaningful given the function's structural profile.
    """

    @staticmethod
    def get_prioritized_categories(
        is_pure: bool,
        branch_count: int,
        has_strings: bool,
        has_numbers: bool,
        covered_categories: set[MutationOperatorCategory] | None = None,
    ) -> set[MutationOperatorCategory]:
        """Determine which categories of mutation operators are most relevant."""
        relevant = set()

        # Branch-heavy logic must be tested for edge cases
        if branch_count > 0:
            relevant.add(MutationOperatorCategory.CONDITIONAL)
            relevant.add(MutationOperatorCategory.KEYWORD)

        # Mathematical or pure logic operations need strict checks
        if is_pure or has_numbers:
            relevant.add(MutationOperatorCategory.ARITHMETIC)
            relevant.add(MutationOperatorCategory.NUMBER)

        # String manipulation heavily implies string bounds testing
        if has_strings:
            relevant.add(MutationOperatorCategory.STRING)

        # If very simple, try everything (baseline testing)
        if branch_count == 0 and not has_strings and not has_numbers:
            relevant.update(
                [
                    MutationOperatorCategory.ARITHMETIC,
                    MutationOperatorCategory.CONDITIONAL,
                    MutationOperatorCategory.KEYWORD,
                    MutationOperatorCategory.NUMBER,
                    MutationOperatorCategory.STRING,
                ]
            )

        if covered_categories:
            relevant = relevant - covered_categories

        return relevant

    @staticmethod
    def map_mutmut_type_to_category(
        mutmut_type: str,
    ) -> MutationOperatorCategory | None:
        """Map mutmut's internal mutation names to our broad categories."""
        mapping = {
            "operator": MutationOperatorCategory.ARITHMETIC,
            "keyword": MutationOperatorCategory.KEYWORD,
            "number": MutationOperatorCategory.NUMBER,
            "name": MutationOperatorCategory.KEYWORD,
            "string": MutationOperatorCategory.STRING,
            "argument": MutationOperatorCategory.KEYWORD,
            "or_test": MutationOperatorCategory.CONDITIONAL,
            "and_test": MutationOperatorCategory.CONDITIONAL,
            "cmpop": MutationOperatorCategory.CONDITIONAL,
            "expr": MutationOperatorCategory.ARITHMETIC,
            "decorator": MutationOperatorCategory.DECORATOR,
            "annassign": None,  # Usually skips as irrelevant for functionality dropping
        }
        return mapping.get(mutmut_type)
