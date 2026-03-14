"""Tests for the prescription engine — taxonomy, phase selection, risk scheduling."""

from __future__ import annotations

import math

import pytest

from lintgate.specification.prescriptions import (
    TestPrescription,
    _gen_boundary,
    _gen_cause_effect,
    _gen_decision_table,
    _gen_equivalence,
    _gen_exact_value,
    _gen_property,
    _gen_regression,
    _info_gain,
    _phase_generators,
    _short_name,
    prescribe,
)
from lintgate.specification.types import (
    FunctionSpecification,
    RiskProfile,
    SpecCore,
    TestDesignSignals,
    Traceability,
)


def _make_spec(
    phase: str = "bulk",
    sigma: int = 5,
    is_pure: bool = False,
    priority_band: str = "P2",
    boundary_points: int = 0,
    equivalence_partitions: int = 0,
    decision_rule_count: int = 0,
    predicate_effect_links: int = 0,
    covering_tests: list[str] | None = None,
    prescription_history: list[str] | None = None,
) -> FunctionSpecification:
    return FunctionSpecification(
        function_key="mod::func",
        core=SpecCore(
            estimated_sigma=sigma,
            phase=phase,
            is_pure=is_pure,
        ),
        design_signals=TestDesignSignals(
            boundary_points=boundary_points,
            equivalence_partitions=equivalence_partitions,
            decision_rule_count=decision_rule_count,
            predicate_effect_links=predicate_effect_links,
        ),
        risk=RiskProfile(priority_band=priority_band),
        traceability=Traceability(
            covering_tests=covering_tests or [],
            prescription_history=prescription_history or [],
        ),
    )


class TestPhaseAwarePrescriptions:
    def test_bulk_phase_exact_value(self):
        spec = _make_spec(phase="bulk", sigma=5)
        rxs = prescribe(spec)
        kinds = [r.prescription_kind for r in rxs]
        assert "exact_value" in kinds

    def test_bulk_phase_equivalence(self):
        spec = _make_spec(phase="bulk", equivalence_partitions=3)
        rxs = prescribe(spec)
        kinds = [r.prescription_kind for r in rxs]
        assert "equivalence" in kinds

    def test_transition_phase_boundary(self):
        spec = _make_spec(phase="transition", boundary_points=4)
        rxs = prescribe(spec)
        kinds = [r.prescription_kind for r in rxs]
        assert "boundary" in kinds

    def test_transition_phase_cause_effect(self):
        spec = _make_spec(phase="transition", predicate_effect_links=3)
        rxs = prescribe(spec)
        kinds = [r.prescription_kind for r in rxs]
        assert "cause_effect" in kinds

    def test_transition_phase_decision_table(self):
        spec = _make_spec(phase="transition", decision_rule_count=8)
        rxs = prescribe(spec)
        kinds = [r.prescription_kind for r in rxs]
        assert "decision_table" in kinds

    def test_tail_phase_property(self):
        spec = _make_spec(phase="tail", is_pure=True)
        rxs = prescribe(spec)
        kinds = [r.prescription_kind for r in rxs]
        assert "property" in kinds

    def test_complete_phase_no_prescriptions(self):
        spec = _make_spec(phase="complete")
        rxs = prescribe(spec)
        assert len(rxs) == 0


class TestRegressionMode:
    def test_regression_always_prescribes(self):
        spec = _make_spec(phase="complete")
        rxs = prescribe(spec, regression_mode=True)
        assert len(rxs) > 0
        assert rxs[0].prescription_kind == "regression"
        assert rxs[0].regression_relevant is True


class TestRiskPrioritization:
    def test_p0_first(self):
        spec_p0 = _make_spec(phase="bulk", sigma=10, priority_band="P0")
        spec_p2 = _make_spec(phase="bulk", sigma=10, priority_band="P2")
        rxs_p0 = prescribe(spec_p0)
        rxs_p2 = prescribe(spec_p2)
        if rxs_p0 and rxs_p2:
            assert rxs_p0[0].priority_band == "P0"
            assert rxs_p2[0].priority_band == "P2"


