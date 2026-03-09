"""Tests for B3: Optimization Opportunity Synthesizer."""

from __future__ import annotations

from lintgate.convergence.evidence import (
    Actionability,
    ConvergenceResult,
    LensKind,
)
from lintgate.convergence.extraction_plan import ExtractionPlan, ExtractionStep
from lintgate.convergence.projector import ProjectedOpportunity
from lintgate.convergence.synthesizer import (
    OptimizationLandscape,
    _compute_dependency_order,
    _topo_sort_weighted,
    _unlock_value,
    synthesize_landscape,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _make_plan(
    source_function: str = "mod.py::func",
    steps: int = 3,
    cc_reduction: int = 10,
    opportunities: list[ProjectedOpportunity] | None = None,
) -> ExtractionPlan:
    plan = ExtractionPlan(
        source_function=source_function,
        source_file=source_function.split("::")[0] if "::" in source_function else "mod.py",
        steps=[
            ExtractionStep(order=i + 1, action="create_function", target=f"_f{i}")
            for i in range(steps)
        ],
        estimated_impact={"CC_reduction": cc_reduction},
    )
    plan.post_extraction_opportunities = opportunities or []
    return plan


def _make_convergence(target: str = "mod.py::func") -> ConvergenceResult:
    return ConvergenceResult(
        target=target,
        support_prob=0.8,
        oppose_prob=0.0,
        net_confidence=0.8,
        supporting_lenses=[LensKind.PURITY],
        opposing_lenses=[],
        actionability=Actionability.EXTRACT,
    )


def _cacheable(func_id: str, confidence: float = 0.8) -> ProjectedOpportunity:
    return ProjectedOpportunity(
        function_id=func_id,
        opportunity="cacheable",
        confidence=confidence,
        precondition=f"requires extraction from {func_id}",
        evidence=["projected_pure"],
    )


def _parallelizable(func_id: str, confidence: float = 0.7) -> ProjectedOpportunity:
    return ProjectedOpportunity(
        function_id=func_id,
        opportunity="parallelizable",
        confidence=confidence,
        precondition=f"requires extraction from {func_id}",
        evidence=["no_shared_mutable_state"],
    )


def _jit(func_id: str, confidence: float = 0.6) -> ProjectedOpportunity:
    return ProjectedOpportunity(
        function_id=func_id,
        opportunity="jit_candidate",
        confidence=confidence,
        precondition=f"requires extraction from {func_id}",
        evidence=["numeric_operations"],
    )


def _lazy(func_id: str, confidence: float = 0.5) -> ProjectedOpportunity:
    return ProjectedOpportunity(
        function_id=func_id,
        opportunity="lazy_evaluable",
        confidence=confidence,
        precondition="requires extraction",
        evidence=["idempotent_property"],
    )


def _testable(func_id: str, confidence: float = 0.95) -> ProjectedOpportunity:
    return ProjectedOpportunity(
        function_id=func_id,
        opportunity="directly_testable",
        confidence=confidence,
        precondition="requires extraction",
        evidence=["all_dependencies_explicit_params"],
    )


# ── OptimizationLandscape tests ───────────────────────────────────────


class TestOptimizationLandscape:
    def test_to_dict(self):
        ls = OptimizationLandscape(
            cacheable_functions=[_cacheable("_f1")],
            parallelizable_groups=[["_f1", "_f2"]],
            jit_candidates=[_jit("_f3")],
            lazy_candidates=[],
            directly_testable=[_testable("_f4")],
            total_decomposition_steps=10,
            estimated_cc_reduction=25.0,
            dependency_order=["mod.py::a", "mod.py::b"],
        )
        d = ls.to_dict()
        assert len(d["cacheable_functions"]) == 1
        assert d["parallelizable_groups"] == [["_f1", "_f2"]]
        assert len(d["jit_candidates"]) == 1
        assert d["lazy_candidates"] == []
        assert len(d["directly_testable"]) == 1
        assert d["total_decomposition_steps"] == 10
        assert d["estimated_cc_reduction"] == 25.0
        assert d["dependency_order"] == ["mod.py::a", "mod.py::b"]

    def test_empty_landscape(self):
        ls = OptimizationLandscape()
        d = ls.to_dict()
        assert d["cacheable_functions"] == []
        assert d["total_decomposition_steps"] == 0
        assert d["estimated_cc_reduction"] == 0.0


# ── Synthesize landscape tests ────────────────────────────────────────


class TestSynthesizeLandscape:
    def test_categorizes_opportunities(self):
        """Opportunities are correctly categorized by type."""
        plans = [
            _make_plan(
                "m.py::f1",
                opportunities=[
                    _cacheable("_compute"),
                    _jit("_math"),
                    _testable("_impl_handle"),
                ],
            ),
            _make_plan(
                "m.py::f2",
                opportunities=[
                    _cacheable("_transform"),
                    _lazy("_memo"),
                    _parallelizable("m.py::f2 extracted"),
                ],
            ),
        ]
        results = [_make_convergence("m.py::f1"), _make_convergence("m.py::f2")]

        landscape = synthesize_landscape(results, plans)

        assert len(landscape.cacheable_functions) == 2
        assert len(landscape.jit_candidates) == 1
        assert len(landscape.lazy_candidates) == 1
        assert len(landscape.directly_testable) == 1
        assert len(landscape.parallelizable_groups) == 1

    def test_aggregate_metrics(self):
        """Total steps and CC reduction sum correctly across plans."""
        plans = [
            _make_plan("m.py::f1", steps=4, cc_reduction=15),
            _make_plan("m.py::f2", steps=3, cc_reduction=10),
            _make_plan("m.py::f3", steps=2, cc_reduction=5),
        ]
        results = []

        landscape = synthesize_landscape(results, plans)

        assert landscape.total_decomposition_steps == 9  # 4+3+2
        assert landscape.estimated_cc_reduction == 30.0  # 15+10+5

    def test_empty_plans(self):
        """No plans → empty landscape."""
        landscape = synthesize_landscape([], [])

        assert landscape.cacheable_functions == []
        assert landscape.total_decomposition_steps == 0
        assert landscape.estimated_cc_reduction == 0.0
        assert landscape.dependency_order == []

    def test_plans_without_opportunities(self):
        """Plans with no opportunities → empty categories but valid metrics."""
        plans = [_make_plan("m.py::f1", steps=5, cc_reduction=20)]
        landscape = synthesize_landscape([], plans)

        assert landscape.cacheable_functions == []
        assert landscape.total_decomposition_steps == 5
        assert landscape.estimated_cc_reduction == 20.0


# ── Dependency ordering tests ─────────────────────────────────────────


class TestDependencyOrder:
    def test_highest_unlock_value_first(self):
        """Plan with most downstream opportunities goes first."""
        plans = [
            _make_plan("m.py::low", opportunities=[_cacheable("_a", 0.3)]),
            _make_plan(
                "m.py::high",
                opportunities=[
                    _cacheable("_b", 0.9),
                    _jit("_c", 0.8),
                    _testable("_d", 0.95),
                ],
            ),
        ]
        results = []

        landscape = synthesize_landscape(results, plans)

        # "high" has more unlock value → should be first
        assert landscape.dependency_order[0] == "m.py::high"

    def test_all_plans_in_order(self):
        """All plans appear in dependency_order."""
        plans = [
            _make_plan("m.py::a"),
            _make_plan("m.py::b"),
            _make_plan("m.py::c"),
        ]
        landscape = synthesize_landscape([], plans)

        assert len(landscape.dependency_order) == 3
        assert set(landscape.dependency_order) == {"m.py::a", "m.py::b", "m.py::c"}

    def test_empty_order(self):
        """No plans → empty order."""
        order = _compute_dependency_order([], [])
        assert order == []


# ── Topological sort tests ────────────────────────────────────────────


class TestTopoSort:
    def test_no_edges(self):
        """No dependencies → sorted by score descending."""
        edges = {"a": set(), "b": set(), "c": set()}
        scores = {"a": 1.0, "b": 3.0, "c": 2.0}
        result = _topo_sort_weighted(edges, scores)
        assert result == ["b", "c", "a"]

    def test_linear_chain(self):
        """A → B → C respects dependency order."""
        edges = {"a": set(), "b": {"a"}, "c": {"b"}}
        scores = {"a": 1.0, "b": 2.0, "c": 3.0}
        result = _topo_sort_weighted(edges, scores)
        assert result.index("a") < result.index("b")
        assert result.index("b") < result.index("c")

    def test_diamond(self):
        """Diamond: A→B, A→C, B→D, C→D. A first, D last."""
        edges = {"a": set(), "b": {"a"}, "c": {"a"}, "d": {"b", "c"}}
        scores = {"a": 1.0, "b": 2.0, "c": 2.0, "d": 3.0}
        result = _topo_sort_weighted(edges, scores)
        assert result[0] == "a"
        assert result[-1] == "d"

    def test_tie_breaking_by_score(self):
        """When multiple nodes are ready, highest score goes first."""
        edges = {"x": set(), "y": set()}
        scores = {"x": 0.5, "y": 0.9}
        result = _topo_sort_weighted(edges, scores)
        assert result[0] == "y"


# ── Unlock value tests ────────────────────────────────────────────────


class TestUnlockValue:
    def test_sum_of_confidences(self):
        plan = _make_plan(
            opportunities=[
                _cacheable("_a", 0.8),
                _jit("_b", 0.6),
            ]
        )
        assert abs(_unlock_value(plan) - 1.4) < 0.01

    def test_no_opportunities(self):
        plan = _make_plan(opportunities=[])
        assert _unlock_value(plan) == 0.0


# ── Parallelizable groups tests ───────────────────────────────────────


class TestParallelizableGroups:
    def test_group_from_parallelizable_opportunity(self):
        """Parallelizable opportunity creates a group entry."""
        plans = [
            _make_plan(
                "m.py::f",
                opportunities=[
                    _parallelizable("m.py::f extracted functions"),
                ],
            ),
        ]
        landscape = synthesize_landscape([], plans)

        assert len(landscape.parallelizable_groups) == 1
        assert "m.py::f extracted functions" in landscape.parallelizable_groups[0]

    def test_multiple_groups(self):
        """Multiple parallelizable opportunities → multiple groups."""
        plans = [
            _make_plan("m.py::f1", opportunities=[_parallelizable("group_a")]),
            _make_plan("m.py::f2", opportunities=[_parallelizable("group_b")]),
        ]
        landscape = synthesize_landscape([], plans)

        assert len(landscape.parallelizable_groups) == 2
