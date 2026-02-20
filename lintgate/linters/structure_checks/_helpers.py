"""Shared AST helpers for structure checks."""

from __future__ import annotations

import ast


def collect_class_attributes(node: ast.ClassDef) -> set[str]:
    """Collect all attribute names (instance + class-level) from a class."""
    attrs: set[str] = set()
    for child in ast.walk(node):
        self_attr = _get_self_attr_name(child)
        if self_attr:
            attrs.add(self_attr)
    for stmt in node.body:
        class_attr = _get_class_level_attr(stmt)
        if class_attr:
            attrs.add(class_attr)
    return attrs


def count_local_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Count local variable names in a function via AST walk."""
    local_names: set[str] = set()
    for child in ast.walk(node):
        _collect_locals_from_node(child, local_names)
    return local_names


def _get_self_attr_name(node: ast.AST) -> str | None:
    """Extract attribute name from self.x = ... or self.x: type = ..."""
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Attribute) and _is_self_attribute(target):
                return target.attr
    elif (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Attribute)
        and _is_self_attribute(node.target)
    ):
        return node.target.attr
    return None


def _is_self_attribute(node: ast.AST) -> bool:
    """Check if a node is self.something."""
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _get_class_level_attr(stmt: ast.stmt) -> str | None:
    """Extract attribute name from a class-level assignment."""
    if isinstance(stmt, ast.Assign):
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                return target.id
    elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        return stmt.target.id
    return None


def _collect_locals_from_node(child: ast.AST, local_names: set[str]) -> None:
    """Extract local variable names from a single AST node."""
    if isinstance(child, ast.Assign):
        for target in child.targets:
            _collect_names(target, local_names)
    elif (
        isinstance(child, ast.AnnAssign)
        and child.target
        or isinstance(child, (ast.AugAssign, ast.For, ast.AsyncFor))
    ):
        _collect_names(child.target, local_names)
    elif isinstance(child, ast.With):
        for item in child.items:
            if item.optional_vars:
                _collect_names(item.optional_vars, local_names)


def _collect_names(target: ast.AST, names: set[str]) -> None:
    """Collect variable names from an assignment target."""
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, ast.Tuple):
        for elt in target.elts:
            _collect_names(elt, names)
    elif isinstance(target, ast.Starred):
        _collect_names(target.value, names)
