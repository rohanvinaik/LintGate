"""PERF003: sorted()[0] or sorted()[-1] — use min()/max() instead.

O(n) min/max vs O(n log n) sorted() when only the first or last element is needed.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ...types import LintIssue
from ._helpers import get_constant_index

if TYPE_CHECKING:
    from collections.abc import Iterable


def check_sorted_first_last(tree: ast.AST, file_path: str) -> Iterable[LintIssue]:
    """Flag sorted(x)[0] → min(x) and sorted(x)[-1] → max(x)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue

        if not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not (isinstance(call.func, ast.Name) and call.func.id == "sorted"):
            continue

        index_value = get_constant_index(node.slice)
        if index_value is None:
            continue

        if index_value == 0:
            replacement = "min"
        elif index_value == -1:
            replacement = "max"
        else:
            continue

        yield LintIssue(
            linter="performance_checker",
            kind="PERF003",
            message=(
                f"Use `{replacement}(...)` instead of `sorted(...)[{index_value}]`. "
                f"`{replacement}()` is O(n) vs O(n log n) for sorted()."
            ),
            file=file_path,
            line=node.lineno,
            severity="warning",
            confidence=1.0,
            evidence={"replacement": replacement, "check": "PERF003"},
            suggestions=[
                f"Replace `sorted(...)[{index_value}]` with `{replacement}(...)`.",
            ],
        )
