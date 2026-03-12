"""Minimal mutation-killing tests for convergence aggregator.

Covers all 24 functions: core engine, function-level adapters, and
file-level adapters. Each test exists because it kills at least one
unique mutant or covers a distinct behavioral path.
"""

from __future__ import annotations

from dataclasses import dataclass

from lintgate.convergence.aggregator import (
    _apply_weight,
    _probability_union,
    adapt_algebraic,
    adapt_assertion_quality,
    adapt_call_graph,
    adapt_cochange,
    adapt_cochange_file,
    adapt_cohesion,
    adapt_cohesion_file,
    adapt_composition_gap,
    adapt_contract_coverage,
    adapt_cross_channel,
    adapt_dep_clustering,
    adapt_fan_in,
    adapt_fan_in_file,
    adapt_import_tracing,
    adapt_import_weight_file,
    adapt_mutation,
    adapt_purity,
    adapt_specification,
    aggregate,
    aggregate_file,
    classify_actionability,
    classify_file_actionability,
)
from lintgate.convergence.evidence import (
    Actionability,
    LensEvidence,
    LensKind,
)

# ── Core engine ──────────────────────────────────────────────────────


class TestProbabilityUnion:
    def test_empty_returns_zero(self) -> None:
        assert _probability_union([]) == 0.0

    def test_two_values(self) -> None:
        # 1 - (1-0.6)(1-0.6) = 0.84
        assert abs(_probability_union([0.6, 0.6]) - 0.84) < 1e-10

    def test_mixed_values(self) -> None:
        # 1 - 0.7*0.5*0.2 = 0.93
        assert abs(_probability_union([0.3, 0.5, 0.8]) - 0.93) < 1e-10


class TestClassifyActionability:
    def test_extract_exact_boundary(self) -> None:
        assert classify_actionability(0.75, 3) == Actionability.EXTRACT

    def test_net_below_extract(self) -> None:
        assert classify_actionability(0.74, 3) == Actionability.SPLIT

    def test_count_below_extract(self) -> None:
        assert classify_actionability(0.75, 2) == Actionability.SPLIT

    def test_split_exact_boundary(self) -> None:
        assert classify_actionability(0.5, 2) == Actionability.SPLIT

    def test_net_below_split(self) -> None:
        assert classify_actionability(0.49, 2) == Actionability.INVESTIGATE

    def test_count_below_split(self) -> None:
        assert classify_actionability(0.5, 1) == Actionability.INVESTIGATE


class TestClassifyFileActionability:
    def test_default_investigate(self) -> None:
        assert classify_file_actionability(0.3, 1) == Actionability.INVESTIGATE

    def test_split_high_net(self) -> None:
        assert classify_file_actionability(0.75, 3) == Actionability.SPLIT

    def test_cohesion_driven_split(self) -> None:
        assert (
            classify_file_actionability(0.5, 2, {"score": 0.2, "component_count": 4})
            == Actionability.SPLIT
        )

    def test_cohesion_score_boundary(self) -> None:
        # score=0.3 not < 0.3 → cohesion path skipped
        assert (
            classify_file_actionability(0.5, 2, {"score": 0.3, "component_count": 4})
            == Actionability.INVESTIGATE
        )

    def test_cohesion_components_boundary(self) -> None:
        # components=2 < 3 → cohesion path skipped
        assert (
            classify_file_actionability(0.5, 2, {"score": 0.2, "component_count": 2})
            == Actionability.INVESTIGATE
        )


class TestApplyWeight:
    def test_capped(self) -> None:
        assert _apply_weight(0.8, 2.0) == 1.0

    def test_uncapped(self) -> None:
        assert abs(_apply_weight(0.3, 1.5) - 0.45) < 1e-10

    def test_zero(self) -> None:
        assert _apply_weight(0.0, 2.0) == 0.0


