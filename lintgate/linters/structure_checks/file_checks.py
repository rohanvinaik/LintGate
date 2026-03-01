"""File-level structure checks: size, structural limits, and cohesion."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ...types import LintIssue
from .cohesion_analysis import analyze_file_cohesion

if TYPE_CHECKING:
    from collections.abc import Iterable


def check_file_size(
    filepath: str,
    lines: list[str],
    thresholds: dict[str, int],
    tree: ast.Module | None = None,
    *,
    module_fan_in: dict[str, int] | None = None,
    module_name: str | None = None,
) -> Iterable[LintIssue]:
    """Check file-level size limits, with cohesion analysis for large files."""
    max_lines = thresholds["max_file_lines"]
    line_count = len(lines)

    if line_count > max_lines:
        severity = "blocking" if line_count > max_lines * 2 else "warning"
        evidence: dict = {"lines": line_count, "threshold": max_lines}
        suggestions = [
            "Extract related functions into a separate module",
            "Group by responsibility: data, logic, I/O",
        ]

        # Attach cohesion analysis for file-too-long findings
        cohesion = None
        if tree is not None:
            cohesion_threshold = thresholds.get("cohesion_threshold", 50) / 100.0
            cohesion = analyze_file_cohesion(tree, filepath, cohesion_threshold)
            evidence["cohesion"] = {
                "score": cohesion.score,
                "components": cohesion.components,
                "component_count": cohesion.component_count,
                "is_cli_mixed": cohesion.is_cli_mixed,
            }
            if cohesion.split_proposals:
                # Annotate split proposals with fan-in impact (Gap 1)
                if module_fan_in is not None and module_name:
                    from lintgate.channels.structure_graph import (
                        annotate_proposals_with_fan_in,
                    )

                    annotate_proposals_with_fan_in(
                        cohesion.split_proposals, module_fan_in, module_name
                    )

                split_dicts = [p.to_dict() for p in cohesion.split_proposals]
                # Annotate split proposals with co-change coupling data
                split_dicts = _annotate_with_cochange(split_dicts, filepath)
                evidence["split_proposals"] = split_dicts
                suggestions = [p.action for p in cohesion.split_proposals] + suggestions

        # Registry file detection: pure-dispatch files with many independent
        # items get downgraded — their length is proportional to registry size
        registry = _detect_registry_file(tree, cohesion) if tree is not None else None
        if registry:
            severity = "informational"
            evidence["registry"] = registry
            suggestions = [
                "Registry file — length is proportional to item count, not complexity.",
                "Consider splitting only if a logical cluster of items emerges.",
            ]

        yield LintIssue(
            linter="structure",
            kind="file-too-long",
            message=(
                f"File has {line_count} lines (limit: {max_lines}). "
                f"Consider splitting into focused modules."
            )
            if not registry
            else (
                f"Registry file ({registry['item_count']} independent items, "
                f"avg {registry['avg_item_lines']:.0f} lines each). "
                f"File has {line_count} lines (limit: {max_lines})."
            ),
            file=filepath,
            severity=severity,
            confidence=1.0 if not registry else 0.6,
            evidence=evidence,
            suggestions=suggestions,
        )


def check_file_structure(
    filepath: str,
    tree: ast.Module,
    thresholds: dict[str, int],
) -> Iterable[LintIssue]:
    """Check file-level structural limits (classes, functions count)."""
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    functions = [
        n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

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


def _detect_registry_file(
    tree: ast.Module | None,
    cohesion: object | None,
) -> dict | None:
    """Detect pure-dispatch registry files that should not be penalised for length.

    A file qualifies as a registry when ALL of these hold:
    1. Cohesion score < 0.1 — functions share no state
    2. No individual function exceeds CC 15 — each item is simple
    3. A dispatch dict/table exists at module level mapping strings to callables
    4. At least 5 independent items (otherwise it's just a small file)

    Returns evidence dict if registry detected, else None.
    """
    if tree is None or cohesion is None:
        return None
    score = getattr(cohesion, "score", 1.0)
    if score >= 0.1:
        return None

    # Collect top-level function names and line spans
    func_names: set[str] = set()
    func_lines: list[int] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_names.add(node.name)
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            func_lines.append(end - node.lineno + 1)

    if len(func_names) < 5:
        return None

    # Check for dispatch table: module-level dict with values referencing functions
    dispatch_table_name = _find_dispatch_table(tree, func_names)
    if not dispatch_table_name:
        return None

    avg_lines = sum(func_lines) / len(func_lines) if func_lines else 0

    return {
        "item_count": len(func_names),
        "dispatch_table": dispatch_table_name,
        "avg_item_lines": round(avg_lines, 1),
    }


def _find_dispatch_table(tree: ast.Module, func_names: set[str]) -> str | None:
    """Find a module-level dict whose values reference defined functions."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        # Check if dict values reference known function names
        dict_value_names: set[str] = set()
        for v in node.value.values:
            if isinstance(v, ast.Name):
                dict_value_names.add(v.id)
        overlap = dict_value_names & func_names
        # Require at least 3 function references to qualify as a dispatch table
        if len(overlap) >= 3:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    return target.id
    return None


def _annotate_with_cochange(
    split_dicts: list[dict],
    filepath: str,
) -> list[dict]:
    """Annotate split proposals with git co-change coupling data.

    Gracefully degrades if git is not available or the project is not
    a git repository — returns the proposals unchanged.
    """
    try:
        import os

        from .cochange_analysis import (
            annotate_split_proposals,
            compute_cochange_coupling,
        )

        # Walk up from the file to find the git root
        project_root = os.path.dirname(os.path.abspath(filepath))
        for _ in range(10):
            if os.path.isdir(os.path.join(project_root, ".git")):
                break
            parent = os.path.dirname(project_root)
            if parent == project_root:
                return split_dicts  # Not a git repo
            project_root = parent
        else:
            return split_dicts  # No .git found

        relpath = os.path.relpath(filepath, project_root)
        cochange = compute_cochange_coupling(project_root, days=30)
        return annotate_split_proposals(split_dicts, cochange, relpath)
    except Exception:
        return split_dicts  # Graceful degradation
