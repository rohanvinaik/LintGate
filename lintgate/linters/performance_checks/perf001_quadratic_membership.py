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


def _should_skip_container(container_name: str, tree: ast.AST, body: list[ast.stmt]) -> bool:
    """Check if a container name should be skipped for PERF001."""
    return (
        _is_set_or_dict_name(container_name, tree)
        or _is_string_variable(container_name, tree)
        or _is_small_constant_list(container_name, tree)
        or _is_mutated_in_body(container_name, body)
    )


def _build_perf001_issue(
    container_name: str, file_path: str, lineno: int, param_type: str | None
) -> LintIssue:
    """Build a PERF001 lint issue for a quadratic membership test."""
    confidence_map = {"untyped": 0.25, "typed_slow": 0.60}
    confidence = confidence_map.get(param_type or "", 0.50)

    uncertainty_note = (
        f" (container `{container_name}` is an untyped parameter"
        " — add a type annotation to suppress or confirm)"
        if param_type == "untyped"
        else ""
    )

    return LintIssue(
        linter="performance_checker",
        kind="PERF001",
        message=(
            f"Membership test `in {container_name}` inside loop is "
            f"O(n²) for lists. Convert `{container_name}` to a set "
            f"before the loop for O(n) lookup.{uncertainty_note}"
        ),
        file=file_path,
        line=lineno,
        severity="warning",
        confidence=confidence,
        evidence={"container": container_name, "check": "PERF001", "param_type": param_type},
        suggestions=[
            f"Add `{container_name}_set = set({container_name})` before the loop.",
            f"Then use `x in {container_name}_set` instead.",
        ],
    )


def check_quadratic_membership(tree: ast.AST, file_path: str) -> Iterable[LintIssue]:
    """Flag `x in some_list` inside for-loops when the list is loop-invariant."""
    for loop_node, body in find_loop_bodies(tree):
        for node in ast.walk(loop_node):
            if not isinstance(node, ast.Compare):
                continue
            for op, comparator in zip(node.ops, node.comparators, strict=False):
                if not isinstance(op, ast.In) or not isinstance(comparator, ast.Name):
                    continue

                container_name = comparator.id
                if _should_skip_container(container_name, tree, body):
                    continue

                param_type = _classify_function_parameter(container_name, tree)
                if param_type == "typed_fast":
                    continue

                yield _build_perf001_issue(container_name, file_path, node.lineno, param_type)


_FAST_CALL_NAMES = frozenset({"set", "frozenset", "dict", "range"})
_FAST_LITERAL_TYPES = (ast.Set, ast.Dict, ast.DictComp, ast.SetComp)


def _value_is_fast_container(value: ast.expr) -> bool:
    """Check if an expression produces a container with O(1) membership."""
    if isinstance(value, _FAST_LITERAL_TYPES):
        return True
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in _FAST_CALL_NAMES
    )


def _is_set_or_dict_name(name: str, tree: ast.AST) -> bool:
    """Check if a name uses fast membership semantics (O(1)/optimized).

    Includes set()/frozenset()/dict() and range(), plus set/dict literals,
    and type-annotated dicts/sets.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == name
                    and _value_is_fast_container(node.value)
                ):
                    return True
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
    """Check if name is assigned a string (substring search, not membership test).

    Detects:
    - String literal assignment: ``name = "hello"``
    - f-string assignment: ``name = f"hello {x}"``
    - Type annotation: ``name: str = ...``
    - String method calls: ``name = text.strip()`` / ``text.lower()`` etc.
    - Subscript of splitlines/split result: ``name = lines[i]`` when lines
      comes from ``stdout.splitlines()`` or ``text.split(...)``
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == name
                    and _value_is_string(node.value)
                ):
                    return True
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and _annotation_is_str(node.annotation)
        ):
            return True
    return False


# Methods that always return str when called on a str
_STR_RETURNING_METHODS = frozenset(
    {
        "strip",
        "lstrip",
        "rstrip",
        "lower",
        "upper",
        "title",
        "capitalize",
        "casefold",
        "replace",
        "encode",
        "decode",
        "join",
        "format",
        "center",
        "ljust",
        "rjust",
        "zfill",
        "expandtabs",
        "removeprefix",
        "removesuffix",
        "translate",
        "swapcase",
    }
)


def _value_is_string(value: ast.expr) -> bool:
    """Infer whether an expression evaluates to a string."""
    # Literal string
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return True
    # f-string
    if isinstance(value, ast.JoinedStr):
        return True
    # str method call: text.strip(), text.lower(), etc.
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr in _STR_RETURNING_METHODS
    ):
        return True
    # str() constructor
    return bool(
        isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "str"
    )


def _annotation_is_str(ann: ast.expr) -> bool:
    """Check if a type annotation is str."""
    if isinstance(ann, ast.Name) and ann.id == "str":
        return True
    return bool(isinstance(ann, ast.Constant) and ann.value == "str")


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


# Fast-membership type names (O(1) lookup — skip PERF001 entirely)
_FAST_TYPE_NAMES = {"dict", "set", "frozenset", "str", "Dict", "Set", "FrozenSet"}
# Slow-membership type names (O(n) lookup — flag at full confidence)
_SLOW_TYPE_NAMES = {"list", "tuple", "List", "Tuple"}


def _classify_function_parameter(name: str, tree: ast.AST) -> str | None:
    """Classify a name as a function parameter and check its type annotation.

    Returns:
        ``"typed_fast"``  — annotated as dict/set/frozenset/str (skip PERF001)
        ``"typed_slow"``  — annotated as list/tuple (flag at full confidence)
        ``"untyped"``     — parameter with no annotation (reduce confidence)
        ``None``          — not a function parameter (no effect)
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
            if arg.arg != name:
                continue
            # Found the parameter — check annotation
            if arg.annotation is None:
                return "untyped"
            return _classify_annotation(arg.annotation)
    return None


def _classify_annotation(ann: ast.expr) -> str:
    """Classify a type annotation as fast or slow for membership tests."""
    # Simple name: `items: set`
    if isinstance(ann, ast.Name):
        if ann.id in _FAST_TYPE_NAMES:
            return "typed_fast"
        if ann.id in _SLOW_TYPE_NAMES:
            return "typed_slow"
    # Subscript: `items: dict[str, int]` or `items: list[str]`
    if isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name):
        if ann.value.id in _FAST_TYPE_NAMES:
            return "typed_fast"
        if ann.value.id in _SLOW_TYPE_NAMES:
            return "typed_slow"
    # Union or other complex annotation — can't determine, treat as untyped
    return "untyped"
