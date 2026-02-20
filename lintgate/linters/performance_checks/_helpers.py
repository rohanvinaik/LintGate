"""Shared AST helpers for performance checks."""

from __future__ import annotations

import ast


def get_constant_index(node: ast.AST) -> int | None:
    """Extract a constant integer index from an AST slice.

    Handles both `ast.Constant(value=0)` and `ast.UnaryOp(op=USub(), operand=Constant(value=1))`
    (the latter is how Python represents negative indices like [-1]).
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, int)
    ):
        return -node.operand.value
    return None


def attach_parents(tree: ast.AST) -> None:
    """Attach .parent references to all nodes in the AST."""
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node  # type: ignore[attr-defined]


def find_loop_bodies(tree: ast.AST) -> list[tuple[ast.AST, list[ast.stmt]]]:
    """Find all for/while loops and return (loop_node, body) pairs."""
    result: list[tuple[ast.AST, list[ast.stmt]]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.While)):
            result.append((node, node.body))
    return result


def has_import(tree: ast.AST, module_name: str) -> bool:
    """Check if a module is imported anywhere in the file."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module_name or alias.name.startswith(module_name + "."):
                    return True
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == module_name or node.module.startswith(module_name + "."))
        ):
            return True
    return False


def get_name(node: ast.AST) -> str | None:
    """Extract a simple name from an AST node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = get_name(node.value)
        if parent:
            return f"{parent}.{node.attr}"
    return None