class TestAggregate:
    def test_empty(self) -> None:
        assert aggregate([]) == []

    def test_single_support(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.PURITY, target="f", confidence=0.8, signal="support", detail=""
            )
        ]
        r = aggregate(ev)
        assert len(r) == 1
        assert r[0].target == "f"
        assert r[0].support_prob == 0.8
        assert r[0].oppose_prob == 0.0

    def test_support_minus_oppose(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.PURITY, target="f", confidence=0.8, signal="support", detail=""
            ),
            LensEvidence(
                lens=LensKind.COHESION, target="f", confidence=0.6, signal="oppose", detail=""
            ),
        ]
        r = aggregate(ev)
        assert r[0].net_confidence == max(0.8 - 0.6, 0.0)

    def test_multi_lens_extract(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.PURITY, target="f", confidence=0.6, signal="support", detail=""
            ),
            LensEvidence(
                lens=LensKind.MUTATION, target="f", confidence=0.6, signal="support", detail=""
            ),
            LensEvidence(
                lens=LensKind.COHESION, target="f", confidence=0.6, signal="support", detail=""
            ),
        ]
        r = aggregate(ev)
        assert len(r[0].supporting_lenses) == 3
        assert r[0].actionability == Actionability.EXTRACT


# ── Function-level adapters (already covered) ────────────────────────


class TestAdaptPurity:
    def test_list_format(self) -> None:
        r = adapt_purity([{"name": "fn", "confidence": 0.9, "hints": ["cacheable"]}])
        assert r[0].target == "fn" and r[0].confidence == 0.9 and r[0].lens == LensKind.PURITY

    def test_list_skips_empty_name(self) -> None:
        assert adapt_purity([{"name": "", "confidence": 0.9}]) == []

    def test_dict_format(self) -> None:
        r = adapt_purity({"fn": {"file": "a.py", "confidence": 0.7}})
        assert r[0].target == "fn"

    def test_empty(self) -> None:
        assert adapt_purity([]) == [] and adapt_purity({}) == []


class TestAdaptMutation:
    def test_high_survival(self) -> None:
        r = adapt_mutation({"f": {"survival_rate": 0.8}})
        assert r[0].signal == "support"

    def test_boundary_included(self) -> None:
        r = adapt_mutation({"f": {"survival_rate": 0.3}})
        assert r[0].confidence == 0.3

    def test_below_boundary_skipped(self) -> None:
        assert adapt_mutation({"f": {"survival_rate": 0.29}}) == []


class TestAdaptAlgebraic:
    def test_safe_supports(self) -> None:
        r = adapt_algebraic({"f": {"extraction_safety": "safe", "properties": ["comm"]}})
        assert r[0].signal == "support"

    def test_unsafe_opposes(self) -> None:
        r = adapt_algebraic({"f": {"extraction_safety": "unsafe", "reason": "io"}})
        assert r[0].signal == "oppose"

    def test_unknown_skipped(self) -> None:
        assert adapt_algebraic({"f": {"extraction_safety": "unknown"}}) == []


class TestAdaptCallGraph:
    def test_high_fan_out(self) -> None:
        r = adapt_call_graph({"f": {"fan_out": 10}})
        assert r[0].signal == "support"

    def test_below_threshold_skipped(self) -> None:
        assert adapt_call_graph({"f": {"fan_out": 3}}) == []

    def test_custom_threshold(self) -> None:
        assert len(adapt_call_graph({"f": {"fan_out": 5}}, threshold=5)) == 1
        assert adapt_call_graph({"f": {"fan_out": 5}}, threshold=6) == []


class TestAdaptImportTracing:
    def test_io_opposes(self) -> None:
        r = adapt_import_tracing({"m": {"has_module_level_io": True, "depth": 1}})
        assert r[0].signal == "oppose"

    def test_shallow_supports(self) -> None:
        r = adapt_import_tracing(
            {"m": {"has_module_level_io": False, "depth": 1, "non_stdlib_deps": 2}}
        )
        assert r[0].signal == "support"

    def test_deep_skipped(self) -> None:
        assert (
            adapt_import_tracing(
                {"m": {"has_module_level_io": False, "depth": 5, "non_stdlib_deps": 10}}
            )
            == []
        )


