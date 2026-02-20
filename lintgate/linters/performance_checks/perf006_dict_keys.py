"""PERF006: Redundant .keys() in dict iteration.

`for k in d.keys()` is equivalent to `for k in d` — the .keys() call is unnecessary.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ...types import LintIssue
from ._helpers import get_name

if TYPE_CHECKING:
    from collections.abc import Iterable


def check_dict_keys_iteration(tree: ast.AST, file_path: str) -> Iterable[LintIssue]:
    """Flag `for k in d.keys()` → `for k in d`."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue

        iter_node = node.iter
        if (
            isinstance(iter_node, ast.Call)
            and isinstance(iter_node.func, ast.Attribute)
            and iter_node.func.attr == "keys"
            and not iter_node.args
            and not iter_node.keywords
        ):
            dict_name = get_name(iter_node.func.value)
            if dict_name:
                yield LintIssue(
                    linter="performance_checker",
                    kind="PERF006",
                    message=(
                        f"Redundant `.keys()` in `for k in {dict_name}.keys()`. "
                        f"Use `for k in {dict_name}` — iterating a dict yields keys by default."
                    ),
                    file=file_path,
                    line=node.lineno,
                    severity="informational",
                    confidence=1.0,
                    evidence={"check": "PERF006", "dict_name": dict_name},
                    suggestions=[
                        f"Remove `.keys()`: `for k in {dict_name}`.",
                    ],
                )
