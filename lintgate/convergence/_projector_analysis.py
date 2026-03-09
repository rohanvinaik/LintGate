"""Purity, property, and opportunity analysis helpers for the post-extraction projector.

Analyzes projected sub-ASTs for purity, algebraic properties, numeric density,
testability, and parallelizability.  All functions are pure helpers that produce
data — they never modify plans or AST nodes.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .extraction_plan import ExtractionStep
    from .projector import ProjectedOpportunity


# ── Purity Analysis ───────────────────────────────────────────────────


@dataclass
class ProjectedPurity:
    """Simplified purity analysis result for projected functions."""

    is_pure: bool
    confidence: float
    has_side_effects: bool
    side_effect_kinds: list[str] = field(default_factory=list)


def analyze_projected_purity(func_node: ast.FunctionDef) -> ProjectedPurity:
    """Analyze purity of a projected function using the existing purity analyzer."""
    try:
        from lintgate.linters.performance_checks.purity import analyze_purity

        module = ast.Module(body=[func_node], type_ignores=[])
        ast.fix_missing_locations(module)

        results = analyze_purity(module)
        if results:
            result = next(iter(results.values()))
            return ProjectedPurity(
                is_pure=result.is_pure,
                confidence=result.confidence,
                has_side_effects=len(result.side_effects) > 0,
                side_effect_kinds=[se.kind for se in result.side_effects],
            )
    except Exception:
        pass

    return heuristic_purity(func_node)


def heuristic_purity(func_node: ast.FunctionDef) -> ProjectedPurity:
    """Simple heuristic purity check when full analyzer is unavailable."""
    impure_calls = {"print", "open", "write", "input", "exec", "eval"}
    global_refs = False
    io_calls = False

    for node in ast.walk(func_node):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            global_refs = True
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in impure_calls:
                io_calls = True
            if isinstance(node.func, ast.Attribute) and node.func.attr in {
                "write",
                "read",
                "send",
                "recv",
                "execute",
            }:
                io_calls = True

    is_pure = not global_refs and not io_calls
    side_effects = []
    if global_refs:
        side_effects.append("global_access")
    if io_calls:
        side_effects.append("io_call")

    return ProjectedPurity(
        is_pure=is_pure,
        confidence=0.6 if is_pure else 0.8,
        has_side_effects=bool(side_effects),
        side_effect_kinds=side_effects,
    )


def analyze_projected_properties(
    func_node: ast.FunctionDef,
    purity: ProjectedPurity,
) -> list[str]:
    """Analyze algebraic properties of a projected pure function."""
    if not purity.is_pure:
        return []

    properties: list[str] = []
    try:
        from lintgate.linters.performance_checks.properties import classify_properties
        from lintgate.linters.performance_checks.purity import analyze_purity

        module = ast.Module(body=[func_node], type_ignores=[])
        ast.fix_missing_locations(module)

        purity_results = analyze_purity(module)
        if purity_results:
            purity_result = next(iter(purity_results.values()))
            if purity_result.is_pure:
                func_props = classify_properties(func_node, purity_result)
                for prop in func_props.properties:
                    properties.append(prop.kind.value)
    except Exception:
        pass

    return properties


# ── Opportunity Detection Helpers ──────────────────────────────────────


def is_numeric_heavy(func_node: ast.FunctionDef) -> bool:
    """Check if a function is numeric-heavy (candidate for JIT)."""
    numeric_ops = 0
    total_ops = 0

    for node in ast.walk(func_node):
        if isinstance(node, ast.BinOp):
            total_ops += 1
            if isinstance(
                node.op,
                (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow),
            ):
                numeric_ops += 1
        elif isinstance(node, ast.Compare):
            total_ops += 1
            numeric_ops += 1
        elif isinstance(node, ast.UnaryOp):
            if isinstance(node.op, (ast.UAdd, ast.USub)):
                numeric_ops += 1
                total_ops += 1

    return numeric_ops >= 3


def is_directly_testable(step: ExtractionStep) -> bool:
    """Check if an extracted function is directly testable in isolation."""
    detail = step.detail
    if step.action == "extract_handler":
        captured_writes = detail.get("captured_writes", [])
        return len(captured_writes) == 0
    elif step.action == "create_function":
        params = detail.get("parameters", [])
        return len(params) > 0
    return False


def testability_evidence(
    step: ExtractionStep,
    purity: ProjectedPurity,
) -> list[str]:
    """Build evidence list for directly_testable opportunity."""
    evidence = []
    if step.action == "extract_handler":
        detail = step.detail
        if not detail.get("captured_writes", []):
            evidence.append("no_closure_captures")
        evidence.append("all_dependencies_explicit_params")
    else:
        evidence.append("explicit_parameters")

    if purity.is_pure:
        evidence.append("projected_pure")
    if not purity.has_side_effects:
        evidence.append("no_framework_state")

    return evidence


def find_parallelizable_groups(
    pure_funcs: list[tuple[str, ProjectedPurity]],
    source_function: str,
) -> list[ProjectedOpportunity]:
    """Find groups of pure functions that can be parallelized.

    Pure functions with no shared mutable state are parallelizable.
    """
    from .projector import ProjectedOpportunity as _Opp

    opportunities: list[_Opp] = []

    if len(pure_funcs) < 2:
        return opportunities

    func_ids = [fid for fid, _ in pure_funcs]
    min_confidence = min(p.confidence for _, p in pure_funcs)

    opportunities.append(
        _Opp(
            function_id=f"{source_function} extracted functions",
            opportunity="parallelizable",
            confidence=min_confidence * 0.85,
            precondition=f"requires all {len(func_ids)} functions extracted from {source_function}",
            evidence=["no_shared_mutable_state", "independent_pure_functions"],
        )
    )

    return opportunities
