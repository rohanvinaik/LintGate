"""Deterministic prescription logic mapping mutation profile to refactor/test actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from lintgate.mutation.state import SpecificationLevel
from lintgate.mutation.test_generators import generate_template_for_category
from lintgate.next_action import NextAction

if TYPE_CHECKING:
    from lintgate.mutation.state import FunctionMutationState


class PrescriptionCategory(str, Enum):
    """Broad categories of prescriptions based on mutation survival."""

    ADD_TEST_CASE = "add_test_case"
    ADD_BOUNDS_CHECK = "add_bounds_check"
    DECOMPOSE_FUNCTION = "decompose_function"
    STRENGTHEN_ASSERTION = "strengthen_assertion"
    NO_ACTION_REQUIRED = "no_action_required"


@dataclass
class MutationDiagnosis:
    """A specific recommendation mapped to a profile condition."""

    category: PrescriptionCategory
    reason: str
    suggested_action: str
    gate_lift_projection_percent: float = 0.0
    suggested_test_template: str | None = None
    survivor_category: str | None = None


@dataclass
class Diagnosis:
    """The aggregate analysis of a function's mutation profile."""

    function_id: str
    overall_survival_rate: float
    total_mutants: int
    surviving_categories: set[str]
    prescriptions: list[MutationDiagnosis] = field(default_factory=list)
    gate_status: str = "PASS"  # PASS, WARN, FAIL
    next_actions: list[NextAction] = field(default_factory=list)


class PrescriptionEngine:
    """Maps mutation profiles into actionable, deterministic prescriptions."""

    # Thresholds for decision boundaries
    DECOMPOSITION_THRESHOLD = 0.50  # Survival > 50% across multiple categories
    ACTIONABLE_SURVIVAL_THRESHOLD = 0.10  # 10% survival starts triggering warnings

    def __init__(self, project_root: str | None = None) -> None:
        self.project_root = project_root

    def diagnose(self, state: FunctionMutationState) -> Diagnosis:
        """Analyze a state record and emit a complete diagnosis."""
        rate = state.survival_rate
        func_id = f"{state.file_path}::{state.function_name}"
        surviving_cats = {c for c, count in state.survived_by_category.items() if count > 0}

        diag = Diagnosis(
            function_id=func_id,
            overall_survival_rate=rate,
            total_mutants=state.total,
            surviving_categories=surviving_cats,
        )

        if state.total == 0:
            diag.prescriptions.append(
                MutationDiagnosis(
                    category=PrescriptionCategory.NO_ACTION_REQUIRED,
                    reason="No mutants generated for function.",
                    suggested_action="None",
                )
            )
            diag.gate_status = "PASS"
            return diag

        if rate <= self.ACTIONABLE_SURVIVAL_THRESHOLD:
            diag.gate_status = "PASS"
            if rate > 0:
                diag.prescriptions.append(
                    MutationDiagnosis(
                        category=PrescriptionCategory.NO_ACTION_REQUIRED,
                        reason="Low survival rate, within acceptable bounds.",
                        suggested_action="Review visually if critical.",
                    )
                )
            return diag

        # Hard fail for very high unmitigated survival
        if rate >= self.DECOMPOSITION_THRESHOLD:
            diag.gate_status = "FAIL"
        else:
            diag.gate_status = "WARN"

        # 1. High Multi-Category Entanglement -> Decomposition
        if rate >= self.DECOMPOSITION_THRESHOLD and len(surviving_cats) >= 3:
            diag.prescriptions.append(
                MutationDiagnosis(
                    category=PrescriptionCategory.DECOMPOSE_FUNCTION,
                    reason=f"High survival ({rate:.0%}) across {len(surviving_cats)} semantic categories indicates the function does too much.",
                    suggested_action="Split the function into smaller, independently testable units.",
                    gate_lift_projection_percent=rate * 100.0,
                )
            )
            diag.next_actions.append(
                NextAction(
                    tool="mutation_decompose",
                    args={"path": ".", "file": state.file_path},
                    reason=f"High entanglement ({rate:.0%} survival across {len(surviving_cats)} categories) — decompose first.",
                    priority=1,
                )
            )

        # 2. Specific Category Rules (mapped when decomposition isn't the sole answer)
        else:
            for cat in surviving_cats:
                count = state.survived_by_category.get(cat, 0)
                cat_survival_rate = count / state.total
                template = generate_template_for_category(
                    cat, state, project_root=self.project_root
                )

                if cat == "arithmetic":
                    diag.prescriptions.append(
                        MutationDiagnosis(
                            category=PrescriptionCategory.ADD_TEST_CASE,
                            reason="Arithmetic mutations survived, meaning math edge cases are unchecked.",
                            suggested_action="Add tests specifically verifying exact payload outputs, not just types.",
                            gate_lift_projection_percent=cat_survival_rate * 100.0 * 0.8,
                            suggested_test_template=template,
                            survivor_category=cat,
                        )
                    )
                elif cat == "conditional":
                    diag.prescriptions.append(
                        MutationDiagnosis(
                            category=PrescriptionCategory.ADD_BOUNDS_CHECK,
                            reason="Conditional branch mutations survived.",
                            suggested_action="Add tests covering both branches (True/False) of the logic.",
                            gate_lift_projection_percent=cat_survival_rate * 100.0 * 0.9,
                            suggested_test_template=template,
                            survivor_category=cat,
                        )
                    )
                elif cat == "string":
                    diag.prescriptions.append(
                        MutationDiagnosis(
                            category=PrescriptionCategory.STRENGTHEN_ASSERTION,
                            reason="String mutations survived, indicating weak assertions on text outputs.",
                            suggested_action="Assert exact string matching instead of substring or empty state.",
                            gate_lift_projection_percent=cat_survival_rate * 100.0 * 0.5,
                            suggested_test_template=template,
                            survivor_category=cat,
                        )
                    )
                elif cat == "keyword":
                    diag.prescriptions.append(
                        MutationDiagnosis(
                            category=PrescriptionCategory.STRENGTHEN_ASSERTION,
                            reason="Keyword (e.g. break->continue, True->False) mutations survived.",
                            suggested_action="Verify exact boolean states and loop exit side-effects.",
                            gate_lift_projection_percent=cat_survival_rate * 100.0 * 0.75,
                            survivor_category=cat,
                        )
                    )
                else:
                    diag.prescriptions.append(
                        MutationDiagnosis(
                            category=PrescriptionCategory.ADD_TEST_CASE,
                            reason=f"Mutations in {cat} survived.",
                            suggested_action="Review test coverage missing this semantic block.",
                            gate_lift_projection_percent=cat_survival_rate * 100.0 * 0.5,
                            survivor_category=cat,
                        )
                    )

        # Sort prescriptions by projection (impact)
        diag.prescriptions.sort(key=lambda p: p.gate_lift_projection_percent, reverse=True)

        existing_tools = {a.tool for a in diag.next_actions}
        has_refactor_loop = "mutation_refactor_loop" in existing_tools
        if not has_refactor_loop and any(
            p.category != PrescriptionCategory.DECOMPOSE_FUNCTION for p in diag.prescriptions
        ):
            diag.next_actions.append(
                NextAction(
                    tool="mutation_refactor_loop",
                    args={"path": ".", "file": state.file_path},
                    reason="Apply test improvements and re-profile to measure survival delta.",
                    priority=3,
                )
            )

        return diag


