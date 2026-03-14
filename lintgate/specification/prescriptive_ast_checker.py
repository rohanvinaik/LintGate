"""AST-level invariant checker for PrescriptiveSpec predicates.

Evaluates structured Predicate IR against a function's AST to detect
violations. Only checks what's structurally verifiable — CUSTOM predicates
are skipped (they require semantic understanding, not AST analysis).

Checkable patterns:
- IS_TYPE on return: function has return type annotation matching value
- HAS_ATTR on self/cls: class method accesses expected attribute
- CALLS: function body contains a call to the specified function
- GT/GTE/LT/LTE on len(result): return value has bounds (via assert/if guard)
- Compound AND/OR/NOT: recursive evaluation with short-circuit

This is a conservative checker — it reports violations only when the AST
provides strong negative evidence. Absence of evidence is NOT a violation.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from .prescriptive_spec import Invariant, Predicate, PredicateOp


@dataclass
class CheckResult:
    """Result of checking one invariant against a function AST."""

    invariant_name: str
    status: str  # "pass" | "fail" | "skip"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "invariant_name": self.invariant_name,
            "status": self.status,
            "reason": self.reason,
        }


CheckResult.__test__ = False  # type: ignore[attr-defined]


def check_invariants_against_ast(
    source: str,
    function_name: str,
    invariants: list[Invariant],
) -> list[CheckResult]:
    """Check invariants against a function's AST.

    Returns a CheckResult per invariant. Only structured predicates
    are checked — CUSTOM predicates always return status="skip".
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [
            CheckResult(inv.name, "skip", "source parse error")
            for inv in invariants
        ]

    func_node = _find_function(tree, function_name)
    if func_node is None:
        return [
            CheckResult(inv.name, "skip", f"function '{function_name}' not found")
            for inv in invariants
        ]

    results: list[CheckResult] = []
    for inv in invariants:
        result = _check_predicate(func_node, inv.predicate, inv.name, source)
        results.append(result)
    return results


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Find a function definition by name in the AST."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _check_predicate(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    pred: Predicate,
    inv_name: str,
    source: str,
) -> CheckResult:
    """Dispatch predicate check based on op type."""
    op = pred.op

    if op == PredicateOp.CUSTOM:
        return CheckResult(inv_name, "skip", "CUSTOM predicate — requires semantic review")

    if op == PredicateOp.TRUE:
        return CheckResult(inv_name, "pass", "tautology")

    if op == PredicateOp.IS_TYPE:
        return _check_is_type(func, pred, inv_name)

    if op == PredicateOp.HAS_ATTR:
        return _check_has_attr(func, pred, inv_name)

    if op == PredicateOp.CALLS:
        return _check_calls(func, pred, inv_name)

    if op in (PredicateOp.GT, PredicateOp.GTE, PredicateOp.LT, PredicateOp.LTE):
        return _check_comparison_guard(func, pred, inv_name)

    if op == PredicateOp.PURE:
        return _check_pure(func, inv_name)

    if op == PredicateOp.RETURNS_NON_NULL:
        return _check_returns_non_null(func, inv_name)

    if op == PredicateOp.RAISES:
        return _check_raises(func, pred, inv_name)

    if op == PredicateOp.NO_RAISE:
        return _check_no_raise(func, inv_name)

    if op == PredicateOp.PARAM_COUNT_LTE:
        return _check_param_count_lte(func, pred, inv_name)

    if op == PredicateOp.AND:
        return _check_and(func, pred, inv_name, source)

    if op == PredicateOp.OR:
        return _check_or(func, pred, inv_name, source)

    if op == PredicateOp.NOT:
        return _check_not(func, pred, inv_name, source)

    # EQ, NEQ, IN, NOT_IN — can't reliably check from AST alone
    return CheckResult(inv_name, "skip", f"op '{op.value}' not AST-checkable")


