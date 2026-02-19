"""Cognitive complexity computation — the core algorithm.

Separated from structure_checker.py for file-size hygiene.

Based on the SonarSource cognitive complexity specification.
Fundamentally different from cyclomatic complexity:
- CC counts linearly independent paths through the code
- CogC counts the mental effort needed to understand the code

Key differences: nesting adds incrementally, boolean sequences penalized,
else/elif/catch add to complexity (CC ignores these).
"""

from __future__ import annotations

import ast


def compute_cognitive_complexity(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> int:
    """Compute cognitive complexity for a function.

    Rules:
    1. +1 for each break in linear flow (if, for, while, except, with)
    2. +1 for each nesting level these breaks occur at
    3. +1 for boolean operator sequences (a and b -> +1, a and b and c -> +1)
    4. +1 for else/elif/finally (breaks in flow)
    5. +1 for recursion (calling the function itself)
    """
    func_name = node.name
    total = 0

    def _walk(body: list[ast.stmt], nesting: int) -> None:
        nonlocal total
        for stmt in body:
            total += _cogc_for_statement(stmt, nesting, func_name)
            for child_body, extra_nesting in _nested_bodies(stmt):
                _walk(child_body, nesting + extra_nesting)

    _walk(node.body, 0)
    return total


def compute_max_nesting(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Compute maximum nesting depth in a function."""
    return _nesting_depth(node.body, 0)


def count_statements(node: ast.AST) -> int:
    """Count executable statements in a function body (recursive)."""
    count = 0
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.stmt):
            count += 1
            count += count_statements(child)
    return count


# ─── Internal helpers ────────────────────────────────────────────────────


def _cogc_for_statement(
    stmt: ast.stmt,
    nesting: int,
    func_name: str,
) -> int:
    """Compute the cognitive complexity increment for a single statement."""
    score = 0

    # +1 (+ nesting) for flow-breaking structures
    if isinstance(stmt, (ast.If, ast.For, ast.While, ast.AsyncFor)):
        score += 1 + nesting
    elif isinstance(stmt, ast.Try):
        for _handler in stmt.handlers:
            score += 1 + nesting
    elif isinstance(stmt, ast.With):
        score += 1 + nesting

    # Boolean operator sequences in conditions
    if isinstance(stmt, (ast.If, ast.While)):
        score += _count_boolean_operators(stmt.test)

    # Recursion detection
    score += _check_recursion(stmt, func_name)

    return score


def _check_recursion(stmt: ast.stmt, func_name: str) -> int:
    """Check if a statement contains a recursive call (+1 if so)."""
    for child in ast.walk(stmt):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == func_name
        ):
            return 1
    return 0


def _nested_bodies(stmt: ast.stmt) -> list[tuple[list[ast.stmt], int]]:
    """Get nested bodies of a statement with their nesting increment."""
    bodies: list[tuple[list[ast.stmt], int]] = []

    if isinstance(stmt, ast.If):
        bodies.append((stmt.body, 1))
        if stmt.orelse:
            # elif chains don't increase nesting
            if len(stmt.orelse) == 1 and isinstance(stmt.orelse[0], ast.If):
                bodies.append((stmt.orelse, 0))
            else:
                bodies.append((stmt.orelse, 1))

    elif isinstance(stmt, (ast.For, ast.While, ast.AsyncFor)):
        bodies.append((stmt.body, 1))
        if stmt.orelse:
            bodies.append((stmt.orelse, 1))

    elif isinstance(stmt, ast.Try):
        bodies.append((stmt.body, 1))
        for handler in stmt.handlers:
            bodies.append((handler.body, 1))
        if stmt.orelse:
            bodies.append((stmt.orelse, 1))
        if stmt.finalbody:
            bodies.append((stmt.finalbody, 1))

    elif isinstance(stmt, ast.With):
        bodies.append((stmt.body, 1))

    elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        bodies.append((stmt.body, 0))

    return bodies


def _count_boolean_operators(node: ast.expr) -> int:
    """Count sequences of boolean operators (and/or).

    `a and b` -> 1 (one operator)
    `a and b and c` -> 1 (same operator sequence)
    `a and b or c` -> 2 (mixed operators = two sequences)
    """
    if not isinstance(node, ast.BoolOp):
        return 0

    count = 1
    for value in node.values:
        if isinstance(value, ast.BoolOp):
            if not isinstance(value.op, type(node.op)):
                count += 1
            count += _count_boolean_operators(value)

    return count


def _nesting_depth(body: list[ast.stmt], current: int) -> int:
    """Recursively compute the maximum nesting depth."""
    max_depth = current
    for stmt in body:
        for child_body in _get_nesting_bodies(stmt):
            depth = _nesting_depth(child_body, current + 1)
            max_depth = max(max_depth, depth)
    return max_depth


def _get_nesting_bodies(stmt: ast.stmt) -> list[list[ast.stmt]]:
    """Get nested bodies that increase nesting depth."""
    bodies: list[list[ast.stmt]] = []

    if isinstance(stmt, (ast.If, ast.For, ast.While, ast.AsyncFor)):
        bodies.append(stmt.body)
        if stmt.orelse:
            bodies.append(stmt.orelse)
    elif isinstance(stmt, ast.Try):
        bodies.append(stmt.body)
        for handler in stmt.handlers:
            bodies.append(handler.body)
        if stmt.orelse:
            bodies.append(stmt.orelse)
        if stmt.finalbody:
            bodies.append(stmt.finalbody)
    elif isinstance(stmt, ast.With):
        bodies.append(stmt.body)

    return bodies
