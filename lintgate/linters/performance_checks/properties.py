"""Algebraic property classification for pure Python functions."""

from __future__ import annotations

import ast
import contextlib
from typing import Any

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


def _is_single_expression_return(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.expr | None:
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


def _check_bounded(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> AlgebraicProperty | None:
    """Detect if the function output is mathematically bounded."""

    expr = _is_single_expression_return(node)
    if not expr:
        return None

    # Detect `max(lo, min(x, hi))` or `min(hi, max(x, lo))` patterns
    def _extract_bounds(node: ast.AST) -> tuple[float | None, float | None]:
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            return None, None

        fn = node.func.id
        if fn not in ("max", "min"):
            return None, None

        def _get_val(n: ast.AST) -> float | None:
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
                return float(n.value)
            if (
                isinstance(n, ast.UnaryOp)
                and isinstance(n.op, ast.USub)
                and isinstance(n.operand, ast.Constant)
                and isinstance(n.operand.value, (int, float))
            ):
                return -float(n.operand.value)
            return None

        lo, hi = None, None
        for arg in node.args:
            val = _get_val(arg)
            if val is not None:
                if fn == "max":
                    lo = val
                else:
                    hi = val
            else:
                inner_lo, inner_hi = _extract_bounds(arg)
                if inner_lo is not None:
                    lo = inner_lo
                if inner_hi is not None:
                    hi = inner_hi
        return lo, hi

    lo, hi = _extract_bounds(expr)
    if lo is not None or hi is not None:
        evidence = []
        if lo is not None:
            evidence.append(f"lower={lo}")
        if hi is not None:
            evidence.append(f"upper={hi}")
        return AlgebraicProperty(
            kind=PropertyKind.BOUNDED,
            confidence=0.8,
            evidence=f"Algebraic bound detected: {', '.join(evidence)}",
            bound_spec=BoundSpec(lower=lo, upper=hi, source="clamp"),
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
                self.generic_visit(op)
            else:
                self.is_monotonic = False

        def visit_UnaryOp(self, op: ast.UnaryOp):
            if not isinstance(op.op, ast.UAdd):
                self.is_monotonic = False
            self.generic_visit(op)

        def visit_Call(self, node: ast.Call):
            # We don't know if a general call is monotonic
            self.is_monotonic = False

        def visit_Subscript(self, node: ast.Subscript):
            self.is_monotonic = False

        def visit_Attribute(self, node: ast.Attribute):
            self.is_monotonic = False

    v = MonotonicVisitor()
    v.visit(expr)
    if v.is_monotonic:
        return AlgebraicProperty(
            PropertyKind.MONOTONIC,
            0.5,
            "Single expression using only monotonic operations",
        )
    return None


def _check_idempotent(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    param_types: list[str],
    ret_type: str | None,
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
            PropertyKind.IDEMPOTENT,
            0.9,
            f"Function returns a single {expr.func.id}() cast/call",
        )

    return None


_NUMERIC_TYPES = frozenset({"int", "float", "complex", "Decimal"})
_NON_COMMUTATIVE_ADD_TYPES = frozenset({"str", "bytes", "list", "tuple"})
_SET_TYPES = frozenset({"set", "frozenset"})


def _extract_param_type_annotations(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, str]:
    """Extract parameter type annotations from a function definition."""
    param_types: dict[str, str] = {}
    for arg in node.args.args:
        if arg.annotation:
            with contextlib.suppress(Exception):
                param_types[arg.arg] = ast.unparse(arg.annotation)
    return param_types


def _check_commutative_associative(
    node: ast.FunctionDef | ast.AsyncFunctionDef, param_names: set[str]
) -> tuple[AlgebraicProperty | None, AlgebraicProperty | None]:
    """Detect if f(x, y) == f(y, x) and f(x, f(y, z)) == f(f(x, y), z).

    Type-aware: consults parameter type annotations to avoid false positives
    for non-commutative types like str and list concatenation.
    """
    if len(param_names) < 2:
        return None, None

    expr = _is_single_expression_return(node)
    if not expr:
        return None, None

    if not isinstance(expr, ast.BinOp):
        return None, None

    op = expr.op
    param_type_map = _extract_param_type_annotations(node)
    resolved_types = set(param_type_map.values())

    # Set operations (|, &, ^) — commutative + associative for set types
    if isinstance(op, (ast.BitAnd, ast.BitOr, ast.BitXor)):
        type_ctx = param_type_map if param_type_map else None
        if resolved_types and resolved_types <= _SET_TYPES:
            confidence = 0.9
            evidence = "Returns a commutative set operation (|, &, ^) with set-typed parameters"
        elif resolved_types:
            confidence = 0.8
            evidence = "Returns a commutative binary operation (&, |, ^)"
        else:
            confidence = 0.6
            evidence = "Returns a binary operation (&, |, ^) (unannotated parameters)"
        comm = AlgebraicProperty(
            PropertyKind.COMMUTATIVE, confidence, evidence, type_context=type_ctx
        )
        assoc = AlgebraicProperty(
            PropertyKind.ASSOCIATIVE,
            confidence,
            "Returns an associative binary operation",
            type_context=type_ctx,
        )
        return comm, assoc

    # Addition and multiplication — must check for non-commutative types
    if isinstance(op, (ast.Add, ast.Mult)):
        # If any parameter is annotated as a non-commutative type, skip entirely
        if resolved_types & _NON_COMMUTATIVE_ADD_TYPES:
            return None, None

        type_ctx = param_type_map if param_type_map else None

        if resolved_types and resolved_types <= _NUMERIC_TYPES:
            confidence = 0.9
            evidence = "Returns a commutative binary operation (+, *) with numeric-typed parameters"
        elif resolved_types:
            # Some other annotated type — keep detection but standard confidence
            confidence = 0.8
            evidence = "Returns a commutative binary operation (+, *)"
        else:
            # No annotations — reduced confidence
            confidence = 0.6
            evidence = "Returns a binary operation (+, *) (unannotated — may be non-commutative for str/list)"

        comm = AlgebraicProperty(
            PropertyKind.COMMUTATIVE, confidence, evidence, type_context=type_ctx
        )
        assoc = AlgebraicProperty(
            PropertyKind.ASSOCIATIVE,
            confidence,
            "Returns an associative binary operation",
            type_context=type_ctx,
        )
        return comm, assoc

    return None, None


def _apply_spec_level_gate(
    mutation_state: Any | None,
    confidence: float,
    evidence_prefix: str,
) -> tuple[bool, float, str]:
    """Gate confidence based on mutation survival rate.

    Reads lightweight fields from cached mutation results (dict with
    ``survival_rate``, ``coverage_depth``, ``total_mutants``).

    Three tiers (theory §2, §8.1):
    - survival > 0.5  → GATED:    conf = 0.10
    - 0.2 < survival ≤ 0.5 → PENALIZED: conf *= 0.5
    - survival ≤ 0.2  → VERIFIED: conf = max(conf, 0.90)

    Returns ``(gated, new_confidence, updated_evidence_prefix)``.
    """
    if mutation_state is None:
        return False, confidence, evidence_prefix

    survival = _read_float(mutation_state, "survival_rate", -1.0)
    total = _read_int(mutation_state, "total_mutants", 0)
    depth = _read_str(mutation_state, "coverage_depth", "")

    if total == 0 or survival < 0:
        return False, confidence, evidence_prefix

    is_gateable = depth == "profiled"

    if survival > 0.5:
        if is_gateable:
            return True, 0.1, f"[MUTATION GATED surv={survival:.0%}] "
        return False, confidence, f"[ADVISORY surv={survival:.0%}] "

    if survival > 0.2:
        label = "MUTATION PENALIZED" if is_gateable else "ADVISORY"
        new_conf = confidence * 0.5 if is_gateable else confidence
        return False, new_conf, f"[{label} surv={survival:.0%}] "

    # survival ≤ 0.2 — verified (only gateable data can boost confidence)
    if is_gateable:
        return False, max(confidence, 0.9), f"[MUTATION VERIFIED surv={survival:.0%}] "
    return False, confidence, f"[ADVISORY surv={survival:.0%}] "


def _apply_spec_level_hint_suppression(
    mutation_state: Any | None,
    hints: list[str],
) -> list[str]:
    """Suppress non-essential optimization hints when specification is weak.

    When mutation data is gateable and survival > 50%, strip all hints
    (the function's behavior is too underspecified for any optimization).
    When survival is 20–50%, keep only ``cacheable`` (the safest hint).
    """
    if mutation_state is None:
        return hints

    survival = _read_float(mutation_state, "survival_rate", -1.0)
    total = _read_int(mutation_state, "total_mutants", 0)
    depth = _read_str(mutation_state, "coverage_depth", "")

    if total == 0 or survival < 0 or depth != "profiled":
        return hints

    if survival > 0.5:
        return []
    if survival > 0.2:
        return [h for h in hints if h == "cacheable"]
    return hints


def _read_float(state: Any, key: str, default: float) -> float:
    """Read a float from a dict or object attribute, safely."""
    try:
        val = state[key] if isinstance(state, dict) else getattr(state, key, default)
        return float(val)
    except (TypeError, ValueError, KeyError):
        return default


def _read_int(state: Any, key: str, default: int) -> int:
    """Read an int from a dict or object attribute, safely."""
    try:
        val = state[key] if isinstance(state, dict) else getattr(state, key, default)
        return int(val)
    except (TypeError, ValueError, KeyError):
        return default


def _read_str(state: Any, key: str, default: str) -> str:
    """Read a str from a dict or object attribute, safely."""
    try:
        val = state[key] if isinstance(state, dict) else getattr(state, key, default)
        return str(val)
    except (TypeError, ValueError, KeyError):
        return default


def classify_properties(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    purity: PurityResult,
    mutation_state: Any | None = None,
    enforcement_mode: str = "audit",
) -> FunctionProperties:
    """Classify algebraic properties for a known-pure function.

    When ``mutation_state`` is provided (a dict or object with
    ``survival_rate``, ``total_mutants``, ``coverage_depth``), the gate
    modulates confidence and suppresses optimization hints based on
    specification completeness.
    """
    _ = enforcement_mode

    gated, confidence, evidence_prefix = _apply_spec_level_gate(
        mutation_state, purity.confidence, ""
    )

    properties: list[AlgebraicProperty] = [
        AlgebraicProperty(
            PropertyKind.PURE,
            confidence,
            f"{evidence_prefix}Passed pure function detector pass 1 and 2",
        )
    ]
    hints: list[str] = ["cacheable"]

    param_names = {arg.arg for arg in func_node.args.args}
    param_types: list[str] = [
        t
        for arg in func_node.args.args
        if isinstance((t := getattr(arg.annotation, "id", None)), str)
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

    # SpecificationLevel hint suppression (checked before numeric gate)
    hints = _apply_spec_level_hint_suppression(mutation_state, hints)

    return FunctionProperties(
        purity=purity,
        properties=tuple(properties),
        optimization_hints=tuple(set(hints)),  # deduplicate hints
    )
