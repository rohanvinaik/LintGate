"""Module responsibility diffusion check: too many unrelated public exports."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ...types import LintIssue

if TYPE_CHECKING:
    from collections.abc import Iterable


def check_module_exports(
    files: list[str],
    max_exports: int,
) -> Iterable[LintIssue]:
    """Check if modules export too many unrelated public names."""
    for filepath in files:
        exports = _count_public_exports(filepath)
        if exports > max_exports:
            yield LintIssue(
                linter="architecture",
                kind="too-many-exports",
                message=(
                    f"Module has {exports} public exports (limit: {max_exports}). "
                    f"This suggests mixed responsibilities."
                ),
                file=filepath,
                severity="informational",
                confidence=0.8,
                evidence={"count": exports, "threshold": max_exports},
                suggestions=[
                    "Split into focused sub-modules",
                    "Use __all__ to explicitly define the public API",
                    "Consider whether all exports serve the same purpose",
                ],
            )


def _count_public_exports(filepath: str) -> int:
    """Count public (non-underscore) top-level definitions in a module."""
    try:
        with open(filepath) as f:
            source = f.read()
    except OSError:
        return 0

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return 0

    all_count = _get_all_count(tree)
    if all_count is not None:
        return all_count

    return _count_public_names(tree)


def _get_all_count(tree: ast.Module) -> int | None:
    """Get count from __all__ if defined, or None."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Name)
                and target.id == "__all__"
                and isinstance(node.value, (ast.List, ast.Tuple))
            ):
                return len(node.value.elts)
    return None


def _count_public_names(tree: ast.Module) -> int:
    """Count public top-level names (functions, classes, variables)."""
    count = 0
    for node in tree.body:
        name = _get_public_name(node)
        if name and not name.startswith("_"):
            count += 1
    return count


def _get_public_name(node: ast.stmt) -> str | None:
    """Extract the public name from a top-level statement, if any."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name
    if isinstance(node, ast.ClassDef):
        return node.name
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                return target.id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return None
