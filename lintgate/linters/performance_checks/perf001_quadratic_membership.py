"""PERF001: Quadratic membership — `x in some_list` inside for-loops.

Flags O(n²) membership tests when the container is loop-invariant.

Skips:
- set/dict/frozenset targets (already O(1))
- Small constant lists (e.g. [1, 2, 3])
- Containers mutated inside the loop body
- Non-Name targets (inline expressions)
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ...types import LintIssue
from ._helpers import find_loop_bodies

if TYPE_CHECKING:
    from collections.abc import Iterable


def check_quadratic_membership(tree: ast.AST, file_path: str) -> Iterable[LintIssue]:
    """Flag `x in some_list` inside for-loops when the list is loop-invariant."""
    for loop_node, body in find_loop_bodies(tree):
        for node in ast.walk(loop_node):
            if not isinstance(node, ast.Compare):
                continue
            for op, comparator in zip(node.ops, node.comparators, strict=False):
                if not isinstance(op, ast.In):
                    continue
                if not isinstance(comparator, ast.Name):
                    continue

                container_name = comparator.id

                if _is_set_or_dict_name(container_name, tree):
                    continue

                if _is_string_variable(container_name, tree):
                    continue  # Substring search, not membership test

                if _is_small_constant_list(container_name, tree):
                    continue

                if _is_mutated_in_body(container_name, body):
                    continue

                yield LintIssue(
                    linter="performance_checker",
                    kind="PERF001",
                    message=(
                        f"Membership test `in {container_name}` inside loop is "
                        f"O(n²) for lists. Convert `{container_name}` to a set "
                        f"before the loop for O(n) lookup."
                    ),
                    file=file_path,
                    line=node.lineno,
                    severity="warning",
                    confidence=0.60,
                    evidence={
                        "container": container_name,
                        "check": "PERF001",
                    },
                    suggestions=[
                        f"Add `{container_name}_set = set({container_name})` before the loop.",
                        f"Then use `x in {container_name}_set` instead.",
                    ],
                )


def _is_set_or_dict_name(name: str, tree: ast.AST) -> bool:
    """Check if a name uses fast membership semantics (O(1)/optimized).

    Includes set()/frozenset()/dict() and range(), plus set/dict literals,
    and type-annotated dicts/sets.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    value = node.value
                    if (
                        isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Name)
                        and value.func.id in ("set", "frozenset", "dict", "range")
                    ):
                        return True
                    if isinstance(value, ast.Set):
                        return True
                    if isinstance(value, ast.Dict):
                        return True
                    if isinstance(value, ast.DictComp):
                        return True
                    if isinstance(value, ast.SetComp):
                        return True
        # Type-annotated dicts/sets: x: dict[str, int] = ...
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and _annotation_is_dict_or_set(node.annotation)
        ):
            return True
    return False


def _annotation_is_dict_or_set(ann: ast.expr) -> bool:
    """Check if a type annotation refers to dict/set/frozenset."""
    fast_names = {"dict", "set", "frozenset", "Dict", "Set", "FrozenSet"}
    if isinstance(ann, ast.Name) and ann.id in fast_names:
        return True
    if isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name):
        return ann.value.id in fast_names
    return False


def _is_string_variable(name: str, tree: ast.AST) -> bool:
    """Check if name is assigned a string (substring search, not membership test)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        return True
                    if isinstance(node.value, ast.JoinedStr):  # f-string
                        return True
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and isinstance(node.annotation, ast.Name)
            and node.annotation.id == "str"
        ):
            return True
    return False


def _is_small_constant_list(name: str, tree: ast.AST) -> bool:
    """Check if name is assigned a small constant list (≤ 5 elements)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == name
                    and isinstance(node.value, ast.List)
                    and len(node.value.elts) <= 5
                    and all(isinstance(e, ast.Constant) for e in node.value.elts)
                ):
                    return True
    return False


def _is_mutated_in_body(name: str, body: list[ast.stmt]) -> bool:
    """Check if a name is mutated inside a loop body.

    Detects: .append(), .extend(), .remove(), .insert(), .pop(), .clear(),
             .add(), .discard(), .update(), del name[...], name[...] = ...,
             name = ... (reassignment), name += ...
    """
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if _is_mutation_call(node, name):
            return True
        if _is_delete_mutation(node, name):
            return True
        if _is_assign_mutation(node, name):
            return True
        if _is_augassign_mutation(node, name):
            return True
    return False


_MUTATION_METHODS = frozenset(
    {
        "append",
        "extend",
        "remove",
        "insert",
        "pop",
        "clear",
        "add",
        "discard",
        "update",
        "sort",
        "reverse",
    }
)


def _is_mutation_call(node: ast.AST, name: str) -> bool:
    """Check for name.method() mutation calls."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == name
        and node.func.attr in _MUTATION_METHODS
    )


def _targets_name_or_subscript(target: ast.AST, name: str) -> bool:
    """Check if an assignment target is `name` or `name[...]`."""
    if isinstance(target, ast.Name) and target.id == name:
        return True
    return (
        isinstance(target, ast.Subscript)
        and isinstance(target.value, ast.Name)
        and target.value.id == name
    )


def _is_delete_mutation(node: ast.AST, name: str) -> bool:
    """Check for `del name` or `del name[...]`."""
    if not isinstance(node, ast.Delete):
        return False
    return any(_targets_name_or_subscript(t, name) for t in node.targets)


def _is_assign_mutation(node: ast.AST, name: str) -> bool:
    """Check for `name = ...` or `name[...] = ...`."""
    if not isinstance(node, ast.Assign):
        return False
    return any(_targets_name_or_subscript(t, name) for t in node.targets)


def _is_augassign_mutation(node: ast.AST, name: str) -> bool:
    """Check for `name += ...`."""
    return (
        isinstance(node, ast.AugAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == name
    )
