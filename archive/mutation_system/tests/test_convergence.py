"""Tests for the convergence aggregator package."""

from __future__ import annotations

import pytest

from lintgate.convergence.aggregator import (
    _probability_union,
    adapt_algebraic,
    adapt_assertion_quality,
    adapt_call_graph,
    adapt_cochange,
    adapt_cochange_file,
    adapt_cohesion,
    adapt_cohesion_file,
    adapt_cross_channel,
    adapt_dep_clustering,
    adapt_fan_in,
    adapt_fan_in_file,
    adapt_import_tracing,
    adapt_import_weight_file,
    adapt_mutation,
    adapt_purity,
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
from lintgate.convergence.integration import (
    convergence_to_metrics,
    enrich_decomposition_candidates,
    extract_all_evidence,
    extract_file_evidence,
    file_convergence_to_metrics,
)

# ── TestProbabilityUnion ─────────────────────────────────────────────


class TestProbabilityUnion:
    def test_empty(self):
        assert _probability_union([]) == 0.0

    def test_single_identity(self):
        assert _probability_union([0.6]) == pytest.approx(0.6)

    def test_three_at_06(self):
        # 1 - 0.4^3 = 1 - 0.064 = 0.936
        assert _probability_union([0.6, 0.6, 0.6]) == pytest.approx(0.936)

    def test_all_ones(self):
        assert _probability_union([1.0, 1.0, 1.0]) == pytest.approx(1.0)

    def test_all_zeros(self):
        assert _probability_union([0.0, 0.0, 0.0]) == pytest.approx(0.0)

    def test_mixed(self):
        # 1 - (1-0.3)(1-0.5)(1-0.7) = 1 - 0.7*0.5*0.3 = 1 - 0.105 = 0.895
        assert _probability_union([0.3, 0.5, 0.7]) == pytest.approx(0.895)


# ── TestClassifyActionability ────────────────────────────────────────


class TestClassifyActionability:
    def test_extract_threshold(self):
        assert classify_actionability(0.75, 3) == Actionability.EXTRACT

    def test_extract_above_threshold(self):
        assert classify_actionability(0.9, 5) == Actionability.EXTRACT

    def test_split_threshold(self):
        assert classify_actionability(0.5, 2) == Actionability.SPLIT

    def test_investigate_fallback(self):
        assert classify_actionability(0.3, 1) == Actionability.INVESTIGATE

    def test_high_conf_few_lenses(self):
        # High confidence but only 2 lenses → SPLIT, not EXTRACT
        assert classify_actionability(0.9, 2) == Actionability.SPLIT

    def test_many_lenses_low_conf(self):
        # Many lenses but low confidence → INVESTIGATE
        assert classify_actionability(0.3, 5) == Actionability.INVESTIGATE


# ── TestLensEvidence ─────────────────────────────────────────────────


class TestLensEvidence:
    def test_valid_creation(self):
        e = LensEvidence(
            lens=LensKind.PURITY,
            target="mod::func",
            confidence=0.8,
            signal="support",
            detail="Pure function",
        )
        assert e.lens == LensKind.PURITY
        assert e.signal == "support"

    def test_invalid_signal_raises(self):
        with pytest.raises(ValueError, match="signal must be"):
            LensEvidence(
                lens=LensKind.PURITY,
                target="x",
                confidence=0.5,
                signal="maybe",
                detail="bad",
            )

    def test_confidence_below_zero_raises(self):
        with pytest.raises(ValueError, match="confidence must be"):
            LensEvidence(
                lens=LensKind.PURITY,
                target="x",
                confidence=-0.1,
                signal="support",
                detail="bad",
            )

    def test_confidence_above_one_raises(self):
        with pytest.raises(ValueError, match="confidence must be"):
            LensEvidence(
                lens=LensKind.PURITY,
                target="x",
                confidence=1.1,
                signal="support",
                detail="bad",
            )

    def test_frozen(self):
        e = LensEvidence(
            lens=LensKind.PURITY,
            target="x",
            confidence=0.5,
            signal="support",
            detail="ok",
        )
        with pytest.raises(AttributeError):
            e.confidence = 0.9  # type: ignore[misc]


# ── TestAggregate ────────────────────────────────────────────────────


class TestAggregate:
    def test_empty_input(self):
        assert aggregate([]) == []

    def test_groups_by_target(self):
        evidence = [
            LensEvidence(LensKind.PURITY, "a::f", 0.6, "support", "pure"),
            LensEvidence(LensKind.MUTATION, "a::f", 0.5, "support", "survival"),
            LensEvidence(LensKind.PURITY, "b::g", 0.7, "support", "pure"),
        ]
        results = aggregate(evidence)
        assert len(results) == 2
        targets = {r.target for r in results}
        assert targets == {"a::f", "b::g"}

    def test_support_oppose_subtraction(self):
        evidence = [
            LensEvidence(LensKind.PURITY, "a::f", 0.8, "support", "pure"),
            LensEvidence(LensKind.FAN_IN, "a::f", 0.6, "oppose", "high fan-in"),
        ]
        results = aggregate(evidence)
        assert len(results) == 1
        r = results[0]
        assert r.support_prob == pytest.approx(0.8)
        assert r.oppose_prob == pytest.approx(0.6)
        assert r.net_confidence == pytest.approx(0.2)

    def test_net_confidence_clamped_to_zero(self):
        evidence = [
            LensEvidence(LensKind.PURITY, "a::f", 0.3, "support", "pure"),
            LensEvidence(LensKind.FAN_IN, "a::f", 0.9, "oppose", "high fan-in"),
        ]
        results = aggregate(evidence)
        assert results[0].net_confidence == 0.0

    def test_sorted_descending(self):
        evidence = [
            LensEvidence(LensKind.PURITY, "low", 0.3, "support", "pure"),
            LensEvidence(LensKind.PURITY, "high", 0.9, "support", "pure"),
        ]
        results = aggregate(evidence)
        assert results[0].target == "high"
        assert results[1].target == "low"

    def test_to_dict(self):
        evidence = [
            LensEvidence(LensKind.PURITY, "a::f", 0.6, "support", "pure"),
        ]
        results = aggregate(evidence)
        d = results[0].to_dict()
        assert d["target"] == "a::f"
        assert "support_prob" in d
        assert "actionability" in d
        assert d["evidence_count"] == 1


# ── TestAdapters ─────────────────────────────────────────────────────


class TestAdaptPurity:
    def test_typical(self):
        data = {
            "mod::func": {"file": "mod.py", "confidence": 0.8, "hints": ["cacheable"]}
        }
        results = adapt_purity(data)
        assert len(results) == 1
        assert results[0].lens == LensKind.PURITY
        assert results[0].signal == "support"
        assert results[0].confidence == 0.8

    def test_empty(self):
        assert adapt_purity({}) == []
        assert adapt_purity(None) == []


class TestAdaptMutation:
    def test_high_survival(self):
        data = {
            "a::f": {
                "survival_rate": 0.6,
                "survived_categories": ["arith", "cond", "boundary"],
            }
        }
        results = adapt_mutation(data)
        assert len(results) == 1
        assert results[0].signal == "support"

    def test_low_survival_skipped(self):
        data = {"a::f": {"survival_rate": 0.1, "survived_categories": []}}
        assert adapt_mutation(data) == []

    def test_empty(self):
        assert adapt_mutation({}) == []
        assert adapt_mutation(None) == []


class TestAdaptCohesion:
    def test_low_cohesion(self):
        data = {"file.py": {"score": 0.3, "component_count": 3}}
        results = adapt_cohesion(data)
        assert len(results) == 1
        assert results[0].signal == "support"

    def test_high_cohesion_skipped(self):
        data = {"file.py": {"score": 0.8, "component_count": 1}}
        assert adapt_cohesion(data) == []

    def test_empty(self):
        assert adapt_cohesion({}) == []


class TestAdaptFanIn:
    def test_high_fan_in_opposes(self):
        data = {"mod": 10}
        results = adapt_fan_in(data, threshold=5)
        assert len(results) == 1
        assert results[0].signal == "oppose"

    def test_zero_fan_in_supports(self):
        data = {"mod": 0}
        results = adapt_fan_in(data, threshold=5)
        assert len(results) == 1
        assert results[0].signal == "support"

    def test_mid_range_skipped(self):
        data = {"mod": 3}
        assert adapt_fan_in(data, threshold=5) == []

    def test_empty(self):
        assert adapt_fan_in({}) == []


class TestAdaptCochange:
    def test_high_coupling_opposes(self):
        data = {
            "pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.8}]
        }
        results = adapt_cochange(data, threshold=0.4)
        assert len(results) == 1
        assert results[0].signal == "oppose"

    def test_low_coupling_supports(self):
        data = {
            "pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.1}]
        }
        results = adapt_cochange(data, threshold=0.4)
        assert len(results) == 1
        assert results[0].signal == "support"

    def test_empty(self):
        assert adapt_cochange({}) == []
        assert adapt_cochange(None) == []


class TestAdaptDepClustering:
    def test_with_prescription_objects(self):
        class FakePrescription:
            target = "file.py::func"
            confidence = 0.7
            action = "extract block"

        results = adapt_dep_clustering([FakePrescription()])
        assert len(results) == 1
        assert results[0].signal == "support"

    def test_empty(self):
        assert adapt_dep_clustering([]) == []
        assert adapt_dep_clustering(None) == []


class TestAdaptAssertionQuality:
    def test_weak_assertions(self):
        data = {
            "mod::func": {
                "effectiveness_score": 0.3,
                "weakness_taxonomy": ["tautological"],
            }
        }
        results = adapt_assertion_quality(data)
        assert len(results) == 1
        assert results[0].signal == "support"

    def test_strong_assertions_skipped(self):
        data = {"mod::func": {"effectiveness_score": 0.9, "weakness_taxonomy": []}}
        assert adapt_assertion_quality(data) == []

    def test_empty(self):
        assert adapt_assertion_quality({}) == []


class TestAdaptAlgebraic:
    def test_safe_with_properties(self):
        data = {
            "mod::func": {
                "properties": ["commutative"],
                "extraction_safety": "safe",
                "hints": [],
            }
        }
        results = adapt_algebraic(data)
        assert len(results) == 1
        assert results[0].signal == "support"

    def test_unsafe_opposes(self):
        data = {
            "mod::func": {
                "properties": [],
                "extraction_safety": "unsafe",
                "reason": "side effects",
            }
        }
        results = adapt_algebraic(data)
        assert len(results) == 1
        assert results[0].signal == "oppose"

    def test_empty(self):
        assert adapt_algebraic({}) == []


class TestAdaptImportTracing:
    def test_module_level_io_opposes(self):
        data = {"mod": {"has_module_level_io": True, "depth": 1, "non_stdlib_deps": 2}}
        results = adapt_import_tracing(data)
        assert len(results) == 1
        assert results[0].signal == "oppose"

    def test_shallow_supports(self):
        data = {"mod": {"has_module_level_io": False, "depth": 1, "non_stdlib_deps": 1}}
        results = adapt_import_tracing(data)
        assert len(results) == 1
        assert results[0].signal == "support"

    def test_empty(self):
        assert adapt_import_tracing({}) == []


class TestAdaptCallGraph:
    def test_high_fan_out(self):
        data = {"mod::func": {"fan_in": 2, "fan_out": 12}}
        results = adapt_call_graph(data, threshold=8)
        assert len(results) == 1
        assert results[0].signal == "support"

    def test_low_fan_out_skipped(self):
        data = {"mod::func": {"fan_in": 2, "fan_out": 3}}
        assert adapt_call_graph(data, threshold=8) == []

    def test_empty(self):
        assert adapt_call_graph({}) == []


class TestAdaptCrossChannel:
    def test_coh_finding(self):
        class FakeFinding:
            file = "a.py"
            kind = "COH001"
            message = "coupled failure"

        results = adapt_cross_channel([FakeFinding()])
        assert len(results) == 1
        assert results[0].signal == "support"

    def test_empty(self):
        assert adapt_cross_channel([]) == []
        assert adapt_cross_channel(None) == []


# ── TestIntegration ──────────────────────────────────────────────────


class TestExtractAllEvidence:
    def _make_channel_result(self, channel, metrics=None, findings=None):
        from lintgate.controlplane.types import ChannelResult

        return ChannelResult(
            channel=channel,
            status="pass",
            severity="none",
            findings=findings or [],
            metrics=metrics or {},
        )

    def test_full_pipeline(self):
        crs = [
            self._make_channel_result(
                "structure",
                metrics={
                    "_module_fan_in": {"mod_a": 0, "mod_b": 10},
                    "cohesion": {"file.py": {"score": 0.3, "component_count": 2}},
                },
            ),
            self._make_channel_result(
                "mutation",
                metrics={
                    "purity_profile": {
                        "mod::func": {"confidence": 0.7, "hints": ["cacheable"]}
                    },
                    "mutation_survival": {
                        "mod::func": {
                            "survival_rate": 0.5,
                            "survived_categories": ["a", "b"],
                        }
                    },
                },
            ),
        ]
        results = extract_all_evidence(crs)
        assert len(results) > 0
        for r in results:
            assert r.net_confidence >= 0.0

    def test_empty_channels(self):
        crs = [self._make_channel_result("lint")]
        assert extract_all_evidence(crs) == []


class TestConvergenceToMetrics:
    def test_format(self):
        evidence = [
            LensEvidence(LensKind.PURITY, "a::f", 0.8, "support", "pure"),
            LensEvidence(LensKind.MUTATION, "a::f", 0.6, "support", "survival"),
            LensEvidence(LensKind.COHESION, "a::f", 0.5, "support", "low cohesion"),
        ]
        results = aggregate(evidence)
        metrics = convergence_to_metrics(results)
        assert "total_targets" in metrics
        assert "top_targets" in metrics
        assert metrics["total_targets"] == 1

    def test_empty(self):
        metrics = convergence_to_metrics([])
        assert metrics["total_targets"] == 0


class TestEnrichDecompositionCandidates:
    def test_enrichment(self):
        from lintgate.mutation.decomposition import DecompositionCandidate

        candidate = DecompositionCandidate(
            function_id="mod::func",
            file_path="mod.py",
            survival_rate=0.6,
            surviving_categories=["a", "b"],
            total_mutants=10,
            reason="test",
            confidence=0.6,
        )
        evidence = [
            LensEvidence(LensKind.PURITY, "mod::func", 0.8, "support", "pure"),
            LensEvidence(LensKind.MUTATION, "mod::func", 0.6, "support", "survival"),
            LensEvidence(LensKind.COHESION, "mod::func", 0.5, "support", "low"),
        ]
        convergence = aggregate(evidence)
        enriched = enrich_decomposition_candidates([candidate], convergence)
        assert len(enriched) == 1
        assert enriched[0].confidence > 0.6
        assert any("convergence:" in e for e in enriched[0].evidence)

    def test_no_match(self):
        from lintgate.mutation.decomposition import DecompositionCandidate

        candidate = DecompositionCandidate(
            function_id="other::func",
            file_path="other.py",
            survival_rate=0.6,
            surviving_categories=["a"],
            total_mutants=5,
            reason="test",
            confidence=0.6,
        )
        evidence = [
            LensEvidence(LensKind.PURITY, "mod::func", 0.8, "support", "pure"),
        ]
        convergence = aggregate(evidence)
        enriched = enrich_decomposition_candidates([candidate], convergence)
        assert enriched[0].confidence == 0.6  # unchanged


# ── TestLensKindEnum ─────────────────────────────────────────────────


class TestLensKindEnum:
    def test_eleven_values(self):
        assert len(LensKind) == 11

    def test_string_values(self):
        assert LensKind.PURITY.value == "purity"
        assert LensKind.CROSS_CHANNEL.value == "cross_channel"


class TestActionabilityEnum:
    def test_three_values(self):
        assert len(Actionability) == 3

    def test_string_values(self):
        assert Actionability.EXTRACT.value == "extract"
        assert Actionability.SPLIT.value == "split"
        assert Actionability.INVESTIGATE.value == "investigate"


# ── File-level adapter tests ────────────────────────────────────────


class TestAdaptCohesionFile:
    def test_low_cohesion(self):
        data = {"big.py": {"score": 0.2, "component_count": 4}}
        results = adapt_cohesion_file(data)
        assert len(results) == 1
        assert results[0].signal == "support"
        assert results[0].target == "big.py"
        assert results[0].confidence == pytest.approx(0.8)

    def test_high_cohesion_skipped(self):
        data = {"good.py": {"score": 0.9, "component_count": 1}}
        assert adapt_cohesion_file(data) == []

    def test_empty(self):
        assert adapt_cohesion_file({}) == []
        assert adapt_cohesion_file(None) == []


class TestAdaptFanInFile:
    def test_high_fan_in_opposes(self):
        data = {"mod": 10}
        results = adapt_fan_in_file(data, threshold=5)
        assert len(results) == 1
        assert results[0].signal == "oppose"

    def test_low_fan_in_supports(self):
        data = {"mod": 1}
        results = adapt_fan_in_file(data, threshold=5)
        assert len(results) == 1
        assert results[0].signal == "support"
        assert results[0].confidence == 0.4

    def test_mid_range_skipped(self):
        data = {"mod": 3}
        assert adapt_fan_in_file(data, threshold=5) == []

    def test_empty(self):
        assert adapt_fan_in_file({}) == []
        assert adapt_fan_in_file(None) == []


class TestAdaptCochangeFile:
    def test_coupled_pair(self):
        data = {
            "pairs": [{"file_a": "a.py", "file_b": "b.py", "coupling_strength": 0.8}]
        }
        results = adapt_cochange_file(data, "a.py", threshold=0.4)
        assert len(results) == 1
        assert results[0].signal == "oppose"
        assert results[0].target == "a.py"

    def test_irrelevant_pair_skipped(self):
        data = {
            "pairs": [{"file_a": "x.py", "file_b": "y.py", "coupling_strength": 0.9}]
        }
        results = adapt_cochange_file(data, "a.py", threshold=0.4)
        assert results == []

    def test_empty(self):
        assert adapt_cochange_file({}, "a.py") == []
        assert adapt_cochange_file(None, "a.py") == []


class TestAdaptImportWeightFile:
    def test_io_opposes(self):
        data = {"mod": {"has_module_level_io": True, "depth": 2, "non_stdlib_deps": 5}}
        results = adapt_import_weight_file(data)
        assert len(results) == 1
        assert results[0].signal == "oppose"

    def test_shallow_supports(self):
        data = {"mod": {"has_module_level_io": False, "depth": 1, "non_stdlib_deps": 1}}
        results = adapt_import_weight_file(data)
        assert len(results) == 1
        assert results[0].signal == "support"

    def test_empty(self):
        assert adapt_import_weight_file({}) == []
        assert adapt_import_weight_file(None) == []


class TestAggregateFile:
    def test_empty(self):
        assert aggregate_file([]) == []

    def test_weighted_cohesion(self):
        # Cohesion weight is 2.0, so 0.4 conf → 0.8 effective
        evidence = [
            LensEvidence(LensKind.COHESION, "big.py", 0.4, "support", "low cohesion"),
        ]
        results = aggregate_file(evidence)
        assert len(results) == 1
        assert results[0].support_prob == pytest.approx(0.8)

    def test_weighted_fan_in(self):
        # Fan-in weight is 1.5, so 0.4 conf → 0.6 effective
        evidence = [
            LensEvidence(LensKind.FAN_IN, "big.py", 0.4, "oppose", "high fan-in"),
        ]
        results = aggregate_file(evidence)
        assert len(results) == 1
        assert results[0].oppose_prob == pytest.approx(0.6)

    def test_target_type_is_file(self):
        evidence = [
            LensEvidence(LensKind.COHESION, "big.py", 0.6, "support", "low"),
        ]
        results = aggregate_file(evidence)
        assert results[0].target_type == "file"

    def test_split_proposals_attached(self):
        evidence = [
            LensEvidence(LensKind.COHESION, "big.py", 0.6, "support", "low"),
        ]
        proposals = [{"kind": "split_file", "target": "big.py", "action": "split"}]
        results = aggregate_file(evidence, split_proposals_map={"big.py": proposals})
        assert results[0].split_proposals == proposals


class TestClassifyFileActionability:
    def test_low_cohesion_low_fan_in_split(self):
        # cohesion<0.3, 3+ components, net>=0.5, 2+ lenses → SPLIT
        cohesion = {"score": 0.2, "component_count": 4}
        assert classify_file_actionability(0.6, 2, cohesion) == Actionability.SPLIT

    def test_low_cohesion_high_fan_in_investigate(self):
        # Without meeting all criteria → INVESTIGATE
        cohesion = {"score": 0.2, "component_count": 4}
        assert (
            classify_file_actionability(0.3, 1, cohesion) == Actionability.INVESTIGATE
        )

    def test_high_cohesion_investigate(self):
        cohesion = {"score": 0.8, "component_count": 1}
        assert (
            classify_file_actionability(0.6, 2, cohesion) == Actionability.INVESTIGATE
        )

    def test_high_net_many_lenses_split(self):
        # net>=0.75 + 3+ lenses → SPLIT regardless of cohesion
        assert classify_file_actionability(0.8, 3) == Actionability.SPLIT


class TestExtractFileEvidence:
    def _make_channel_result(self, channel, metrics=None, findings=None):
        from lintgate.controlplane.types import ChannelResult

        return ChannelResult(
            channel=channel,
            status="pass",
            severity="none",
            findings=findings or [],
            metrics=metrics or {},
        )

    def test_structure_channel_cohesion(self):
        crs = [
            self._make_channel_result(
                "structure",
                metrics={
                    "_file_cohesion": {
                        "big.py": {
                            "score": 0.2,
                            "component_count": 4,
                            "split_proposals": [],
                        },
                    },
                    "_module_fan_in": {"big.py": 1},
                },
            ),
        ]
        results = extract_file_evidence(crs)
        assert len(results) == 1
        assert results[0].target == "big.py"
        assert results[0].target_type == "file"

    def test_lint_channel_fallback(self):
        crs = [
            self._make_channel_result("structure", metrics={}),
            self._make_channel_result(
                "lint",
                metrics={
                    "_file_cohesion": {
                        "fallback.py": {
                            "score": 0.3,
                            "component_count": 3,
                            "split_proposals": [],
                        },
                    },
                },
            ),
        ]
        results = extract_file_evidence(crs)
        assert len(results) == 1
        assert results[0].target == "fallback.py"

    def test_empty_channels(self):
        crs = [self._make_channel_result("lint")]
        assert extract_file_evidence(crs) == []


class TestFileConvergenceToMetrics:
    def test_format(self):
        evidence = [
            LensEvidence(LensKind.COHESION, "big.py", 0.8, "support", "low"),
            LensEvidence(LensKind.FAN_IN, "big.py", 0.4, "support", "low fan-in"),
        ]
        results = aggregate_file(evidence)
        metrics = file_convergence_to_metrics(results)
        assert "total_files" in metrics
        assert "actionable_split" in metrics
        assert "actionable_investigate" in metrics
        assert "top_files" in metrics
        assert metrics["total_files"] == 1

    def test_empty(self):
        metrics = file_convergence_to_metrics([])
        assert metrics["total_files"] == 0
