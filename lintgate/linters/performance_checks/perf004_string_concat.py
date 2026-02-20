"""PERF004: String concatenation with += inside loops.

Creates a new string object each iteration. Collect parts in a list and
use ''.join(parts) after the loop instead.

Skips loops with 1-2 statements (small string building is fine).
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ...types import LintIssue
from ._helpers import find_loop_bodies

if TYPE_CHECKING:
    from collections.abc import Iterable


def check_string_concat_in_loop(tree: ast.AST, file_path: str) -> Iterable[LintIssue]:
    """Flag string += inside for/while loops."""
    for loop_node, body in find_loop_bodies(tree):
        if len(body) <= 2:
            continue

        for node in ast.walk(loop_node):
            if not isinstance(node, ast.AugAssign):
                continue
            if not isinstance(node.op, ast.Add):
                continue

            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                yield LintIssue(
                    linter="performance_checker",
                    kind="PERF004",
                    message=(
                        "String concatenation with `+=` inside a loop creates a new "
                        "string object each iteration. Collect parts in a list and "
                        "use `''.join(parts)` after the loop."
                    ),
                    file=file_path,
                    line=node.lineno,
                    severity="warning",
                    confidence=0.9,
                    evidence={"check": "PERF004"},
                    suggestions=[
                        "Collect string parts in a list: `parts.append(piece)`.",
                        "After the loop: `result = ''.join(parts)`.",
                    ],
                )
            elif isinstance(node.value, ast.JoinedStr):
                yield LintIssue(
                    linter="performance_checker",
                    kind="PERF004",
                    message=(
                        "String concatenation with `+=` (f-string) inside a loop. "
                        "Collect parts in a list and use `''.join(parts)` after the loop."
                    ),
                    file=file_path,
                    line=node.lineno,
                    severity="warning",
                    confidence=0.9,
                    evidence={"check": "PERF004"},
                    suggestions=[
                        "Collect string parts in a list: `parts.append(f'...')`.",
                        "After the loop: `result = ''.join(parts)`.",
                    ],
                )
