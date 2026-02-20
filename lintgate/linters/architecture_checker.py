"""Architecture and boundary enforcement checker.

Tier 3 — runs on architectural changes. Pure Python stdlib (AST-based),
no external dependencies.

Checks are implemented in sub-modules under architecture_checks/:
- layer_contracts: Declarative layer boundary enforcement
- circular_imports: DFS-based import cycle detection
- module_exports: Responsibility diffusion check

This file is the thin orchestrator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .architecture_checks.circular_imports import check_circular_imports
from .architecture_checks.layer_contracts import check_layer_contracts
from .architecture_checks.module_exports import check_module_exports
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..types import LinterContext, LintIssue


class ArchitectureChecker(BaseLinter):
    """Architecture boundary enforcer — catches layer violations."""

    name = "architecture_checker"
    tier = 3
    timeout_ms = 5000
    required_tool = None

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Run architecture checks on specified files."""
        layers = ctx.config.get("layers", [])
        max_exports = ctx.config.get("max_module_exports", 15)

        if layers:
            yield from check_layer_contracts(ctx.files, layers, ctx)

        yield from check_circular_imports(ctx.files, ctx)
        yield from check_module_exports(ctx.files, max_exports)
