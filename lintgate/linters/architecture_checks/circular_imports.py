"""Circular import detection using DFS cycle detection."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from ...types import LintIssue
from ._helpers import extract_imports, filepath_to_module, is_project_local

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ...types import LinterContext


def check_circular_imports(
    files: list[str],
    ctx: LinterContext,
) -> Iterable[LintIssue]:
    """Detect circular import chains among the linted files."""
    graph: dict[str, set[str]] = defaultdict(set)
    file_map: dict[str, str] = {}

    for filepath in files:
        module = filepath_to_module(filepath, ctx.project_root)
        if module:
            file_map[module] = filepath
            imports = extract_imports(filepath)
            for imp_module, _ in imports:
                if is_project_local(imp_module, ctx.project_root):
                    graph[module].add(imp_module)

    cycles = _find_cycles(graph)

    seen_cycles: set[frozenset[str]] = set()
    for cycle in cycles:
        cycle_key = frozenset(cycle)
        if cycle_key in seen_cycles:
            continue
        seen_cycles.add(cycle_key)

        relevant_files = [m for m in cycle if m in file_map]
        if not relevant_files:
            continue

        cycle_str = " → ".join(cycle + [cycle[0]])
        filepath = file_map.get(relevant_files[0], "")

        yield LintIssue(
            linter="architecture",
            kind="circular-import",
            message=f"Circular import detected: {cycle_str}",
            file=filepath,
            severity="warning",
            confidence=0.9,
            evidence={"cycle": cycle, "length": len(cycle)},
            suggestions=[
                "Break the cycle by moving shared types to a common module",
                "Use lazy imports (import inside function) if the cycle is unavoidable",
                "Consider dependency injection to decouple the modules",
            ],
        )


def _find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Find all cycles in a directed graph using DFS.

    Only finds cycles up to length 5 (deeper cycles are unusual).
    """
    cycles: list[list[str]] = []
    visited: set[str] = set()
    path: list[str] = []
    path_set: set[str] = set()

    def dfs(node: str) -> None:
        if len(path) > 5:
            return
        if node in path_set:
            cycle_start = path.index(node)
            cycles.append(path[cycle_start:])
            return
        if node in visited:
            return

        path.append(node)
        path_set.add(node)

        for neighbor in graph.get(node, set()):
            dfs(neighbor)

        path.pop()
        path_set.discard(node)
        visited.add(node)

    for node in graph:
        if node not in visited:
            dfs(node)

    return cycles
