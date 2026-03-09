"""Tests for the prescription engine — taxonomy, phase selection, risk scheduling."""

from __future__ import annotations

from lintgate.specification.prescriptions import prescribe
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
