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


def test_mutation_telemetry_covered_skip_non_overlap():
    """Test that mutants_skipped_covered is tracked separately from policy skip."""
    telemetry = MutationTelemetry(run_id="test_run")

    # Simulate both types of skips
    telemetry.mutants_skipped_policy += 10
    telemetry.mutants_skipped_covered += 5

    # Verify non-overlap: they are independent counters
    assert telemetry.mutants_skipped_policy == 10
    assert telemetry.mutants_skipped_covered == 5
    assert telemetry.mutants_skipped_policy != telemetry.mutants_skipped_covered

    # Total skipped = budget + policy + covered + equivalent
    # (budget not set in this test)
    assert telemetry.mutants_skipped_policy > 0
    assert telemetry.mutants_skipped_covered > 0


def test_mutation_telemetry_covered_default():
    """Test that mutants_skipped_covered defaults to 0."""
    telemetry = MutationTelemetry(run_id="test_run")
    assert telemetry.mutants_skipped_covered == 0


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
            is_pure=True,
            branch_count=0,
            has_strings=False,
            has_numbers=True,
            covered_categories={MutationOperatorCategory.ARITHMETIC},
        )
        assert MutationOperatorCategory.ARITHMETIC not in cats
        assert MutationOperatorCategory.NUMBER in cats

    def test_mutmut_type_mapping(self):
        assert (
            OperatorRelevanceMatrix.map_mutmut_type_to_category("operator")
            == MutationOperatorCategory.ARITHMETIC
        )
        assert (
            OperatorRelevanceMatrix.map_mutmut_type_to_category("string")
            == MutationOperatorCategory.STRING
        )
        assert OperatorRelevanceMatrix.map_mutmut_type_to_category("annassign") is None
        assert OperatorRelevanceMatrix.map_mutmut_type_to_category("unknown_type") is None


def test_calibrated_policy_calibration_mode_fallback():
    """Test fallback mode when insufficient sample size."""
    from unittest.mock import MagicMock

    from lintgate.mutation.policy import CalibratedPolicy

    policy = CalibratedPolicy()

    # Create mock states (less than 6 to trigger fallback)
    mock_states = {
        f"func_{i}": MagicMock(survival_rate=0.2, total=10)
        for i in range(3)  # Only 3 states - below MIN_CALIBRATION_SAMPLE (6)
    }

    test_state = MagicMock(survival_rate=0.3, total=10)
    warning, blocking, metadata = policy.get_thresholds(test_state, mock_states)

    assert metadata.calibration_mode == "fallback"
    assert metadata.sample_size == 3
    assert warning == policy.base_warning_threshold
    assert blocking == policy.base_blocking_threshold


def test_calibrated_policy_calibration_mode_calibrated():
    """Test calibrated mode with sufficient sample size."""
    from unittest.mock import MagicMock

    from lintgate.mutation.policy import CalibratedPolicy

    policy = CalibratedPolicy()

    # Create mock states (6 or more to trigger calibration)
    mock_states = {
        f"func_{i}": MagicMock(survival_rate=0.2 + (i * 0.05), total=10)
        for i in range(6)  # 6 states - meets MIN_CALIBRATION_SAMPLE
    }

    test_state = MagicMock(survival_rate=0.3, total=10)
    warning, blocking, metadata = policy.get_thresholds(test_state, mock_states)

    assert metadata.calibration_mode == "calibrated"
    assert metadata.sample_size == 6
    assert metadata.mean_survival > 0
    # Warning should be mean + 0.10, with clamping
    assert warning >= 0.15


def test_calibrated_policy_confidence_extreme_survival():
    """Test confidence penalty for extreme survival (>80%)."""
    from unittest.mock import MagicMock

    from lintgate.mutation.policy import CalibratedPolicy

    policy = CalibratedPolicy()

    # State with >80% survival
    mock_state = MagicMock(survival_rate=0.85, depth="profiled")

    confidence, metadata = policy.get_confidence(mock_state)

    assert confidence < 0.8  # Should be penalized
    assert metadata["extreme_survival_penalty"] is True
    assert metadata["penalty_applied"] > 0


def test_calibrated_policy_confidence_normal_survival():
    """Test normal confidence without penalty."""
    from unittest.mock import MagicMock

    from lintgate.mutation.policy import CalibratedPolicy

    policy = CalibratedPolicy()

    # State with normal survival
    mock_state = MagicMock(survival_rate=0.3, depth="profiled")

    confidence, metadata = policy.get_confidence(mock_state)

    assert confidence == 0.8  # Base confidence for profiled
    assert "extreme_survival_penalty" not in metadata


def test_calibration_metadata_serialization():
    """Test CalibrationMetadata to_dict."""
    from lintgate.mutation.policy import CalibrationMetadata

    metadata = CalibrationMetadata(
        calibration_mode="calibrated",
        sample_size=10,
        mean_survival=0.35,
    )

    d = metadata.to_dict()
    assert d["calibration_mode"] == "calibrated"
    assert d["sample_size"] == 10
    assert d["mean_survival"] == 0.35
    assert "strategy_version" in d
