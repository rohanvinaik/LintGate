"""Tests for lintgate.specification.composition module."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from lintgate.specification.composition import (
    CompositionResult,
    analyze_composition,
    compute_composition_edge,
    compute_integration_surface,
)
from lintgate.specification.types import (
    ASTMetrics,
    CompositionEdge,
    FunctionSpecification,
    IntegrationSurface,
    ModuleSpecification,
    SpecCore,
    SpecificationLedger,
    TestabilityProfile,
)

# ---------------------------------------------------------------------------
# Helpers — lightweight stand-ins for CrossModuleCallGraph
# ---------------------------------------------------------------------------


@dataclass
class _FakeCallGraph:
    """Minimal stand-in for CrossModuleCallGraph used by composition functions."""

    calls: dict[str, set[str]] = field(default_factory=dict)
    called_by: dict[str, set[str]] = field(default_factory=dict)


def _fake_graph(**kwargs: Any) -> Any:
    """Create a _FakeCallGraph typed as Any to satisfy duck-typing."""
    return _FakeCallGraph(**kwargs)


def _make_func_spec(
    key: str = "",
    param_count: int = 2,
    spec_level: float = 0.5,
    is_pure: bool = False,
    is_stateful: bool = False,
    estimated_sigma: int = 3,
) -> FunctionSpecification:
    """Create a FunctionSpecification with controlled defaults."""
    return FunctionSpecification(
        function_key=key,
        core=SpecCore(
            estimated_sigma=estimated_sigma,
            specification_level=spec_level,
            is_pure=is_pure,
        ),
        ast_metrics=ASTMetrics(parameter_count=param_count),
        testability=TestabilityProfile(is_stateful=is_stateful),
    )


def _make_ledger(functions: dict[str, FunctionSpecification]) -> SpecificationLedger:
    """Build a SpecificationLedger from a dict of function specs."""
    return SpecificationLedger(functions=functions)


# =========================================================================
# compute_integration_surface
# =========================================================================


class TestComputeIntegrationSurface:
    """Tests for compute_integration_surface."""

    def test_basic_surface_area(self):
        """Surface area = param_count * max(param_count, 1)."""
        callee = _make_func_spec(key="mod_b::func_b", param_count=3)
        ledger = _make_ledger(
            {"mod_a::func_a": _make_func_spec(key="mod_a::func_a"), "mod_b::func_b": callee}
        )
        graph = _fake_graph()

        surface = compute_integration_surface("mod_a::func_a", "mod_b::func_b", graph, ledger)

        assert surface.caller == "mod_a::func_a"
        assert surface.callee == "mod_b::func_b"
        assert surface.callee_param_count == 3
        assert surface.call_arg_count == 3
        assert surface.surface_area == 9.0  # 3 * 3
        assert surface.shared_mutable_state is False
        assert surface.type_boundary_crossing is False
        assert surface.interface_complexity == 9.0  # no shared state multiplier

    def test_shared_mutable_state_multiplier(self):
        """When both caller and callee are stateful, complexity *= 1.5."""
        caller = _make_func_spec(key="mod_a::f", param_count=2, is_stateful=True)
        callee = _make_func_spec(key="mod_b::g", param_count=2, is_stateful=True)
        ledger = _make_ledger({"mod_a::f": caller, "mod_b::g": callee})
        graph = _fake_graph()

        surface = compute_integration_surface("mod_a::f", "mod_b::g", graph, ledger)

        assert surface.shared_mutable_state is True
        assert surface.surface_area == 4.0  # 2 * 2
        assert surface.interface_complexity == 6.0  # 4.0 * 1.5

    def test_only_caller_stateful_no_multiplier(self):
        """Shared mutable state requires BOTH to be stateful."""
        caller = _make_func_spec(key="mod_a::f", param_count=2, is_stateful=True)
        callee = _make_func_spec(key="mod_b::g", param_count=2, is_stateful=False)
        ledger = _make_ledger({"mod_a::f": caller, "mod_b::g": callee})
        graph = _fake_graph()

        surface = compute_integration_surface("mod_a::f", "mod_b::g", graph, ledger)

        assert surface.shared_mutable_state is False
        assert surface.interface_complexity == 4.0

    def test_only_callee_stateful_no_multiplier(self):
        """Shared mutable state requires BOTH to be stateful."""
        caller = _make_func_spec(key="mod_a::f", param_count=2, is_stateful=False)
        callee = _make_func_spec(key="mod_b::g", param_count=2, is_stateful=True)
        ledger = _make_ledger({"mod_a::f": caller, "mod_b::g": callee})
        graph = _fake_graph()

        surface = compute_integration_surface("mod_a::f", "mod_b::g", graph, ledger)

        assert surface.shared_mutable_state is False
        assert surface.interface_complexity == 4.0

    def test_callee_not_in_ledger_returns_zero_params(self):
        """If callee is not in the ledger, param_count defaults to 0."""
        caller = _make_func_spec(key="mod_a::f", param_count=2)
        ledger = _make_ledger({"mod_a::f": caller})
        graph = _fake_graph()

        surface = compute_integration_surface("mod_a::f", "mod_b::missing", graph, ledger)

        assert surface.callee_param_count == 0
        assert surface.call_arg_count == 0
        # surface_area = 0 * max(0, 1) = 0
        assert surface.surface_area == 0.0
        assert surface.interface_complexity == 0.0

    def test_caller_not_in_ledger(self):
        """If caller is not in the ledger, shared_mutable_state is False."""
        callee = _make_func_spec(key="mod_b::g", param_count=3, is_stateful=True)
        ledger = _make_ledger({"mod_b::g": callee})
        graph = _fake_graph()

        surface = compute_integration_surface("mod_a::missing", "mod_b::g", graph, ledger)

        assert surface.shared_mutable_state is False
        assert surface.callee_param_count == 3
        assert surface.surface_area == 9.0

    def test_single_param_callee(self):
        """Surface area = 1 * max(1, 1) = 1 for single-param callee."""
        callee = _make_func_spec(key="mod_b::g", param_count=1)
        ledger = _make_ledger({"mod_a::f": _make_func_spec(key="mod_a::f"), "mod_b::g": callee})
        graph = _fake_graph()

        surface = compute_integration_surface("mod_a::f", "mod_b::g", graph, ledger)

        assert surface.surface_area == 1.0
        assert surface.interface_complexity == 1.0

    def test_zero_param_callee(self):
        """Zero-param callee: surface_area = 0 * max(0, 1) = 0."""
        callee = _make_func_spec(key="mod_b::g", param_count=0)
        ledger = _make_ledger({"mod_a::f": _make_func_spec(key="mod_a::f"), "mod_b::g": callee})
        graph = _fake_graph()

        surface = compute_integration_surface("mod_a::f", "mod_b::g", graph, ledger)

        assert surface.surface_area == 0.0


# =========================================================================
# compute_composition_edge
# =========================================================================


class TestComputeCompositionEdge:
    """Tests for compute_composition_edge."""

    def test_basic_gamma_calculation(self):
        """Gamma = interface_complexity * (1 - spec_level)."""
        callee = _make_func_spec(key="mod_b::g", param_count=2, spec_level=0.5)
        ledger = _make_ledger({"mod_a::f": _make_func_spec(key="mod_a::f"), "mod_b::g": callee})
        graph = _fake_graph()

        edge = compute_composition_edge("mod_a::f", "mod_b::g", graph, ledger)

        # surface_area = 2*2 = 4, complexity = 4.0
        # gamma = 4.0 * (1 - 0.5) = 2.0
        assert edge.gamma == 2.0
        assert edge.specification_independent is False

    def test_pure_high_spec_level_is_spec_independent(self):
        """Pure callee with spec_level >= 0.95 -> gamma = 0, spec_independent = True."""
        callee = _make_func_spec(key="mod_b::g", param_count=3, spec_level=0.96, is_pure=True)
        ledger = _make_ledger({"mod_a::f": _make_func_spec(key="mod_a::f"), "mod_b::g": callee})
        graph = _fake_graph()

        edge = compute_composition_edge("mod_a::f", "mod_b::g", graph, ledger)

        assert edge.gamma == 0.0
        assert edge.specification_independent is True

    def test_pure_at_threshold_is_spec_independent(self):
        """Pure callee with spec_level == 0.95 (exact threshold) -> spec_independent."""
        callee = _make_func_spec(key="mod_b::g", param_count=2, spec_level=0.95, is_pure=True)
        ledger = _make_ledger({"mod_a::f": _make_func_spec(key="mod_a::f"), "mod_b::g": callee})
        graph = _fake_graph()

        edge = compute_composition_edge("mod_a::f", "mod_b::g", graph, ledger)

        assert edge.gamma == 0.0
        assert edge.specification_independent is True

    def test_pure_below_threshold_not_spec_independent(self):
        """Pure callee with spec_level < 0.95 -> not spec_independent."""
        callee = _make_func_spec(key="mod_b::g", param_count=2, spec_level=0.94, is_pure=True)
        ledger = _make_ledger({"mod_a::f": _make_func_spec(key="mod_a::f"), "mod_b::g": callee})
        graph = _fake_graph()

        edge = compute_composition_edge("mod_a::f", "mod_b::g", graph, ledger)

        # gamma = 4.0 * (1 - 0.94) = 4.0 * 0.06 = 0.24
        assert edge.gamma == 0.24
        assert edge.specification_independent is False

    def test_impure_high_spec_level_not_spec_independent(self):
        """Impure callee with high spec_level -> not spec_independent."""
        callee = _make_func_spec(key="mod_b::g", param_count=2, spec_level=0.99, is_pure=False)
        ledger = _make_ledger({"mod_a::f": _make_func_spec(key="mod_a::f"), "mod_b::g": callee})
        graph = _fake_graph()

        edge = compute_composition_edge("mod_a::f", "mod_b::g", graph, ledger)

        # gamma = 4.0 * (1 - 0.99) = 4.0 * 0.01 = 0.04
        assert edge.gamma == 0.04
        assert edge.specification_independent is False

    def test_callee_not_in_ledger_defaults(self):
        """Missing callee: spec_level=0.0, is_pure=False -> full gamma."""
        caller = _make_func_spec(key="mod_a::f", param_count=2)
        ledger = _make_ledger({"mod_a::f": caller})
        graph = _fake_graph()

        edge = compute_composition_edge("mod_a::f", "mod_b::missing", graph, ledger)

        # callee has 0 params -> surface_area = 0, complexity = 0
        # gamma = 0 * (1 - 0) = 0
        assert edge.gamma == 0.0
        assert edge.specification_independent is False

    def test_zero_spec_level_full_gamma(self):
        """spec_level = 0 -> gamma = full interface_complexity."""
        callee = _make_func_spec(key="mod_b::g", param_count=2, spec_level=0.0)
        ledger = _make_ledger({"mod_a::f": _make_func_spec(key="mod_a::f"), "mod_b::g": callee})
        graph = _fake_graph()

        edge = compute_composition_edge("mod_a::f", "mod_b::g", graph, ledger)

        # gamma = 4.0 * 1.0 = 4.0
        assert edge.gamma == 4.0

    def test_gamma_rounded_to_three_decimals(self):
        """Gamma values are rounded to 3 decimal places."""
        callee = _make_func_spec(key="mod_b::g", param_count=3, spec_level=0.333)
        ledger = _make_ledger({"mod_a::f": _make_func_spec(key="mod_a::f"), "mod_b::g": callee})
        graph = _fake_graph()

        edge = compute_composition_edge("mod_a::f", "mod_b::g", graph, ledger)

        # complexity = 9.0, gamma = 9.0 * (1 - 0.333) = 9.0 * 0.667 = 6.003
        assert edge.gamma == 6.003

    def test_edge_has_interface_mutant_count(self):
        """Edge includes interface_mutant_count from mutation point counting."""
        callee = _make_func_spec(key="mod_b::g", param_count=3, spec_level=0.0)
        ledger = _make_ledger({"mod_a::f": _make_func_spec(key="mod_a::f"), "mod_b::g": callee})
        graph = _fake_graph()

        edge = compute_composition_edge("mod_a::f", "mod_b::g", graph, ledger)

        # spec_level=0 -> uncertainty=1.0, raw=3+3+0=6, mutant_count=6
        assert edge.interface_mutant_count == 6


# =========================================================================
# analyze_composition
# =========================================================================


class TestAnalyzeComposition:
    """Tests for analyze_composition."""

    def test_empty_ledger_and_graph(self):
        """Empty inputs produce empty results with sheaf_holds=True."""
        ledger = _make_ledger({})
        graph = _fake_graph()

        result = analyze_composition(graph, ledger)

        assert result.edges == []
        assert result.modules == []
        assert result.total_gamma == 0.0
        assert result.sheaf_holds is True
        assert result.sheaf_obstruction == 0.0

    def test_no_cross_module_edges(self):
        """Functions in same module produce no edges."""
        ledger = _make_ledger(
            {
                "mod_a::f": _make_func_spec(key="mod_a::f"),
                "mod_a::g": _make_func_spec(key="mod_a::g"),
            }
        )
        graph = _fake_graph(calls={"mod_a::f": {"mod_a::g"}})

        result = analyze_composition(graph, ledger)

        assert result.edges == []
        assert len(result.modules) == 1  # one module: mod_a
        assert result.total_gamma == 0.0

    def test_cross_module_edge_detected(self):
        """Cross-module calls produce composition edges."""
        ledger = _make_ledger(
            {
                "mod_a::f": _make_func_spec(key="mod_a::f", param_count=2),
                "mod_b::g": _make_func_spec(key="mod_b::g", param_count=2, spec_level=0.5),
            }
        )
        graph = _fake_graph(calls={"mod_a::f": {"mod_b::g"}})

        result = analyze_composition(graph, ledger)

        assert len(result.edges) == 1
        assert result.edges[0].caller == "mod_a::f"
        assert result.edges[0].callee == "mod_b::g"
        assert result.edges[0].gamma == 2.0  # 4.0 * 0.5

    def test_callee_not_in_ledger_skipped(self):
        """Callees not in ledger.functions are skipped."""
        ledger = _make_ledger({"mod_a::f": _make_func_spec(key="mod_a::f")})
        graph = _fake_graph(calls={"mod_a::f": {"mod_b::unknown"}})

        result = analyze_composition(graph, ledger)

        assert result.edges == []

    def test_sheaf_holds_when_below_threshold(self):
        """Sheaf condition holds when total_gamma < 5.0."""
        ledger = _make_ledger(
            {
                "mod_a::f": _make_func_spec(key="mod_a::f", param_count=2),
                "mod_b::g": _make_func_spec(key="mod_b::g", param_count=2, spec_level=0.5),
            }
        )
        graph = _fake_graph(calls={"mod_a::f": {"mod_b::g"}})

        result = analyze_composition(graph, ledger)

        # gamma = 4.0 * 0.5 = 2.0 < 5.0
        assert result.sheaf_holds is True
        assert result.total_gamma == 2.0

    def test_sheaf_fails_when_above_threshold(self):
        """Sheaf condition fails when total_gamma >= 5.0."""
        ledger = _make_ledger(
            {
                "mod_a::f": _make_func_spec(key="mod_a::f", param_count=3),
                "mod_b::g": _make_func_spec(key="mod_b::g", param_count=3, spec_level=0.0),
                "mod_c::h": _make_func_spec(key="mod_c::h", param_count=2, spec_level=0.0),
            }
        )
        graph = _fake_graph(calls={"mod_a::f": {"mod_b::g", "mod_c::h"}})

        result = analyze_composition(graph, ledger)

        # Edge 1: mod_a::f -> mod_b::g: 9.0 * 1.0 = 9.0
        # Edge 2: mod_a::f -> mod_c::h: 4.0 * 1.0 = 4.0
        # total_gamma = 13.0 >= 5.0
        assert result.sheaf_holds is False
        assert result.total_gamma == 13.0

    def test_module_specification_built(self):
        """Module specs aggregate sigma and gamma per module."""
        ledger = _make_ledger(
            {
                "mod_a::f": _make_func_spec(key="mod_a::f", param_count=2, estimated_sigma=5),
                "mod_b::g": _make_func_spec(
                    key="mod_b::g", param_count=2, spec_level=0.5, estimated_sigma=3
                ),
            }
        )
        graph = _fake_graph(calls={"mod_a::f": {"mod_b::g"}})

        result = analyze_composition(graph, ledger)

        assert len(result.modules) == 2
        # Find mod_a module spec
        mod_a = next(m for m in result.modules if m.module_path == "mod_a")
        assert mod_a.local_sigma_sum == 5
        assert mod_a.interface_gamma_sum == 2.0  # one edge, gamma=2.0
        assert mod_a.mean_integration_complexity == 2.0  # 2.0 / 1 edge
        assert mod_a.sheaf_compatible is True  # 2.0 < 5.0

    def test_module_with_no_outgoing_edges(self):
        """Module with no outgoing edges has zero gamma."""
        ledger = _make_ledger(
            {
                "mod_a::f": _make_func_spec(key="mod_a::f", param_count=2),
                "mod_b::g": _make_func_spec(key="mod_b::g", param_count=2, spec_level=0.5),
            }
        )
        # mod_b::g has no outgoing edges
        graph = _fake_graph(calls={"mod_a::f": {"mod_b::g"}})

        result = analyze_composition(graph, ledger)

        mod_b = next(m for m in result.modules if m.module_path == "mod_b")
        assert mod_b.interface_gamma_sum == 0.0
        assert mod_b.mean_integration_complexity == 0.0

    def test_multiple_edges_same_module(self):
        """Multiple outgoing edges from same module accumulate gamma."""
        ledger = _make_ledger(
            {
                "mod_a::f1": _make_func_spec(key="mod_a::f1", param_count=2),
                "mod_a::f2": _make_func_spec(key="mod_a::f2", param_count=2),
                "mod_b::g": _make_func_spec(key="mod_b::g", param_count=2, spec_level=0.0),
            }
        )
        graph = _fake_graph(calls={"mod_a::f1": {"mod_b::g"}, "mod_a::f2": {"mod_b::g"}})

        result = analyze_composition(graph, ledger)

        assert len(result.edges) == 2
        # Each edge: 4.0 * 1.0 = 4.0, total = 8.0
        assert result.total_gamma == 8.0

    def test_result_is_composition_result_type(self):
        """analyze_composition returns a CompositionResult instance."""
        result = analyze_composition(_fake_graph(), _make_ledger({}))
        assert isinstance(result, CompositionResult)


# =========================================================================
# CompositionResult
# =========================================================================


class TestCompositionResult:
    """Tests for the CompositionResult class."""

    def test_to_dict_empty(self):
        """Empty result serializes correctly."""
        result = CompositionResult(
            edges=[],
            modules=[],
            total_gamma=0.0,
            sheaf_holds=True,
            sheaf_obstruction=0.0,
        )

        d = result.to_dict()

        assert d["total_gamma"] == 0.0
        assert d["sheaf_holds"] is True
        assert d["sheaf_obstruction"] == 0.0
        assert d["edge_count"] == 0
        assert d["module_count"] == 0
        assert d["composition_gaps"] == {}

    def test_to_dict_filters_zero_gamma_edges(self):
        """to_dict only includes edges with gamma > 0 in composition_gaps."""
        surface = IntegrationSurface(
            caller="a::f",
            callee="b::g",
            interface_complexity=5.0,
        )
        edge_nonzero = CompositionEdge(
            caller="a::f",
            callee="b::g",
            gamma=2.5,
            integration_surface=surface,
        )
        edge_zero = CompositionEdge(
            caller="a::f",
            callee="c::h",
            gamma=0.0,
            integration_surface=IntegrationSurface(),
            specification_independent=True,
        )
        result = CompositionResult(
            edges=[edge_nonzero, edge_zero],
            modules=[],
            total_gamma=2.5,
            sheaf_holds=True,
            sheaf_obstruction=2.5,
        )

        d = result.to_dict()

        assert d["edge_count"] == 2
        assert len(d["composition_gaps"]) == 1
        assert "a::f::b::g" in d["composition_gaps"]
        gap = d["composition_gaps"]["a::f::b::g"]
        assert gap["gamma"] == 2.5
        assert gap["integration_surface"] == 5.0
        assert gap["spec_independent"] is False

    def test_to_dict_module_count(self):
        """Module count reflects actual module list length."""
        modules = [
            ModuleSpecification(module_path="mod_a"),
            ModuleSpecification(module_path="mod_b"),
        ]
        result = CompositionResult(
            edges=[],
            modules=modules,
            total_gamma=0.0,
            sheaf_holds=True,
            sheaf_obstruction=0.0,
        )

        assert result.to_dict()["module_count"] == 2

    def test_slots_prevent_arbitrary_attributes(self):
        """CompositionResult uses __slots__ — no ad-hoc attributes."""
        result = CompositionResult(
            edges=[],
            modules=[],
            total_gamma=0.0,
            sheaf_holds=True,
            sheaf_obstruction=0.0,
        )
        with pytest.raises(AttributeError):
            result.extra_field = "nope"  # type: ignore[attr-defined]


# =========================================================================
# _count_interface_mutation_points (tested via compute_composition_edge)
# =========================================================================


class TestInterfaceMutationPoints:
    """Tests for interface mutation point counting via compute_composition_edge."""

    def test_zero_params_zero_mutations(self):
        """Zero-param callee -> 0 mutation points."""
        callee = _make_func_spec(key="mod_b::g", param_count=0, spec_level=0.0)
        ledger = _make_ledger({"mod_a::f": _make_func_spec(key="mod_a::f"), "mod_b::g": callee})
        graph = _fake_graph()

        edge = compute_composition_edge("mod_a::f", "mod_b::g", graph, ledger)
        assert edge.interface_mutant_count == 0

    def test_one_param_value_only(self):
        """One-param callee: 1 value mutation, 0 swap mutations."""
        callee = _make_func_spec(key="mod_b::g", param_count=1, spec_level=0.0)
        ledger = _make_ledger({"mod_a::f": _make_func_spec(key="mod_a::f"), "mod_b::g": callee})
        graph = _fake_graph()

        edge = compute_composition_edge("mod_a::f", "mod_b::g", graph, ledger)
        # value=1, swap=0, state=0, raw=1 * 1.0 = 1
        assert edge.interface_mutant_count == 1

    def test_two_params_value_and_swap(self):
        """Two-param callee: 2 value + 1 swap = 3 mutations."""
        callee = _make_func_spec(key="mod_b::g", param_count=2, spec_level=0.0)
        ledger = _make_ledger({"mod_a::f": _make_func_spec(key="mod_a::f"), "mod_b::g": callee})
        graph = _fake_graph()

        edge = compute_composition_edge("mod_a::f", "mod_b::g", graph, ledger)
        # value=2, swap=1, state=0, raw=3 * 1.0 = 3
        assert edge.interface_mutant_count == 3

    def test_three_params_with_swap_combinations(self):
        """Three-param callee: 3 value + 3 swap = 6 mutations."""
        callee = _make_func_spec(key="mod_b::g", param_count=3, spec_level=0.0)
        ledger = _make_ledger({"mod_a::f": _make_func_spec(key="mod_a::f"), "mod_b::g": callee})
        graph = _fake_graph()

        edge = compute_composition_edge("mod_a::f", "mod_b::g", graph, ledger)
        # value=3, swap=C(3,2)=3, state=0, raw=6 * 1.0 = 6
        assert edge.interface_mutant_count == 6

    def test_shared_state_adds_state_mutations(self):
        """Shared mutable state adds 2 state mutation points."""
        caller = _make_func_spec(key="mod_a::f", param_count=2, is_stateful=True)
        callee = _make_func_spec(key="mod_b::g", param_count=2, spec_level=0.0, is_stateful=True)
        ledger = _make_ledger({"mod_a::f": caller, "mod_b::g": callee})
        graph = _fake_graph()

        edge = compute_composition_edge("mod_a::f", "mod_b::g", graph, ledger)
        # value=2, swap=1, state=2, raw=5 * 1.0 = 5
        assert edge.interface_mutant_count == 5

    def test_high_spec_level_reduces_mutation_count(self):
        """High callee spec_level reduces effective mutation count."""
        callee = _make_func_spec(key="mod_b::g", param_count=3, spec_level=0.8)
        ledger = _make_ledger({"mod_a::f": _make_func_spec(key="mod_a::f"), "mod_b::g": callee})
        graph = _fake_graph()

        edge = compute_composition_edge("mod_a::f", "mod_b::g", graph, ledger)
        # raw = 3 + 3 + 0 = 6, uncertainty = 0.2, 6 * 0.2 = 1.2 -> round = 1
        # floor of 1 applies since raw > 0
        assert edge.interface_mutant_count == 1

    def test_full_spec_level_minimum_one(self):
        """Fully specified callee (spec_level=1.0): floor of 1 when params exist."""
        callee = _make_func_spec(key="mod_b::g", param_count=2, spec_level=1.0)
        ledger = _make_ledger({"mod_a::f": _make_func_spec(key="mod_a::f"), "mod_b::g": callee})
        graph = _fake_graph()

        edge = compute_composition_edge("mod_a::f", "mod_b::g", graph, ledger)
        # raw = 2 + 1 + 0 = 3, uncertainty = 0.0, 3 * 0.0 = 0 -> max(1, 0) = 1
        assert edge.interface_mutant_count == 1


# =========================================================================
# Module-key extraction (tested via analyze_composition)
# =========================================================================


class TestModuleKeyExtraction:
    """Tests for module key extraction logic via analyze_composition."""

    def test_double_colon_separator(self):
        """Module path is everything before '::'."""
        ledger = _make_ledger(
            {
                "path/to/mod_a.py::func": _make_func_spec(key="path/to/mod_a.py::func"),
                "path/to/mod_b.py::func": _make_func_spec(
                    key="path/to/mod_b.py::func", param_count=2, spec_level=0.5
                ),
            }
        )
        graph = _fake_graph(calls={"path/to/mod_a.py::func": {"path/to/mod_b.py::func"}})

        result = analyze_composition(graph, ledger)

        assert len(result.edges) == 1
        mod_paths = {m.module_path for m in result.modules}
        assert "path/to/mod_a.py" in mod_paths
        assert "path/to/mod_b.py" in mod_paths

    def test_no_separator_entire_key_is_module(self):
        """Without '::', the full key is the module path."""
        ledger = _make_ledger(
            {
                "just_a_name": _make_func_spec(key="just_a_name"),
                "another_name": _make_func_spec(key="another_name", param_count=2, spec_level=0.5),
            }
        )
        graph = _fake_graph(calls={"just_a_name": {"another_name"}})

        result = analyze_composition(graph, ledger)

        # These are in different "modules" (since keys differ)
        assert len(result.edges) == 1

    def test_same_module_prefix_no_edge(self):
        """Functions in the same module produce no composition edges."""
        ledger = _make_ledger(
            {
                "mod::f": _make_func_spec(key="mod::f"),
                "mod::g": _make_func_spec(key="mod::g"),
            }
        )
        graph = _fake_graph(calls={"mod::f": {"mod::g"}})

        result = analyze_composition(graph, ledger)

        assert result.edges == []


# =========================================================================
# Integration: end-to-end scenario
# =========================================================================


class TestEndToEnd:
    """Integration tests covering multi-module composition analysis."""

    def test_three_module_chain(self):
        """A -> B -> C chain produces edges A->B and B->C."""
        ledger = _make_ledger(
            {
                "mod_a::entry": _make_func_spec(
                    key="mod_a::entry", param_count=1, estimated_sigma=2
                ),
                "mod_b::process": _make_func_spec(
                    key="mod_b::process",
                    param_count=3,
                    spec_level=0.5,
                    estimated_sigma=4,
                ),
                "mod_c::store": _make_func_spec(
                    key="mod_c::store",
                    param_count=2,
                    spec_level=0.8,
                    estimated_sigma=1,
                ),
            }
        )
        graph = _fake_graph(
            calls={
                "mod_a::entry": {"mod_b::process"},
                "mod_b::process": {"mod_c::store"},
            }
        )

        result = analyze_composition(graph, ledger)

        assert len(result.edges) == 2

        edge_ab = next(e for e in result.edges if e.callee == "mod_b::process")
        # process has 3 params: surface_area = 3*3 = 9, gamma = 9 * 0.5 = 4.5
        assert edge_ab.gamma == 4.5

        edge_bc = next(e for e in result.edges if e.callee == "mod_c::store")
        # store has 2 params: surface_area = 2*2 = 4, gamma = 4 * 0.2 = 0.8
        assert edge_bc.gamma == 0.8

        # total_gamma = 4.5 + 0.8 = 5.3 >= 5.0 -> sheaf fails
        assert result.total_gamma == 5.3
        assert result.sheaf_holds is False
        assert result.sheaf_obstruction == 5.3

        # Module sigma sums
        mod_a = next(m for m in result.modules if m.module_path == "mod_a")
        assert mod_a.local_sigma_sum == 2
        mod_b = next(m for m in result.modules if m.module_path == "mod_b")
        assert mod_b.local_sigma_sum == 4

    def test_pure_spec_independent_edge_in_chain(self):
        """Pure, high-spec callee contributes zero gamma to total."""
        ledger = _make_ledger(
            {
                "mod_a::f": _make_func_spec(key="mod_a::f", param_count=2),
                "mod_b::pure_helper": _make_func_spec(
                    key="mod_b::pure_helper",
                    param_count=4,
                    spec_level=0.99,
                    is_pure=True,
                ),
                "mod_c::impure": _make_func_spec(
                    key="mod_c::impure",
                    param_count=2,
                    spec_level=0.0,
                ),
            }
        )
        graph = _fake_graph(calls={"mod_a::f": {"mod_b::pure_helper", "mod_c::impure"}})

        result = analyze_composition(graph, ledger)

        assert len(result.edges) == 2

        pure_edge = next(e for e in result.edges if e.callee == "mod_b::pure_helper")
        assert pure_edge.gamma == 0.0
        assert pure_edge.specification_independent is True

        impure_edge = next(e for e in result.edges if e.callee == "mod_c::impure")
        assert impure_edge.gamma == 4.0  # 4.0 * 1.0
        assert impure_edge.specification_independent is False

        # Only impure edge contributes
        assert result.total_gamma == 4.0

    def test_to_dict_round_trip_consistency(self):
        """to_dict output is consistent with direct attribute access."""
        ledger = _make_ledger(
            {
                "mod_a::f": _make_func_spec(key="mod_a::f", param_count=2),
                "mod_b::g": _make_func_spec(key="mod_b::g", param_count=2, spec_level=0.5),
            }
        )
        graph = _fake_graph(calls={"mod_a::f": {"mod_b::g"}})

        result = analyze_composition(graph, ledger)
        d = result.to_dict()

        assert d["total_gamma"] == result.total_gamma
        assert d["sheaf_holds"] == result.sheaf_holds
        assert d["sheaf_obstruction"] == result.sheaf_obstruction
        assert d["edge_count"] == len(result.edges)
        assert d["module_count"] == len(result.modules)