class TestPrescriptionHistory:
    def test_skips_already_prescribed(self):
        spec = _make_spec(
            phase="bulk",
            sigma=5,
            prescription_history=["exact_value", "equivalence"],
            equivalence_partitions=3,
        )
        rxs = prescribe(spec)
        kinds = [r.prescription_kind for r in rxs]
        assert "exact_value" not in kinds
        assert "equivalence" not in kinds


class TestMaxPrescriptions:
    def test_respects_max(self):
        spec = _make_spec(
            phase="transition",
            boundary_points=5,
            predicate_effect_links=5,
            decision_rule_count=10,
        )
        rxs = prescribe(spec, max_prescriptions=2)
        assert len(rxs) <= 2


class TestOrthogonalArrayHinting:
    def test_large_decision_table_oa_hint(self):
        spec = _make_spec(phase="transition", decision_rule_count=32)
        rxs = prescribe(spec)
        dt_rxs = [r for r in rxs if r.prescription_kind == "decision_table"]
        if dt_rxs:
            assert (
                "pairwise" in dt_rxs[0].description.lower()
                or "covering" in dt_rxs[0].description.lower()
            )


class TestAssertionCountFix:
    """Verify prescriptions use assertion_count, not len(covering_tests)."""

    def test_assertion_count_closes_gap(self):
        """When assertion_count >= sigma, no exact_value prescription is generated."""
        spec = _make_spec(
            phase="bulk",
            sigma=3,
            covering_tests=["test_a"],  # Only 1 covering test
        )
        # Override assertion_count to match sigma (gap = 0)
        spec.traceability.assertion_count = 3
        rxs = prescribe(spec)
        kinds = [r.prescription_kind for r in rxs]
        assert "exact_value" not in kinds

    def test_covering_tests_alone_do_not_close_gap(self):
        """covering_tests count is NOT used for gap computation."""
        spec = _make_spec(
            phase="bulk",
            sigma=3,
            covering_tests=["test_a", "test_b", "test_c", "test_d", "test_e"],
        )
        # assertion_count defaults to 0, so gap = sigma - 0 = 3
        rxs = prescribe(spec)
        kinds = [r.prescription_kind for r in rxs]
        assert "exact_value" in kinds


# ── Unit tests for individual generator functions ──────────────────


def _make_fs(
    *,
    key: str = "mod.py::func",
    sigma: int = 5,
    assertion_count: int = 2,
    phase: str = "bulk",
    is_pure: bool = False,
    boundary_points: int = 3,
    equivalence_partitions: int = 4,
    decision_rule_count: int = 0,
    predicate_effect_links: int = 2,
    priority_band: str = "P1",
    prescription_history: list[str] | None = None,
) -> FunctionSpecification:
    return FunctionSpecification(
        function_key=key,
        core=SpecCore(
            estimated_sigma=sigma,
            phase=phase,
            is_pure=is_pure,
        ),
        design_signals=TestDesignSignals(
            boundary_points=boundary_points,
            equivalence_partitions=equivalence_partitions,
            decision_rule_count=decision_rule_count,
            predicate_effect_links=predicate_effect_links,
        ),
        risk=RiskProfile(priority_band=priority_band),
        traceability=Traceability(
            assertion_count=assertion_count,
            prescription_history=prescription_history or [],
        ),
    )


class TestShortName:
    def test_qualified_key(self):
        assert _short_name("mod.py::func") == "func"

    def test_no_separator(self):
        assert _short_name("func") == "func"

    def test_empty(self):
        assert _short_name("") == ""

    def test_nested(self):
        assert _short_name("a::b::c") == "c"

    def test_single_colon_not_split(self):
        """Ensure only '::' splits, not ':'."""
        assert _short_name("a:b") == "a:b"

    def test_returns_last_segment(self):
        """Ensure it's the last segment, not first."""
        assert _short_name("first::second") == "second"
        assert _short_name("first::second") != "first"


