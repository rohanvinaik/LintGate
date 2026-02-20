"""Function-level structure checks: args, locals, statements, returns, nesting, cognitive complexity."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ...types import LintIssue
from ..cognitive_complexity import (
    compute_cognitive_complexity,
    compute_max_nesting,
    count_statements,
)
from ._helpers import count_local_names

if TYPE_CHECKING:
    from collections.abc import Iterable


def check_function(
    filepath: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Run all function-level checks. Skips dunder methods."""
    name = node.name
    if name.startswith("__") and name.endswith("__"):
        return

    yield from check_function_args(filepath, node, name, thresholds)
    yield from check_function_locals(filepath, node, name, thresholds)
    yield from check_function_statements(filepath, node, name, thresholds)
    yield from check_function_returns(filepath, node, name, thresholds)
    yield from check_nesting_depth(filepath, node, name, thresholds)
    yield from check_cognitive_complexity(filepath, node, name, thresholds)


def check_function_args(
    filepath: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check function argument count (pylint R0913)."""
    args = node.args
    all_args = args.posonlyargs + args.args + args.kwonlyargs
    arg_names = [a.arg for a in all_args if a.arg not in ("self", "cls")]
    count = len(arg_names)

    max_args = thresholds["max_function_args"]
    if count > max_args:
        severity = "blocking" if count > max_args + 4 else "warning"
        yield LintIssue(
            linter="structure",
            kind="too-many-args",
            message=(
                f"Function '{name}' has {count} arguments (limit: {max_args}). "
                f"Consider using a config dataclass or **kwargs."
            ),
            file=filepath,
            line=node.lineno,
            severity=severity,
            confidence=1.0,
            evidence={"count": count, "threshold": max_args, "args": arg_names},
            suggestions=[
                "Group related args into a dataclass or TypedDict",
                "Use builder pattern for complex construction",
            ],
        )


def check_function_locals(
    filepath: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check local variable count in function (pylint R0914)."""
    local_names = count_local_names(node)
    count = len(local_names)
    max_locals = thresholds["max_function_locals"]

    if count > max_locals:
        yield LintIssue(
            linter="structure",
            kind="too-many-locals",
            message=(
                f"Function '{name}' has {count} local variables (limit: {max_locals}). "
                f"This suggests the function is doing too many things."
            ),
            file=filepath,
            line=node.lineno,
            severity="warning",
            confidence=0.9,
            evidence={"count": count, "threshold": max_locals},
            suggestions=[
                "Extract helper functions for distinct logical steps",
                "Consider whether some variables track the same concept",
            ],
        )


def check_function_statements(
    filepath: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check statement count in function (pylint R0915)."""
    count = count_statements(node)
    max_stmts = thresholds["max_function_statements"]

    if count > max_stmts:
        severity = "blocking" if count > max_stmts * 2 else "warning"
        yield LintIssue(
            linter="structure",
            kind="too-many-statements",
            message=(
                f"Function '{name}' has {count} statements (limit: {max_stmts}). "
                f"Functions should do one thing well."
            ),
            file=filepath,
            line=node.lineno,
            severity=severity,
            confidence=1.0,
            evidence={"count": count, "threshold": max_stmts},
            suggestions=[
                "Extract logical blocks into named helper functions",
                "Each function should have one clear purpose",
            ],
        )


def check_function_returns(
    filepath: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check return statement count (too many exit points)."""
    returns = sum(1 for n in ast.walk(node) if isinstance(n, ast.Return))
    max_returns = thresholds["max_function_returns"]

    if returns > max_returns:
        yield LintIssue(
            linter="structure",
            kind="too-many-returns",
            message=(
                f"Function '{name}' has {returns} return statements (limit: {max_returns}). "
                f"Multiple exit points make control flow harder to follow."
            ),
            file=filepath,
            line=node.lineno,
            severity="informational",
            confidence=0.85,
            evidence={"count": returns, "threshold": max_returns},
            suggestions=[
                "Consider restructuring to reduce exit points",
                "Guard clauses at the top are fine — mid-function returns are the issue",
            ],
        )


def check_nesting_depth(
    filepath: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check maximum nesting depth (the 'arrowhead anti-pattern')."""
    max_depth = compute_max_nesting(node)
    threshold = thresholds["max_nesting_depth"]

    if max_depth > threshold:
        severity = "blocking" if max_depth > threshold + 2 else "warning"
        yield LintIssue(
            linter="structure",
            kind="deep-nesting",
            message=(
                f"Function '{name}' has nesting depth {max_depth} (limit: {threshold}). "
                f"Deeply nested code is hard to understand and maintain."
            ),
            file=filepath,
            line=node.lineno,
            severity=severity,
            confidence=1.0,
            evidence={"depth": max_depth, "threshold": threshold},
            suggestions=[
                "Use early returns (guard clauses) to flatten nesting",
                "Extract nested blocks into named helper functions",
                "Consider whether the logic can be simplified",
            ],
        )


def check_cognitive_complexity(
    filepath: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check cognitive complexity — measures understanding difficulty."""
    cogc = compute_cognitive_complexity(node)
    threshold = thresholds["cognitive_complexity_threshold"]

    if cogc > threshold:
        severity = "blocking" if cogc > threshold * 2 else "warning"
        yield LintIssue(
            linter="structure",
            kind="cognitive-complexity",
            message=(
                f"Function '{name}' has cognitive complexity {cogc} "
                f"(limit: {threshold}). This function is too hard to understand."
            ),
            file=filepath,
            line=node.lineno,
            severity=severity,
            confidence=1.0,
            evidence={"cognitive_complexity": cogc, "threshold": threshold},
            suggestions=[
                "Cognitive complexity measures understanding difficulty, not path count",
                "Reduce nesting, simplify boolean expressions, extract helper functions",
                "Each function should be understandable in one mental pass",
            ],
        )
