"""PERF011: Pure function uncached in loop."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lintgate.linters.performance_checks._helpers import find_loop_bodies, get_name
from lintgate.linters.performance_checks.purity import (
    _KNOWN_PURE_BUILTINS,
    analyze_purity,
)

from ...types import LintIssue

if TYPE_CHECKING:
    from collections.abc import Iterable

# Module-level manifest pure-names cache. Populated by the performance channel
# or by lint_files/lint_project when running at tier 2+. When set, PERF011
# checks project-local pure functions in addition to builtins.
_manifest_pure_names: set[str] | None = None


def set_manifest_pure_names(names: set[str] | None) -> None:
    """Inject manifest-derived pure function names for this lint run.

    Call with ``None`` to clear after the run completes.
    """
    global _manifest_pure_names  # noqa: PLW0603
    _manifest_pure_names = names


def _analyze_file_purity(tree: ast.AST) -> set[str]:
    """Lightweight same-file purity scan for the lint-path fallback.

    When running via ``lint_files``/``lint_project`` (not ControlPlane),
    the manifest hasn't been injected.  This runs the purity detector on
    the current file so PERF011 can still detect project-local pure
    functions called inside loops.
    """
    # analyze_purity is already imported at the module level.

    results = analyze_purity(tree)
    return {name for name, result in results.items() if result.is_pure}


def _is_known_pure(
    func_name: str, local_pure_names: set[str] | None = None
) -> tuple[bool, str]:
    """Check if a function is known-pure. Returns (is_pure, source).

    Source is one of ``"builtin"``, ``"manifest"``, or ``"local_purity"``.
    """
    if func_name in _KNOWN_PURE_BUILTINS:
        return True, "builtin"
    if _manifest_pure_names is not None and func_name in _manifest_pure_names:
        return True, "manifest"
    if local_pure_names is not None and func_name in local_pure_names:
        return True, "local_purity"
    return False, ""


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
    targets: set[str] = set()
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


def _get_assignments_in_statement(stmt: ast.stmt) -> set[str]:
    """Extract variable names assigned within a single statement."""
    statement_assignments: set[str] = set()
    for child in ast.walk(stmt):
        if isinstance(child, ast.Assign):
            for t in child.targets:
                if isinstance(t, ast.Name):
                    statement_assignments.add(t.id)
        elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
            statement_assignments.add(child.target.id)
    return statement_assignments


def _collect_loop_assignments(body: list[ast.stmt]) -> set[str]:
    """Collect variable names assigned inside a loop body."""
    assigned: set[str] = set()
    for stmt in body:
        assigned.update(_get_assignments_in_statement(stmt))
    return assigned


def _check_positional_args_invariant(
    args: list[ast.expr], loop_targets: set[str]
) -> bool:
    """Return True if all positional args are loop-invariant."""
    return all(_is_loop_invariant(arg, loop_targets) for arg in args)


def _check_keyword_args_invariant(
    keywords: list[ast.keyword], loop_targets: set[str]
) -> bool:
    """Return True if all keyword args are loop-invariant."""
    return all(_is_loop_invariant(kwarg.value, loop_targets) for kwarg in keywords)


def _check_all_args_invariant(node: ast.Call, loop_targets: set[str]) -> bool:
    """Return True if all positional and keyword args are loop-invariant."""
    return _check_positional_args_invariant(
        node.args, loop_targets
    ) and _check_keyword_args_invariant(node.keywords, loop_targets)


def _check_call_in_loop(
    node: ast.Call,
    loop_targets: set[str],
    file_path: str,
    local_pure_names: set[str] | None = None,
) -> LintIssue | None:
    """Check a single call node for uncached pure function with invariant args."""
    func_name = get_name(node.func)
    if not func_name:
        return None

    is_pure, source = _is_known_pure(func_name, local_pure_names)
    if not is_pure:
        return None

    if not (node.args or node.keywords):
        return None

    if not _check_all_args_invariant(node, loop_targets):
        return None

    # Builtins get higher confidence since the list is curated;
    # manifest/local purity analysis is heuristic-based.
    confidence = 0.8 if source == "builtin" else 0.7
    return LintIssue(
        linter="performance_checker",
        kind="PERF011",
        message=(
            f"Uncached pure call in loop. "
            f"'{func_name}' is called with loop-invariant arguments. "
            f"Hoist this call before the loop or use @lru_cache "
            f"to prevent redundant computation."
        ),
        file=file_path,
        line=node.lineno,
        severity="informational",
        confidence=confidence,
        evidence={
            "func": func_name,
            "check": "PERF011",
            "source": source,
        },
    )


def _analyze_loop_body_for_uncached_calls(
    body: list[ast.stmt],
    loop_targets: set[str],
    file_path: str,
    local_pure_names: set[str] | None = None,
) -> Iterable[LintIssue]:
    """Walk the loop body and check each call for purity and invariance."""
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                issue = _check_call_in_loop(
                    node, loop_targets, file_path, local_pure_names
                )
                if issue is not None:
                    yield issue


def check_pure_uncached_in_loop(tree: ast.AST, file_path: str) -> Iterable[LintIssue]:
    """Detect pure functions called inside a loop with loop-invariant arguments.

    Three purity sources, checked in order:

    1. **Builtins** — curated list (``abs``, ``len``, ``sorted``, etc.)
    2. **Manifest** — project-wide pure names injected by the performance
       channel via ``set_manifest_pure_names()`` (ControlPlane path)
    3. **Per-file fallback** — when the manifest is *not* available (normal
       ``lint_files``/``lint_project`` path), run a lightweight same-file
       purity scan so project-local pure functions are still detected.
    """
    # Per-file purity fallback: when the manifest hasn't been injected
    # (i.e., running via lint_files/lint_project, not ControlPlane),
    # analyse this file's functions for purity on-the-fly.
    local_pure_names: set[str] | None = None
    if _manifest_pure_names is None:
        local_pure_names = _analyze_file_purity(tree)

    for loop_node, body in find_loop_bodies(tree):
        loop_targets_list = _get_loop_targets(loop_node) | _collect_loop_assignments(
            body
        )
        loop_targets = set(loop_targets_list)  # Convert to set for O(1) average lookup
        yield from _analyze_loop_body_for_uncached_calls(
            body, loop_targets, file_path, local_pure_names
        )
