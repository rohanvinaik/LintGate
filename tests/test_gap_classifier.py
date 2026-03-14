"""Tests for specification gap classification."""

from __future__ import annotations

from lintgate.specification.gap_classifier import (
    GapClass,
    classify_from_func_data,
    classify_gap,
)

# ── classify_gap direct tests ────────────────────────────────────


class TestSpecifiedBucket:
    def test_stop_criteria_met(self):
        assert classify_gap(spec_level=0.1, stop_criteria_met=True) == GapClass.SPECIFIED

    def test_empirical_confirms_zero_survival(self):
        """Empirical data with zero survival → specified."""
        assert (
            classify_gap(spec_level=0.9, survival_rate=0.0, overlay_status="AGREES")
            == GapClass.SPECIFIED
        )

    def test_empirical_contradicts_zero_survival(self):
        """Even CONTRADICTS with zero survival → specified (all mutants killed)."""
        assert (
            classify_gap(spec_level=0.3, survival_rate=0.0, overlay_status="CONTRADICTS")
            == GapClass.SPECIFIED
        )


class TestUnprofiledBucket:
    def test_no_empirical_data_low_spec(self):
        """NO_EMPIRICAL_DATA + low spec → unprofiled, not specified."""
        assert (
            classify_gap(spec_level=0.1, overlay_status="NO_EMPIRICAL_DATA") == GapClass.UNPROFILED
        )

    def test_no_empirical_data_high_spec_is_specified(self):
        """NO_EMPIRICAL_DATA + high spec → specified (static confidence sufficient)."""
        assert (
            classify_gap(spec_level=0.9, overlay_status="NO_EMPIRICAL_DATA") == GapClass.SPECIFIED
        )

    def test_no_empirical_data_medium_spec(self):
        """NO_EMPIRICAL_DATA + medium spec (below 0.8 threshold) → unprofiled."""
        assert (
            classify_gap(spec_level=0.5, overlay_status="NO_EMPIRICAL_DATA") == GapClass.UNPROFILED
        )

    def test_empty_overlay_status(self):
        """Empty overlay_status treated as unprofiled."""
        assert classify_gap(spec_level=0.5, overlay_status="") == GapClass.UNPROFILED

    def test_default_overlay_status(self):
        """Default args (no overlay_status) → unprofiled."""
        assert classify_gap(spec_level=0.5) == GapClass.UNPROFILED

    def test_stop_criteria_overrides_unprofiled(self):
        """stop_criteria_met takes priority even without empirical data."""
        assert (
            classify_gap(spec_level=0.1, stop_criteria_met=True, overlay_status="NO_EMPIRICAL_DATA")
            == GapClass.SPECIFIED
        )


class TestDiscoveryFailureBucket:
    def test_truth_label_discovery_artifact(self):
        assert (
            classify_gap(spec_level=0.1, mutation_truth_label="DISCOVERY_ARTIFACT")
            == GapClass.DISCOVERY_FAILURE
        )

    def test_overlay_status_discovery_failure(self):
        assert (
            classify_gap(spec_level=0.1, overlay_status="DISCOVERY_FAILURE")
            == GapClass.DISCOVERY_FAILURE
        )

    def test_no_test_files_state(self):
        assert (
            classify_gap(spec_level=0.1, discovery_state="NO_TEST_FILES")
            == GapClass.DISCOVERY_FAILURE
        )

    def test_import_failed_state(self):
        assert (
            classify_gap(spec_level=0.1, discovery_state="DISCOVERY_IMPORT_FAILED")
            == GapClass.DISCOVERY_FAILURE
        )

    def test_none_linked_state(self):
        assert (
            classify_gap(spec_level=0.1, discovery_state="TEST_FILES_FOUND_NONE_LINKED")
            == GapClass.DISCOVERY_FAILURE
        )


