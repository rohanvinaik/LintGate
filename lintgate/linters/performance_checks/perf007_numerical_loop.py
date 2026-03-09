"""PERF007: Arithmetic-heavy loops without vectorization.

Flags loops over large ranges that contain arithmetic on the loop variable,
suggesting numpy/numba for numerical computation.

Strict gating:
- Skip if loop bound is a small constant (< 100)
- Only flag when body contains arithmetic ops (BinOp) on the loop variable
- Skip if numpy, pandas, or numba already imported
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ...types import LintIssue
from ._helpers import has_import

if TYPE_CHECKING:
    from collections.abc import Iterable


def check_numerical_loop(tree: ast.AST, file_path: str) -> Iterable[LintIssue]:
    """Flag arithmetic-heavy loops over large ranges without numpy/numba."""
    if has_import(tree, "numpy") or has_import(tree, "pandas") or has_import(tree, "numba"):
        return

    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue

        iter_node = node.iter
        if not isinstance(iter_node, ast.Call):
            continue
        if not (isinstance(iter_node.func, ast.Name) and iter_node.func.id == "range"):
            continue

        if _is_small_range_bound(iter_node):
            continue

        if not isinstance(node.target, ast.Name):
            continue
        loop_var = node.target.id

        if not _has_arithmetic_on_var(node.body, loop_var):
            continue

        yield LintIssue(
            linter="performance_checker",
            kind="PERF007",
            message=(
                "Arithmetic-heavy loop over a large range without vectorization. "
                "Consider numpy/numba for numerical computation — vectorized operations "
                "can be orders of magnitude faster than pure Python loops."
            ),
            file=file_path,
            line=node.lineno,
            severity="informational",
            confidence=0.6,
            evidence={"loop_var": loop_var, "check": "PERF007"},
            suggestions=[
                "If this is numerical work, consider `import numpy as np` with vectorized ops.",
                "For hot loops, `@numba.jit` can provide 10-100x speedup.",
            ],
        )


def _is_small_range_bound(call: ast.Call) -> bool:
    """Check if range() has a small constant bound (< 100)."""
    if len(call.args) == 1:
        arg = call.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
            return arg.value < 100
    elif len(call.args) >= 2:
        stop = call.args[1]
        if isinstance(stop, ast.Constant) and isinstance(stop.value, int):
            start = call.args[0]
            if isinstance(start, ast.Constant) and isinstance(start.value, int):
                return (stop.value - start.value) < 100
    return False


def _has_arithmetic_on_var(body: list[ast.stmt], var_name: str) -> bool:
    """Check if a loop body contains arithmetic BinOp that references the loop variable."""
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if (
            isinstance(node, ast.BinOp)
            and isinstance(
                node.op,
                (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.FloorDiv, ast.Mod),
            )
            and (_references_var(node.left, var_name) or _references_var(node.right, var_name))
        ):
            return True
    return False


def _references_var(node: ast.AST, var_name: str) -> bool:
    """Check if an expression references a specific variable name."""
    if isinstance(node, ast.Name) and node.id == var_name:
        return True
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Name)
        and node.slice.id == var_name
    )
