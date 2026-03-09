"""AST-based symbol span extraction for the symbol coverage gate.

Extracts function/method spans from Python source files, with decorator-aware
start lines and nested-function skipping (v1 — nested lines subsume under outer).
"""

from __future__ import annotations

import ast
import os
from pathlib import PurePosixPath

from lintgate.channels._symbol_types import SymbolSpan


def _canonicalize_symbol_key(filepath: str, symbol_name: str, project_root: str) -> str:
    """Normalize to POSIX relative path + :: + symbol name.

    Handles Windows paths, trailing slashes, absolute vs relative.
    """
    # Normalize project root (strip trailing separators)
    root = os.path.normpath(project_root)
    fpath = os.path.normpath(filepath)

    # Make relative to project root
    try:
        rel = os.path.relpath(fpath, root)
    except ValueError:
        # Different drives on Windows
        rel = fpath

    # Convert to POSIX
    posix_rel = str(PurePosixPath(*rel.split(os.sep))) if os.sep != "/" else rel

    return f"{posix_rel}::{symbol_name}"


def extract_symbol_spans(filepath: str, project_root: str) -> list[SymbolSpan]:
    """Extract function/method spans from a Python file via AST.

    Skips nested functions (their lines subsume under the outer function).
    Decorator-aware: start_line includes first decorator if present.
    """
    try:
        with open(filepath, encoding="utf-8", errors="replace") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=filepath)
    except (OSError, SyntaxError):
        return []

    spans: list[SymbolSpan] = []
    _visit_node(tree, filepath, project_root, spans, current_class=None, depth=0)
    return spans


def _visit_node(
    node: ast.AST,
    filepath: str,
    project_root: str,
    spans: list[SymbolSpan],
    current_class: str | None,
    depth: int,
) -> None:
    """Recursively visit AST nodes to extract function/method spans."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            # Visit class body with class context
            _visit_node(child, filepath, project_root, spans, child.name, depth)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Skip nested functions (depth > 0 within a function)
            if depth > 0:
                continue

            end_line = getattr(child, "end_lineno", None)
            if end_line is None:
                continue

            # Decorator-aware start line
            if child.decorator_list:
                start_line = min(child.decorator_list[0].lineno, child.lineno)
            else:
                start_line = child.lineno

            is_method = current_class is not None
            name = f"{current_class}.{child.name}" if is_method else child.name

            symbol_key = _canonicalize_symbol_key(filepath, name, project_root)

            spans.append(
                SymbolSpan(
                    file=filepath,
                    symbol_key=symbol_key,
                    name=name,
                    start_line=start_line,
                    end_line=end_line,
                    is_method=is_method,
                    class_name=current_class,
                )
            )

            # Visit nested functions at depth+1 (they will be skipped)
            _visit_node(child, filepath, project_root, spans, current_class, depth + 1)