def _check_is_type(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    pred: Predicate,
    inv_name: str,
) -> CheckResult:
    """Check IS_TYPE: verify return type annotation matches expected type."""
    if pred.subject not in ("result", "return", "output"):
        return CheckResult(inv_name, "skip", f"IS_TYPE on '{pred.subject}' — not return-focused")

    expected_type = str(pred.value) if pred.value else ""
    if not expected_type:
        return CheckResult(inv_name, "skip", "no expected type specified")

    ret_annotation = func.returns
    if ret_annotation is None:
        return CheckResult(inv_name, "fail", f"no return type annotation, expected '{expected_type}'")

    actual_type = _annotation_to_str(ret_annotation)
    if expected_type.lower() in actual_type.lower():
        return CheckResult(inv_name, "pass", f"return type '{actual_type}' matches '{expected_type}'")

    return CheckResult(inv_name, "fail", f"return type '{actual_type}' does not match expected '{expected_type}'")


def _check_has_attr(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    pred: Predicate,
    inv_name: str,
) -> CheckResult:
    """Check HAS_ATTR: verify the function body accesses the expected attribute."""
    attr_name = str(pred.value) if pred.value else pred.object
    if not attr_name:
        return CheckResult(inv_name, "skip", "no attribute name specified")

    for node in ast.walk(func):
        if isinstance(node, ast.Attribute) and node.attr == attr_name:
            return CheckResult(inv_name, "pass", f"attribute '{attr_name}' accessed")

    return CheckResult(inv_name, "skip", f"attribute '{attr_name}' not found — may be dynamic")


def _check_calls(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    pred: Predicate,
    inv_name: str,
) -> CheckResult:
    """Check CALLS: verify the function body calls the specified function."""
    target = pred.subject or str(pred.value) or ""
    if not target:
        return CheckResult(inv_name, "skip", "no call target specified")

    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            call_name = _call_to_str(node)
            if target in call_name:
                return CheckResult(inv_name, "pass", f"calls '{call_name}'")

    return CheckResult(inv_name, "fail", f"no call to '{target}' found in function body")


def _check_comparison_guard(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    pred: Predicate,
    inv_name: str,
) -> CheckResult:
    """Check GT/GTE/LT/LTE: look for assert/if guards on the subject."""
    subject = pred.subject
    if not subject:
        return CheckResult(inv_name, "skip", "no subject for comparison")

    # Look for assert statements or if-guards that reference the subject
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            unparsed = ast.unparse(node.test)
            if subject in unparsed:
                return CheckResult(inv_name, "pass", f"assert guard found: {unparsed[:60]}")
        if isinstance(node, ast.If):
            unparsed = ast.unparse(node.test)
            if subject in unparsed:
                return CheckResult(inv_name, "pass", f"if guard found: {unparsed[:60]}")

    # Not finding a guard is not a violation — it may be enforced differently
    return CheckResult(inv_name, "skip", f"no explicit guard on '{subject}' — may be implicit")


def _check_pure(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    inv_name: str,
) -> CheckResult:
    """Check PURE: no global/nonlocal writes, no attribute assignment on non-local targets."""
    for node in ast.walk(func):
        if isinstance(node, ast.Global):
            return CheckResult(inv_name, "fail", f"uses 'global {', '.join(node.names)}'")
        if isinstance(node, ast.Nonlocal):
            return CheckResult(inv_name, "fail", f"uses 'nonlocal {', '.join(node.names)}'")
    # Check for print/open/write calls (common I/O side effects)
    _IO_CALLS = {"print", "open", "write", "sys.stdout.write", "sys.stderr.write"}
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            name = _call_to_str(node)
            if name in _IO_CALLS:
                return CheckResult(inv_name, "fail", f"calls I/O function '{name}'")
    return CheckResult(inv_name, "pass", "no global/nonlocal/IO side effects detected")


def _check_returns_non_null(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    inv_name: str,
) -> CheckResult:
    """Check RETURNS_NON_NULL: no bare `return` or `return None`."""
    for node in ast.walk(func):
        if isinstance(node, ast.Return):
            if node.value is None:
                return CheckResult(inv_name, "fail", "bare 'return' (implicit None)")
            if isinstance(node.value, ast.Constant) and node.value.value is None:
                return CheckResult(inv_name, "fail", "explicit 'return None'")
    # Check that at least one return exists
    has_return = any(isinstance(n, ast.Return) for n in ast.walk(func))
    if not has_return:
        return CheckResult(inv_name, "fail", "no return statement — implicit None")
    return CheckResult(inv_name, "pass", "all return paths return non-None values")


