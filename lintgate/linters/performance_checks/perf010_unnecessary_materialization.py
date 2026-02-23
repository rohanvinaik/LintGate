"""PERF010: Unnecessary intermediate materialization."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable


def check_unnecessary_materialization(tree: ast.AST, file_path: str) -> Iterable[dict[str, Any]]:
    """Detect unnecessary list materialization (e.g. `sum([x for x in y])` -> `sum(x for x in y)`)."""

    aggregators = {"sum", "any", "all", "max", "min"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name in aggregators and node.args:
                first_arg = node.args[0]

                # Check 1: sum([x for x in iterable])
                if isinstance(first_arg, ast.ListComp):
                    yield {
                        "file": file_path,
                        "line": node.lineno,
                        "col": node.col_offset,
                        "message": (
                            f"PERF010: Unnecessary materialization. "
                            f"Passing a list comprehension to {func_name}() forces memory allocation. "
                            f"Use a generator expression instead: {func_name}(x for x in y)."
                        ),
                        "code": "PERF010",
                    }

                # Check 2: len(list(generator)) -> the list materialization isn't needed if we just want count
                # Note: len() consumes iterators differently, so you can't just `len(gen)`.
                # But `sum(1 for _ in gen)` is the zero-allocation equivalent.
            elif func_name == "len" and node.args:
                first_arg = node.args[0]
                if (
                    isinstance(first_arg, ast.Call)
                    and isinstance(first_arg.func, ast.Name)
                    and first_arg.func.id == "list"
                ):
                    # len(list(x))
                    yield {
                        "file": file_path,
                        "line": node.lineno,
                        "col": node.col_offset,
                        "message": (
                            "PERF010: Unnecessary materialization. "
                            "len(list(x)) materializes the entire list in memory just to count it. "
                            "Use sum(1 for _ in x) for generators."
                        ),
                        "code": "PERF010",
                    }
