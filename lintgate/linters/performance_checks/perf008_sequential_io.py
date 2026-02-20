"""PERF008: Sequential I/O inside loops.

Flags requests.* calls and repeated variable-path open() inside loops.

Narrow scope:
- Only requests.get/post/put/delete/head/patch
- open() only with variable paths (not constant paths)
- Only inside for/while body
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ...types import LintIssue
from ._helpers import find_loop_bodies

if TYPE_CHECKING:
    from collections.abc import Iterable

_REQUESTS_METHODS = {"get", "post", "put", "delete", "head", "patch", "request"}


def check_sequential_io_in_loop(tree: ast.AST, file_path: str) -> Iterable[LintIssue]:
    """Flag requests.* calls and repeated variable-path open() inside loops."""
    for loop_node, _body in find_loop_bodies(tree):
        for node in ast.walk(loop_node):
            if not isinstance(node, ast.Call):
                continue

            # requests.get(...), requests.post(...), etc.
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "requests"
                and node.func.attr in _REQUESTS_METHODS
            ):
                yield LintIssue(
                    linter="performance_checker",
                    kind="PERF008",
                    message=(
                        f"Sequential `requests.{node.func.attr}()` inside a loop. "
                        f"Consider batching requests, using async I/O (aiohttp), "
                        f"or concurrent.futures for parallel execution."
                    ),
                    file=file_path,
                    line=node.lineno,
                    severity="informational",
                    confidence=0.7,
                    evidence={"method": node.func.attr, "check": "PERF008"},
                    suggestions=[
                        "Use `concurrent.futures.ThreadPoolExecutor` for parallel I/O.",
                        "Consider `asyncio` + `aiohttp` for async HTTP requests.",
                        "If the API supports it, use batch endpoints.",
                    ],
                )

            # open() with variable path inside loop
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "open"
                and node.args
                and not isinstance(node.args[0], ast.Constant)
            ):
                yield LintIssue(
                    linter="performance_checker",
                    kind="PERF008",
                    message=(
                        "Repeated `open()` with variable path inside a loop. "
                        "Consider reading all paths at once or using batch I/O."
                    ),
                    file=file_path,
                    line=node.lineno,
                    severity="informational",
                    confidence=0.7,
                    evidence={"io_type": "open", "check": "PERF008"},
                    suggestions=[
                        "Collect paths and read in batch if possible.",
                        "Consider `concurrent.futures.ThreadPoolExecutor` for parallel reads.",
                    ],
                )
