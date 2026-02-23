"""Algebraic property classification for pure Python functions."""

from __future__ import annotations

import ast

from lintgate.linters.performance_checks.algebra_types import (
    AlgebraicProperty,
    BoundSpec,
    FunctionProperties,
    PropertyKind,
    PurityResult,
)


def _get_return_nodes(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Return]:
    """Find all return statements in a function body."""
    returns = []
    for child in ast.walk(node):
        if isinstance(child, ast.Return):
            returns.append(child)
        # Don't descend into nested functions/classes
        elif (
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and child is not node
        ):
            continue
    return returns


def _is_single_expression_return(node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.expr | None:
    """If the function is effectively just `return <expr>`, return the expr."""
    returns = _get_return_nodes(node)

    # Needs exactly one return
    if len(returns) != 1:
        return None

    ret = returns[0]
    if not ret.value:
        return None

    # We also check that the body doesn't do much else. For heuristic purposes,
    # if it just assigns some things and returns them, it might still qualify,
    # but the strictest/easiest check is literally a single statement.
    if len(node.body) == 1 and isinstance(node.body[0], ast.Return):
        return ret.value

    # Could be `def f(x): "doc"; return x`
    if (
        len(node.body) == 2
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[1], ast.Return)
    ):
        return ret.value

    return None


def _check_bounded(node: ast.FunctionDef | ast.AsyncFunctionDef) -> AlgebraicProperty | None:
    """Detect if the function output is mathematically bounded."""

    expr = _is_single_expression_return(node)
    if not expr:
        return None

    # Detect `max(0, min(1, x))` or `min(max(x, 0), 1)`
    # This is a bit brittle as an AST check but catches common clamp patterns.
    if (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Name)
        and expr.func.id in ("max", "min")
    ):
        # Simple heuristic: if we return min/max with a constant, there is a bound.
        for arg in expr.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)):
                if expr.func.id == "max":
                    return AlgebraicProperty(
                        kind=PropertyKind.BOUNDED,
                        confidence=0.7,
                        evidence="Returns max(..., constant)",
                        bound_spec=BoundSpec(lower=float(arg.value), upper=None, source="clamp"),
                    )
                else:  # min
                    return AlgebraicProperty(
                        kind=PropertyKind.BOUNDED,
                        confidence=0.7,
                        evidence="Returns min(..., constant)",
                        bound_spec=BoundSpec(lower=None, upper=float(arg.value), source="clamp"),
                    )

    # Detect ratios like `x / (x + 1)` which are bounded [0, 1) for x > 0
    # Or bool returns which are bounded [0, 1]
    if getattr(node.returns, "id", None) == "bool":
        return AlgebraicProperty(
            kind=PropertyKind.BOUNDED,
            confidence=1.0,
            evidence="Return type annotation is bool",
            bound_spec=BoundSpec(lower=0.0, upper=1.0, source="annotation"),
        )

    return None


def _check_monotonic(
    node: ast.FunctionDef | ast.AsyncFunctionDef, param_names: set[str]
) -> AlgebraicProperty | None:
    """Detect if the function is monotonic relative to its inputs."""
    expr = _is_single_expression_return(node)
    if not expr:
        return None

    # Heuristic: if the expression only involves inputs and +, *, or positive constants...
    # For now, we look for simple specific patterns like `abs(x)` or `x + c`
    class MonotonicVisitor(ast.NodeVisitor):
        def __init__(self):
            self.is_monotonic = True

        def visit_BinOp(self, op: ast.BinOp):
            if isinstance(op.op, (ast.Add, ast.Mult)):
                # addition and multiplication are monotonic for positive numbers
                self.generic_visit(op)
            else:
                self.is_monotonic = False

        def visit_UnaryOp(self, op: ast.UnaryOp):
            if not isinstance(op.op, ast.UAdd):  # USub reverses monotonicity
                self.is_monotonic = False
            self.generic_visit(op)

    v = MonotonicVisitor()
    v.visit(expr)
    if v.is_monotonic:
        return AlgebraicProperty(
            PropertyKind.MONOTONIC, 0.5, "Single expression using only monotonic operations"
        )
    return None


def _check_idempotent(
    node: ast.FunctionDef | ast.AsyncFunctionDef, param_types: list[str], ret_type: str | None
) -> AlgebraicProperty | None:
    """Detect idempotent f(f(x)) == f(x)."""
    # Needs exactly 1 parameter of the same type as the return type
    if len(param_types) != 1 or not ret_type or param_types[0] != ret_type:
        return None

    expr = _is_single_expression_return(node)
    if not expr:
        return None

    # Example: `return abs(x)` is idempotent.
    if (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Name)
        and expr.func.id in ("abs", "set", "list", "dict", "str", "int", "float", "bool")
    ):
        # Type casting is generally idempotent
        return AlgebraicProperty(
            PropertyKind.IDEMPOTENT, 0.9, f"Function returns a single {expr.func.id}() cast/call"
        )

    return None


def _check_commutative_associative(
    node: ast.FunctionDef | ast.AsyncFunctionDef, param_names: set[str]
) -> tuple[AlgebraicProperty | None, AlgebraicProperty | None]:
    """Detect if f(x, y) == f(y, x) and f(x, f(y, z)) == f(f(x, y), z)."""
    if len(param_names) < 2:
        return None, None

    expr = _is_single_expression_return(node)
    if not expr:
        return None, None

    # Heuristic: the expression uses only Commutative operations (+, *, &, |)
    # on all arguments equally.

    if isinstance(expr, ast.BinOp) and isinstance(
        expr.op, (ast.Add, ast.Mult, ast.BitAnd, ast.BitOr, ast.BitXor)
    ):
        comm = AlgebraicProperty(
            PropertyKind.COMMUTATIVE, 0.8, "Returns a commutative binary operation (+, *, &, |, ^)"
        )
        assoc = AlgebraicProperty(
            PropertyKind.ASSOCIATIVE, 0.8, "Returns an associative binary operation"
        )
        return comm, assoc

    return None, None


def classify_properties(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef, purity: PurityResult
) -> FunctionProperties:
    """
    Given a known-pure function, attempt to classify its algebraic properties.
    """
    properties: list[AlgebraicProperty] = [
        AlgebraicProperty(
            PropertyKind.PURE, purity.confidence, "Passed pure function detector pass 1 and 2"
        )
    ]
    hints: list[str] = ["cacheable"]

    param_names = {arg.arg for arg in func_node.args.args}
    param_types = [
        getattr(arg.annotation, "id", None)
        for arg in func_node.args.args
        if getattr(arg.annotation, "id", None)
    ]

    # 1. Bounded
    bounded = _check_bounded(func_node)
    if bounded:
        properties.append(bounded)
        hints.append("overflow-safe")

    # 2. Monotonic
    monotonic = _check_monotonic(func_node, param_names)
    if monotonic:
        properties.append(monotonic)

    # 3. Idempotent
    idempot = _check_idempotent(func_node, param_types, purity.return_annotation)
    if idempot:
        properties.append(idempot)
        hints.append("safe-to-retry")
        if "cacheable" in hints:
            hints.append("cache-without-invalidation")  # very cheap to cache

    # 4 & 5. Commutative / Associative
    comm, assoc = _check_commutative_associative(func_node, param_names)
    if comm:
        properties.append(comm)
        hints.append("parameter-order-independent")
    if assoc:
        properties.append(assoc)
        hints.append("parallelizable")
        hints.append("map-reduce-compatible")

    return FunctionProperties(
        purity=purity,
        properties=tuple(properties),
        optimization_hints=tuple(set(hints)),  # deduplicate hints
    )
