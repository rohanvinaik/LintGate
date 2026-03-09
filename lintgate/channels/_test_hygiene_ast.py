"""AST helpers for test hygiene analysis.

Extracted from test_hygiene_channel.py to keep the main module under 400 lines.
"""

from __future__ import annotations

import ast
import hashlib


def _parse_file(filepath: str) -> ast.Module | None:
    """Parse a Python file, returning None on failure."""
    try:
        with open(filepath, encoding="utf-8") as f:
            return ast.parse(f.read(), filename=filepath)
    except (OSError, SyntaxError):
        return None


def _read_source(filepath: str) -> str | None:
    """Read file source, returning None on failure."""
    try:
        with open(filepath, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _extract_class_test_methods(
    class_node: ast.ClassDef,
) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, str]]:
    """Extract test methods from a test class."""
    return [
        (item.name, item, class_node.name)
        for item in class_node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name.startswith("test_")
    ]


def _extract_test_functions(
    tree: ast.Module,
) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, str | None]]:
    """Extract (name, node, class_name) for all test functions/methods."""
    results: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, str | None]] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            results.append((node.name, node, None))
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            results.extend(_extract_class_test_methods(node))
    return results


def _function_body_source(source: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Extract the body source of a function (excluding the def line and docstring)."""
    body = node.body
    if not body:
        return ""
    # Skip docstring if present
    start_idx = 0
    if (
        isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        start_idx = 1
    if start_idx >= len(body):
        return ""
    first = body[start_idx]
    last = body[-1]
    lines = source.splitlines()
    start_line = first.lineno - 1
    end_line = getattr(last, "end_lineno", last.lineno)
    return "\n".join(lines[start_line:end_line])


def _function_context_hash(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Hash decorators + parameter names to distinguish context-different tests.

    Two tests with the same body but different decorators (e.g. parametrize)
    or different fixture parameters are semantically different.
    """
    parts: list[str] = []
    # Decorators
    for dec in node.decorator_list:
        parts.append(ast.dump(dec, annotate_fields=False))
    # Parameter names (fixture injection)
    for arg in node.args.args:
        if arg.arg != "self":
            parts.append(arg.arg)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _function_body_ast_hash(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Hash the AST-normalized body (strip docstrings, comments, whitespace)."""
    body = list(node.body)
    # Strip leading docstring
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if not body:
        return hashlib.sha256(b"empty").hexdigest()[:16]
    # Create a minimal module with just the body statements
    wrapper = ast.Module(body=body, type_ignores=[])
    dumped = ast.dump(wrapper, annotate_fields=False)
    return hashlib.sha256(dumped.encode()).hexdigest()[:16]
