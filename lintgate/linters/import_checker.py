"""Import verification checker.

Tier 1 — runs on import and config changes. Verifies that all
modules in changed files can actually be imported without error.

This catches the common LLM anti-pattern of importing modules that
don't exist (hallucinated imports) or have been renamed/moved.

Two modes:
1. AST-based (fast, default): Parse import statements from changed files
   and verify the target modules exist, without executing anything.
2. Command-based (optional): Run a user-defined import verification
   command from lintgate.yaml config.
"""

from __future__ import annotations

import ast
import importlib.util
import os
from typing import TYPE_CHECKING

from ..types import LinterContext, LintIssue
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable


class ImportChecker(BaseLinter):
    """Import checker — catches hallucinated and broken imports.

    Default mode: AST-parse changed files, extract import statements,
    verify each imported module can be found by importlib.

    No external tool required — pure Python stdlib.
    """

    name = "import_checker"
    tier = 1
    timeout_ms = 5000
    required_tool = None  # Built-in

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Check imports in specified files."""

        # If a custom command is configured, run that instead
        custom_cmd = ctx.config.get("command")
        if custom_cmd:
            yield from self._run_custom(custom_cmd, ctx)
            return

        # Default: AST-based import verification
        for filepath in ctx.files:
            yield from self._check_file_imports(filepath, ctx)

    def _check_file_imports(
        self, filepath: str, ctx: LinterContext
    ) -> Iterable[LintIssue]:
        """Parse a file's imports and verify they resolve."""
        try:
            with open(filepath) as f:
                source = f.read()
        except OSError:
            return

        try:
            tree = ast.parse(source, filename=filepath)
        except SyntaxError:
            # Syntax errors are caught by ruff — don't duplicate
            return

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not self._module_exists(alias.name, ctx.project_root):
                        yield LintIssue(
                            linter="import_checker",
                            kind="unresolved-import",
                            message=f"Cannot resolve import '{alias.name}'",
                            file=filepath,
                            line=node.lineno,
                            column=node.col_offset,
                            severity="warning",
                            confidence=0.85,  # Not 1.0 — could be installed but not on sys.path
                            suggestions=[
                                f"Check that '{alias.name}' is installed and on sys.path",
                            ],
                        )

            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.level == 0
                and not self._module_exists(node.module, ctx.project_root)
            ):
                # Skip relative imports
                yield LintIssue(
                    linter="import_checker",
                    kind="unresolved-import",
                    message=f"Cannot resolve 'from {node.module} import ...'",
                    file=filepath,
                    line=node.lineno,
                    column=node.col_offset,
                    severity="warning",
                    confidence=0.85,
                    suggestions=[
                        f"Check that '{node.module}' is installed and on sys.path",
                    ],
                )

    def _module_exists(self, module_name: str, project_root: str) -> bool:
        """Check if a module can be found by importlib.

        Checks both installed packages and local project modules.
        Uses importlib.util.find_spec which doesn't execute the module.
        """
        # Check installed packages
        try:
            spec = importlib.util.find_spec(module_name)
            if spec is not None:
                return True
        except (ModuleNotFoundError, ValueError):
            pass

        # Check local project files (src/module_name.py, module_name/__init__.py)
        top_level = module_name.split(".")[0]
        local_candidates = [
            os.path.join(project_root, top_level + ".py"),
            os.path.join(project_root, top_level, "__init__.py"),
            os.path.join(project_root, "src", top_level + ".py"),
            os.path.join(project_root, "src", top_level, "__init__.py"),
        ]

        return any(os.path.exists(c) for c in local_candidates)

    def _run_custom(
        self, command: str, ctx: LinterContext
    ) -> Iterable[LintIssue]:
        """Run a user-defined import verification command."""
        import shlex

        cmd = shlex.split(command)
        result = self.run_command(cmd, ctx.project_root)

        if result.returncode != 0:
            yield LintIssue(
                linter="import_checker",
                kind="import-verify-failed",
                message=f"Import verification failed: {result.stderr.strip()[:200]}",
                severity="warning",
                confidence=1.0,
            )
