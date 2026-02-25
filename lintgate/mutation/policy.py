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

    max_inline_ms_per_function: int = 5000  # 5 seconds per function max for inline profiling
    max_mutants_per_function_inline: int = 15  # Sample limit
    max_mutants_per_function_background: int = 100  # Deep sweep limit
    max_workers: int = 4
    enabled: bool = True


@dataclass
class MutationTelemetry:
    """Tracks metrics and budget adherence across a mutation run.

    Telemetry is organized into four buckets:
    1. integrity: parse success/failure, state read/write consistency
    2. efficiency: mutants executed, skipped, runtime/cost
    3. signal_quality: sample quality classification counts
    4. actionability: prescriptions emitted, decomposition plans, refactor-loop deltas
    """

    run_id: str
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0

    # ── Efficiency bucket ─────────────────────────────────────────────────
    total_mutants_evaluated: int = 0
    mutants_executed: int = 0
    mutants_skipped_budget: int = 0
    mutants_skipped_policy: int = 0
    mutants_skipped_covered: int = 0  # Skipped due to TEFF strong assertions covering the category
    mutants_skipped_equivalent: int = 0

    inline_functions_profiled: int = 0
    inline_time_ms_spent: float = 0.0
    background_functions_profiled: int = 0

    # ── Integrity bucket ─────────────────────────────────────────────────
    state_load_success: int = 0
    state_load_failure: int = 0
    state_save_success: int = 0
    state_save_failure: int = 0
    parse_success: int = 0
    parse_failure: int = 0

    # ── Signal Quality bucket ─────────────────────────────────────────────
    profiled_runs: int = 0  # Authoritative runs
    sampled_high_runs: int = 0  # Near-authoritative runs
    sampled_low_runs: int = 0  # Advisory-only runs

    # ── Actionability bucket ──────────────────────────────────────────────
    prescriptions_emitted: int = 0
    decomposition_plans_emitted: int = 0
    refactor_loop_deltas: int = 0
    refactor_loop_improvements: int = 0  # Positive delta in survival rate

    def finish(self) -> None:
        self.end_time = time.time()

    def add_inline_time(self, ms: float) -> None:
        self.inline_time_ms_spent += ms
        self.inline_functions_profiled += 1

    def to_bucket_dict(self) -> dict[str, Any]:
        """Return telemetry organized into four buckets."""
        return {
            "integrity": {
                "state_load_success": self.state_load_success,
                "state_load_failure": self.state_load_failure,
                "state_save_success": self.state_save_success,
                "state_save_failure": self.state_save_failure,
                "parse_success": self.parse_success,
                "parse_failure": self.parse_failure,
            },
            "efficiency": {
                "total_mutants_evaluated": self.total_mutants_evaluated,
                "mutants_executed": self.mutants_executed,
                "mutants_skipped_budget": self.mutants_skipped_budget,
                "mutants_skipped_policy": self.mutants_skipped_policy,
                "mutants_skipped_covered": self.mutants_skipped_covered,
                "mutants_skipped_equivalent": self.mutants_skipped_equivalent,
                "inline_functions_profiled": self.inline_functions_profiled,
                "inline_time_ms_spent": round(self.inline_time_ms_spent, 2),
                "background_functions_profiled": self.background_functions_profiled,
            },
            "signal_quality": {
                "profiled_runs": self.profiled_runs,
                "sampled_high_runs": self.sampled_high_runs,
                "sampled_low_runs": self.sampled_low_runs,
            },
            "actionability": {
                "prescriptions_emitted": self.prescriptions_emitted,
                "decomposition_plans_emitted": self.decomposition_plans_emitted,
                "refactor_loop_deltas": self.refactor_loop_deltas,
                "refactor_loop_improvements": self.refactor_loop_improvements,
            },
        }


@dataclass
class CalibrationMetadata:
    """Metadata about threshold calibration for reproducibility."""

    calibration_mode: str  # "calibrated" or "fallback"
    sample_size: int  # Number of states used for calibration
    mean_survival: float  # Mean survival rate of sample
    strategy_version: str = "v1"  # Calibration strategy version

    def to_dict(self) -> dict[str, Any]:
        return {
            "calibration_mode": self.calibration_mode,
            "sample_size": self.sample_size,
            "mean_survival": round(self.mean_survival, 3),
            "strategy_version": self.strategy_version,
        }


@dataclass
class CalibratedPolicy:
    """Dynamic thresholds based on repository mutation history."""

    base_blocking_threshold: float = 0.60
    base_warning_threshold: float = 0.30

    # Penalty for uncertain equivalent mutants (extreme survival often implies equivalence)
    equivalent_mutant_penalty: float = 0.15

    # Minimum sample size required for calibration (not fallback)
    MIN_CALIBRATION_SAMPLE: int = 6

    def get_thresholds(
        self, state: Any, all_states: dict[str, Any]
    ) -> tuple[float, float, CalibrationMetadata]:
        """Returns (warning_threshold, blocking_threshold, calibration_metadata).

        Deterministic formula:
        - If sample_size >= 6: use repository-driven calibration
          - warning = clamp(mean_survival + 0.10, 0.15, 0.50)
          - blocking = clamp(mean_survival + 0.30, warning + 0.20, 0.80)
        - Otherwise: fall back to static base thresholds
        """
        valid_states = [s for s in all_states.values() if getattr(s, "total", 0) > 0]
        sample_size = len(valid_states)

        if sample_size >= self.MIN_CALIBRATION_SAMPLE:
            # Repository-driven calibration
            mean_survival = sum(s.survival_rate for s in valid_states) / sample_size

            # Deterministic formula with clamping
            warning = max(0.15, min(mean_survival + 0.10, 0.50))
            blocking = max(warning + 0.20, min(mean_survival + 0.30, 0.80))

            metadata = CalibrationMetadata(
                calibration_mode="calibrated",
                sample_size=sample_size,
                mean_survival=mean_survival,
            )
        else:
            # Fallback to static thresholds
            warning = self.base_warning_threshold
            blocking = self.base_blocking_threshold

            metadata = CalibrationMetadata(
                calibration_mode="fallback",
                sample_size=sample_size,
                mean_survival=0.0,  # Not applicable for fallback
            )

        return warning, blocking, metadata

    def get_confidence(self, state: Any) -> tuple[float, dict[str, Any]]:
        """Adjust confidence based on depth and equivalent mutant uncertainty.

        Returns confidence score and metadata about confidence adjustments.
        """
        from lintgate.mutation.state import CoverageDepth

        base_confidence = (
            0.8 if getattr(state, "depth", CoverageDepth.NONE) == CoverageDepth.PROFILED else 0.5
        )
        confidence_metadata: dict[str, Any] = {"base_confidence": base_confidence}

        rate = getattr(state, "survival_rate", 0.0)

        # Explicit confidence penalty for extreme survival (>80%)
        if rate > 0.8:
            adjusted = max(0.1, base_confidence - self.equivalent_mutant_penalty)
            confidence_metadata["extreme_survival_penalty"] = True
            confidence_metadata["original_confidence"] = base_confidence
            confidence_metadata["penalty_applied"] = base_confidence - adjusted
            return adjusted, confidence_metadata

        return base_confidence, confidence_metadata


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
    def map_mutmut_type_to_category(mutmut_type: str) -> MutationOperatorCategory | None:
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