class TestEquivalentOrLowValueBucket:
    def test_truth_label_equivalent(self):
        assert (
            classify_gap(spec_level=0.1, mutation_truth_label="EQUIVALENT_OR_UNINTERESTING")
            == GapClass.EQUIVALENT_OR_LOW_VALUE
        )

    def test_truth_label_budget_instability_is_separate(self):
        """BUDGET_INSTABILITY is its own bucket, not equivalent."""
        assert (
            classify_gap(spec_level=0.1, mutation_truth_label="BUDGET_INSTABILITY")
            == GapClass.BUDGET_INSTABILITY
        )

    def test_serialization_value_only_to_dict(self):
        result = classify_gap(
            spec_level=0.1,
            survival_rate=0.5,
            surviving_categories=["VALUE", "VALUE"],
            function_name="to_dict",
        )
        assert result == GapClass.EQUIVALENT_OR_LOW_VALUE

    def test_serialization_value_only_from_json(self):
        result = classify_gap(
            spec_level=0.1,
            survival_rate=0.5,
            surviving_categories=["VALUE"],
            function_name="from_json",
        )
        assert result == GapClass.EQUIVALENT_OR_LOW_VALUE

    def test_serialization_mixed_categories_not_equivalent(self):
        """to_dict with non-VALUE survivors is real underspecification."""
        result = classify_gap(
            spec_level=0.1,
            survival_rate=0.5,
            surviving_categories=["VALUE", "SWAP"],
            function_name="to_dict",
        )
        assert result != GapClass.EQUIVALENT_OR_LOW_VALUE

    def test_value_only_non_serializer_not_equivalent(self):
        """VALUE-only survivors on a non-serializer function is real gap."""
        result = classify_gap(
            spec_level=0.1,
            survival_rate=0.5,
            surviving_categories=["VALUE"],
            function_name="compute_score",
        )
        assert result == GapClass.REAL_UNDERSPECIFICATION

    def test_qualified_name_to_dict(self):
        """Class.to_dict should still match via rsplit."""
        result = classify_gap(
            spec_level=0.1,
            survival_rate=0.5,
            surviving_categories=["VALUE"],
            function_name="MyClass.to_dict",
        )
        assert result == GapClass.EQUIVALENT_OR_LOW_VALUE


class TestIntegrationOnlyBucket:
    def test_mock_boundary_artifact(self):
        assert (
            classify_gap(
                spec_level=0.1,
                survival_rate=0.5,
                mutation_truth_label="MOCK_BOUNDARY_ARTIFACT",
            )
            == GapClass.INTEGRATION_ONLY
        )

    def test_mock_boundary_dominant_topology(self):
        assert (
            classify_gap(
                spec_level=0.1,
                survival_rate=0.5,
                topology_state="MOCK_BOUNDARY_DOMINANT",
            )
            == GapClass.INTEGRATION_ONLY
        )

    def test_regime_b_swap_dominant(self):
        result = classify_gap(
            spec_level=0.1,
            survival_rate=0.5,
            regime="B",
            surviving_categories=["SWAP", "SWAP", "VALUE"],
        )
        assert result == GapClass.INTEGRATION_ONLY

    def test_regime_b_value_dominant_not_integration(self):
        """Regime B but VALUE-dominant → real underspecification."""
        result = classify_gap(
            spec_level=0.1,
            survival_rate=0.5,
            regime="B",
            surviving_categories=["VALUE", "VALUE", "SWAP"],
        )
        assert result == GapClass.REAL_UNDERSPECIFICATION

    def test_regime_a_swap_dominant_not_integration(self):
        """Regime A with SWAP-dominant → real underspecification (not integration)."""
        result = classify_gap(
            spec_level=0.1,
            survival_rate=0.5,
            regime="A",
            surviving_categories=["SWAP", "SWAP"],
        )
        assert result == GapClass.REAL_UNDERSPECIFICATION


class TestRealUnderspecificationBucket:
    def test_basic_survivors(self):
        result = classify_gap(
            spec_level=0.2,
            survival_rate=0.3,
            surviving_categories=["VALUE", "BOUNDARY"],
        )
        assert result == GapClass.REAL_UNDERSPECIFICATION

    def test_low_spec_meaningful_label(self):
        result = classify_gap(
            spec_level=0.1,
            survival_rate=0.5,
            mutation_truth_label="MEANINGFUL",
        )
        assert result == GapClass.REAL_UNDERSPECIFICATION


# ── Priority ordering tests ──────────────────────────────────────