class TestInfoGain:
    def test_sigma_zero(self):
        assert _info_gain(0) == 1.0

    def test_sigma_one(self):
        assert _info_gain(1) == 1.0

    def test_sigma_two(self):
        assert _info_gain(2) == pytest.approx(math.log2(2 / 1))

    def test_sigma_ten(self):
        assert _info_gain(10) == pytest.approx(math.log2(10 / 9))

    def test_decreasing(self):
        assert _info_gain(5) > _info_gain(10)


class TestPhaseGenerators:
    def test_bulk(self):
        gens = _phase_generators("bulk", False)
        assert gens == [_gen_exact_value, _gen_equivalence]

    def test_transition(self):
        gens = _phase_generators("transition", False)
        assert gens == [_gen_boundary, _gen_cause_effect, _gen_decision_table]

    def test_tail(self):
        gens = _phase_generators("tail", False)
        assert gens == [_gen_property, _gen_cause_effect]

    def test_complete(self):
        assert _phase_generators("complete", False) == []

    def test_unknown(self):
        assert _phase_generators("unknown", False) == []

    def test_regression_mode(self):
        gens = _phase_generators("bulk", True)
        assert gens == [_gen_regression]


class TestGenExactValue:
    def test_produces_prescription(self):
        fs = _make_fs(sigma=5, assertion_count=2, priority_band="P1")
        out: list[TestPrescription] = []
        _gen_exact_value(fs, 5, 2, set(), out)
        assert len(out) == 1
        p = out[0]
        assert p.prescription_kind == "exact_value"
        assert p.function_key == "mod.py::func"
        assert p.priority == 1
        assert p.priority_band == "P1"
        assert p.uncovered_dimension == "value correctness"
        assert p.suggested_assertion == "assert func(...) == expected"
        assert "gap: 3" in p.description
        assert p.estimated_info_gain == pytest.approx(_info_gain(5) * 2)
        assert p.targets_count is None
        assert p.regression_relevant is False

    def test_gap_uses_sigma_minus_assertion(self):
        """SWAP-killer: verify gap = sigma - assertion_count, not reversed."""
        fs = _make_fs(sigma=10, assertion_count=3)
        out: list[TestPrescription] = []
        _gen_exact_value(fs, 10, 3, set(), out)
        assert "gap: 7" in out[0].description

    def test_skips_when_in_history(self):
        out: list[TestPrescription] = []
        _gen_exact_value(_make_fs(), 5, 2, {"exact_value"}, out)
        assert out == []

    def test_skips_when_no_gap(self):
        out: list[TestPrescription] = []
        _gen_exact_value(_make_fs(sigma=3, assertion_count=5), 3, 5, set(), out)
        assert out == []


class TestGenEquivalence:
    def test_produces_prescription(self):
        fs = _make_fs(equivalence_partitions=4, priority_band="P2")
        out: list[TestPrescription] = []
        _gen_equivalence(fs, 5, 2, set(), out)
        assert len(out) == 1
        p = out[0]
        assert p.prescription_kind == "equivalence"
        assert p.function_key == "mod.py::func"
        assert p.priority == 2
        assert p.priority_band == "P2"
        assert p.targets_count == 4
        assert p.uncovered_dimension == "input class coverage"
        assert p.suggested_assertion == "Test one representative from each input partition"
        assert p.estimated_info_gain == pytest.approx(_info_gain(5))
        assert "4 equivalence partitions" in p.description

    def test_skips_zero_partitions(self):
        out: list[TestPrescription] = []
        _gen_equivalence(_make_fs(equivalence_partitions=0), 5, 2, set(), out)
        assert out == []

    def test_skips_when_in_history(self):
        out: list[TestPrescription] = []
        _gen_equivalence(_make_fs(), 5, 2, {"equivalence"}, out)
        assert out == []


