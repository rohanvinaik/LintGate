"""PERF004: String concatenation with += inside loops.

Creates a new string object each iteration. Collect parts in a list and
use ''.join(parts) after the loop instead.

Skips loops with 1-2 statements (small string building is fine).
Reduces confidence when the accumulator is consumed/reset within the
same iteration (per-iteration building pattern).
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
    for _loop_node, body in find_loop_bodies(tree):
        if len(body) <= 2:
            continue

        for stmt_idx, stmt in enumerate(body):
            for node in ast.walk(stmt):
                if not isinstance(node, ast.AugAssign):
                    continue
                if not isinstance(node.op, ast.Add):
                    continue

                target_name = node.target.id if isinstance(node.target, ast.Name) else None
                is_str_const = isinstance(node.value, ast.Constant) and isinstance(
                    node.value.value, str
                )
                is_fstring = isinstance(node.value, ast.JoinedStr)

                if not (is_str_const or is_fstring):
                    continue

                confidence = 0.9
                if target_name and _is_per_iteration_pattern(target_name, body, stmt_idx):
                    confidence = 0.40

                kind_detail = " (f-string)" if is_fstring else ""
                suggestion_append = (
                    "Collect string parts in a list: `parts.append(f'...')`."
                    if is_fstring
                    else "Collect string parts in a list: `parts.append(piece)`."
                )

                yield LintIssue(
                    linter="performance_checker",
                    kind="PERF004",
                    message=(
                        f"String concatenation with `+=`{kind_detail} inside a loop"
                        " creates a new string object each iteration. Collect parts"
                        " in a list and use `''.join(parts)` after the loop."
                    ),
                    file=file_path,
                    line=node.lineno,
                    severity="warning",
                    confidence=confidence,
                    evidence={"check": "PERF004", "target": target_name},
                    suggestions=[
                        suggestion_append,
                        "After the loop: `result = ''.join(parts)`.",
                    ],
                )


def _is_per_iteration_pattern(
    target_name: str,
    body: list[ast.stmt],
    aug_index: int,
) -> bool:
    """Heuristic: accumulator consumed/reset after += in same iteration.

    Returns True if the pattern looks like per-iteration building
    (e.g., ``msg += ...; log(msg); msg = ""``).  This is a confidence
    downgrade signal, not a suppression — ``some_func(msg)`` does not
    always mean "consumed".

    Also checks for reset *before* the += (per-iteration building pattern):
        for item in items:
            msg = ""           # reset (before +=)
            msg += f"{item}"   # not cross-iteration accumulation
    """
    # Check for reset/consumption AFTER the += in same iteration
    for stmt in body[aug_index + 1 :]:
        for node in ast.walk(stmt):
            # Reset: target = ... (any reassignment)
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == target_name for t in node.targets
            ):
                return True
            # Consumption heuristic: target appears as positional arg
            if isinstance(node, ast.Call) and any(
                isinstance(a, ast.Name) and a.id == target_name for a in node.args
            ):
                return True

    # Check for reset BEFORE the += in same iteration
    for stmt in body[:aug_index]:
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if isinstance(t, ast.Name) and t.id == target_name:
                    return True
        if (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.target.id == target_name
        ):
            return True
    return False