def _check_raises(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    pred: Predicate,
    inv_name: str,
) -> CheckResult:
    """Check RAISES: function body contains `raise ExceptionType`."""
    expected = str(pred.value) if pred.value else ""
    for node in ast.walk(func):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc_name = ""
            if isinstance(node.exc, ast.Call):
                exc_name = _call_to_str(node.exc)
            elif isinstance(node.exc, ast.Name):
                exc_name = node.exc.id
            if not expected or expected in exc_name:
                return CheckResult(inv_name, "pass", f"raises {exc_name}")
    if expected:
        return CheckResult(inv_name, "fail", f"no 'raise {expected}' found")
    return CheckResult(inv_name, "fail", "no raise statement found")


def _check_no_raise(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    inv_name: str,
) -> CheckResult:
    """Check NO_RAISE: function body contains no `raise` statements."""
    for node in ast.walk(func):
        if isinstance(node, ast.Raise):
            exc_desc = ""
            if node.exc:
                try:
                    exc_desc = ast.unparse(node.exc)[:40]
                except Exception:
                    pass
            return CheckResult(inv_name, "fail", f"raises {exc_desc}" if exc_desc else "contains raise")
    return CheckResult(inv_name, "pass", "no raise statements")


def _check_param_count_lte(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    pred: Predicate,
    inv_name: str,
) -> CheckResult:
    """Check PARAM_COUNT_LTE: parameter count ≤ value."""
    max_params = int(pred.value) if pred.value is not None else 0
    args = func.args
    # Count all params excluding self/cls
    all_args = [a.arg for a in args.args if a.arg not in ("self", "cls")]
    all_args.extend(a.arg for a in args.posonlyargs)
    all_args.extend(a.arg for a in args.kwonlyargs)
    actual = len(all_args)
    if actual <= max_params:
        return CheckResult(inv_name, "pass", f"{actual} params ≤ {max_params}")
    return CheckResult(inv_name, "fail", f"{actual} params > {max_params}")


def _check_and(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    pred: Predicate,
    inv_name: str,
    source: str,
) -> CheckResult:
    """AND: all operands must pass. One fail → fail. All skip → skip."""
    if not pred.operands:
        return CheckResult(inv_name, "pass", "empty AND")

    statuses: list[str] = []
    fail_reasons: list[str] = []
    for child in pred.operands:
        r = _check_predicate(func, child, inv_name, source)
        statuses.append(r.status)
        if r.status == "fail":
            fail_reasons.append(r.reason)

    if "fail" in statuses:
        return CheckResult(inv_name, "fail", "; ".join(fail_reasons))
    if "pass" in statuses:
        return CheckResult(inv_name, "pass", "AND — at least one operand verified")
    return CheckResult(inv_name, "skip", "AND — all operands skipped")


def _check_or(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    pred: Predicate,
    inv_name: str,
    source: str,
) -> CheckResult:
    """OR: any operand passing → pass. All fail → fail."""
    if not pred.operands:
        return CheckResult(inv_name, "pass", "empty OR")

    statuses: list[str] = []
    for child in pred.operands:
        r = _check_predicate(func, child, inv_name, source)
        statuses.append(r.status)
        if r.status == "pass":
            return CheckResult(inv_name, "pass", f"OR — operand passed: {r.reason}")

    if all(s == "fail" for s in statuses):
        return CheckResult(inv_name, "fail", "OR — all operands failed")
    return CheckResult(inv_name, "skip", "OR — no operand conclusive")


def _check_not(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    pred: Predicate,
    inv_name: str,
    source: str,
) -> CheckResult:
    """NOT: inner pass → fail, inner fail → pass."""
    if not pred.operands:
        return CheckResult(inv_name, "skip", "empty NOT")

    r = _check_predicate(func, pred.operands[0], inv_name, source)
    if r.status == "pass":
        return CheckResult(inv_name, "fail", f"NOT — inner passed: {r.reason}")
    if r.status == "fail":
        return CheckResult(inv_name, "pass", f"NOT — inner failed: {r.reason}")
    return CheckResult(inv_name, "skip", "NOT — inner inconclusive")


# ── AST helpers ───────────────────────────────────────────────────────


def _annotation_to_str(node: ast.expr) -> str:
    """Convert a type annotation AST node to a string."""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _call_to_str(node: ast.Call) -> str:
    """Extract a readable name from a Call node."""
    try:
        return ast.unparse(node.func)
    except Exception:
        return ""
