"""Test design signal extraction from AST — BVA, EQ, decision tables, cause-effect.

Pure AST analysis. No test execution, no external dependencies.
"""

from __future__ import annotations

import ast

from .types import TestDesignSignals


def extract_boundary_points(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count boundary points from comparisons with literal operands.

    Each comparison with a constant = 1 boundary point.
    Off-by-one boundaries (< N implies test at N-1, N, N+1) = 3 BVA test
    points per comparison, but we count the *comparisons* not the test points.
    """
    count = 0
    for node in ast.walk(func_node):
        if isinstance(node, ast.Compare):
            for comparator in node.comparators:
                if _is_literal_or_len(comparator):
                    count += 1
            if _is_literal_or_len(node.left):
                count += 1
    return count


def extract_equivalence_partitions(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count distinct equivalence partition categories from type checks."""
    partitions = 0
    for node in ast.walk(func_node):
        partitions += _node_partition_count(node)
    return partitions


def extract_decision_rules(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count unique condition combinations in nested if/elif/match.

    Capped at 256 to signal complexity explosion (Regime B).
    """
    count = _count_decision_paths(func_node)
    return min(count, 256)


def extract_predicate_effects(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count predicate→effect edges (cause-effect links).

    For each if/elif predicate, count distinct effect kinds in its body.
    Each predicate × effect_kind = 1 cause-effect link.
    """
    links = 0
    for node in ast.walk(func_node):
        if isinstance(node, ast.If):
            body_effects = _count_effect_kinds(node.body)
            links += body_effects
            if node.orelse:
                else_effects = _count_effect_kinds(node.orelse)
                links += else_effects
    return links


def extract_all(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> TestDesignSignals:
    """Extract all test design signals from a function AST node."""
    return TestDesignSignals(
        boundary_points=extract_boundary_points(func_node),
        equivalence_partitions=extract_equivalence_partitions(func_node),
        decision_rule_count=extract_decision_rules(func_node),
        predicate_effect_links=extract_predicate_effects(func_node),
    )


# ── Helpers ──────────────────────────────────────────────────────────


def _node_partition_count(node: ast.AST) -> int:
    """Count equivalence partitions contributed by a single AST node."""
    # isinstance(x, T) → 1 per type
    if isinstance(node, ast.Call) and _is_isinstance_call(node):
        return _count_isinstance_types(node)
    # if x is None / is not None → 2 partitions
    if isinstance(node, ast.Compare):
        count = 0
        for op, comp in zip(node.ops, node.comparators, strict=False):
            if isinstance(op, (ast.Is, ast.IsNot)) and _is_none(comp):
                count += 2
        return count
    # match statement → 1 per case
    if isinstance(node, ast.Match):
        return len(node.cases)
    return 0


def _is_literal_or_len(node: ast.expr) -> bool:
    """Check if node is a literal constant or len() call."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float))
    if isinstance(node, ast.Call):
        return _is_name(node.func, "len")
    return False


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _is_isinstance_call(node: ast.Call) -> bool:
    return _is_name(node.func, "isinstance") and len(node.args) >= 2


def _is_name(node: ast.expr, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _count_isinstance_types(node: ast.Call) -> int:
    """Count types in isinstance(x, (A, B, C)) → 3."""
    if len(node.args) < 2:
        return 0
    type_arg = node.args[1]
    if isinstance(type_arg, ast.Tuple):
        return len(type_arg.elts)
    return 1


def _count_decision_paths(node: ast.AST) -> int:
    """Count decision paths through nested if/elif/match structures."""
    total = 0
    for child in ast.walk(node):
        if isinstance(child, ast.If) and child is not node:
            # Count branches: if body + elif/else
            branches = 1  # the if body
            orelse = child.orelse
            while orelse:
                branches += 1
                if len(orelse) == 1 and isinstance(orelse[0], ast.If):
                    orelse = orelse[0].orelse
                else:
                    break
            total += branches
        elif isinstance(child, ast.Match) and child is not node:
            total += len(child.cases)
    # At least 1 path if there are any statements
    return max(total, 1) if total > 0 else 0


_EFFECT_MAP: dict[type, str] = {
    ast.Return: "return",
    ast.Assign: "assign",
    ast.AugAssign: "assign",
    ast.AnnAssign: "assign",
    ast.Raise: "raise",
    ast.Yield: "yield",
    ast.YieldFrom: "yield",
}


def _count_effect_kinds(stmts: list[ast.stmt]) -> int:
    """Count distinct effect kinds (return, assign, raise, yield) in statements."""
    effects: set[str] = set()
    for stmt in stmts:
        for node in ast.walk(stmt):
            kind = _EFFECT_MAP.get(type(node))
            if kind:
                effects.add(kind)
    return len(effects)
