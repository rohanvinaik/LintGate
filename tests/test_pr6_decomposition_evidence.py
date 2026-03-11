"""PR6: Cross-lens decomposition evidence tests.

Validates that decomposition recommendations require multi-lens agreement
rather than mutation survival alone.
"""

from __future__ import annotations

from lintgate.specification.decomposition_evidence import (
    DecompositionRecommendation,
    DecompositionVerdict,
    LensSignal,
    evaluate_decomposition,
)

# ── Single-lens: mutation alone is not enough ────────────────────


class TestMutationAloneInsufficient:
    def test_two_categories_no_spec_data_keeps_testing(self):
        verdict = evaluate_decomposition(
            function_key="mod.py::f",
            surviving_categories=["VALUE", "SWAP"],
            mutation_cache_entry={"survival_rate": 0.6},
        )
        assert verdict.recommendation == DecompositionRecommendation.KEEP_TESTING
        assert len(verdict.supporting_lenses) == 1
        assert verdict.supporting_lenses[0].lens == "mutation"

    def test_single_category_no_signal(self):
        verdict = evaluate_decomposition(
            function_key="mod.py::f",
            surviving_categories=["VALUE"],
        )
        assert verdict.recommendation == DecompositionRecommendation.INSUFFICIENT_EVIDENCE
        assert len(verdict.supporting_lenses) == 0


# ── Multi-lens agreement → EXTRACT_BOUNDARY ──────────────────────


class TestMultiLensExtraction:
    def test_mutation_plus_spec_extracts(self):
        verdict = evaluate_decomposition(
            function_key="mod.py::f",
            surviving_categories=["VALUE", "SWAP", "STATE"],
            mutation_cache_entry={"survival_rate": 0.7},
            spec_data={"sigma": 20, "regime": "B", "specification_level": 0.2},
        )
        assert verdict.recommendation == DecompositionRecommendation.EXTRACT_BOUNDARY
        assert len(verdict.supporting_lenses) >= 2
        lens_names = [s.lens for s in verdict.supporting_lenses]
        assert "mutation" in lens_names
        assert "specification" in lens_names
        assert verdict.cross_lens_score >= 0.7

    def test_mutation_plus_composition_extracts(self):
        verdict = evaluate_decomposition(
            function_key="mod.py::f",
            surviving_categories=["VALUE", "BOUNDARY"],
            mutation_cache_entry={"survival_rate": 0.5},
            composition_gamma=5.0,
        )
        assert verdict.recommendation == DecompositionRecommendation.EXTRACT_BOUNDARY
        lens_names = [s.lens for s in verdict.supporting_lenses]
        assert "composition_gap" in lens_names

    def test_all_three_lenses_high_score(self):
        verdict = evaluate_decomposition(
            function_key="mod.py::f",
            surviving_categories=["VALUE", "SWAP", "BOUNDARY"],
            mutation_cache_entry={"survival_rate": 0.8},
            spec_data={"sigma": 25, "regime": "B", "specification_level": 0.1},
            composition_gamma=4.0,
        )
        assert verdict.recommendation == DecompositionRecommendation.EXTRACT_BOUNDARY
        assert len(verdict.supporting_lenses) == 3
        assert verdict.cross_lens_score > 0.85


# ── Topology-limited: prefer KEEP_TESTING ────────────────────────


class TestTopologyLimited:
    def test_mock_dominant_blocks_extraction(self):
        verdict = evaluate_decomposition(
            function_key="mod.py::f",
            surviving_categories=["VALUE", "SWAP", "STATE"],
            mutation_cache_entry={"survival_rate": 0.9},
            spec_data={"sigma": 20, "regime": "B", "specification_level": 0.2},
            topology_state="MOCK_BOUNDARY_DOMINANT",
        )
        assert verdict.recommendation == DecompositionRecommendation.KEEP_TESTING
        assert "mock" in verdict.rationale.lower()


# ── Specification lens ───────────────────────────────────────────


class TestSpecificationLens:
    def test_regime_b_low_spec_produces_signal(self):
        verdict = evaluate_decomposition(
            function_key="mod.py::f",
            surviving_categories=["VALUE", "SWAP"],
            spec_data={"sigma": 15, "regime": "B", "specification_level": 0.3},
        )
        spec_lenses = [s for s in verdict.supporting_lenses if s.lens == "specification"]
        assert len(spec_lenses) == 1

    def test_regime_a_no_spec_signal(self):
        verdict = evaluate_decomposition(
            function_key="mod.py::f",
            surviving_categories=["VALUE", "SWAP"],
            spec_data={"sigma": 5, "regime": "A", "specification_level": 0.8},
        )
        spec_lenses = [s for s in verdict.supporting_lenses if s.lens == "specification"]
        assert len(spec_lenses) == 0

    def test_high_sigma_low_spec_produces_signal(self):
        verdict = evaluate_decomposition(
            function_key="mod.py::f",
            surviving_categories=["VALUE", "SWAP"],
            spec_data={"sigma": 20, "regime": "A", "specification_level": 0.1},
        )
        spec_lenses = [s for s in verdict.supporting_lenses if s.lens == "specification"]
        assert len(spec_lenses) == 1


