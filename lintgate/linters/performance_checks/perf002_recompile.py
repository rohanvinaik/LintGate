"""PERF002: re.compile() inside function bodies.

Flags constant-pattern re.compile() calls that should be hoisted to module level.

Skips:
- Module-level re.compile (correct usage)
- @lru_cache/@cache decorated functions (compiled once, cached)
- Non-constant pattern arguments
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ...types import LintIssue

if TYPE_CHECKING:
    from collections.abc import Iterable


def check_recompile_in_function(tree: ast.AST, file_path: str) -> Iterable[LintIssue]:
    """Flag re.compile() called inside function bodies."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        if _has_cache_decorator(node):
            continue

        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            if not _is_re_compile_call(inner):
                continue
            if not inner.args or not isinstance(inner.args[0], ast.Constant):
                continue
            if not isinstance(inner.args[0].value, str):
                continue

            yield LintIssue(
                linter="performance_checker",
                kind="PERF002",
                message=(
                    f"re.compile() with constant pattern at line {inner.lineno} "
                    f"is inside a function. Hoist to module level to compile once."
                ),
                file=file_path,
                line=inner.lineno,
                severity="warning",
                confidence=0.95,
                evidence={"check": "PERF002"},
                suggestions=[
                    "Move the re.compile() call to module level.",
                    "Assign it to a module-level constant (e.g., _PATTERN = re.compile(...)).",
                ],
            )


def _is_re_compile_call(node: ast.Call) -> bool:
    """Check if a Call node is re.compile(...)."""
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "compile"
        and isinstance(func.value, ast.Name)
        and func.value.id == "re"
    )


def _has_cache_decorator(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Check if a function has @lru_cache, @cache, or @functools.lru_cache."""
    cache_names = {"lru_cache", "cache"}
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id in cache_names:
            return True
        if isinstance(decorator, ast.Attribute) and decorator.attr in cache_names:
            return True
        if isinstance(decorator, ast.Call):
            func = decorator.func
            if isinstance(func, ast.Name) and func.id in cache_names:
                return True
            if isinstance(func, ast.Attribute) and func.attr in cache_names:
                return True
    return False
