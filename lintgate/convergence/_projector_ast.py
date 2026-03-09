"""AST construction helpers for the post-extraction projector.

Builds synthetic ast.FunctionDef nodes from ExtractionStep metadata,
optionally extracting real bodies from a source AST by line range.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .extraction_plan import ExtractionStep


def build_function_projected_ast(
    step: ExtractionStep,
    source_ast: ast.Module | None,
) -> ast.FunctionDef | None:
    """Build projected AST for a create_function step."""
    detail = step.detail
    params = detail.get("parameters", [])
    proposed_name = detail.get("proposed_name", step.target)
    source_lines = detail.get("source_lines", [])

    args = ast.arguments(
        posonlyargs=[],
        args=[ast.arg(arg=p, annotation=None) for p in params],
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=[],
    )

    body: list[ast.stmt] = []
    if source_ast and source_lines and len(source_lines) == 2:
        body = extract_body_from_ast(source_ast, source_lines[0], source_lines[1])

    if not body:
        body = [ast.Pass()]

    func = ast.FunctionDef(
        name=proposed_name,
        args=args,
        body=body,
        decorator_list=[],
        returns=None,
        lineno=1,
        col_offset=0,
    )
    ast.fix_missing_locations(func)
    return func


def build_handler_projected_ast(
    step: ExtractionStep,
    source_ast: ast.Module | None,
) -> ast.FunctionDef | None:
    """Build projected AST for an extract_handler step."""
    detail = step.detail
    captured = detail.get("captured_variables", [])
    proposed_name = detail.get("proposed_name", step.target)
    source_lines = detail.get("source_lines", [])

    all_params = list(captured)

    args = ast.arguments(
        posonlyargs=[],
        args=[ast.arg(arg=p, annotation=None) for p in all_params],
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=[],
    )

    body: list[ast.stmt] = []
    if source_ast and source_lines and len(source_lines) == 2:
        body = extract_body_from_ast(source_ast, source_lines[0], source_lines[1])

    if not body:
        body = [ast.Pass()]

    func = ast.FunctionDef(
        name=proposed_name,
        args=args,
        body=body,
        decorator_list=[],
        returns=None,
        lineno=1,
        col_offset=0,
    )
    ast.fix_missing_locations(func)
    return func


def extract_body_from_ast(
    source_ast: ast.Module,
    start_line: int,
    end_line: int,
) -> list[ast.stmt]:
    """Extract statements from source AST by line range."""
    body: list[ast.stmt] = []
    for node in ast.walk(source_ast):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for stmt in node.body:
                if hasattr(stmt, "lineno") and start_line <= stmt.lineno <= end_line:
                    body.append(stmt)
    return body
