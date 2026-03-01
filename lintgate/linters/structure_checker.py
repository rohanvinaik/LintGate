"""Structure and clean-code checker — AST-based architectural enforcement.

Tier 2 — runs on logic and structural changes. Pure Python stdlib,
no external dependencies. This is the "clean code conscience" of LintGate.

Checks are implemented in sub-modules under structure_checks/:
- file_checks: file size, class/function counts
- function_checks: args, locals, statements, returns, nesting, cognitive complexity
- class_checks: attributes, methods, parents

This file is the thin orchestrator. Thresholds are strictness-aware.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ..types import LintIssue
from .base import BaseLinter
from .structure_checks.ast_cache import (
    FunctionAnalysisCache,
    hash_file_imports,
    hash_function_source,
)
from .structure_checks.class_checks import check_class
from .structure_checks.file_checks import check_file_size, check_file_structure
from .structure_checks.function_checks import check_function

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..types import LinterContext

# ─── Default thresholds by strictness ────────────────────────────────────

_DEFAULTS: dict[str, dict[str, int]] = {
    "relaxed": {
        "max_function_args": 8,
        "max_function_locals": 20,
        "max_function_statements": 60,
        "max_function_returns": 8,
        "max_class_attributes": 15,
        "max_class_methods": 20,
        "max_class_parents": 4,
        "max_nesting_depth": 6,
        "max_file_lines": 600,
        "max_file_classes": 6,
        "max_file_functions": 20,
        "cognitive_complexity_threshold": 25,
    },
    "normal": {
        "max_function_args": 6,
        "max_function_locals": 15,
        "max_function_statements": 50,
        "max_function_returns": 6,
        "max_class_attributes": 10,
        "max_class_methods": 15,
        "max_class_parents": 3,
        "max_nesting_depth": 5,
        "max_file_lines": 400,
        "max_file_classes": 4,
        "max_file_functions": 15,
        "cognitive_complexity_threshold": 15,
    },
    "strict": {
        "max_function_args": 4,
        "max_function_locals": 10,
        "max_function_statements": 30,
        "max_function_returns": 4,
        "max_class_attributes": 7,
        "max_class_methods": 10,
        "max_class_parents": 2,
        "max_nesting_depth": 4,
        "max_file_lines": 300,
        "max_file_classes": 3,
        "max_file_functions": 10,
        "cognitive_complexity_threshold": 10,
    },
}


class StructureChecker(BaseLinter):
    """AST-based structure and clean-code checker."""

    name = "structure_checker"
    tier = 2
    timeout_ms = 3000
    required_tool = None

    # Session-scoped cache shared across all runs within the same process
    _cache = FunctionAnalysisCache()

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Run all structure checks on specified files."""
        thresholds = _get_thresholds(ctx)
        for filepath in ctx.files:
            yield from self._check_file(filepath, thresholds)

    def _check_file(
        self,
        filepath: str,
        thresholds: dict[str, int],
    ) -> Iterable[LintIssue]:
        """Run all checks on a single file."""
        try:
            with open(filepath) as f:
                source = f.read()
        except OSError:
            return

        try:
            tree = ast.parse(source, filename=filepath)
        except SyntaxError:
            return

        lines = source.splitlines()

        yield from check_file_size(filepath, lines, thresholds, tree=tree)
        yield from check_file_structure(filepath, tree, thresholds)

        # Compute import hash for cache invalidation
        import_hash = hash_file_imports(tree)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_hash = hash_function_source(node)
                cached = self._cache.get(filepath, node.name, func_hash)
                if cached:
                    # Replay cached issues
                    for issue_dict in cached.analysis.get("issues", []):
                        yield LintIssue(**issue_dict)
                else:
                    # Analyze and cache
                    issues = list(check_function(filepath, node, thresholds))
                    self._cache.set(
                        filepath,
                        node.name,
                        func_hash,
                        {"issues": [i.to_dict() for i in issues]},
                        import_hash=import_hash,
                    )
                    yield from issues
            elif isinstance(node, ast.ClassDef):
                yield from check_class(filepath, node, thresholds)


def _get_thresholds(ctx: LinterContext) -> dict[str, int]:
    """Get thresholds for the current strictness, with config overrides."""
    defaults = _DEFAULTS.get(ctx.strictness, _DEFAULTS["normal"])
    thresholds = dict(defaults)
    for key in thresholds:
        if key in ctx.config:
            thresholds[key] = ctx.config[key]
    return thresholds
