"""B2: Post-Extraction Projector — surface optimization opportunities from decomposition.

Simulates extraction by building projected sub-ASTs and re-running purity +
algebraic property analysis. Discovers cacheable, parallelizable, directly
testable, and other optimization opportunities that emerge after extraction.

Plans are enriched, not modified — projector produces pure data.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

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

    # Collect extraction steps that produce new functions
    extraction_steps = _get_extraction_steps(plan)
    if not extraction_steps:
        return opportunities

    # Analyze each projected function
    projected_purities: dict[str, _ProjectedPurity] = {}

    for step in extraction_steps:
        projected_ast = build_projected_ast(step, source_ast)
        if projected_ast is None:
            continue

        purity = _analyze_projected_purity(projected_ast)
        func_id = _step_function_id(step)
        projected_purities[func_id] = purity

        precondition = f"requires extraction from {plan.source_function}"

        # Pure function → cacheable
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

            # Check algebraic properties for additional opportunities
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

            # JIT candidate: pure + numeric-heavy
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

        # Directly testable: all dependencies are explicit parameters
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

    # Check for parallelizable pairs
    pure_funcs = [(fid, p) for fid, p in projected_purities.items() if p.is_pure]
    if len(pure_funcs) >= 2:
        group_opportunities = _find_parallelizable_groups(
            pure_funcs, plan.source_function
        )
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


def _build_function_projected_ast(
    step: ExtractionStep,
    source_ast: ast.Module | None,
) -> ast.FunctionDef | None:
    """Build projected AST for a create_function step."""
    detail = step.detail
    params = detail.get("parameters", [])
    proposed_name = detail.get("proposed_name", step.target)
    source_lines = detail.get("source_lines", [])

    # Build parameter list
    args = ast.arguments(
        posonlyargs=[],
        args=[ast.arg(arg=p, annotation=None) for p in params],
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=[],
    )

    # Try to extract body from source AST
    body: list[ast.stmt] = []
    if source_ast and source_lines and len(source_lines) == 2:
        body = _extract_body_from_ast(source_ast, source_lines[0], source_lines[1])

    if not body:
        # Fallback: create a placeholder body
        body = [ast.Pass()]

    func = ast.FunctionDef(
        name=proposed_name,
        args=args,
        body=body,
        decorator_list=[],
        returns=None,
        lineno=1,
        col_offset=0,
    )
    ast.fix_missing_locations(func)
    return func


def _build_handler_projected_ast(
    step: ExtractionStep,
    source_ast: ast.Module | None,
) -> ast.FunctionDef | None:
    """Build projected AST for an extract_handler step."""
    detail = step.detail
    captured = detail.get("captured_variables", [])
    proposed_name = detail.get("proposed_name", step.target)
    source_lines = detail.get("source_lines", [])

    # Handler's original params + captured variables become explicit params
    all_params = list(captured)

    args = ast.arguments(
        posonlyargs=[],
        args=[ast.arg(arg=p, annotation=None) for p in all_params],
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=[],
    )

    body: list[ast.stmt] = []
    if source_ast and source_lines and len(source_lines) == 2:
        body = _extract_body_from_ast(source_ast, source_lines[0], source_lines[1])

    if not body:
        body = [ast.Pass()]

    func = ast.FunctionDef(
        name=proposed_name,
        args=args,
        body=body,
        decorator_list=[],
        returns=None,
        lineno=1,
        col_offset=0,
    )
    ast.fix_missing_locations(func)
    return func


def _extract_body_from_ast(
    source_ast: ast.Module,
    start_line: int,
    end_line: int,
) -> list[ast.stmt]:
    """Extract statements from source AST by line range."""
    body: list[ast.stmt] = []
    for node in ast.walk(source_ast):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for stmt in node.body:
                if hasattr(stmt, "lineno") and start_line <= stmt.lineno <= end_line:
                    body.append(stmt)
    return body


# ── Purity Analysis ───────────────────────────────────────────────────


@dataclass
class _ProjectedPurity:
    """Simplified purity analysis result for projected functions."""

    is_pure: bool
    confidence: float
    has_side_effects: bool
    side_effect_kinds: list[str] = field(default_factory=list)


def _analyze_projected_purity(func_node: ast.FunctionDef) -> _ProjectedPurity:
    """Analyze purity of a projected function using the existing purity analyzer."""
    try:
        from lintgate.linters.performance_checks.purity import analyze_purity

        # Wrap in a module for the analyzer
        module = ast.Module(body=[func_node], type_ignores=[])
        ast.fix_missing_locations(module)

        results = analyze_purity(module)
        if results:
            # Get the first (and should be only) result
            result = next(iter(results.values()))
            return _ProjectedPurity(
                is_pure=result.is_pure,
                confidence=result.confidence,
                has_side_effects=len(result.side_effects) > 0,
                side_effect_kinds=[se.kind for se in result.side_effects],
            )
    except Exception:
        pass

    # Fallback: heuristic analysis
    return _heuristic_purity(func_node)


def _heuristic_purity(func_node: ast.FunctionDef) -> _ProjectedPurity:
    """Simple heuristic purity check when full analyzer is unavailable."""
    impure_calls = {"print", "open", "write", "input", "exec", "eval"}
    global_refs = False
    io_calls = False

    for node in ast.walk(func_node):
        if isinstance(node, ast.Global) or isinstance(node, ast.Nonlocal):
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

    return _ProjectedPurity(
        is_pure=is_pure,
        confidence=0.6 if is_pure else 0.8,
        has_side_effects=bool(side_effects),
        side_effect_kinds=side_effects,
    )


def _analyze_projected_properties(
    func_node: ast.FunctionDef,
    purity: _ProjectedPurity,
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


def _is_numeric_heavy(func_node: ast.FunctionDef) -> bool:
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


def _is_directly_testable(step: ExtractionStep) -> bool:
    """Check if an extracted function is directly testable in isolation."""
    detail = step.detail
    if step.action == "extract_handler":
        captured_writes = detail.get("captured_writes", [])
        return len(captured_writes) == 0
    elif step.action == "create_function":
        # Block extraction with explicit params → testable
        params = detail.get("parameters", [])
        return len(params) > 0
    return False


def _testability_evidence(
    step: ExtractionStep,
    purity: _ProjectedPurity,
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


def _find_parallelizable_groups(
    pure_funcs: list[tuple[str, _ProjectedPurity]],
    source_function: str,
) -> list[ProjectedOpportunity]:
    """Find groups of pure functions that can be parallelized.

    Pure functions with no shared mutable state are parallelizable.
    """
    opportunities: list[ProjectedOpportunity] = []

    if len(pure_funcs) < 2:
        return opportunities

    # All pure functions are independent by definition (no shared mutable state)
    func_ids = [fid for fid, _ in pure_funcs]
    min_confidence = min(p.confidence for _, p in pure_funcs)

    opportunities.append(
        ProjectedOpportunity(
            function_id=f"{source_function} extracted functions",
            opportunity="parallelizable",
            confidence=min_confidence * 0.85,
            precondition=f"requires all {len(func_ids)} functions extracted from {source_function}",
            evidence=["no_shared_mutable_state", "independent_pure_functions"],
        )
    )

    return opportunities


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