def classify_specification_level(state: FunctionMutationState) -> SpecificationLevel:
    """Classify a function's specification completeness from its mutation profile.

    Pure function: FunctionMutationState -> SpecificationLevel.

    Decision tree (mutation-theory.md Section 4, retrospective Part IV):
    1. No data at all → UNSPECIFIED
    2. Zero survival → SPECIFIED (all mutants killed)
    3. Low survival (<20%), single category → NEARLY_SPECIFIED (quick SICP win)
    4. Moderate survival (<50%), multi-category (2+) → DECOMPOSITION_CANDIDATE
    5. High survival (>=50%), multi-category (3+) → TANGLED (entangled, decompose)
    6. High survival (>=50%), few categories (<=2) → FUZZY_ATOMIC (irreducible)
    7. Fallback → DECOMPOSITION_CANDIDATE (conservative default)
    """
    if state.total == 0:
        return SpecificationLevel.UNSPECIFIED

    rate = state.survival_rate
    surviving_cats = [c for c, count in state.survived_by_category.items() if count > 0]
    n_cats = len(surviving_cats)

    if rate == 0.0:
        return SpecificationLevel.SPECIFIED

    if rate < 0.20 and n_cats <= 1:
        return SpecificationLevel.NEARLY_SPECIFIED

    if rate < 0.50 and n_cats >= 2:
        return SpecificationLevel.DECOMPOSITION_CANDIDATE

    if rate >= 0.50 and n_cats >= 3:
        return SpecificationLevel.TANGLED

    if rate >= 0.50 and n_cats <= 2:
        return SpecificationLevel.FUZZY_ATOMIC

    # Conservative fallback for edge cases (e.g. rate 0.20-0.50 with 1 cat)
    return SpecificationLevel.DECOMPOSITION_CANDIDATE


MUTCH004_ENFORCEMENT_THRESHOLDS: dict[str, dict[float, float]] = {
    "audit": {},  # no gating
    "graduated": {0.1: 0.2, 0.3: 0.5},  # spec<0.1→conf*0.2, spec<0.3→conf*0.5
    "strict": {0.5: 0.0},  # spec<0.5→suppress (multiply by 0)
}


def resolve_gate_status(spec_strength: float, enforcement_mode: str) -> tuple[str, float]:
    """Resolve MUTCH004 gate status based on spec_strength and enforcement mode.

    Returns (status, multiplier):
    - "pass", 1.0 — no gating applied
    - "warn", multiplier — confidence reduced proportionally
    - "fail", 0.0 — hints fully suppressed

    For audit mode: always ("pass", 1.0).
    For graduated: check thresholds in ascending order.
    For strict: spec<0.5 → ("fail", 0.0).
    Unknown mode → ("pass", 1.0).
    """
    thresholds = MUTCH004_ENFORCEMENT_THRESHOLDS.get(enforcement_mode)
    if not thresholds:
        return ("pass", 1.0)

    # Check thresholds in ascending order of spec_strength boundary
    for boundary in sorted(thresholds.keys()):
        if spec_strength < boundary:
            multiplier = thresholds[boundary]
            status = "fail" if multiplier == 0.0 else "warn"
            return (status, multiplier)

    return ("pass", 1.0)
