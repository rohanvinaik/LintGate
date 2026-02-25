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


# Deterministic mapping from mutation category to test_skeleton_hints
# These hints provide specific test case ideas for each category
MUTATION_CATEGORY_HINTS: dict[str, list[str]] = {
    "arithmetic": [
        "Test zero and negative inputs",
        "Test maximum value boundaries (INT_MAX, FLOAT_MAX)",
        "Test exact equality for math results, not just type",
        "Test overflow/underflow scenarios",
    ],
    "number": [
        "Test boundary values: 0, 1, -1, MAX_INT, MIN_INT",
        "Test decimal precision edge cases",
        "Test numeric type conversions",
        "Test division by zero handling",
    ],
    "conditional": [
        "Add test with condition=True branch",
        "Add test with condition=False branch",
        "Test both paths in ternary expressions",
        "Verify default/fallback behavior when condition not met",
    ],
    "string": [
        "Test empty string input",
        "Test unicode/non-ASCII characters",
        "Test exact string match, not substring",
        "Test whitespace handling (leading, trailing, multiple)",
    ],
    "keyword": [
        "Test exact boolean state transitions",
        "Test loop exit conditions (break vs natural end)",
        "Verify continue/skip logic in loops",
        "Test None vs falsy value handling",
    ],
    "comparison": [
        "Test equality boundaries (<= vs <)",
        "Test edge cases at comparison points",
        "Verify comparison operator correctness",
    ],
    # Fallback for decomposition (general hint)
    "decomposition": [
        "Extract each branch path into separate helper function",
        "Move independent computation blocks to standalone functions",
        "Create separate function for each semantic responsibility",
    ],
}

# Structured hint format aligned with controlplane_test_skeleton archetypes
# Each hint contains archetype-style information for test skeleton generation
STRUCTURED_HINTS: dict[str, list[dict[str, str]]] = {
    "arithmetic": [
        {
            "archetype": "numeric_boundaries",
            "hint": "Test zero and negative inputs",
            "test_type": "boundary",
        },
        {
            "archetype": "numeric_boundaries",
            "hint": "Test maximum value boundaries (INT_MAX, FLOAT_MAX)",
            "test_type": "boundary",
        },
        {
            "archetype": "exact_equality",
            "hint": "Test exact equality for math results, not just type",
            "test_type": "assertion",
        },
        {
            "archetype": "numeric_edge_cases",
            "hint": "Test overflow/underflow scenarios",
            "test_type": "edge_case",
        },
    ],
    "number": [
        {
            "archetype": "numeric_boundaries",
            "hint": "Test boundary values: 0, 1, -1, MAX_INT, MIN_INT",
            "test_type": "boundary",
        },
        {
            "archetype": "numeric_precision",
            "hint": "Test decimal precision edge cases",
            "test_type": "precision",
        },
        {
            "archetype": "type_coercion",
            "hint": "Test numeric type conversions",
            "test_type": "conversion",
        },
        {
            "archetype": "numeric_edge_cases",
            "hint": "Test division by zero handling",
            "test_type": "edge_case",
        },
    ],
    "conditional": [
        {
            "archetype": "branch_coverage",
            "hint": "Add test with condition=True branch",
            "test_type": "branch",
        },
        {
            "archetype": "branch_coverage",
            "hint": "Add test with condition=False branch",
            "test_type": "branch",
        },
        {
            "archetype": "branch_coverage",
            "hint": "Test both paths in ternary expressions",
            "test_type": "branch",
        },
        {
            "archetype": "fallback_behavior",
            "hint": "Verify default/fallback behavior when condition not met",
            "test_type": "fallback",
        },
    ],
    "string": [
        {
            "archetype": "string_edge_cases",
            "hint": "Test empty string input",
            "test_type": "edge_case",
        },
        {
            "archetype": "string_edge_cases",
            "hint": "Test unicode/non-ASCII characters",
            "test_type": "edge_case",
        },
        {
            "archetype": "exact_equality",
            "hint": "Test exact string match, not substring",
            "test_type": "assertion",
        },
        {
            "archetype": "string_edge_cases",
            "hint": "Test whitespace handling (leading, trailing, multiple)",
            "test_type": "edge_case",
        },
    ],
    "keyword": [
        {
            "archetype": "boolean_state",
            "hint": "Test exact boolean state transitions",
            "test_type": "state",
        },
        {
            "archetype": "loop_behavior",
            "hint": "Test loop exit conditions (break vs natural end)",
            "test_type": "loop",
        },
        {
            "archetype": "loop_behavior",
            "hint": "Verify continue/skip logic in loops",
            "test_type": "loop",
        },
        {
            "archetype": "null_handling",
            "hint": "Test None vs falsy value handling",
            "test_type": "null",
        },
    ],
    "comparison": [
        {
            "archetype": "comparison_edge_cases",
            "hint": "Test equality boundaries (<= vs <)",
            "test_type": "boundary",
        },
        {
            "archetype": "comparison_edge_cases",
            "hint": "Test edge cases at comparison points",
            "test_type": "boundary",
        },
        {
            "archetype": "comparison_correctness",
            "hint": "Verify comparison operator correctness",
            "test_type": "assertion",
        },
    ],
}


def get_test_skeleton_hints(
    categories: list[str],
    include_decomposition: bool = False,
    decomposition_axes: list | None = None,
) -> list[dict[str, str]]:
    """Get deterministic test skeleton hints for the given categories.

    Args:
        categories: List of mutation categories that survived
        include_decomposition: Whether to include decomposition hints
        decomposition_axes: Optional list of DecompositionAxis for per-axis hints

    Returns:
        List of structured hint dicts with archetype-style information
    """
    hints: list[dict[str, str]] = []
    seen: set[str] = set()

    # Add per-axis decomposition hints if axes are provided
    if decomposition_axes:
        for axis in decomposition_axes:
            # Generate specific hints based on the axis category and line range
            cat = axis.get("category", "unknown")
            line_range = f"lines {axis.get('line_start', '?')}-{axis.get('line_end', '?')}"

            if cat in STRUCTURED_HINTS:
                for hint_dict in STRUCTURED_HINTS[cat]:
                    key = f"{hint_dict['archetype']}:{axis.get('line_start', 0)}"
                    if key not in seen:
                        # Add axis-specific context to the hint
                        enhanced_hint = {
                            "archetype": hint_dict["archetype"],
                            "hint": f"{hint_dict['hint']} ({line_range})",
                            "test_type": hint_dict["test_type"],
                            "category": cat,
                            "line_start": axis.get("line_start", 0),
                            "line_end": axis.get("line_end", 0),
                        }
                        hints.append(enhanced_hint)
                        seen.add(key)
    else:
        # Standard category-based hints
        for category in categories:
            if category in seen:
                continue

            if category in STRUCTURED_HINTS:
                for hint_dict in STRUCTURED_HINTS[category]:
                    key = hint_dict["archetype"]
                    if key not in seen:
                        hints.append(hint_dict)
                        seen.add(key)

    # Add decomposition hints if high survival across multiple categories
    if include_decomposition and not decomposition_axes:
        decomposition_hints = [
            {
                "archetype": "function_decomposition",
                "hint": "Extract each branch path into separate helper function",
                "test_type": "refactor",
            },
            {
                "archetype": "function_decomposition",
                "hint": "Move independent computation blocks to standalone functions",
                "test_type": "refactor",
            },
            {
                "archetype": "function_decomposition",
                "hint": "Create separate function for each semantic responsibility",
                "test_type": "refactor",
            },
        ]
        for hint_dict in decomposition_hints:
            if hint_dict["archetype"] not in seen:
                hints.append(hint_dict)
                seen.add(hint_dict["archetype"])

    # Add generic fallback if no specific hints were found
    if not hints:
        fallback = [
            {
                "archetype": "general_coverage",
                "hint": "Review test coverage for this function",
                "test_type": "review",
            },
            {
                "archetype": "general_coverage",
                "hint": "Add explicit assertions for return values",
                "test_type": "assertion",
            },
            {
                "archetype": "general_coverage",
                "hint": "Test edge cases relevant to this function's logic",
                "test_type": "edge_case",
            },
        ]
        hints.extend(fallback)

    return hints


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
            diag.prescriptions.append(
                Prescription(
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
                    Prescription(
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
                Prescription(
                    category=PrescriptionCategory.DECOMPOSE_FUNCTION,
                    reason=f"High survival ({rate:.0%}) across {len(surviving_cats)} semantic categories indicates the function does too much.",
                    suggested_action="Split the function into smaller, independently testable units.",
                    gate_lift_projection_percent=rate * 100.0,
                )
            )
            diag.next_actions.append("mutation_decompose")

        # 2. Specific Category Rules (mapped when decomposition isn't the sole answer)
        else:
            for cat in surviving_cats:
                count = state.survived_by_category.get(cat, 0)
                cat_survival_rate = count / state.total

                if cat == "arithmetic":
                    diag.prescriptions.append(
                        Prescription(
                            category=PrescriptionCategory.ADD_TEST_CASE,
                            reason="Arithmetic mutations survived, meaning math edge cases are unchecked.",
                            suggested_action="Add tests specifically verifying exact payload outputs, not just types.",
                            gate_lift_projection_percent=cat_survival_rate * 100.0 * 0.8,
                        )
                    )
                elif cat == "conditional":
                    diag.prescriptions.append(
                        Prescription(
                            category=PrescriptionCategory.ADD_BOUNDS_CHECK,
                            reason="Conditional branch mutations survived.",
                            suggested_action="Add tests covering both branches (True/False) of the logic.",
                            gate_lift_projection_percent=cat_survival_rate * 100.0 * 0.9,
                        )
                    )
                elif cat == "string":
                    diag.prescriptions.append(
                        Prescription(
                            category=PrescriptionCategory.STRENGTHEN_ASSERTION,
                            reason="String mutations survived, indicating weak assertions on text outputs.",
                            suggested_action="Assert exact string matching instead of substring or empty state.",
                            gate_lift_projection_percent=cat_survival_rate * 100.0 * 0.5,
                        )
                    )
                elif cat == "keyword":
                    diag.prescriptions.append(
                        Prescription(
                            category=PrescriptionCategory.STRENGTHEN_ASSERTION,
                            reason="Keyword (e.g. break->continue, True->False) mutations survived.",
                            suggested_action="Verify exact boolean states and loop exit side-effects.",
                            gate_lift_projection_percent=cat_survival_rate * 100.0 * 0.75,
                        )
                    )
                else:
                    diag.prescriptions.append(
                        Prescription(
                            category=PrescriptionCategory.ADD_TEST_CASE,
                            reason=f"Mutations in {cat} survived.",
                            suggested_action="Review test coverage missing this semantic block.",
                            gate_lift_projection_percent=cat_survival_rate * 100.0 * 0.5,
                        )
                    )

        # Sort prescriptions by projection (impact)
        diag.prescriptions.sort(key=lambda p: p.gate_lift_projection_percent, reverse=True)

        has_tests = "mutation_refactor_loop" not in diag.next_actions
        if has_tests and any(
            p.category != PrescriptionCategory.DECOMPOSE_FUNCTION for p in diag.prescriptions
        ):
            diag.next_actions.append("mutation_refactor_loop")

        return diag
