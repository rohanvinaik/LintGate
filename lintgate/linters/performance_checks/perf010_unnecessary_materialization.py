"""PERF010: Unnecessary intermediate materialization."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ...types import LintIssue

if TYPE_CHECKING:
    from collections.abc import Iterable


def check_unnecessary_materialization(tree: ast.AST, file_path: str) -> Iterable[LintIssue]:
    """Detect unnecessary list materialization (e.g. `sum([x for x in y])` -> `sum(x for x in y)`)."""

    aggregators = {"sum", "any", "all", "max", "min"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name in aggregators and node.args:
                first_arg = node.args[0]

                # Check 1: sum([x for x in iterable])
                if isinstance(first_arg, ast.ListComp):
                    yield LintIssue(
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

            elif func_name == "len" and node.args:
                first_arg = node.args[0]
                if (
                    isinstance(first_arg, ast.Call)
                    and isinstance(first_arg.func, ast.Name)
                    and first_arg.func.id == "list"
                ):
                    # len(list(x))
                    yield LintIssue(
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

    # Check 3: result = list(genexp); for x in result: ...
    # Detect variable assigned a list(genexp) then used in a loop
    assigned_lists: dict[str, int] = {}  # name -> line
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if (
                isinstance(target, ast.Name)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "list"
                and node.value.args
                and isinstance(node.value.args[0], (ast.GeneratorExp, ast.ListComp))
            ):
                assigned_lists[target.id] = node.lineno

        if (
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
