"""Algebraic property classification for pure Python functions."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lintgate.linters.performance_checks.algebra_types import (
    AlgebraicProperty,
    BoundSpec,
    FunctionProperties,
    PropertyKind,
    PurityResult,
)

if TYPE_CHECKING:
    from lintgate.mutation.state import FunctionMutationState


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
        and expr.func.id
        in ("abs", "set", "list", "dict", "str", "int", "float", "bool")
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
            try:
                param_types[arg.arg] = ast.unparse(arg.annotation)
            except Exception:
                pass
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


def classify_properties(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    purity: PurityResult,
    mutation_state: FunctionMutationState | None = None,
    enforcement_mode: str = "audit",
) -> FunctionProperties:
    """
    Given a known-pure function, attempt to classify its algebraic properties.
    """
    confidence = purity.confidence
    evidence_prefix = ""

    # Integrated Cross-Channel Gate (#207: depth/confidence/assertion-aware)
    if mutation_state and mutation_state.total > 0:
        survival_rate = mutation_state.survival_rate
        spec_strength = mutation_state.specification_strength

        if mutation_state.is_gateable:
            # Hard gate: sufficient evidence to modify hints
            # Survival > 0.5 always gates (independent of enforcement mode)
            # Spec-strength < 0.5 only gates when enforcement_mode != "audit"
            should_gate = survival_rate > 0.5
            if not should_gate and enforcement_mode != "audit":
                from lintgate.mutation.prescriptions import resolve_gate_status
                gate_status, _ = resolve_gate_status(spec_strength, enforcement_mode)
                should_gate = gate_status != "pass"

            if should_gate:
                confidence = min(confidence, 0.1)
                evidence_prefix = (
                    f"[MUTATION GATED: survival={survival_rate:.0%}, "
                    f"spec_strength={spec_strength:.0%}] "
                )
            elif survival_rate > 0.2:
                confidence *= 0.5
                evidence_prefix = f"[MUTATION PENALIZED: survival={survival_rate:.0%}] "
            else:
                confidence = max(confidence, 0.9)
                evidence_prefix = (
                    f"[MUTATION VERIFIED: survival={survival_rate:.0%}, "
                    f"spec_strength={spec_strength:.0%}] "
                )
        else:
            # Advisory only: sampled/low-confidence — hints NOT modified
            if survival_rate > 0.3:
                evidence_prefix = (
                    f"[MUTATION ADVISORY: survival={survival_rate:.0%}, "
                    f"depth={mutation_state.depth.value}] "
                )
            elif survival_rate <= 0.2:
                confidence = max(confidence, 0.9)
                evidence_prefix = f"[MUTATION VERIFIED: survival={survival_rate:.0%}] "

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

    # #207: Hard gate — suppress hints when gateable evidence says specification is weak.
    # Applied after all property detection so ALL hints are subject to the gate.
    if mutation_state and mutation_state.is_gateable and mutation_state.total > 0:
        survival_rate = mutation_state.survival_rate
        spec_strength = mutation_state.specification_strength
        # Survival > 0.5 always suppresses hints
        should_suppress = survival_rate > 0.5
        if not should_suppress and enforcement_mode != "audit":
            from lintgate.mutation.prescriptions import resolve_gate_status
            gate_status, _ = resolve_gate_status(spec_strength, enforcement_mode)
            should_suppress = gate_status != "pass"

        if should_suppress:
            hints = []  # Fully gated — no optimization hints
        elif survival_rate > 0.2:
            hints = [h for h in hints if h == "cacheable"]  # Only cacheable survives

    return FunctionProperties(
        purity=purity,
        properties=tuple(properties),
        optimization_hints=tuple(set(hints)),  # deduplicate hints
    )
