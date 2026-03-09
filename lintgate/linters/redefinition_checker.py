"""Redefinition checker — AST-based duplicate definition detection.

Tier 1 — catches a common LLM coding anti-pattern where the agent loses
track of what it already defined and silently redefines a function or class
in the same scope. The first definition is overridden and its logic is lost.

This is fundamentally a context-window artifact: as files grow long, the
agent forgets earlier definitions and creates new ones with the same name.

False-positive guardrails:
- @overload decorators (typing overloads are intentional redefinitions)
- if TYPE_CHECKING blocks (type stubs, not runtime code)
- try/except fallback patterns (intentional conditional definitions)
- Conditional branches (if/elif/else define same name = intentional)
- async def and def tracked uniformly
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ..types import LinterContext, LintIssue
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable


class RedefinitionChecker(BaseLinter):
    """Detect duplicate function/class definitions at the same scope level."""

    name = "redefinition_checker"
    tier = 1
    timeout_ms = 2000
    required_tool = None  # Pure AST, no external deps

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        for file_path in ctx.files:
            yield from _check_file(file_path)


def _check_file(file_path: str) -> Iterable[LintIssue]:
    """Parse one file and check for redefinitions at each scope."""
    try:
        with open(file_path) as f:
            source = f.read()
    except OSError:
        return

    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return

    # Check module-level definitions
    yield from _check_scope(tree.body, file_path, scope_name="<module>")

    # Check class-level definitions (methods)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            yield from _check_scope(node.body, file_path, scope_name=f"class {node.name}")


def _check_scope(
    body: list[ast.stmt],
    file_path: str,
    scope_name: str,
) -> Iterable[LintIssue]:
    """Check a single scope (module body or class body) for redefinitions."""
    # name -> (line_number, node_type) for first definition seen
    seen: dict[str, tuple[int, str]] = {}

    for node in body:
        # Skip nodes inside TYPE_CHECKING blocks
        if _is_type_checking_block(node):
            continue

        # Skip nodes inside try/except blocks (fallback patterns)
        if isinstance(node, ast.Try):
            continue

        # Skip conditional blocks (if/elif/else — intentional branching)
        if isinstance(node, ast.If):
            continue

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Skip @overload decorated functions
            if _has_overload_decorator(node):
                continue
            # Skip @property, @x.setter, @x.deleter (valid same-name methods)
            if _has_property_decorator(node):
                continue

            name = node.name
            node_type = "function"
        elif isinstance(node, ast.ClassDef):
            name = node.name
            node_type = "class"
        else:
            continue

        if name in seen:
            first_line, first_type = seen[name]
            yield LintIssue(
                linter="redefinition_checker",
                kind="redefinition",
                message=(
                    f"{node_type.capitalize()} '{name}' redefined at line {node.lineno} "
                    f"(first defined at line {first_line} as {first_type}). "
                    f"The first definition is silently overridden."
                ),
                file=file_path,
                line=node.lineno,
                severity="blocking",
                confidence=1.0,
                evidence={
                    "scope": scope_name,
                    "name": name,
                    "first_line": first_line,
                    "first_type": first_type,
                    "second_line": node.lineno,
                    "second_type": node_type,
                },
                suggestions=[
                    f"Remove or rename one of the two '{name}' definitions.",
                    f"First definition is at line {first_line}, second at line {node.lineno}.",
                ],
            )
        else:
            seen[name] = (node.lineno, node_type)


def _has_overload_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function has @overload or @typing.overload decorator."""
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id == "overload":
            return True
        if isinstance(decorator, ast.Attribute) and decorator.attr == "overload":
            return True
    return False


def _has_property_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function has @property, @x.setter, or @x.deleter decorator.

    These create same-name method definitions that are valid Python idiom:
        @property
        def name(self): ...
        @name.setter
        def name(self, value): ...
    """
    for decorator in node.decorator_list:
        # @property
        if isinstance(decorator, ast.Name) and decorator.id == "property":
            return True
        # @name.setter or @name.deleter
        if isinstance(decorator, ast.Attribute) and decorator.attr in (
            "setter",
            "deleter",
        ):
            return True
        # @functools.cached_property or similar
        if isinstance(decorator, ast.Attribute) and decorator.attr in ("cached_property",):
            return True
        if isinstance(decorator, ast.Name) and decorator.id == "cached_property":
            return True
    return False


def _is_type_checking_block(node: ast.stmt) -> bool:
    """Check if a node is an `if TYPE_CHECKING:` block."""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    # if TYPE_CHECKING: or if typing.TYPE_CHECKING:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )
