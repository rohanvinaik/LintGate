from lintgate.mutation.policy import (
    MutationOperatorCategory,
    MutationTelemetry,
    OperatorRelevanceMatrix,
    RuntimeBudget,
)


def test_runtime_budget_defaults():
    budget = RuntimeBudget()
    assert budget.max_inline_ms_per_function == 5000
    assert budget.max_mutants_per_function_inline == 15
    assert budget.max_mutants_per_function_background == 100
    assert budget.max_workers == 4
    assert budget.enabled is True


def test_mutation_telemetry():
    telemetry = MutationTelemetry(run_id="test_run")
    assert telemetry.total_mutants_evaluated == 0
    assert telemetry.inline_time_ms_spent == 0.0

    telemetry.add_inline_time(150.5)
    telemetry.total_mutants_evaluated += 10
    telemetry.mutants_executed += 5
    telemetry.mutants_skipped_budget += 3
    telemetry.mutants_skipped_policy += 2

    assert telemetry.inline_time_ms_spent == 150.5
    assert telemetry.inline_functions_profiled == 1

    telemetry.finish()
    assert telemetry.end_time >= telemetry.start_time


class TestOperatorRelevanceMatrix:
    def test_branch_heavy_function(self):
        cats = OperatorRelevanceMatrix.get_prioritized_categories(
            is_pure=False, branch_count=5, has_strings=False, has_numbers=False
        )
        assert MutationOperatorCategory.CONDITIONAL in cats
        assert MutationOperatorCategory.KEYWORD in cats
        assert MutationOperatorCategory.ARITHMETIC not in cats

    def test_pure_math_function(self):
        cats = OperatorRelevanceMatrix.get_prioritized_categories(
            is_pure=True, branch_count=0, has_strings=False, has_numbers=True
        )
        assert MutationOperatorCategory.ARITHMETIC in cats
        assert MutationOperatorCategory.NUMBER in cats
        assert MutationOperatorCategory.CONDITIONAL not in cats

    def test_string_manipulation(self):
        cats = OperatorRelevanceMatrix.get_prioritized_categories(
            is_pure=False, branch_count=1, has_strings=True, has_numbers=False
        )
        assert MutationOperatorCategory.STRING in cats
        assert MutationOperatorCategory.CONDITIONAL in cats

    def test_simple_function_fallback(self):
        cats = OperatorRelevanceMatrix.get_prioritized_categories(
            is_pure=False, branch_count=0, has_strings=False, has_numbers=False
        )
        assert len(cats) >= 5  # Selects all base categories
        assert MutationOperatorCategory.ARITHMETIC in cats
        assert MutationOperatorCategory.CONDITIONAL in cats

    def test_category_exclusion(self):
        # Base: ARITHMETIC and NUMBER
        base_cats = OperatorRelevanceMatrix.get_prioritized_categories(
            is_pure=True, branch_count=0, has_strings=False, has_numbers=True
        )
        assert MutationOperatorCategory.ARITHMETIC in base_cats

        # Exclude ARITHMETIC
        cats = OperatorRelevanceMatrix.get_prioritized_categories(
            is_pure=True, branch_count=0, has_strings=False, has_numbers=True,
            covered_categories={MutationOperatorCategory.ARITHMETIC}
        )
        assert MutationOperatorCategory.ARITHMETIC not in cats
        assert MutationOperatorCategory.NUMBER in cats

    def test_mutmut_type_mapping(self):
        assert OperatorRelevanceMatrix.map_mutmut_type_to_category("operator") == MutationOperatorCategory.ARITHMETIC
        assert OperatorRelevanceMatrix.map_mutmut_type_to_category("string") == MutationOperatorCategory.STRING
        assert OperatorRelevanceMatrix.map_mutmut_type_to_category("annassign") is None
        assert OperatorRelevanceMatrix.map_mutmut_type_to_category("unknown_type") is None