class TestGenBoundary:
    def test_produces_prescription(self):
        fs = _make_fs(boundary_points=3, priority_band="P0")
        out: list[TestPrescription] = []
        _gen_boundary(fs, 5, 2, set(), out)
        assert len(out) == 1
        p = out[0]
        assert p.prescription_kind == "boundary"
        assert p.function_key == "mod.py::func"
        assert p.priority == 1
        assert p.priority_band == "P0"
        assert p.targets_count == 3
        assert p.uncovered_dimension == "boundary behavior"
        assert p.suggested_assertion == "Test at boundary-1, boundary, boundary+1"
        assert p.estimated_info_gain == pytest.approx(_info_gain(5) * 1.5)
        assert "3 boundary points" in p.description
        assert "func" in p.description

    def test_skips_zero_bp(self):
        out: list[TestPrescription] = []
        _gen_boundary(_make_fs(boundary_points=0), 5, 2, set(), out)
        assert out == []

    def test_skips_when_in_history(self):
        out: list[TestPrescription] = []
        _gen_boundary(_make_fs(), 5, 2, {"boundary"}, out)
        assert out == []


class TestGenCauseEffect:
    def test_produces_prescription(self):
        fs = _make_fs(predicate_effect_links=5, priority_band="P1")
        out: list[TestPrescription] = []
        _gen_cause_effect(fs, 5, 2, set(), out)
        assert len(out) == 1
        p = out[0]
        assert p.prescription_kind == "cause_effect"
        assert p.function_key == "mod.py::func"
        assert p.priority == 2
        assert p.priority_band == "P1"
        assert p.uncovered_dimension == "predicate-effect coverage"
        assert p.suggested_assertion == "For each predicate, verify the expected effect"
        assert p.estimated_info_gain == pytest.approx(_info_gain(5))
        assert "5 predicate" in p.description
        assert p.targets_count is None

    def test_skips_zero_links(self):
        out: list[TestPrescription] = []
        _gen_cause_effect(_make_fs(predicate_effect_links=0), 5, 2, set(), out)
        assert out == []

    def test_skips_when_in_history(self):
        out: list[TestPrescription] = []
        _gen_cause_effect(_make_fs(), 5, 2, {"cause_effect"}, out)
        assert out == []


class TestGenDecisionTable:
    def test_small_table(self):
        fs = _make_fs(decision_rule_count=8)
        out: list[TestPrescription] = []
        _gen_decision_table(fs, 5, 2, set(), out)
        assert len(out) == 1
        assert out[0].prescription_kind == "decision_table"
        assert "8 decision rules" in out[0].description
        assert out[0].suggested_assertion == "Test 8 condition combinations"

    def test_large_table_uses_covering_array(self):
        fs = _make_fs(decision_rule_count=20)
        out: list[TestPrescription] = []
        _gen_decision_table(fs, 5, 2, set(), out)
        assert len(out) == 1
        covering = math.ceil(math.log2(20)) + 1
        assert f"pairwise covering array requires {covering}" in out[0].description
        assert out[0].suggested_assertion == f"Test {covering} condition combinations"

    def test_skips_four_or_fewer(self):
        out: list[TestPrescription] = []
        _gen_decision_table(_make_fs(decision_rule_count=4), 5, 2, set(), out)
        assert out == []


class TestGenProperty:
    def test_produces_for_pure(self):
        fs = _make_fs(is_pure=True)
        out: list[TestPrescription] = []
        _gen_property(fs, 5, 2, set(), out)
        assert len(out) == 1
        assert out[0].prescription_kind == "property"
        assert "Hypothesis" in out[0].description

    def test_skips_impure(self):
        out: list[TestPrescription] = []
        _gen_property(_make_fs(is_pure=False), 5, 2, set(), out)
        assert out == []


class TestGenRegression:
    def test_always_produces(self):
        fs = _make_fs()
        out: list[TestPrescription] = []
        _gen_regression(fs, 5, 2, set(), out)
        assert len(out) == 1
        assert out[0].prescription_kind == "regression"
        assert out[0].regression_relevant is True
        assert out[0].priority == 0