# ── Function-level adapters (previously untested) ────────────────────


class TestAdaptSpecification:
    def test_regime_b_low_spec_supports(self) -> None:
        r = adapt_specification({"f": {"regime": "B", "spec_level": 0.2}})
        assert r[0].signal == "support"
        assert abs(r[0].confidence - 0.8) < 1e-10  # 1.0 - 0.2

    def test_high_spec_opposes(self) -> None:
        r = adapt_specification({"f": {"regime": "A", "spec_level": 0.8}})
        assert r[0].signal == "oppose"
        assert r[0].confidence == 0.8

    def test_regime_a_low_spec_skipped(self) -> None:
        # Regime A + low spec → no evidence (only B triggers support)
        assert adapt_specification({"f": {"regime": "A", "spec_level": 0.3}}) == []

    def test_regime_b_high_spec_opposes(self) -> None:
        # Regime B but spec >= 0.7 → oppose (well-specified)
        r = adapt_specification({"f": {"regime": "B", "spec_level": 0.7}})
        assert r[0].signal == "oppose"

    def test_empty(self) -> None:
        assert adapt_specification({}) == []


class TestAdaptCompositionGap:
    def test_positive_gamma_supports(self) -> None:
        r = adapt_composition_gap({"a→b": {"gamma": 3.0}})
        assert r[0].signal == "support"
        assert abs(r[0].confidence - 0.6) < 1e-10  # 3.0 / 5.0

    def test_zero_gamma_skipped(self) -> None:
        assert adapt_composition_gap({"a→b": {"gamma": 0.0}}) == []

    def test_large_gamma_caps_confidence(self) -> None:
        r = adapt_composition_gap({"a→b": {"gamma": 10.0}})
        assert r[0].confidence == 1.0  # min(10/5, 1.0)

    def test_empty(self) -> None:
        assert adapt_composition_gap({}) == []


class TestAdaptCohesion:
    def test_low_score_supports(self) -> None:
        r = adapt_cohesion({"f.py": {"score": 0.3, "component_count": 1}})
        assert r[0].signal == "support"

    def test_multi_component_supports(self) -> None:
        r = adapt_cohesion({"f.py": {"score": 0.6, "component_count": 2}})
        assert r[0].signal == "support"

    def test_high_score_single_component_skipped(self) -> None:
        assert adapt_cohesion({"f.py": {"score": 0.5, "component_count": 1}}) == []

    def test_confidence_formula(self) -> None:
        r = adapt_cohesion({"f.py": {"score": 0.3, "component_count": 2}})
        assert abs(r[0].confidence - 0.7) < 1e-10  # max(1.0 - 0.3, 0.1)


class TestAdaptFanIn:
    def test_high_fan_in_opposes(self) -> None:
        r = adapt_fan_in({"m": 8})
        assert r[0].signal == "oppose"
        assert abs(r[0].confidence - 0.8) < 1e-10  # 8 / 10

    def test_zero_fan_in_supports(self) -> None:
        r = adapt_fan_in({"m": 0})
        assert r[0].signal == "support"
        assert r[0].confidence == 0.4

    def test_middle_fan_in_skipped(self) -> None:
        assert adapt_fan_in({"m": 3}) == []

    def test_empty(self) -> None:
        assert adapt_fan_in({}) == []


class TestAdaptCochange:
    def test_high_coupling_opposes(self) -> None:
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.6}]}
        r = adapt_cochange(data)
        assert r[0].signal == "oppose"
        assert r[0].target == "a.py<->b.py"

    def test_low_coupling_supports(self) -> None:
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.2}]}
        r = adapt_cochange(data)
        assert r[0].signal == "support"

    def test_boundary_coupling(self) -> None:
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.4}]}
        r = adapt_cochange(data)
        assert r[0].signal == "oppose"  # >= threshold

    def test_empty(self) -> None:
        assert adapt_cochange({}) == []


