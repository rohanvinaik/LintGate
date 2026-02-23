"""PERF009: Multi-pass over same collection."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

from lintgate.linters.performance_checks._helpers import get_name

if TYPE_CHECKING:
    from collections.abc import Iterable


def check_multi_pass(tree: ast.AST, file_path: str) -> Iterable[dict[str, Any]]:
    """Detect multiple `for` loops iterating over the same collection in the same scope."""
    # Group loops by their parent node (scope)
    scope_loops: dict[ast.AST, list[ast.For]] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.For):
            parent = getattr(node, "_parent", None)
            if parent:
                scope_loops.setdefault(parent, []).append(node)

    for _parent, loops in scope_loops.items():
        if len(loops) < 2:
            continue

        # Group by the collection being iterated
        collection_iters: dict[str, list[ast.For]] = {}
        for loop in loops:
            # We only track simple named collections
            coll_name = get_name(loop.iter)
            if coll_name:
                collection_iters.setdefault(coll_name, []).append(loop)

        for coll_name, iter_loops in collection_iters.items():
            if len(iter_loops) >= 2:
                # We should theoretically check if the collection is mutated between loops.
                # For this heuristic, we assume multiple simple for-loops on the same name
                # in the same scope constitutes a multi-pass optimization opportunity.
                [str(loop.lineno) for loop in iter_loops]

                # We yield one issue for the *second* loop, pointing to the first.
                first_loop = iter_loops[0]
                for subsequent_loop in iter_loops[1:]:
                    yield {
                        "file": file_path,
                        "line": subsequent_loop.lineno,
                        "col": subsequent_loop.col_offset,
                        "message": (
                            f"PERF009: Multi-pass over '{coll_name}'. "
                            f"This loops over the same collection as line {first_loop.lineno}. "
                            "Consider combining into a single pass using accumulator variables."
                        ),
                        "code": "PERF009",
                    }
