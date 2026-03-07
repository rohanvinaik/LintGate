"""Tests for composition analysis — gamma, integration surface, sheaf condition."""

from __future__ import annotations

from lintgate.specification.call_graph import CrossModuleCallGraph
from lintgate.specification.composition import (
    analyze_composition,
    compute_composition_edge,
    compute_integration_surface,
)
from lintgate.specification.types import (
    ASTMetrics,
    FunctionSpecification,
    SpecCore,
    SpecificationLedger,
    TestabilityProfile,
)


def _make_ledger(funcs: dict[str, dict]) -> SpecificationLedger:
    """Build a ledger from a simplified dict."""
    ledger = SpecificationLedger()
    for key, info in funcs.items():
        ledger.functions[key] = FunctionSpecification(
            function_key=key,
            core=SpecCore(
                estimated_sigma=info.get("sigma", 5),
                specification_level=info.get("spec_level", 0.5),
                is_pure=info.get("is_pure", False),
                regime=info.get("regime", "A"),
            ),
            ast_metrics=ASTMetrics(
                parameter_count=info.get("params", 2),
            ),
            testability=TestabilityProfile(
                is_stateful=info.get("stateful", False),
            ),
        )
    ledger.update_metrics()
    return ledger


def _make_graph(edges: dict[str, list[str]]) -> CrossModuleCallGraph:
    """Build a call graph from a simple edge dict."""
    graph = CrossModuleCallGraph()
    for caller, callees in edges.items():
        for callee in callees:
            graph.calls.setdefault(caller, set()).add(callee)
            graph.called_by.setdefault(callee, set()).add(caller)
    return graph


class TestIntegrationSurface:
    def test_basic_surface(self):
        ledger = _make_ledger({
            "a::caller": {"params": 2},
            "b::callee": {"params": 3},
        })
        graph = _make_graph({"a::caller": ["b::callee"]})
        surface = compute_integration_surface("a::caller", "b::callee", graph, ledger)
        assert surface.callee_param_count == 3
        assert surface.surface_area > 0
        assert surface.interface_complexity > 0

    def test_shared_mutable_state(self):
        ledger = _make_ledger({
            "a::caller": {"params": 2, "stateful": True},
            "b::callee": {"params": 2, "stateful": True},
        })
        graph = _make_graph({"a::caller": ["b::callee"]})
        surface = compute_integration_surface("a::caller", "b::callee", graph, ledger)
        assert surface.shared_mutable_state is True
        assert surface.interface_complexity > surface.surface_area


class TestCompositionEdge:
    def test_spec_independent_pure_high_spec(self):
        ledger = _make_ledger({
            "a::caller": {"spec_level": 0.5},
            "b::callee": {"spec_level": 0.96, "is_pure": True},
        })
        graph = _make_graph({"a::caller": ["b::callee"]})
        edge = compute_composition_edge("a::caller", "b::callee", graph, ledger)
        assert edge.specification_independent is True
        assert edge.gamma == 0.0

    def test_nonzero_gamma_for_impure(self):
        ledger = _make_ledger({
            "a::caller": {"spec_level": 0.5},
            "b::callee": {"spec_level": 0.3, "is_pure": False, "params": 3},
        })
        graph = _make_graph({"a::caller": ["b::callee"]})
        edge = compute_composition_edge("a::caller", "b::callee", graph, ledger)
        assert edge.gamma > 0.0
        assert edge.specification_independent is False

    def test_gamma_decreases_with_spec_level(self):
        ledger_low = _make_ledger({
            "a::caller": {},
            "b::callee": {"spec_level": 0.2, "params": 3},
        })
        ledger_high = _make_ledger({
            "a::caller": {},
            "b::callee": {"spec_level": 0.8, "params": 3},
        })
        graph = _make_graph({"a::caller": ["b::callee"]})
        edge_low = compute_composition_edge("a::caller", "b::callee", graph, ledger_low)
        edge_high = compute_composition_edge("a::caller", "b::callee", graph, ledger_high)
        assert edge_low.gamma > edge_high.gamma


class TestAnalyzeComposition:
    def test_same_module_no_edges(self):
        ledger = _make_ledger({
            "a::f1": {"spec_level": 0.5},
            "a::f2": {"spec_level": 0.5},
        })
        graph = _make_graph({"a::f1": ["a::f2"]})
        result = analyze_composition(graph, ledger)
        assert len(result.edges) == 0

    def test_cross_module_edges(self):
        ledger = _make_ledger({
            "a::f1": {"spec_level": 0.5},
            "b::f2": {"spec_level": 0.3, "params": 3},
        })
        graph = _make_graph({"a::f1": ["b::f2"]})
        result = analyze_composition(graph, ledger)
        assert len(result.edges) == 1
        assert result.total_gamma > 0

    def test_sheaf_holds_low_gamma(self):
        ledger = _make_ledger({
            "a::f1": {},
            "b::f2": {"spec_level": 0.9, "is_pure": True},
        })
        graph = _make_graph({"a::f1": ["b::f2"]})
        result = analyze_composition(graph, ledger)
        assert result.sheaf_holds is True

    def test_to_dict(self):
        ledger = _make_ledger({
            "a::f1": {},
            "b::f2": {"spec_level": 0.3, "params": 3},
        })
        graph = _make_graph({"a::f1": ["b::f2"]})
        result = analyze_composition(graph, ledger)
        d = result.to_dict()
        assert "total_gamma" in d
        assert "sheaf_holds" in d
        assert "composition_gaps" in d

    def test_empty_graph(self):
        ledger = _make_ledger({"a::f1": {}})
        graph = CrossModuleCallGraph()
        result = analyze_composition(graph, ledger)
        assert len(result.edges) == 0
        assert result.sheaf_holds is True
