"""File-level structure checks: size and structural limits."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ...types import LintIssue

if TYPE_CHECKING:
    from collections.abc import Iterable


def check_file_size(
    filepath: str,
    lines: list[str],
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check file-level size limits."""
    max_lines = thresholds["max_file_lines"]
    line_count = len(lines)

    if line_count > max_lines:
        severity = "blocking" if line_count > max_lines * 2 else "warning"
        yield LintIssue(
            linter="structure",
            kind="file-too-long",
            message=(
                f"File has {line_count} lines (limit: {max_lines}). "
                f"Consider splitting into focused modules."
            ),
            file=filepath,
            severity=severity,
            confidence=1.0,
            evidence={"lines": line_count, "threshold": max_lines},
            suggestions=[
                "Extract related functions into a separate module",
                "Group by responsibility: data, logic, I/O",
            ],
        )


def check_file_structure(
    filepath: str,
    tree: ast.Module,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check file-level structural limits (classes, functions count)."""
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    functions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    max_classes = thresholds["max_file_classes"]
    if len(classes) > max_classes:
        yield LintIssue(
            linter="structure",
            kind="too-many-classes",
            message=(
                f"File has {len(classes)} classes (limit: {max_classes}). "
                f"This suggests mixed responsibilities."
            ),
            file=filepath,
            severity="warning",
            confidence=0.9,
            evidence={"count": len(classes), "threshold": max_classes},
            suggestions=[
                "Each class should represent one clear concept",
                "Split into one module per class (or per related group)",
            ],
        )

    max_functions = thresholds["max_file_functions"]
    if len(functions) > max_functions:
        yield LintIssue(
            linter="structure",
            kind="too-many-functions",
            message=(
                f"File has {len(functions)} top-level functions (limit: {max_functions}). "
                f"Consider grouping into classes or modules."
            ),
            file=filepath,
            severity="informational",
            confidence=0.85,
            evidence={"count": len(functions), "threshold": max_functions},
            suggestions=[
                "Group related functions into a class or separate module",
            ],
        )
