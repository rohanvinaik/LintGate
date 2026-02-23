"""Performance anti-pattern checker — AST-based detection of structurally wrong choices.

Tier 2 — catches performance anti-patterns that are mathematically wrong regardless
of context: O(n²) when O(n) is available, re.compile() inside functions,
sorted()[0] instead of min(), etc. These are not style preferences — they are
facts about complexity classes and execution models.

The 8 checks (PERF001–PERF008) are implemented as individual modules in
lintgate/linters/performance_checks/. This file is the thin orchestrator.

False-positive guardrails:
- PERF001: Only loop-invariant containers (skip if mutated in loop body)
- PERF001: Skip set/dict/frozenset targets and small constant lists
- PERF002: Skip @lru_cache/@cache decorated functions
- PERF004: Skip when loop body is 1-2 statements
- PERF005: Only when list() feeds directly into for-loop iteration
- PERF007: Skip small constant bounds, require arithmetic on loop vars
- PERF007: Skip when numpy/pandas/numba already imported
- PERF008: Only requests.* calls and variable-path open(), not constant paths
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from .base import BaseLinter
from .performance_checks._helpers import attach_parents
from .performance_checks.perf001_quadratic_membership import check_quadratic_membership
from .performance_checks.perf002_recompile import check_recompile_in_function
from .performance_checks.perf003_sorted_first_last import check_sorted_first_last
from .performance_checks.perf004_string_concat import check_string_concat_in_loop
from .performance_checks.perf005_unnecessary_list_wrap import check_unnecessary_list_wrap
from .performance_checks.perf006_dict_keys import check_dict_keys_iteration
from .performance_checks.perf007_numerical_loop import check_numerical_loop
from .performance_checks.perf008_sequential_io import check_sequential_io_in_loop
from .performance_checks.perf009_multi_pass import check_multi_pass
from .performance_checks.perf010_unnecessary_materialization import (
    check_unnecessary_materialization,
)
from .performance_checks.perf011_pure_uncached_in_loop import check_pure_uncached_in_loop

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..types import LinterContext, LintIssue


# All checks with their IDs, for disable-filtering
_CHECKS: list[tuple[str, object]] = [
    ("PERF001", check_quadratic_membership),
    ("PERF002", check_recompile_in_function),
    ("PERF003", check_sorted_first_last),
    ("PERF004", check_string_concat_in_loop),
    ("PERF005", check_unnecessary_list_wrap),
    ("PERF006", check_dict_keys_iteration),
    ("PERF007", check_numerical_loop),
    ("PERF008", check_sequential_io_in_loop),
    ("PERF009", check_multi_pass),
    ("PERF010", check_unnecessary_materialization),
    ("PERF011", check_pure_uncached_in_loop),
]


class PerformanceChecker(BaseLinter):
    """Detect performance anti-patterns via pure AST analysis."""

    name = "performance_checker"
    tier = 2
    timeout_ms = 3000
    required_tool = None  # Pure AST, always available

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        disabled = set(ctx.config.get("disabled_checks", []))
        for file_path in ctx.files:
            yield from _check_file(file_path, disabled)


def _check_file(file_path: str, disabled: set[str]) -> Iterable[LintIssue]:
    """Parse one file and run all performance checks."""
    try:
        with open(file_path) as f:
            source = f.read()
    except (OSError, UnicodeDecodeError):
        return

    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return

    # Attach parent references for ancestor lookups
    attach_parents(tree)

    # Dedupe guard: track (file, line, check_id) to avoid duplicates
    seen: set[tuple[str, int, str]] = set()

    for check_id, check_fn in _CHECKS:
        if check_id in disabled:
            continue
        for issue in check_fn(tree, file_path):
            key = (file_path, issue.line, issue.kind)
            if key not in seen:
                seen.add(key)
                yield issue