class TestClassificationPriority:
    """Verify classification priority: discovery > equivalent > integration > real."""

    def test_discovery_beats_equivalent(self):
        """DISCOVERY_ARTIFACT takes priority over EQUIVALENT_OR_UNINTERESTING."""
        result = classify_gap(
            spec_level=0.1,
            mutation_truth_label="DISCOVERY_ARTIFACT",
        )
        assert result == GapClass.DISCOVERY_FAILURE

    def test_discovery_beats_integration(self):
        result = classify_gap(
            spec_level=0.1,
            mutation_truth_label="DISCOVERY_ARTIFACT",
            topology_state="MOCK_BOUNDARY_DOMINANT",
        )
        assert result == GapClass.DISCOVERY_FAILURE

    def test_equivalent_beats_integration(self):
        result = classify_gap(
            spec_level=0.1,
            survival_rate=0.5,
            mutation_truth_label="EQUIVALENT_OR_UNINTERESTING",
            topology_state="MOCK_BOUNDARY_DOMINANT",
        )
        assert result == GapClass.EQUIVALENT_OR_LOW_VALUE

    def test_budget_instability_beats_discovery(self):
        """BUDGET_INSTABILITY takes priority over discovery signals."""
        result = classify_gap(
            spec_level=0.1,
            mutation_truth_label="BUDGET_INSTABILITY",
            discovery_state="NO_TEST_FILES",
        )
        assert result == GapClass.BUDGET_INSTABILITY

    def test_stop_criteria_beats_everything(self):
        result = classify_gap(
            spec_level=0.1,
            stop_criteria_met=True,
            mutation_truth_label="DISCOVERY_ARTIFACT",
            survival_rate=1.0,
        )
        assert result == GapClass.SPECIFIED


# ── classify_from_func_data convenience wrapper ──────────────────


class TestClassifyFromFuncData:
    def test_basic_specified(self):
        func_data = {
            "specification_level": 0.9,
            "reconciled_spec_level": 0.9,
            "stop_criteria_met": True,
            "empirical_overlay": {"status": "AGREES"},
            "regime": "A",
        }
        assert classify_from_func_data(func_data) == GapClass.SPECIFIED

    def test_discovery_failure_from_mutation_entry(self):
        func_data = {
            "specification_level": 0.1,
            "empirical_overlay": {"status": "DISCOVERY_FAILURE"},
            "regime": "A",
        }
        mutation_entry = {
            "mutation_truth_label": "DISCOVERY_ARTIFACT",
            "discovery_state": "NO_TEST_FILES",
            "survival_rate": 1.0,
        }
        assert classify_from_func_data(func_data, mutation_entry) == GapClass.DISCOVERY_FAILURE

    def test_budget_instability_from_mutation_entry(self):
        func_data = {
            "specification_level": 0.1,
            "empirical_overlay": {"status": "NO_EMPIRICAL_DATA"},
            "regime": "A",
        }
        mutation_entry = {
            "mutation_truth_label": "BUDGET_INSTABILITY",
            "survival_rate": 0.5,
        }
        assert classify_from_func_data(func_data, mutation_entry) == GapClass.BUDGET_INSTABILITY

    def test_real_gap_from_surviving_categories(self):
        func_data = {
            "specification_level": 0.2,
            "empirical_overlay": {"status": "AGREES"},
            "regime": "A",
        }
        mutation_entry = {
            "mutation_truth_label": "MEANINGFUL",
            "survival_rate": 0.3,
            "per_category": [
                {"category": "VALUE", "survived": 2},
                {"category": "BOUNDARY", "survived": 1},
            ],
        }
        assert (
            classify_from_func_data(func_data, mutation_entry) == GapClass.REAL_UNDERSPECIFICATION
        )

    def test_no_mutation_entry_low_spec(self):
        """No mutation data + low spec → unprofiled (unmeasured, not specified)."""
        func_data = {
            "specification_level": 0.1,
            "empirical_overlay": {"status": "NO_EMPIRICAL_DATA"},
            "regime": "A",
        }
        assert classify_from_func_data(func_data) == GapClass.UNPROFILED

    def test_surviving_categories_from_survivor_records(self):
        """Categories extracted from survivor_records take precedence."""
        func_data = {
            "specification_level": 0.1,
            "empirical_overlay": {"status": "AGREES"},
            "regime": "A",
        }
        mutation_entry = {
            "mutation_truth_label": "MEANINGFUL",
            "survival_rate": 0.5,
            "survivor_records": [
                {"category": "SWAP", "id": "SWAP_0"},
                {"category": "SWAP", "id": "SWAP_1"},
            ],
            "per_category": [
                {"category": "SWAP", "survived": 2},
            ],
        }
        # regime A + SWAP dominant → real_underspecification (not integration)
        assert (
            classify_from_func_data(func_data, mutation_entry) == GapClass.REAL_UNDERSPECIFICATION
        )


# ── GapClass enum values ────────────────────────────────────────


class TestGapClassEnum:
    def test_all_values_are_strings(self):
        for gc in GapClass:
            assert isinstance(gc.value, str)

    def test_exactly_seven_classes(self):
        assert len(GapClass) == 7

    def test_value_names(self):
        expected = {
            "specified",
            "unprofiled",
            "real_underspecification",
            "equivalent_or_low_value",
            "budget_instability",
            "integration_only",
            "discovery_failure",
        }
        assert {gc.value for gc in GapClass} == expected
