"""B2: Post-Extraction Projector — surface optimization opportunities from decomposition.

Simulates extraction by building projected sub-ASTs and re-running purity +
algebraic property analysis. Discovers cacheable, parallelizable, directly
testable, and other optimization opportunities that emerge after extraction.

Plans are enriched, not modified — projector produces pure data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ._projector_analysis import (
    ProjectedPurity as _ProjectedPurity,
)
from ._projector_analysis import (
    analyze_projected_properties as _analyze_projected_properties,
)
from ._projector_analysis import (
    analyze_projected_purity as _analyze_projected_purity,
)
from ._projector_analysis import (
    find_parallelizable_groups as _find_parallelizable_groups,
)
from ._projector_analysis import (
    is_directly_testable as _is_directly_testable,
)
from ._projector_analysis import (
    is_numeric_heavy as _is_numeric_heavy,
)
from ._projector_analysis import (
    testability_evidence as _testability_evidence,
)
from ._projector_ast import (
    build_function_projected_ast as _build_function_projected_ast,
)
from ._projector_ast import (
    build_handler_projected_ast as _build_handler_projected_ast,
)

if TYPE_CHECKING:
    import ast

    from .extraction_plan import ExtractionPlan, ExtractionStep


@dataclass
class ProjectedOpportunity:
    """An optimization opportunity that emerges after extraction."""

    function_id: str
    opportunity: str  # "cacheable" | "parallelizable" | "jit_candidate" | "lazy_evaluable" | "directly_testable"
    confidence: float
    precondition: str
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "function_id": self.function_id,
            "opportunity": self.opportunity,
            "confidence": round(self.confidence, 3),
            "precondition": self.precondition,
            "evidence": self.evidence,
        }


# ── Public API ─────────────────────────────────────────────────────────


def project_post_extraction(
    plan: ExtractionPlan,
    source_ast: ast.Module | None = None,
) -> list[ProjectedOpportunity]:
    """Project optimization opportunities from an extraction plan.

    For each extraction step, builds a projected sub-AST and analyzes
    purity and algebraic properties to discover opportunities.

    Args:
        plan: Extraction plan with ordered steps.
        source_ast: Parsed AST of the source module (optional, for deeper analysis).

    Returns:
        List of projected opportunities discovered.
    """
    opportunities: list[ProjectedOpportunity] = []

    extraction_steps = _get_extraction_steps(plan)
    if not extraction_steps:
        return opportunities

    projected_purities: dict[str, _ProjectedPurity] = {}

    for step in extraction_steps:
        projected_ast = build_projected_ast(step, source_ast)
        if projected_ast is None:
            continue

        purity = _analyze_projected_purity(projected_ast)
        func_id = _step_function_id(step)
        projected_purities[func_id] = purity

        precondition = f"requires extraction from {plan.source_function}"

        if purity.is_pure:
            opportunities.append(
                ProjectedOpportunity(
                    function_id=func_id,
                    opportunity="cacheable",
                    confidence=purity.confidence * 0.9,
                    precondition=precondition,
                    evidence=["projected_pure", "deterministic_io"],
                )
            )

            properties = _analyze_projected_properties(projected_ast, purity)
            for prop in properties:
                if prop == "idempotent":
                    opportunities.append(
                        ProjectedOpportunity(
                            function_id=func_id,
                            opportunity="lazy_evaluable",
                            confidence=purity.confidence * 0.8,
                            precondition=precondition,
                            evidence=["projected_pure", "idempotent_property"],
                        )
                    )

            if _is_numeric_heavy(projected_ast):
                opportunities.append(
                    ProjectedOpportunity(
                        function_id=func_id,
                        opportunity="jit_candidate",
                        confidence=purity.confidence * 0.7,
                        precondition=precondition,
                        evidence=["projected_pure", "numeric_operations"],
                    )
                )

        if _is_directly_testable(step):
            opportunities.append(
                ProjectedOpportunity(
                    function_id=func_id,
                    opportunity="directly_testable",
                    confidence=0.95 if not purity.has_side_effects else 0.7,
                    precondition=precondition,
                    evidence=_testability_evidence(step, purity),
                )
            )

    pure_funcs = [(fid, p) for fid, p in projected_purities.items() if p.is_pure]
    if len(pure_funcs) >= 2:
        group_opportunities = _find_parallelizable_groups(pure_funcs, plan.source_function)
        opportunities.extend(group_opportunities)

    return opportunities


# ── Projected AST Construction ─────────────────────────────────────────


def build_projected_ast(
    step: ExtractionStep,
    source_ast: ast.Module | None = None,
) -> ast.FunctionDef | None:
    """Build a projected sub-AST for an extraction step.

    Creates a synthetic ast.FunctionDef from the step's detail: parameters,
    body lines, return type, etc.
    """
    action = step.action

    if action == "extract_handler":
        return _build_handler_projected_ast(step, source_ast)
    elif action == "create_function":
        return _build_function_projected_ast(step, source_ast)
    else:
        return None


# ── Extraction Step Helpers ────────────────────────────────────────────


def _get_extraction_steps(plan: ExtractionPlan) -> list[ExtractionStep]:
    """Get steps that produce new functions (create_function or extract_handler)."""
    return [s for s in plan.steps if s.action in ("create_function", "extract_handler")]


def _step_function_id(step: ExtractionStep) -> str:
    """Get a function identifier from an extraction step."""
    detail = step.detail
    if step.action == "extract_handler":
        return detail.get("proposed_name", step.target)
    return detail.get("proposed_name", step.target)
