"""B3: Optimization Opportunity Synthesizer — project-wide optimization landscape.

Aggregates per-function ProjectedOpportunity from B2 across the project to
produce a strategic view: which functions to cache, which to parallelize,
which extractions to do first (highest unlock value), and total estimated impact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .evidence import ConvergenceResult
from .extraction_plan import ExtractionPlan
from .projector import ProjectedOpportunity


@dataclass
class OptimizationLandscape:
    """Project-wide optimization opportunity map."""

    cacheable_functions: list[ProjectedOpportunity] = field(default_factory=list)
    parallelizable_groups: list[list[str]] = field(default_factory=list)
    jit_candidates: list[ProjectedOpportunity] = field(default_factory=list)
    lazy_candidates: list[ProjectedOpportunity] = field(default_factory=list)
    directly_testable: list[ProjectedOpportunity] = field(default_factory=list)
    total_decomposition_steps: int = 0
    estimated_cc_reduction: float = 0.0
    dependency_order: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cacheable_functions": [o.to_dict() for o in self.cacheable_functions],
            "parallelizable_groups": self.parallelizable_groups,
            "jit_candidates": [o.to_dict() for o in self.jit_candidates],
            "lazy_candidates": [o.to_dict() for o in self.lazy_candidates],
            "directly_testable": [o.to_dict() for o in self.directly_testable],
            "total_decomposition_steps": self.total_decomposition_steps,
            "estimated_cc_reduction": self.estimated_cc_reduction,
            "dependency_order": self.dependency_order,
        }


# ── Public API ─────────────────────────────────────────────────────────


def synthesize_landscape(
    results: list[ConvergenceResult],
    plans: list[ExtractionPlan],
) -> OptimizationLandscape:
    """Synthesize a project-wide optimization landscape from plans.

    Collects all ProjectedOpportunity from plans, categorizes by type,
    computes parallelizable groups, aggregate metrics, and extraction
    dependency ordering.

    Args:
        results: Convergence results (for context and dependency info).
        plans: Extraction plans with post_extraction_opportunities populated.

    Returns:
        OptimizationLandscape with categorized opportunities and ordering.
    """
    landscape = OptimizationLandscape()

    # Collect all opportunities from all plans
    all_opportunities: list[ProjectedOpportunity] = []
    for plan in plans:
        all_opportunities.extend(plan.post_extraction_opportunities)

    # Categorize by type
    for opp in all_opportunities:
        if opp.opportunity == "cacheable":
            landscape.cacheable_functions.append(opp)
        elif opp.opportunity == "parallelizable":
            landscape.parallelizable_groups.append(_extract_group_members(opp))
        elif opp.opportunity == "jit_candidate":
            landscape.jit_candidates.append(opp)
        elif opp.opportunity == "lazy_evaluable":
            landscape.lazy_candidates.append(opp)
        elif opp.opportunity == "directly_testable":
            landscape.directly_testable.append(opp)

    # Aggregate metrics
    landscape.total_decomposition_steps = sum(len(p.steps) for p in plans)
    landscape.estimated_cc_reduction = sum(
        p.estimated_impact.get("CC_reduction", 0) for p in plans
    )

    # Compute dependency ordering
    landscape.dependency_order = _compute_dependency_order(plans, results)

    return landscape


# ── Dependency Ordering ────────────────────────────────────────────────


def _compute_dependency_order(
    plans: list[ExtractionPlan],
    results: list[ConvergenceResult],
) -> list[str]:
    """Compute extraction dependency order with unlock-value heuristic.

    Extractions that unlock the most downstream opportunities go first.
    This is a topological sort of the extraction DAG weighted by unlock value.

    The DAG is implicit: extraction A must happen before B if B's projected
    opportunity depends on A being extracted first (via precondition references).
    """
    if not plans:
        return []

    # Build per-plan unlock scores
    plan_scores: list[tuple[str, float]] = []
    for plan in plans:
        score = _unlock_value(plan)
        plan_scores.append((plan.source_function, score))

    # Build dependency edges from preconditions
    plan_by_func: dict[str, ExtractionPlan] = {p.source_function: p for p in plans}
    edges: dict[str, set[str]] = {p.source_function: set() for p in plans}

    for plan in plans:
        for opp in plan.post_extraction_opportunities:
            # Parse preconditions for dependency references
            deps = _parse_precondition_deps(opp.precondition, plan_by_func)
            for dep in deps:
                if dep != plan.source_function and dep in edges:
                    edges[plan.source_function].add(dep)

    # Topological sort with unlock-value tie-breaking
    return _topo_sort_weighted(edges, dict(plan_scores))


def _unlock_value(plan: ExtractionPlan) -> float:
    """Score how much downstream value an extraction unlocks."""
    return sum(opp.confidence for opp in plan.post_extraction_opportunities)


def _parse_precondition_deps(
    precondition: str,
    plan_by_func: dict[str, ExtractionPlan],
) -> list[str]:
    """Extract dependency function references from a precondition string."""
    deps: list[str] = []
    for func_name in plan_by_func:
        if func_name in precondition:
            deps.append(func_name)
    return deps


def _topo_sort_weighted(
    edges: dict[str, set[str]],
    scores: dict[str, float],
) -> list[str]:
    """Topological sort with highest-unlock-value-first tie-breaking.

    Uses Kahn's algorithm. ``edges[node]`` is the set of *prerequisites*
    (dependencies) of ``node`` — i.e. ``node`` cannot start until all
    members of ``edges[node]`` are done.

    When multiple nodes have zero in-degree, picks the one with the
    highest unlock score.
    """
    # Build in-degree from the dependency sets
    in_degree: dict[str, int] = {n: 0 for n in edges}
    for node, deps in edges.items():
        in_degree[node] = len(deps & edges.keys())  # only count known nodes

    # Start with zero in-degree nodes, sorted by score descending
    queue: list[str] = sorted(
        [n for n, d in in_degree.items() if d == 0],
        key=lambda n: scores.get(n, 0),
        reverse=True,
    )

    result: list[str] = []
    while queue:
        node = queue.pop(0)
        result.append(node)

        # Find all nodes that depend on `node` and decrease their in-degree
        for dependent, deps in edges.items():
            if node in deps and dependent not in result:
                in_degree[dependent] -= 1

        # Re-check for newly unblocked nodes
        for n in edges:
            if n not in result and n not in queue and in_degree.get(n, 1) == 0:
                queue.append(n)
        queue.sort(key=lambda n: scores.get(n, 0), reverse=True)

    # Add any remaining nodes (cycles — shouldn't happen but be safe)
    for node in edges:
        if node not in result:
            result.append(node)

    return result


# ── Helpers ────────────────────────────────────────────────────────────


def _extract_group_members(opp: ProjectedOpportunity) -> list[str]:
    """Extract member function names from a parallelizable opportunity."""
    # The function_id for parallelizable opportunities is typically
    # "{source} extracted functions" — we return it as a single-item group
    # since the individual members aren't tracked in the opportunity.
    # For richer data, the caller can resolve from the plan steps.
    return [opp.function_id]
