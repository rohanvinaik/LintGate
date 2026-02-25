"""Deterministic prescription logic mapping mutation profile to refactor/test actions."""

from dataclasses import dataclass, field
from enum import Enum

from lintgate.mutation.state import FunctionMutationState


class PrescriptionCategory(str, Enum):
    """Broad categories of prescriptions based on mutation survival."""
    ADD_TEST_CASE = "add_test_case"
    ADD_BOUNDS_CHECK = "add_bounds_check"
    DECOMPOSE_FUNCTION = "decompose_function"
    STRENGTHEN_ASSERTION = "strengthen_assertion"
    NO_ACTION_REQUIRED = "no_action_required"


@dataclass
class Prescription:
    """A specific recommendation mapped to a profile condition."""
    category: PrescriptionCategory
    reason: str
    suggested_action: str
    gate_lift_projection_percent: float = 0.0


@dataclass
class Diagnosis:
    """The aggregate analysis of a function's mutation profile."""
    function_id: str
    overall_survival_rate: float
    total_mutants: int
    surviving_categories: set[str]
    prescriptions: list[Prescription] = field(default_factory=list)
    gate_status: str = "PASS"  # PASS, WARN, FAIL
    next_actions: list[str] = field(default_factory=list)


class PrescriptionEngine:
    """Maps mutation profiles into actionable, deterministic prescriptions."""

    # Thresholds for decision boundaries
    DECOMPOSITION_THRESHOLD = 0.50  # Survival > 50% across multiple categories
    ACTIONABLE_SURVIVAL_THRESHOLD = 0.10  # 10% survival starts triggering warnings

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
            diag.prescriptions.append(Prescription(
                category=PrescriptionCategory.NO_ACTION_REQUIRED,
                reason="No mutants generated for function.",
                suggested_action="None",
            ))
            diag.gate_status = "PASS"
            return diag

        if rate <= self.ACTIONABLE_SURVIVAL_THRESHOLD:
            diag.gate_status = "PASS"
            if rate > 0:
                diag.prescriptions.append(Prescription(
                    category=PrescriptionCategory.NO_ACTION_REQUIRED,
                    reason="Low survival rate, within acceptable bounds.",
                    suggested_action="Review visually if critical.",
                ))
            return diag

        # Hard fail for very high unmitigated survival
        if rate >= self.DECOMPOSITION_THRESHOLD:
            diag.gate_status = "FAIL"
        else:
            diag.gate_status = "WARN"

        # 1. High Multi-Category Entanglement -> Decomposition
        if rate >= self.DECOMPOSITION_THRESHOLD and len(surviving_cats) >= 3:
            diag.prescriptions.append(Prescription(
                category=PrescriptionCategory.DECOMPOSE_FUNCTION,
                reason=f"High survival ({rate:.0%}) across {len(surviving_cats)} semantic categories indicates the function does too much.",
                suggested_action="Split the function into smaller, independently testable units.",
                gate_lift_projection_percent=rate * 100.0,
            ))
            diag.next_actions.append("mutation_decompose")

        # 2. Specific Category Rules (mapped when decomposition isn't the sole answer)
        else:
            for cat in surviving_cats:
                count = state.survived_by_category.get(cat, 0)
                cat_survival_rate = count / state.total

                if cat == "arithmetic":
                    diag.prescriptions.append(Prescription(
                        category=PrescriptionCategory.ADD_TEST_CASE,
                        reason="Arithmetic mutations survived, meaning math edge cases are unchecked.",
                        suggested_action="Add tests specifically verifying exact payload outputs, not just types.",
                        gate_lift_projection_percent=cat_survival_rate * 100.0 * 0.8,
                    ))
                elif cat == "conditional":
                    diag.prescriptions.append(Prescription(
                        category=PrescriptionCategory.ADD_BOUNDS_CHECK,
                        reason="Conditional branch mutations survived.",
                        suggested_action="Add tests covering both branches (True/False) of the logic.",
                        gate_lift_projection_percent=cat_survival_rate * 100.0 * 0.9,
                    ))
                elif cat == "string":
                    diag.prescriptions.append(Prescription(
                        category=PrescriptionCategory.STRENGTHEN_ASSERTION,
                        reason="String mutations survived, indicating weak assertions on text outputs.",
                        suggested_action="Assert exact string matching instead of substring or empty state.",
                        gate_lift_projection_percent=cat_survival_rate * 100.0 * 0.5,
                    ))
                elif cat == "keyword":
                    diag.prescriptions.append(Prescription(
                        category=PrescriptionCategory.STRENGTHEN_ASSERTION,
                        reason="Keyword (e.g. break->continue, True->False) mutations survived.",
                        suggested_action="Verify exact boolean states and loop exit side-effects.",
                        gate_lift_projection_percent=cat_survival_rate * 100.0 * 0.75,
                    ))
                else:
                    diag.prescriptions.append(Prescription(
                        category=PrescriptionCategory.ADD_TEST_CASE,
                        reason=f"Mutations in {cat} survived.",
                        suggested_action="Review test coverage missing this semantic block.",
                        gate_lift_projection_percent=cat_survival_rate * 100.0 * 0.5,
                    ))

        # Sort prescriptions by projection (impact)
        diag.prescriptions.sort(key=lambda p: p.gate_lift_projection_percent, reverse=True)

        has_tests = "mutation_refactor_loop" not in diag.next_actions
        if has_tests and any(p.category != PrescriptionCategory.DECOMPOSE_FUNCTION for p in diag.prescriptions):
             diag.next_actions.append("mutation_refactor_loop")

        return diag