class TestAdaptDepClustering:
    def test_dict_prescription(self) -> None:
        r = adapt_dep_clustering([{"target": "block_a", "confidence": 0.7, "action": "extract"}])
        assert r[0].target == "block_a"
        assert r[0].confidence == 0.7
        assert r[0].lens == LensKind.DEP_CLUSTERING

    def test_object_prescription(self) -> None:
        @dataclass
        class FakePrescription:
            target: str = "block_b"
            confidence: float = 0.6
            action: str = "inline"

        r = adapt_dep_clustering([FakePrescription()])
        assert r[0].target == "block_b"
        assert r[0].confidence == 0.6

    def test_empty(self) -> None:
        assert adapt_dep_clustering([]) == []
        assert adapt_dep_clustering(None) == []


class TestAdaptAssertionQuality:
    def test_low_score_supports(self) -> None:
        r = adapt_assertion_quality(
            {"f": {"effectiveness_score": 0.3, "weakness_taxonomy": ["weak"]}}
        )
        assert r[0].signal == "support"
        assert abs(r[0].confidence - 0.7) < 1e-10

    def test_high_score_no_weakness_skipped(self) -> None:
        assert (
            adapt_assertion_quality({"f": {"effectiveness_score": 0.9, "weakness_taxonomy": []}})
            == []
        )

    def test_high_score_with_weakness_supports(self) -> None:
        r = adapt_assertion_quality(
            {"f": {"effectiveness_score": 0.9, "weakness_taxonomy": ["partial"]}}
        )
        assert r[0].signal == "support"

    def test_empty(self) -> None:
        assert adapt_assertion_quality({}) == []


class TestAdaptContractCoverage:
    def test_published_only_supports(self) -> None:
        published = {"f": {"channel": "perf", "metric_key": "purity"}}
        consumed = {}
        r = adapt_contract_coverage(published, consumed)
        assert r[0].signal == "support"
        assert r[0].target == "f"
        assert r[0].lens == LensKind.CONTRACT_COVERAGE

    def test_consumed_targets_excluded(self) -> None:
        published = {"f": {"channel": "perf"}}
        consumed = {"f": {"channel": "spec"}}
        assert adapt_contract_coverage(published, consumed) == []

    def test_empty(self) -> None:
        assert adapt_contract_coverage({}, {}) == []


class TestAdaptCrossChannel:
    def test_findings_produce_evidence(self) -> None:
        @dataclass
        class FakeFinding:
            file: str = "a.py"
            kind: str = "COH001"
            message: str = "drift detected"

        r = adapt_cross_channel([FakeFinding()])
        assert r[0].target == "a.py"
        assert r[0].lens == LensKind.CROSS_CHANNEL
        assert r[0].signal == "support"

    def test_empty(self) -> None:
        assert adapt_cross_channel([]) == []
        assert adapt_cross_channel(None) == []


# ── File-level adapters ──────────────────────────────────────────────


class TestAdaptCohesionFile:
    def test_low_score_supports(self) -> None:
        r = adapt_cohesion_file({"f.py": {"score": 0.3, "component_count": 2}})
        assert r[0].signal == "support"
        assert abs(r[0].confidence - 0.7) < 1e-10  # max(1.0 - 0.3, 0.1)
        assert r[0].lens == LensKind.COHESION

    def test_high_score_single_component_skipped(self) -> None:
        assert adapt_cohesion_file({"f.py": {"score": 0.5, "component_count": 1}}) == []

    def test_boundary_score(self) -> None:
        # score=0.49 < 0.5 → not skipped (score >= 0.5 check fails)
        r = adapt_cohesion_file({"f.py": {"score": 0.49, "component_count": 1}})
        assert len(r) == 1

    def test_boundary_components(self) -> None:
        # components=2 > 1 → not skipped
        r = adapt_cohesion_file({"f.py": {"score": 0.6, "component_count": 2}})
        assert len(r) == 1


