"""Composition analyzer — gamma estimation, integration surface, sheaf condition.

Computes composition gaps between modules using integration surface metrics
and specification levels. No subprocess spawning, no test execution.

Key concepts:
- Integration surface: interface complexity at a cross-module call edge
- Gamma (composition gap): interface_complexity * (1 - callee.spec_level)
- Sheaf condition: holds when total obstruction < threshold
- Specification-independent: pure callee with spec_level >= 0.95 → gamma = 0
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types import CompositionEdge, IntegrationSurface, ModuleSpecification

if TYPE_CHECKING:
    from .call_graph import CrossModuleCallGraph
    from .types import FunctionSpecification, SpecificationLedger

_SHEAF_THRESHOLD = 5.0
_SHARED_STATE_MULTIPLIER = 1.5
_SPEC_INDEPENDENT_THRESHOLD = 0.95


def compute_integration_surface(
    caller_key: str,
    callee_key: str,
    call_graph: CrossModuleCallGraph,
    ledger: SpecificationLedger,
) -> IntegrationSurface:
    """Compute integration surface metrics for a cross-module call edge.

    Args:
        caller_key: Qualified key of the calling function.
        callee_key: Qualified key of the called function.
        call_graph: Cross-module call graph (unused beyond key validation).
        ledger: Specification ledger with function specifications.
    """
    caller_spec = ledger.functions.get(caller_key)
    callee_spec = ledger.functions.get(callee_key)

    callee_params = _get_param_count(callee_spec)

    # Estimate call arg count from callee's parameter count as proxy
    call_arg_count = callee_params
    shared_mutable = _detect_shared_mutable_state(caller_spec, callee_spec)

    surface_area = float(call_arg_count * max(callee_params, 1))
    multiplier = _SHARED_STATE_MULTIPLIER if shared_mutable else 1.0
    interface_complexity = surface_area * multiplier

    return IntegrationSurface(
        caller=caller_key,
        callee=callee_key,
        call_arg_count=call_arg_count,
        callee_param_count=callee_params,
        shared_mutable_state=shared_mutable,
        type_boundary_crossing=False,
        surface_area=surface_area,
        interface_complexity=interface_complexity,
    )


def compute_composition_edge(
    caller_key: str,
    callee_key: str,
    call_graph: CrossModuleCallGraph,
    ledger: SpecificationLedger,
) -> CompositionEdge:
    """Compute composition edge (gamma) for a cross-module call.

    Gamma = interface_complexity * (1 - callee.spec_level)
    Specification-independent when callee is pure with spec_level >= 0.95.
    """
    surface = compute_integration_surface(caller_key, callee_key, call_graph, ledger)
    callee_spec = ledger.functions.get(callee_key)

    spec_level = callee_spec.core.specification_level if callee_spec else 0.0
    is_pure = callee_spec.core.is_pure if callee_spec else False

    spec_independent = is_pure and spec_level >= _SPEC_INDEPENDENT_THRESHOLD
    gamma = 0.0 if spec_independent else surface.interface_complexity * (1.0 - spec_level)

    return CompositionEdge(
        caller=caller_key,
        callee=callee_key,
        gamma=round(gamma, 3),
        integration_surface=surface,
        interface_mutant_count=int(gamma),
        specification_independent=spec_independent,
    )


def analyze_composition(
    call_graph: CrossModuleCallGraph,
    ledger: SpecificationLedger,
) -> CompositionResult:
    """Run full composition analysis across all cross-module edges.

    Returns composition edges, module specifications, and sheaf status.
    """
    edges: list[CompositionEdge] = []
    module_map: dict[str, list[str]] = {}

    # Build module → functions mapping
    for func_key in ledger.functions:
        module = _module_from_key(func_key)
        module_map.setdefault(module, []).append(func_key)

    # Find cross-module edges
    for caller_key, callees in call_graph.calls.items():
        caller_module = _module_from_key(caller_key)
        for callee_key in callees:
            callee_module = _module_from_key(callee_key)
            if caller_module == callee_module:
                continue
            if callee_key not in ledger.functions:
                continue
            edge = compute_composition_edge(caller_key, callee_key, call_graph, ledger)
            edges.append(edge)

    # Sheaf condition
    total_gamma = sum(e.gamma for e in edges)
    sheaf_holds = total_gamma < _SHEAF_THRESHOLD

    # Module specifications
    modules = _build_module_specs(module_map, edges, ledger)

    return CompositionResult(
        edges=edges,
        modules=modules,
        total_gamma=round(total_gamma, 3),
        sheaf_holds=sheaf_holds,
        sheaf_obstruction=round(total_gamma, 3),
    )


class CompositionResult:
    """Result of composition analysis."""

    __slots__ = ("edges", "modules", "total_gamma", "sheaf_holds", "sheaf_obstruction")

    def __init__(
        self,
        edges: list[CompositionEdge],
        modules: list[ModuleSpecification],
        total_gamma: float,
        sheaf_holds: bool,
        sheaf_obstruction: float,
    ) -> None:
        self.edges = edges
        self.modules = modules
        self.total_gamma = total_gamma
        self.sheaf_holds = sheaf_holds
        self.sheaf_obstruction = sheaf_obstruction

    def to_dict(self) -> dict:
        return {
            "total_gamma": self.total_gamma,
            "sheaf_holds": self.sheaf_holds,
            "sheaf_obstruction": self.sheaf_obstruction,
            "edge_count": len(self.edges),
            "module_count": len(self.modules),
            "composition_gaps": {
                f"{e.caller}::{e.callee}": {
                    "gamma": e.gamma,
                    "integration_surface": e.integration_surface.interface_complexity,
                    "spec_independent": e.specification_independent,
                }
                for e in self.edges
                if e.gamma > 0
            },
        }


def _get_param_count(spec: FunctionSpecification | None) -> int:
    """Get parameter count from a function specification."""
    if spec is None:
        return 0
    return spec.ast_metrics.parameter_count


def _detect_shared_mutable_state(
    caller: FunctionSpecification | None,
    callee: FunctionSpecification | None,
) -> bool:
    """Detect if both functions are stateful (proxy for shared mutable state)."""
    if caller is None or callee is None:
        return False
    return caller.testability.is_stateful and callee.testability.is_stateful


def _module_from_key(func_key: str) -> str:
    """Extract module path from a qualified function key."""
    if "::" in func_key:
        return func_key.split("::")[0]
    return func_key


def _build_module_specs(
    module_map: dict[str, list[str]],
    edges: list[CompositionEdge],
    ledger: SpecificationLedger,
) -> list[ModuleSpecification]:
    """Build per-module specification summaries."""
    # Pre-compute gamma sums per module
    module_gamma: dict[str, float] = {}
    module_edge_count: dict[str, int] = {}
    for edge in edges:
        caller_mod = _module_from_key(edge.caller)
        module_gamma[caller_mod] = module_gamma.get(caller_mod, 0.0) + edge.gamma
        module_edge_count[caller_mod] = module_edge_count.get(caller_mod, 0) + 1

    modules: list[ModuleSpecification] = []
    for module_path, func_keys in module_map.items():
        sigma_sum = sum(
            ledger.functions[k].core.estimated_sigma for k in func_keys if k in ledger.functions
        )
        gamma_sum = module_gamma.get(module_path, 0.0)
        edge_count = module_edge_count.get(module_path, 0)
        mean_complexity = gamma_sum / edge_count if edge_count > 0 else 0.0

        modules.append(
            ModuleSpecification(
                module_path=module_path,
                functions=func_keys,
                local_sigma_sum=sigma_sum,
                interface_gamma_sum=round(gamma_sum, 3),
                mean_integration_complexity=round(mean_complexity, 3),
                sheaf_compatible=gamma_sum < _SHEAF_THRESHOLD,
            )
        )

    return modules