# ── Composition lens ─────────────────────────────────────────────


class TestCompositionLens:
    def test_high_gamma_produces_signal(self):
        verdict = evaluate_decomposition(
            function_key="mod.py::f",
            surviving_categories=["VALUE", "SWAP"],
            composition_gamma=4.0,
        )
        comp_lenses = [s for s in verdict.supporting_lenses if s.lens == "composition_gap"]
        assert len(comp_lenses) == 1

    def test_low_gamma_no_signal(self):
        verdict = evaluate_decomposition(
            function_key="mod.py::f",
            surviving_categories=["VALUE", "SWAP"],
            composition_gamma=1.0,
        )
        comp_lenses = [s for s in verdict.supporting_lenses if s.lens == "composition_gap"]
        assert len(comp_lenses) == 0


# ── Verdict output contract ──────────────────────────────────────


class TestVerdictToDict:
    def test_basic_fields(self):
        verdict = DecompositionVerdict(
            function_key="f",
            recommendation=DecompositionRecommendation.KEEP_TESTING,
            cross_lens_score=0.45,
            rationale="Keep testing.",
        )
        d = verdict.to_dict()
        assert d["recommendation"] == "KEEP_TESTING"
        assert d["cross_lens_score"] == 0.45
        assert d["rationale"] == "Keep testing."
        assert "responsibility_boundary" not in d
        assert "expected_benefits" not in d

    def test_extract_includes_boundary_and_benefits(self):
        verdict = DecompositionVerdict(
            function_key="f",
            recommendation=DecompositionRecommendation.EXTRACT_BOUNDARY,
            supporting_lenses=[
                LensSignal("mutation", 0.8, "3 cats"),
                LensSignal("specification", 0.6, "regime B"),
            ],
            cross_lens_score=0.92,
            responsibility_boundary="Separate computation from state",
            expected_benefits=["reduced_coupling", "better_local_testability"],
            rationale="Extract.",
        )
        d = verdict.to_dict()
        assert d["recommendation"] == "EXTRACT_BOUNDARY"
        assert d["responsibility_boundary"] == "Separate computation from state"
        assert "reduced_coupling" in d["expected_benefits"]
        assert len(d["supporting_lenses"]) == 2


# ── Boundary inference ───────────────────────────────────────────


class TestBoundaryInference:
    def test_value_and_state_boundary(self):
        verdict = evaluate_decomposition(
            function_key="f",
            surviving_categories=["VALUE", "STATE"],
            mutation_cache_entry={"survival_rate": 0.6},
            spec_data={"sigma": 15, "regime": "B", "specification_level": 0.2},
        )
        if verdict.recommendation == DecompositionRecommendation.EXTRACT_BOUNDARY:
            assert "pure computation" in verdict.responsibility_boundary.lower() or \
                   "stateful" in verdict.responsibility_boundary.lower()

    def test_swap_and_boundary_boundary(self):
        verdict = evaluate_decomposition(
            function_key="f",
            surviving_categories=["SWAP", "BOUNDARY"],
            mutation_cache_entry={"survival_rate": 0.6},
            spec_data={"sigma": 15, "regime": "B", "specification_level": 0.2},
        )
        if verdict.recommendation == DecompositionRecommendation.EXTRACT_BOUNDARY:
            assert "parameter" in verdict.responsibility_boundary.lower() or \
                   "boundary" in verdict.responsibility_boundary.lower()


# ── Benefits inference ───────────────────────────────────────────


class TestBenefitsInference:
    def test_extract_has_benefits(self):
        verdict = evaluate_decomposition(
            function_key="f",
            surviving_categories=["VALUE", "SWAP", "STATE"],
            mutation_cache_entry={"survival_rate": 0.8},
            spec_data={"sigma": 25, "regime": "B", "specification_level": 0.1},
        )
        assert verdict.recommendation == DecompositionRecommendation.EXTRACT_BOUNDARY
        assert len(verdict.expected_benefits) >= 2
        assert "better_local_testability" in verdict.expected_benefits