class TestAdaptFanInFile:
    def test_high_opposes(self) -> None:
        r = adapt_fan_in_file({"m": 8})
        assert r[0].signal == "oppose"

    def test_low_supports(self) -> None:
        r = adapt_fan_in_file({"m": 1})
        assert r[0].signal == "support"

    def test_middle_skipped(self) -> None:
        assert adapt_fan_in_file({"m": 3}) == []


class TestAdaptCochangeFile:
    def test_matching_filepath_opposes(self) -> None:
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.6}]}
        r = adapt_cochange_file(data, "a.py")
        assert r[0].signal == "oppose"
        assert r[0].target == "a.py"

    def test_non_matching_filepath_skipped(self) -> None:
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.6}]}
        assert adapt_cochange_file(data, "c.py") == []

    def test_below_threshold_skipped(self) -> None:
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.2}]}
        assert adapt_cochange_file(data, "a.py") == []


class TestAdaptImportWeightFile:
    def test_io_opposes(self) -> None:
        r = adapt_import_weight_file({"m": {"has_module_level_io": True, "depth": 1}})
        assert r[0].signal == "oppose"
        assert r[0].confidence == 0.6
        assert r[0].lens == LensKind.IMPORT_TRACING

    def test_shallow_supports(self) -> None:
        r = adapt_import_weight_file(
            {"m": {"has_module_level_io": False, "depth": 1, "non_stdlib_deps": 2}}
        )
        assert r[0].signal == "support"
        assert r[0].confidence == 0.4

    def test_deep_skipped(self) -> None:
        assert (
            adapt_import_weight_file(
                {"m": {"has_module_level_io": False, "depth": 5, "non_stdlib_deps": 10}}
            )
            == []
        )

    def test_boundary_depth(self) -> None:
        # depth=2 <= 2 and non_stdlib=3 <= 3 → support
        r = adapt_import_weight_file(
            {"m": {"has_module_level_io": False, "depth": 2, "non_stdlib_deps": 3}}
        )
        assert r[0].signal == "support"

    def test_boundary_depth_over(self) -> None:
        # depth=3 > 2 → skipped
        assert (
            adapt_import_weight_file(
                {"m": {"has_module_level_io": False, "depth": 3, "non_stdlib_deps": 2}}
            )
            == []
        )


class TestAggregateFile:
    def test_empty(self) -> None:
        assert aggregate_file([]) == []

    def test_weighted_aggregation(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.COHESION, target="f.py", confidence=0.4, signal="support", detail=""
            ),
            LensEvidence(
                lens=LensKind.FAN_IN, target="f.py", confidence=0.4, signal="support", detail=""
            ),
        ]
        r = aggregate_file(ev)
        assert len(r) == 1
        assert r[0].target_type == "file"
        # COHESION weight=2.0, FAN_IN weight=1.5
        # weighted: 0.8, 0.6 → union = 1 - 0.2*0.4 = 0.92
        assert r[0].support_prob > 0.8

    def test_with_cohesion_map(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.COHESION, target="f.py", confidence=0.5, signal="support", detail=""
            ),
            LensEvidence(
                lens=LensKind.FAN_IN, target="f.py", confidence=0.5, signal="support", detail=""
            ),
        ]
        cohesion = {"f.py": {"score": 0.2, "component_count": 4}}
        r = aggregate_file(ev, cohesion_map=cohesion)
        assert r[0].actionability == Actionability.SPLIT

    def test_sorted_descending(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.COHESION, target="low.py", confidence=0.2, signal="support", detail=""
            ),
            LensEvidence(
                lens=LensKind.COHESION,
                target="high.py",
                confidence=0.9,
                signal="support",
                detail="",
            ),
        ]
        r = aggregate_file(ev)
        assert r[0].target == "high.py"
