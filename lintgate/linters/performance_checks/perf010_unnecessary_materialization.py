"""PERF010: Unnecessary intermediate materialization."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ...types import LintIssue

if TYPE_CHECKING:
    from collections.abc import Iterable

_AGGREGATORS = frozenset({"sum", "any", "all", "max", "min"})


def _check_aggregator_listcomp(node: ast.Call, func_name: str, file_path: str) -> LintIssue | None:
    """Check: sum([x for x in iterable]) → sum(x for x in iterable)."""
    if not node.args or not isinstance(node.args[0], ast.ListComp):
        return None
    return LintIssue(
        linter="performance_checker",
        kind="PERF010",
        message=(
            f"Unnecessary materialization. "
            f"Passing a list comprehension to {func_name}() forces memory allocation. "
            f"Use a generator expression instead: {func_name}(x for x in y)."
        ),
        file=file_path,
        line=node.lineno,
        severity="informational",
        confidence=0.9,
        evidence={"func": func_name, "check": "PERF010"},
    )


def _check_len_list(node: ast.Call, file_path: str) -> LintIssue | None:
    """Check: len(list(x)) → sum(1 for _ in x)."""
    if not node.args:
        return None
    first_arg = node.args[0]
    if not (
        isinstance(first_arg, ast.Call)
        and isinstance(first_arg.func, ast.Name)
        and first_arg.func.id == "list"
    ):
        return None
    return LintIssue(
        linter="performance_checker",
        kind="PERF010",
        message=(
            "Unnecessary materialization. "
            "len(list(x)) materializes the entire list in memory just to count it. "
            "Use sum(1 for _ in x) for generators."
        ),
        file=file_path,
        line=node.lineno,
        severity="informational",
        confidence=0.8,
        evidence={"func": "len", "check": "PERF010"},
    )


def _is_list_genexp_assign(node: ast.Assign) -> tuple[str, int] | None:
    """Check if assignment is `name = list(genexp/listcomp)`. Returns (name, line) or None."""
    if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
        return None
    val = node.value
    if not (
        isinstance(val, ast.Call)
        and isinstance(val.func, ast.Name)
        and val.func.id == "list"
        and val.args
        and isinstance(val.args[0], (ast.GeneratorExp, ast.ListComp))
    ):
        return None
    return node.targets[0].id, node.lineno


def check_unnecessary_materialization(tree: ast.AST, file_path: str) -> Iterable[LintIssue]:
    """Detect unnecessary list materialization (e.g. `sum([x for x in y])` -> `sum(x for x in y)`)."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        func_name = node.func.id
        if func_name in _AGGREGATORS:
            issue = _check_aggregator_listcomp(node, func_name, file_path)
            if issue:
                yield issue
        elif func_name == "len":
            issue = _check_len_list(node, file_path)
            if issue:
                yield issue

    # Check 3: result = list(genexp); for x in result: ... (iterate-only)
    assigned_lists: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            pair = _is_list_genexp_assign(node)
            if pair:
                assigned_lists[pair[0]] = pair[1]
        elif (
            isinstance(node, ast.For)
            and isinstance(node.iter, ast.Name)
            and node.iter.id in assigned_lists
        ):
            yield LintIssue(
                linter="performance_checker",
                kind="PERF010",
                message=(
                    f"Unnecessary materialization. "
                    f"Variable '{node.iter.id}' is materialized as a list starting at line {assigned_lists[node.iter.id]} "
                    "but is only iterated. Use the generator directly."
                ),
                file=file_path,
                line=node.lineno,
                severity="informational",
                confidence=0.7,
                evidence={"var": node.iter.id, "check": "PERF010"},
            )
