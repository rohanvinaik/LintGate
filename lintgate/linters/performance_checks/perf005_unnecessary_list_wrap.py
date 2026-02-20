"""PERF005: Unnecessary list() wrapping in for-loop iteration.

Flags list(range(...)) or list(genexpr) when used directly as a for-loop iterable.
The list materialization is wasted since for-loop consumes lazily.

Only flags when the list() result is the iterable of a for-loop.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ...types import LintIssue

if TYPE_CHECKING:
    from collections.abc import Iterable


def check_unnecessary_list_wrap(tree: ast.AST, file_path: str) -> Iterable[LintIssue]:
    """Flag list(range(...)) or list(genexpr) when used directly in for-loop iteration."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue

        iter_node = node.iter
        if not isinstance(iter_node, ast.Call):
            continue
        if not (isinstance(iter_node.func, ast.Name) and iter_node.func.id == "list"):
            continue

        if not iter_node.args:
            continue

        inner = iter_node.args[0]

        # list(range(...))
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
            if inner.func.id == "range":
                yield LintIssue(
                    linter="performance_checker",
                    kind="PERF005",
                    message=(
                        "Unnecessary `list(range(...))` in for-loop. "
                        "`range()` is already iterable — use `for x in range(...)` directly."
                    ),
                    file=file_path,
                    line=node.lineno,
                    severity="informational",
                    confidence=0.8,
                    evidence={"check": "PERF005", "inner": "range"},
                    suggestions=[
                        "Remove the `list()` wrapper: `for x in range(...)` instead.",
                    ],
                )
        # list(generator expression)
        elif isinstance(inner, ast.GeneratorExp):
            yield LintIssue(
                linter="performance_checker",
                kind="PERF005",
                message=(
                    "Unnecessary `list(genexpr)` in for-loop. "
                    "Iterate the generator directly to avoid materializing the list."
                ),
                file=file_path,
                line=node.lineno,
                severity="informational",
                confidence=0.8,
                evidence={"check": "PERF005", "inner": "genexpr"},
                suggestions=[
                    "Replace `for x in list(gen)` with `for x in gen`.",
                ],
            )
