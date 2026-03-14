"""Comprehensive tests for convergence aggregator.

Covers all public functions: core engine, function-level adapters,
file-level adapters, and file-level weighted aggregation.
Each test class covers one logical unit. Edge cases include empty inputs,
missing keys, boundary values, and None inputs.
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

    def test_single_value(self) -> None:
        assert _probability_union([0.7]) == 0.7

    def test_single_zero(self) -> None:
        assert _probability_union([0.0]) == 0.0

    def test_single_one(self) -> None:
        assert _probability_union([1.0]) == 1.0

    def test_two_equal_values(self) -> None:
        # 1 - (1-0.6)(1-0.6) = 1 - 0.16 = 0.84
        assert abs(_probability_union([0.6, 0.6]) - 0.84) < 1e-10

    def test_three_values(self) -> None:
        # 1 - 0.7*0.5*0.2 = 1 - 0.07 = 0.93
        assert abs(_probability_union([0.3, 0.5, 0.8]) - 0.93) < 1e-10

    def test_all_zeros(self) -> None:
        assert _probability_union([0.0, 0.0, 0.0]) == 0.0

    def test_one_certain_source(self) -> None:
        # If any confidence is 1.0, result is 1.0
        assert _probability_union([0.5, 1.0, 0.3]) == 1.0

    def test_many_low_confidences_accumulate(self) -> None:
        # 10 sources at 0.1 each: 1 - 0.9^10 ~= 0.6513
        result = _probability_union([0.1] * 10)
        expected = 1.0 - 0.9**10
        assert abs(result - expected) < 1e-10


class TestClassifyActionability:
    def test_extract_exact_boundary(self) -> None:
        assert classify_actionability(0.75, 3) == Actionability.EXTRACT

    def test_extract_above_boundary(self) -> None:
        assert classify_actionability(0.9, 5) == Actionability.EXTRACT

    def test_net_below_extract_falls_to_split(self) -> None:
        assert classify_actionability(0.74, 3) == Actionability.SPLIT

    def test_count_below_extract_falls_to_split(self) -> None:
        assert classify_actionability(0.75, 2) == Actionability.SPLIT

    def test_split_exact_boundary(self) -> None:
        assert classify_actionability(0.5, 2) == Actionability.SPLIT

    def test_net_below_split_falls_to_investigate(self) -> None:
        assert classify_actionability(0.49, 2) == Actionability.INVESTIGATE

    def test_count_below_split_falls_to_investigate(self) -> None:
        assert classify_actionability(0.5, 1) == Actionability.INVESTIGATE

    def test_zero_net_zero_count(self) -> None:
        assert classify_actionability(0.0, 0) == Actionability.INVESTIGATE

    def test_high_net_zero_count(self) -> None:
        assert classify_actionability(1.0, 0) == Actionability.INVESTIGATE

    def test_zero_net_high_count(self) -> None:
        assert classify_actionability(0.0, 10) == Actionability.INVESTIGATE


class TestApplyWeight:
    def test_capped_at_one(self) -> None:
        assert _apply_weight(0.8, 2.0) == 1.0

    def test_uncapped(self) -> None:
        assert abs(_apply_weight(0.3, 1.5) - 0.45) < 1e-10

    def test_zero_confidence(self) -> None:
        assert _apply_weight(0.0, 2.0) == 0.0

    def test_zero_weight(self) -> None:
        assert _apply_weight(0.5, 0.0) == 0.0

    def test_exactly_one(self) -> None:
        assert _apply_weight(0.5, 2.0) == 1.0

    def test_identity_weight(self) -> None:
        assert _apply_weight(0.7, 1.0) == 0.7


class TestClassifyFileActionability:
    def test_default_investigate(self) -> None:
        assert classify_file_actionability(0.3, 1) == Actionability.INVESTIGATE

    def test_split_high_net_and_count(self) -> None:
        assert classify_file_actionability(0.75, 3) == Actionability.SPLIT

    def test_split_high_net_insufficient_count(self) -> None:
        assert classify_file_actionability(0.75, 2) == Actionability.INVESTIGATE

    def test_cohesion_driven_split(self) -> None:
        assert (
            classify_file_actionability(0.5, 2, {"score": 0.2, "component_count": 4})
            == Actionability.SPLIT
        )

    def test_cohesion_score_at_boundary_not_split(self) -> None:
        # score=0.3 is not < 0.3, so cohesion path skipped
        assert (
            classify_file_actionability(0.5, 2, {"score": 0.3, "component_count": 4})
            == Actionability.INVESTIGATE
        )

    def test_cohesion_components_below_boundary(self) -> None:
        # components=2 < 3, so cohesion path skipped
        assert (
            classify_file_actionability(0.5, 2, {"score": 0.2, "component_count": 2})
            == Actionability.INVESTIGATE
        )

    def test_cohesion_net_below_threshold(self) -> None:
        # net=0.49 < 0.5, so cohesion path skipped
        assert (
            classify_file_actionability(0.49, 2, {"score": 0.2, "component_count": 4})
            == Actionability.INVESTIGATE
        )

    def test_cohesion_count_below_threshold(self) -> None:
        # count=1 < 2, so cohesion path skipped
        assert (
            classify_file_actionability(0.5, 1, {"score": 0.2, "component_count": 4})
            == Actionability.INVESTIGATE
        )

    def test_none_cohesion_data(self) -> None:
        assert classify_file_actionability(0.6, 2, None) == Actionability.INVESTIGATE

    def test_empty_cohesion_data(self) -> None:
        # Empty dict is falsy
        assert classify_file_actionability(0.6, 2, {}) == Actionability.INVESTIGATE

    def test_cohesion_path_triggers_before_high_net(self) -> None:
        # Both cohesion and high-net paths would qualify; cohesion checked first
        assert (
            classify_file_actionability(0.75, 3, {"score": 0.1, "component_count": 5})
            == Actionability.SPLIT
        )


# ── Aggregate (function-level) ───────────────────────────────────────


class TestAggregate:
    def test_empty_list(self) -> None:
        assert aggregate([]) == []

    def test_single_support_evidence(self) -> None:
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
        assert r[0].net_confidence == 0.8
        assert r[0].supporting_lenses == [LensKind.PURITY]
        assert r[0].opposing_lenses == []

    def test_single_oppose_evidence(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.FAN_IN, target="g", confidence=0.7, signal="oppose", detail=""
            )
        ]
        r = aggregate(ev)
        assert r[0].support_prob == 0.0
        assert r[0].oppose_prob == 0.7
        assert r[0].net_confidence == 0.0  # max(0 - 0.7, 0.0)
        assert r[0].opposing_lenses == [LensKind.FAN_IN]

    def test_support_minus_oppose_clamped_to_zero(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.PURITY, target="f", confidence=0.3, signal="support", detail=""
            ),
            LensEvidence(
                lens=LensKind.FAN_IN, target="f", confidence=0.9, signal="oppose", detail=""
            ),
        ]
        r = aggregate(ev)
        assert r[0].net_confidence == 0.0

    def test_support_minus_oppose_positive(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.PURITY, target="f", confidence=0.8, signal="support", detail=""
            ),
            LensEvidence(
                lens=LensKind.COHESION, target="f", confidence=0.6, signal="oppose", detail=""
            ),
        ]
        r = aggregate(ev)
        assert abs(r[0].net_confidence - 0.2) < 1e-10

    def test_multi_lens_extract_classification(self) -> None:
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

    def test_sorted_descending_by_net_confidence(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.PURITY, target="low", confidence=0.2, signal="support", detail=""
            ),
            LensEvidence(
                lens=LensKind.PURITY, target="high", confidence=0.9, signal="support", detail=""
            ),
            LensEvidence(
                lens=LensKind.PURITY, target="mid", confidence=0.5, signal="support", detail=""
            ),
        ]
        r = aggregate(ev)
        assert [x.target for x in r] == ["high", "mid", "low"]

    def test_multiple_evidence_same_lens_deduplicated_in_lens_list(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.PURITY, target="f", confidence=0.5, signal="support", detail="a"
            ),
            LensEvidence(
                lens=LensKind.PURITY, target="f", confidence=0.6, signal="support", detail="b"
            ),
        ]
        r = aggregate(ev)
        # Two evidence items but same LensKind -> only 1 unique supporting lens
        assert len(r[0].supporting_lenses) == 1
        # But evidence list has both items
        assert len(r[0].evidence) == 2
        # Probability union of [0.5, 0.6] = 1 - 0.5*0.4 = 0.8
        assert abs(r[0].support_prob - 0.8) < 1e-10

    def test_evidence_list_preserved(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.PURITY, target="f", confidence=0.5, signal="support", detail="d1"
            ),
            LensEvidence(
                lens=LensKind.FAN_IN, target="f", confidence=0.3, signal="oppose", detail="d2"
            ),
        ]
        r = aggregate(ev)
        assert len(r[0].evidence) == 2

    def test_supporting_lenses_sorted_by_value(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.MUTATION, target="f", confidence=0.5, signal="support", detail=""
            ),
            LensEvidence(
                lens=LensKind.COHESION, target="f", confidence=0.5, signal="support", detail=""
            ),
            LensEvidence(
                lens=LensKind.PURITY, target="f", confidence=0.5, signal="support", detail=""
            ),
        ]
        r = aggregate(ev)
        values = [lk.value for lk in r[0].supporting_lenses]
        assert values == sorted(values)


# ── Function-level adapters ──────────────────────────────────────────


class TestAdaptPurity:
    def test_list_format_basic(self) -> None:
        r = adapt_purity([{"name": "fn", "confidence": 0.9, "hints": ["cacheable"]}])
        assert len(r) == 1
        assert r[0].target == "fn"
        assert r[0].confidence == 0.9
        assert r[0].lens == LensKind.PURITY
        assert r[0].signal == "support"
        assert "cacheable" in r[0].detail

    def test_list_format_default_confidence(self) -> None:
        r = adapt_purity([{"name": "fn"}])
        assert r[0].confidence == 0.8  # default

    def test_list_skips_empty_name(self) -> None:
        assert adapt_purity([{"name": "", "confidence": 0.9}]) == []

    def test_list_skips_missing_name_key(self) -> None:
        assert adapt_purity([{"confidence": 0.9}]) == []

    def test_dict_format(self) -> None:
        r = adapt_purity({"fn": {"file": "a.py", "confidence": 0.7}})
        assert r[0].target == "fn"
        assert r[0].confidence == 0.7

    def test_dict_format_default_confidence(self) -> None:
        r = adapt_purity({"fn": {"file": "a.py"}})
        assert r[0].confidence == 0.5  # dict default

    def test_empty_list(self) -> None:
        assert adapt_purity([]) == []

    def test_empty_dict(self) -> None:
        assert adapt_purity({}) == []

    def test_list_multiple_items(self) -> None:
        r = adapt_purity(
            [
                {"name": "a", "confidence": 0.6},
                {"name": "b", "confidence": 0.7},
            ]
        )
        assert len(r) == 2
        assert {e.target for e in r} == {"a", "b"}

    def test_dict_multiple_items(self) -> None:
        r = adapt_purity({"a": {"confidence": 0.6}, "b": {"confidence": 0.7}})
        assert len(r) == 2

    def test_raw_field_preserved_list(self) -> None:
        item = {"name": "fn", "confidence": 0.9, "hints": ["cacheable"]}
        r = adapt_purity([item])
        assert r[0].raw == item

    def test_raw_field_preserved_dict(self) -> None:
        info = {"file": "a.py", "confidence": 0.7}
        r = adapt_purity({"fn": info})
        assert r[0].raw == info


class TestAdaptMutation:
    def test_high_survival_supports(self) -> None:
        r = adapt_mutation({"f": {"survival_rate": 0.8, "survived_categories": ["VALUE", "SWAP"]}})
        assert r[0].signal == "support"
        assert r[0].confidence == 0.8
        assert "80%" in r[0].detail
        assert "2 categories" in r[0].detail

    def test_boundary_included(self) -> None:
        r = adapt_mutation({"f": {"survival_rate": 0.3}})
        assert len(r) == 1
        assert r[0].confidence == 0.3

    def test_below_boundary_skipped(self) -> None:
        assert adapt_mutation({"f": {"survival_rate": 0.29}}) == []

    def test_confidence_capped_at_one(self) -> None:
        r = adapt_mutation({"f": {"survival_rate": 1.5}})
        assert r[0].confidence == 1.0

    def test_empty_dict(self) -> None:
        assert adapt_mutation({}) == []

    def test_none_input(self) -> None:
        assert adapt_mutation(None) == []  # type: ignore[arg-type]

    def test_default_survival_rate(self) -> None:
        # Missing survival_rate defaults to 0.0, which is below 0.3
        assert adapt_mutation({"f": {}}) == []

    def test_multiple_targets(self) -> None:
        data = {
            "f1": {"survival_rate": 0.5},
            "f2": {"survival_rate": 0.1},
            "f3": {"survival_rate": 0.9},
        }
        r = adapt_mutation(data)
        assert len(r) == 2  # f2 excluded
        targets = {e.target for e in r}
        assert targets == {"f1", "f3"}


class TestAdaptSpecification:
    def test_regime_b_low_spec_supports(self) -> None:
        r = adapt_specification({"f": {"regime": "B", "spec_level": 0.2}})
        assert r[0].signal == "support"
        assert abs(r[0].confidence - 0.8) < 1e-10

    def test_high_spec_opposes(self) -> None:
        r = adapt_specification({"f": {"regime": "A", "spec_level": 0.8}})
        assert r[0].signal == "oppose"
        assert r[0].confidence == 0.8

    def test_regime_a_low_spec_skipped(self) -> None:
        assert adapt_specification({"f": {"regime": "A", "spec_level": 0.3}}) == []

    def test_regime_b_high_spec_opposes(self) -> None:
        r = adapt_specification({"f": {"regime": "B", "spec_level": 0.7}})
        assert r[0].signal == "oppose"

    def test_regime_b_at_boundary_skipped(self) -> None:
        # spec_level=0.5 is not < 0.5 for support, and not >= 0.7 for oppose
        assert adapt_specification({"f": {"regime": "B", "spec_level": 0.5}}) == []

    def test_mid_spec_level_gap(self) -> None:
        # 0.5 <= spec_level < 0.7 in any regime -> no evidence
        assert adapt_specification({"f": {"regime": "A", "spec_level": 0.6}}) == []
        assert adapt_specification({"f": {"regime": "B", "spec_level": 0.6}}) == []

    def test_empty(self) -> None:
        assert adapt_specification({}) == []

    def test_none_input(self) -> None:
        assert adapt_specification(None) == []  # type: ignore[arg-type]

    def test_spec_level_exactly_0_7_opposes(self) -> None:
        r = adapt_specification({"f": {"regime": "B", "spec_level": 0.7}})
        assert r[0].signal == "oppose"
        assert r[0].confidence == 0.7

    def test_confidence_capped_for_support(self) -> None:
        # spec_level=0.0 -> confidence = min(1.0 - 0.0, 1.0) = 1.0
        r = adapt_specification({"f": {"regime": "B", "spec_level": 0.0}})
        assert r[0].confidence == 1.0

    def test_confidence_capped_for_oppose(self) -> None:
        # spec_level=1.0 -> confidence = min(1.0, 1.0) = 1.0
        r = adapt_specification({"f": {"regime": "A", "spec_level": 1.0}})
        assert r[0].confidence == 1.0

    def test_unknown_regime_low_spec_skipped(self) -> None:
        # Only regime "B" triggers support
        assert adapt_specification({"f": {"regime": "unknown", "spec_level": 0.1}}) == []

    def test_detail_contains_spec_level(self) -> None:
        r = adapt_specification({"f": {"regime": "B", "spec_level": 0.2}})
        assert "0.20" in r[0].detail


class TestAdaptCompositionGap:
    def test_positive_gamma_supports(self) -> None:
        r = adapt_composition_gap({"a->b": {"gamma": 3.0}})
        assert r[0].signal == "support"
        assert abs(r[0].confidence - 0.6) < 1e-10

    def test_zero_gamma_skipped(self) -> None:
        assert adapt_composition_gap({"a->b": {"gamma": 0.0}}) == []

    def test_negative_gamma_skipped(self) -> None:
        assert adapt_composition_gap({"a->b": {"gamma": -1.0}}) == []

    def test_large_gamma_caps_confidence(self) -> None:
        r = adapt_composition_gap({"a->b": {"gamma": 10.0}})
        assert r[0].confidence == 1.0

    def test_small_gamma_confidence(self) -> None:
        r = adapt_composition_gap({"a->b": {"gamma": 1.0}})
        assert abs(r[0].confidence - 0.2) < 1e-10

    def test_empty(self) -> None:
        assert adapt_composition_gap({}) == []

    def test_none_input(self) -> None:
        assert adapt_composition_gap(None) == []  # type: ignore[arg-type]

    def test_detail_contains_gamma(self) -> None:
        r = adapt_composition_gap({"a->b": {"gamma": 2.5}})
        assert "2.50" in r[0].detail

    def test_lens_kind(self) -> None:
        r = adapt_composition_gap({"a->b": {"gamma": 1.0}})
        assert r[0].lens == LensKind.COMPOSITION_GAP


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
        assert abs(r[0].confidence - 0.7) < 1e-10

    def test_confidence_floor(self) -> None:
        # score=0.95 -> max(1.0 - 0.95, 0.1) = max(0.05, 0.1) = 0.1
        r = adapt_cohesion({"f.py": {"score": 0.95, "component_count": 2}})
        assert abs(r[0].confidence - 0.1) < 1e-10

    def test_exact_boundary_skipped(self) -> None:
        # score=0.5 and components=1 -> score >= 0.5 and components <= 1 -> skip
        assert adapt_cohesion({"f.py": {"score": 0.5, "component_count": 1}}) == []

    def test_empty(self) -> None:
        assert adapt_cohesion({}) == []

    def test_none_input(self) -> None:
        assert adapt_cohesion(None) == []  # type: ignore[arg-type]

    def test_default_values(self) -> None:
        # Missing score defaults to 1.0, component_count defaults to 1 -> skipped
        assert adapt_cohesion({"f.py": {}}) == []


class TestAdaptFanIn:
    def test_high_fan_in_opposes(self) -> None:
        r = adapt_fan_in({"m": 8})
        assert r[0].signal == "oppose"
        assert abs(r[0].confidence - 0.8) < 1e-10

    def test_zero_fan_in_supports(self) -> None:
        r = adapt_fan_in({"m": 0})
        assert r[0].signal == "support"
        assert r[0].confidence == 0.4

    def test_middle_fan_in_skipped(self) -> None:
        assert adapt_fan_in({"m": 3}) == []

    def test_at_threshold_opposes(self) -> None:
        r = adapt_fan_in({"m": 5})
        assert r[0].signal == "oppose"
        assert abs(r[0].confidence - 0.5) < 1e-10  # 5 / 10

    def test_just_below_threshold_skipped(self) -> None:
        assert adapt_fan_in({"m": 4}) == []

    def test_custom_threshold(self) -> None:
        r = adapt_fan_in({"m": 3}, threshold=3)
        assert r[0].signal == "oppose"

    def test_confidence_capped(self) -> None:
        r = adapt_fan_in({"m": 100})
        assert r[0].confidence == 1.0

    def test_empty(self) -> None:
        assert adapt_fan_in({}) == []

    def test_none_input(self) -> None:
        assert adapt_fan_in(None) == []  # type: ignore[arg-type]

    def test_raw_field(self) -> None:
        r = adapt_fan_in({"m": 8})
        assert r[0].raw == {"fan_in": 8}

    def test_fan_in_one_skipped(self) -> None:
        # count=1 is not 0 and not >= 5 -> skipped
        assert adapt_fan_in({"m": 1}) == []


class TestAdaptCochange:
    def test_high_coupling_opposes(self) -> None:
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.6}]}
        r = adapt_cochange(data)
        assert r[0].signal == "oppose"
        assert r[0].target == "a.py<->b.py"
        assert r[0].confidence == 0.6

    def test_low_coupling_supports(self) -> None:
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.2}]}
        r = adapt_cochange(data)
        assert r[0].signal == "support"
        assert abs(r[0].confidence - 0.8) < 1e-10  # max(1.0 - 0.2, 0.1)

    def test_boundary_coupling_opposes(self) -> None:
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.4}]}
        r = adapt_cochange(data)
        assert r[0].signal == "oppose"

    def test_just_below_boundary_supports(self) -> None:
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.39}]}
        r = adapt_cochange(data)
        assert r[0].signal == "support"

    def test_custom_threshold(self) -> None:
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.3}]}
        r = adapt_cochange(data, threshold=0.3)
        assert r[0].signal == "oppose"

    def test_confidence_capped_for_oppose(self) -> None:
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 1.5}]}
        r = adapt_cochange(data)
        assert r[0].confidence == 1.0

    def test_confidence_floor_for_support(self) -> None:
        # strength=0.95 (below threshold=1.0) -> max(1.0 - 0.95, 0.1) = 0.1
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.35}]}
        r = adapt_cochange(data, threshold=0.4)
        assert r[0].signal == "support"
        assert abs(r[0].confidence - 0.65) < 1e-10

    def test_empty_dict(self) -> None:
        assert adapt_cochange({}) == []

    def test_none_input(self) -> None:
        assert adapt_cochange(None) == []  # type: ignore[arg-type]

    def test_empty_pairs(self) -> None:
        assert adapt_cochange({"pairs": []}) == []

    def test_missing_pairs_key(self) -> None:
        assert adapt_cochange({"other": "value"}) == []

    def test_multiple_pairs(self) -> None:
        data = {
            "pairs": [
                {"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.6},
                {"file_a": "c.py", "file_b": "d.py", "coupling_strength": 0.1},
            ]
        }
        r = adapt_cochange(data)
        assert len(r) == 2
        assert r[0].signal == "oppose"
        assert r[1].signal == "support"


class TestAdaptDepClustering:
    def test_dict_prescription(self) -> None:
        r = adapt_dep_clustering([{"target": "block_a", "confidence": 0.7, "action": "extract"}])
        assert r[0].target == "block_a"
        assert r[0].confidence == 0.7
        assert r[0].lens == LensKind.DEP_CLUSTERING
        assert r[0].signal == "support"
        assert "extract" in r[0].detail

    def test_object_prescription(self) -> None:
        @dataclass
        class FakePrescription:
            target: str = "block_b"
            confidence: float = 0.6
            action: str = "inline"

        r = adapt_dep_clustering([FakePrescription()])
        assert r[0].target == "block_b"
        assert r[0].confidence == 0.6

    def test_dict_default_values(self) -> None:
        r = adapt_dep_clustering([{}])
        assert r[0].target == ""
        assert r[0].confidence == 0.5

    def test_object_missing_attrs_uses_str(self) -> None:
        # Object without 'target' attr falls back to str(p)
        class Bare:
            pass

        obj = Bare()
        r = adapt_dep_clustering([obj])
        assert r[0].target == str(obj)

    def test_empty_list(self) -> None:
        assert adapt_dep_clustering([]) == []

    def test_none_input(self) -> None:
        assert adapt_dep_clustering(None) == []  # type: ignore[arg-type]

    def test_multiple_prescriptions(self) -> None:
        r = adapt_dep_clustering(
            [
                {"target": "a", "confidence": 0.5, "action": "x"},
                {"target": "b", "confidence": 0.6, "action": "y"},
            ]
        )
        assert len(r) == 2


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

    def test_boundary_score_no_weakness_skipped(self) -> None:
        # score=0.8 and no weakness -> score >= 0.8 and not taxonomy -> skip
        assert (
            adapt_assertion_quality({"f": {"effectiveness_score": 0.8, "weakness_taxonomy": []}})
            == []
        )

    def test_boundary_score_with_weakness_supports(self) -> None:
        r = adapt_assertion_quality(
            {"f": {"effectiveness_score": 0.8, "weakness_taxonomy": ["shallow"]}}
        )
        assert r[0].signal == "support"

    def test_just_below_boundary_no_weakness_supports(self) -> None:
        # score=0.79 < 0.8 -> not skipped even without weakness
        r = adapt_assertion_quality({"f": {"effectiveness_score": 0.79, "weakness_taxonomy": []}})
        assert r[0].signal == "support"

    def test_confidence_floor(self) -> None:
        # score=0.95 -> max(1.0 - 0.95, 0.1) = 0.1
        r = adapt_assertion_quality(
            {"f": {"effectiveness_score": 0.95, "weakness_taxonomy": ["x"]}}
        )
        assert abs(r[0].confidence - 0.1) < 1e-10

    def test_empty(self) -> None:
        assert adapt_assertion_quality({}) == []

    def test_none_input(self) -> None:
        assert adapt_assertion_quality(None) == []  # type: ignore[arg-type]

    def test_default_values_skipped(self) -> None:
        # Missing effectiveness_score defaults to 1.0, weakness_taxonomy to []
        # 1.0 >= 0.8 and not [] -> skip
        assert adapt_assertion_quality({"f": {}}) == []

    def test_detail_contains_info(self) -> None:
        r = adapt_assertion_quality(
            {"f": {"effectiveness_score": 0.3, "weakness_taxonomy": ["weak"]}}
        )
        assert "0.30" in r[0].detail
        assert "weak" in r[0].detail


class TestAdaptAlgebraic:
    def test_safe_with_properties_supports(self) -> None:
        r = adapt_algebraic({"f": {"extraction_safety": "safe", "properties": ["comm"]}})
        assert r[0].signal == "support"
        assert r[0].confidence == 0.6
        assert r[0].lens == LensKind.ALGEBRAIC

    def test_unsafe_opposes(self) -> None:
        r = adapt_algebraic({"f": {"extraction_safety": "unsafe", "reason": "io"}})
        assert r[0].signal == "oppose"
        assert r[0].confidence == 0.5

    def test_unknown_skipped(self) -> None:
        assert adapt_algebraic({"f": {"extraction_safety": "unknown"}}) == []

    def test_safe_without_properties_skipped(self) -> None:
        # safe but empty properties -> skipped
        assert adapt_algebraic({"f": {"extraction_safety": "safe", "properties": []}}) == []

    def test_safe_missing_properties_skipped(self) -> None:
        # safe but missing properties key (defaults to []) -> skipped
        assert adapt_algebraic({"f": {"extraction_safety": "safe"}}) == []

    def test_detail_contains_properties(self) -> None:
        r = adapt_algebraic(
            {
                "f": {
                    "extraction_safety": "safe",
                    "properties": ["comm", "assoc"],
                    "hints": ["cache"],
                }
            }
        )
        assert "comm" in r[0].detail
        assert "cache" in r[0].detail

    def test_unsafe_detail_contains_reason(self) -> None:
        r = adapt_algebraic({"f": {"extraction_safety": "unsafe", "reason": "side_effects"}})
        assert "side_effects" in r[0].detail

    def test_empty(self) -> None:
        assert adapt_algebraic({}) == []

    def test_none_input(self) -> None:
        assert adapt_algebraic(None) == []  # type: ignore[arg-type]


class TestAdaptImportTracing:
    def test_io_opposes(self) -> None:
        r = adapt_import_tracing({"m": {"has_module_level_io": True, "depth": 1}})
        assert r[0].signal == "oppose"
        assert r[0].confidence == 0.6

    def test_shallow_supports(self) -> None:
        r = adapt_import_tracing(
            {"m": {"has_module_level_io": False, "depth": 1, "non_stdlib_deps": 2}}
        )
        assert r[0].signal == "support"
        assert r[0].confidence == 0.4

    def test_deep_skipped(self) -> None:
        assert (
            adapt_import_tracing(
                {"m": {"has_module_level_io": False, "depth": 5, "non_stdlib_deps": 10}}
            )
            == []
        )

    def test_boundary_depth_included(self) -> None:
        # depth=2 <= 2 -> included
        r = adapt_import_tracing(
            {"m": {"has_module_level_io": False, "depth": 2, "non_stdlib_deps": 3}}
        )
        assert r[0].signal == "support"

    def test_boundary_depth_over_skipped(self) -> None:
        # depth=3 > 2 -> skipped
        assert (
            adapt_import_tracing(
                {"m": {"has_module_level_io": False, "depth": 3, "non_stdlib_deps": 2}}
            )
            == []
        )

    def test_boundary_non_stdlib_over_skipped(self) -> None:
        # non_stdlib=4 > 3 -> skipped
        assert (
            adapt_import_tracing(
                {"m": {"has_module_level_io": False, "depth": 1, "non_stdlib_deps": 4}}
            )
            == []
        )

    def test_io_takes_priority_over_depth(self) -> None:
        # has_io=True -> oppose regardless of depth
        r = adapt_import_tracing(
            {"m": {"has_module_level_io": True, "depth": 1, "non_stdlib_deps": 1}}
        )
        assert r[0].signal == "oppose"

    def test_empty(self) -> None:
        assert adapt_import_tracing({}) == []

    def test_none_input(self) -> None:
        assert adapt_import_tracing(None) == []  # type: ignore[arg-type]


class TestAdaptCallGraph:
    def test_high_fan_out_supports(self) -> None:
        r = adapt_call_graph({"f": {"fan_out": 10}})
        assert r[0].signal == "support"
        assert r[0].lens == LensKind.CALL_GRAPH

    def test_below_threshold_skipped(self) -> None:
        assert adapt_call_graph({"f": {"fan_out": 3}}) == []

    def test_at_threshold_supports(self) -> None:
        r = adapt_call_graph({"f": {"fan_out": 8}})
        assert len(r) == 1
        assert abs(r[0].confidence - 0.5) < 1e-10  # 8 / 16

    def test_custom_threshold(self) -> None:
        assert len(adapt_call_graph({"f": {"fan_out": 5}}, threshold=5)) == 1
        assert adapt_call_graph({"f": {"fan_out": 5}}, threshold=6) == []

    def test_confidence_capped(self) -> None:
        r = adapt_call_graph({"f": {"fan_out": 100}})
        assert r[0].confidence == 1.0

    def test_confidence_formula(self) -> None:
        # fan_out=12, threshold=8 -> 12/16 = 0.75
        r = adapt_call_graph({"f": {"fan_out": 12}})
        assert abs(r[0].confidence - 0.75) < 1e-10

    def test_empty(self) -> None:
        assert adapt_call_graph({}) == []

    def test_none_input(self) -> None:
        assert adapt_call_graph(None) == []  # type: ignore[arg-type]

    def test_default_fan_out_skipped(self) -> None:
        # Missing fan_out defaults to 0
        assert adapt_call_graph({"f": {}}) == []


class TestAdaptContractCoverage:
    def test_published_only_supports(self) -> None:
        published = {"f": {"channel": "perf", "metric_key": "purity"}}
        consumed: dict[str, object] = {}
        r = adapt_contract_coverage(published, consumed)
        assert len(r) == 1
        assert r[0].signal == "support"
        assert r[0].target == "f"
        assert r[0].lens == LensKind.CONTRACT_COVERAGE
        assert r[0].confidence == 0.5

    def test_consumed_targets_excluded(self) -> None:
        published = {"f": {"channel": "perf"}}
        consumed = {"f": {"channel": "spec"}}
        assert adapt_contract_coverage(published, consumed) == []

    def test_empty(self) -> None:
        assert adapt_contract_coverage({}, {}) == []

    def test_partial_overlap(self) -> None:
        published = {"f": {"channel": "perf"}, "g": {"channel": "spec"}}
        consumed = {"f": {"channel": "spec"}}
        r = adapt_contract_coverage(published, consumed)
        assert len(r) == 1
        assert r[0].target == "g"

    def test_detail_contains_channel(self) -> None:
        r = adapt_contract_coverage({"f": {"channel": "perf"}}, {})
        assert "perf" in r[0].detail

    def test_multiple_published_only(self) -> None:
        published = {"a": {"channel": "ch1"}, "b": {"channel": "ch2"}}
        consumed: dict[str, object] = {}
        r = adapt_contract_coverage(published, consumed)
        assert len(r) == 2


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
        assert r[0].confidence == 0.5
        assert "COH001" in r[0].detail

    def test_empty_list(self) -> None:
        assert adapt_cross_channel([]) == []

    def test_none_input(self) -> None:
        assert adapt_cross_channel(None) == []  # type: ignore[arg-type]

    def test_finding_without_file_attr(self) -> None:
        @dataclass
        class NoFile:
            kind: str = "COH002"
            message: str = "issue"

        # getattr with default None -> target = ""
        r = adapt_cross_channel([NoFile()])
        assert r[0].target == ""

    def test_multiple_findings(self) -> None:
        @dataclass
        class Finding:
            file: str
            kind: str
            message: str

        findings = [
            Finding("a.py", "COH001", "msg1"),
            Finding("b.py", "COH002", "msg2"),
        ]
        r = adapt_cross_channel(findings)
        assert len(r) == 2


# ── File-level adapters ──────────────────────────────────────────────


class TestAdaptCohesionFile:
    def test_low_score_supports(self) -> None:
        r = adapt_cohesion_file({"f.py": {"score": 0.3, "component_count": 2}})
        assert r[0].signal == "support"
        assert abs(r[0].confidence - 0.7) < 1e-10
        assert r[0].lens == LensKind.COHESION

    def test_high_score_single_component_skipped(self) -> None:
        assert adapt_cohesion_file({"f.py": {"score": 0.5, "component_count": 1}}) == []

    def test_boundary_score_below_threshold(self) -> None:
        # score=0.49 < 0.5 -> not skipped
        r = adapt_cohesion_file({"f.py": {"score": 0.49, "component_count": 1}})
        assert len(r) == 1

    def test_boundary_components_above_one(self) -> None:
        r = adapt_cohesion_file({"f.py": {"score": 0.6, "component_count": 2}})
        assert len(r) == 1

    def test_confidence_floor(self) -> None:
        r = adapt_cohesion_file({"f.py": {"score": 0.95, "component_count": 2}})
        assert abs(r[0].confidence - 0.1) < 1e-10

    def test_empty(self) -> None:
        assert adapt_cohesion_file({}) == []

    def test_none_input(self) -> None:
        assert adapt_cohesion_file(None) == []  # type: ignore[arg-type]

    def test_detail_contains_file_prefix(self) -> None:
        r = adapt_cohesion_file({"f.py": {"score": 0.3, "component_count": 2}})
        assert "File cohesion" in r[0].detail


class TestAdaptFanInFile:
    def test_high_opposes(self) -> None:
        r = adapt_fan_in_file({"m": 8})
        assert r[0].signal == "oppose"

    def test_low_supports(self) -> None:
        r = adapt_fan_in_file({"m": 1})
        assert r[0].signal == "support"
        assert r[0].confidence == 0.4

    def test_zero_supports(self) -> None:
        r = adapt_fan_in_file({"m": 0})
        assert r[0].signal == "support"

    def test_middle_skipped(self) -> None:
        assert adapt_fan_in_file({"m": 3}) == []

    def test_at_threshold_opposes(self) -> None:
        r = adapt_fan_in_file({"m": 5})
        assert r[0].signal == "oppose"

    def test_just_below_threshold_and_above_one_skipped(self) -> None:
        # count=2 -> not >= 5 and not <= 1 -> skip
        assert adapt_fan_in_file({"m": 2}) == []

    def test_custom_threshold(self) -> None:
        r = adapt_fan_in_file({"m": 3}, threshold=3)
        assert r[0].signal == "oppose"

    def test_confidence_for_high(self) -> None:
        # count=10, threshold=5 -> 10 / 10 = 1.0
        r = adapt_fan_in_file({"m": 10})
        assert r[0].confidence == 1.0

    def test_detail_for_oppose(self) -> None:
        r = adapt_fan_in_file({"m": 8})
        assert "split risky" in r[0].detail

    def test_detail_for_support(self) -> None:
        r = adapt_fan_in_file({"m": 0})
        assert "safe to restructure" in r[0].detail

    def test_empty(self) -> None:
        assert adapt_fan_in_file({}) == []

    def test_none_input(self) -> None:
        assert adapt_fan_in_file(None) == []  # type: ignore[arg-type]


class TestAdaptCochangeFile:
    def test_matching_file_a_opposes(self) -> None:
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.6}]}
        r = adapt_cochange_file(data, "a.py")
        assert r[0].signal == "oppose"
        assert r[0].target == "a.py"

    def test_matching_file_b_opposes(self) -> None:
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.6}]}
        r = adapt_cochange_file(data, "b.py")
        assert r[0].signal == "oppose"
        assert r[0].target == "b.py"
        # detail should reference the other file (a.py)
        assert "a.py" in r[0].detail

    def test_non_matching_filepath_skipped(self) -> None:
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.6}]}
        assert adapt_cochange_file(data, "c.py") == []

    def test_below_threshold_skipped(self) -> None:
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.2}]}
        assert adapt_cochange_file(data, "a.py") == []

    def test_at_threshold_opposes(self) -> None:
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.4}]}
        r = adapt_cochange_file(data, "a.py")
        assert r[0].signal == "oppose"

    def test_custom_threshold(self) -> None:
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.3}]}
        r = adapt_cochange_file(data, "a.py", threshold=0.3)
        assert r[0].signal == "oppose"

    def test_confidence_capped(self) -> None:
        data = {"pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 1.5}]}
        r = adapt_cochange_file(data, "a.py")
        assert r[0].confidence == 1.0

    def test_empty_data(self) -> None:
        assert adapt_cochange_file({}, "a.py") == []

    def test_none_data(self) -> None:
        assert adapt_cochange_file(None, "a.py") == []  # type: ignore[arg-type]

    def test_empty_pairs(self) -> None:
        assert adapt_cochange_file({"pairs": []}, "a.py") == []


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

    def test_boundary_depth_included(self) -> None:
        r = adapt_import_weight_file(
            {"m": {"has_module_level_io": False, "depth": 2, "non_stdlib_deps": 3}}
        )
        assert r[0].signal == "support"

    def test_boundary_depth_over_skipped(self) -> None:
        assert (
            adapt_import_weight_file(
                {"m": {"has_module_level_io": False, "depth": 3, "non_stdlib_deps": 2}}
            )
            == []
        )

    def test_boundary_non_stdlib_over_skipped(self) -> None:
        assert (
            adapt_import_weight_file(
                {"m": {"has_module_level_io": False, "depth": 1, "non_stdlib_deps": 4}}
            )
            == []
        )

    def test_empty(self) -> None:
        assert adapt_import_weight_file({}) == []

    def test_none_input(self) -> None:
        assert adapt_import_weight_file(None) == []  # type: ignore[arg-type]

    def test_io_detail_contains_depth(self) -> None:
        r = adapt_import_weight_file({"m": {"has_module_level_io": True, "depth": 3}})
        assert "depth=3" in r[0].detail


# ── File-level weighted aggregation ──────────────────────────────────


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
        # COHESION weight=2.0: 0.4*2.0=0.8, FAN_IN weight=1.5: 0.4*1.5=0.6
        # union = 1 - (1-0.8)*(1-0.6) = 1 - 0.2*0.4 = 0.92
        assert abs(r[0].support_prob - 0.92) < 1e-10

    def test_with_cohesion_map_split(self) -> None:
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

    def test_with_split_proposals(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.COHESION, target="f.py", confidence=0.5, signal="support", detail=""
            ),
        ]
        proposals = {"f.py": [{"action": "split into utils"}]}
        r = aggregate_file(ev, split_proposals_map=proposals)
        assert r[0].split_proposals == [{"action": "split into utils"}]

    def test_no_split_proposals_default_empty(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.COHESION, target="f.py", confidence=0.5, signal="support", detail=""
            ),
        ]
        r = aggregate_file(ev)
        assert r[0].split_proposals == []

    def test_opposing_evidence_reduces_net(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.COHESION, target="f.py", confidence=0.5, signal="support", detail=""
            ),
            LensEvidence(
                lens=LensKind.FAN_IN, target="f.py", confidence=0.8, signal="oppose", detail=""
            ),
        ]
        r = aggregate_file(ev)
        # support weighted: 0.5*2.0=1.0, oppose weighted: 0.8*1.5=1.0(capped)
        # net = max(1.0 - 1.0, 0.0) = 0.0
        assert r[0].net_confidence == 0.0

    def test_multiple_targets(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.COHESION, target="a.py", confidence=0.5, signal="support", detail=""
            ),
            LensEvidence(
                lens=LensKind.COHESION, target="b.py", confidence=0.3, signal="support", detail=""
            ),
        ]
        r = aggregate_file(ev)
        assert len(r) == 2
        # Higher net first
        assert r[0].target == "a.py"

    def test_unknown_lens_weight_defaults_to_one(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.ALGEBRAIC, target="f.py", confidence=0.5, signal="support", detail=""
            ),
        ]
        r = aggregate_file(ev)
        # ALGEBRAIC not in _FILE_LENS_WEIGHTS -> weight=1.0
        assert abs(r[0].support_prob - 0.5) < 1e-10

    def test_opposing_lenses_tracked(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.COHESION, target="f.py", confidence=0.5, signal="support", detail=""
            ),
            LensEvidence(
                lens=LensKind.COCHANGE, target="f.py", confidence=0.6, signal="oppose", detail=""
            ),
        ]
        r = aggregate_file(ev)
        assert LensKind.COHESION in r[0].supporting_lenses
        assert LensKind.COCHANGE in r[0].opposing_lenses

    def test_evidence_list_preserved(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.COHESION, target="f.py", confidence=0.5, signal="support", detail="a"
            ),
            LensEvidence(
                lens=LensKind.FAN_IN, target="f.py", confidence=0.3, signal="oppose", detail="b"
            ),
        ]
        r = aggregate_file(ev)
        assert len(r[0].evidence) == 2

    def test_cohesion_map_missing_target_no_error(self) -> None:
        ev = [
            LensEvidence(
                lens=LensKind.COHESION, target="f.py", confidence=0.5, signal="support", detail=""
            ),
        ]
        # cohesion_map has different target than evidence
        cohesion = {"other.py": {"score": 0.1, "component_count": 5}}
        r = aggregate_file(ev, cohesion_map=cohesion)
        assert r[0].actionability == Actionability.INVESTIGATE
