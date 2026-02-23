"""PERF011: Pure function uncached in loop."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

from lintgate.linters.performance_checks._helpers import find_loop_bodies, get_name
from lintgate.linters.performance_checks.purity import _KNOWN_PURE_BUILTINS

if TYPE_CHECKING:
    from collections.abc import Iterable


def _is_loop_invariant(arg: ast.expr, loop_targets: set[str]) -> bool:
    """Check if an argument relies entirely on variables outside the loop."""
    if isinstance(arg, ast.Constant):
        return True

    # Simple check: no name matches any target assigned by the loop
    for node in ast.walk(arg):
        if isinstance(node, ast.Name) and node.id in loop_targets:
            return False

    return True


def _get_loop_targets(loop_node: ast.AST) -> set[str]:
    """Extract names of variables assigned in the loop declaration."""
    targets = set()
    if isinstance(loop_node, ast.For):
        target_nodes = [loop_node.target]
    elif isinstance(loop_node, ast.While):
        target_nodes = []  # While doesn't have iteration targets, body mutations matter
    else:
        return targets

    for target in target_nodes:
        if isinstance(target, ast.Name):
            targets.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                if isinstance(elt, ast.Name):
                    targets.add(elt.id)

    return targets


def check_pure_uncached_in_loop(tree: ast.AST, file_path: str) -> Iterable[dict[str, Any]]:
    """Detect pure functions called inside a loop with loop-invariant arguments."""

    # Ideally, we would load the property manifest here to know exactly
    # which project-local functions are pure. For this node-level check,
    # we'll rely on our standard builtin pure list plus a heuristic
    # (e.g. if the user gives us a manifest via context).
    # Since signature is (tree, file_path), we'll do builtins for now.

    loops = find_loop_bodies(tree)

    for loop_node, body in loops:
        loop_targets = _get_loop_targets(loop_node)

        # We also need to consider variables assigned INSIDE the loop as variant
        for stmt in body:
            for child in ast.walk(stmt):
                if isinstance(child, ast.Assign):
                    for t in child.targets:
                        if isinstance(t, ast.Name):
                            loop_targets.add(t.id)
                elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                    loop_targets.add(child.target.id)

        # Now find all calls in the loop
        for stmt in body:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Call):
                    func_name = get_name(node.func)
                    if not func_name:
                        continue

                    # 1. Is it a known pure function?
                    # Note: in a full implementation, we'd check Manifest, not just builtins
                    if func_name not in _KNOWN_PURE_BUILTINS:
                        continue

                    # 2. Are all arguments loop-invariant?
                    all_invariant = True
                    # Check positional args
                    for arg in node.args:
                        if not _is_loop_invariant(arg, loop_targets):
                            all_invariant = False
                            break
                    # Check kw args
                    if all_invariant:
                        for kwarg in node.keywords:
                            if not _is_loop_invariant(kwarg.value, loop_targets):
                                all_invariant = False
                                break

                    if all_invariant and (node.args or node.keywords):
                        yield {
                            "file": file_path,
                            "line": node.lineno,
                            "col": node.col_offset,
                            "message": (
                                f"PERF011: Uncached pure call in loop. "
                                f"'{func_name}' is called with loop-invariant arguments. "
                                f"Hoist this call before the loop or use @lru_cache to prevent redundant computation."
                            ),
                            "code": "PERF011",
                        }
