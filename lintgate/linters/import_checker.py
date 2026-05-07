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
import sys
from typing import TYPE_CHECKING

from ..types import LinterContext, LintIssue
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable


_COMPAT_EXCEPTIONS = frozenset({"ModuleNotFoundError", "ImportError"})


def _handler_catches_import_error(handler: ast.ExceptHandler) -> bool:
    """Return True if an except handler catches ModuleNotFoundError or ImportError."""
    if handler.type is None:
        # Bare except — catches everything
        return True
    if isinstance(handler.type, ast.Name) and handler.type.id in _COMPAT_EXCEPTIONS:
        return True
    # Handle `except (ImportError, ModuleNotFoundError):`
    if isinstance(handler.type, ast.Tuple):
        return any(
            isinstance(elt, ast.Name) and elt.id in _COMPAT_EXCEPTIONS for elt in handler.type.elts
        )
    return False


def _collect_import_lines_from_stmts(stmts: list[ast.stmt]) -> set[int]:
    """Return line numbers of all import statements within the given statement list."""
    lines: set[int] = set()
    for stmt in stmts:
        for child in ast.walk(stmt):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                lines.add(child.lineno)
    return lines


def _collect_guarded_import_lines(tree: ast.AST) -> set[int]:
    """Return line numbers of imports inside try/except blocks that catch import errors.

    Recognizes the standard compatibility pattern:
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib
    """
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if not any(_handler_catches_import_error(h) for h in node.handlers):
            continue
        # Mark all import lines in both the try body and handler bodies
        guarded.update(_collect_import_lines_from_stmts(node.body))
        for handler in node.handlers:
            guarded.update(_collect_import_lines_from_stmts(handler.body))
    return guarded


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

    def _check_file_imports(self, filepath: str, ctx: LinterContext) -> Iterable[LintIssue]:
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

        # Collect lines of imports guarded by try/except ImportError|ModuleNotFoundError.
        # These are intentional compatibility patterns (e.g. tomllib/tomli).
        guarded_lines = _collect_guarded_import_lines(tree)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if node.lineno in guarded_lines:
                    continue
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
                and node.lineno not in guarded_lines
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
        """Check if a module can be found.

        Resolution order is filesystem-first to avoid namespace-package
        shadowing: when LintGate runs as a long-lived MCP process, its own
        sys.path can contain directories that collide with package names in
        the target project (e.g. LintGate has its own ``scripts/``). A naive
        ``find_spec`` call resolves against LintGate's sys.path and returns
        wrong specs for those collisions, producing
        ``ModuleNotFoundError: ... (unknown location)`` for shadowed
        submodule lookups.

        1. stdlib (``sys.stdlib_module_names``) — unambiguous, can't be shadowed.
        2. Local project filesystem walk against ``project_root``.
        3. ``find_spec`` only when ``module_name``'s top-level does NOT exist
           as a directory under ``project_root`` — prevents the shadow case.
           Namespace-only specs (``origin is None``) are rejected.
        """
        top_level = module_name.split(".")[0]

        if top_level in sys.stdlib_module_names:
            return True

        rel_path = module_name.replace(".", os.sep)
        local_candidates = [
            # Full module path
            os.path.join(project_root, rel_path + ".py"),
            os.path.join(project_root, rel_path, "__init__.py"),
            os.path.join(project_root, "src", rel_path + ".py"),
            os.path.join(project_root, "src", rel_path, "__init__.py"),
            # Top-level package marker (covers cases where the submodule
            # isn't a separate file but lives inside __init__.py)
            os.path.join(project_root, top_level + ".py"),
            os.path.join(project_root, top_level, "__init__.py"),
            os.path.join(project_root, "src", top_level + ".py"),
            os.path.join(project_root, "src", top_level, "__init__.py"),
        ]
        if any(os.path.exists(c) for c in local_candidates):
            return True

        # If the top-level looks project-internal but no file matched above,
        # treat as missing — find_spec would resolve against LintGate's
        # sys.path (a different process) and return shadow specs.
        if os.path.isdir(os.path.join(project_root, top_level)) or os.path.isdir(
            os.path.join(project_root, "src", top_level)
        ):
            return False

        try:
            spec = importlib.util.find_spec(module_name)
        except (ModuleNotFoundError, ValueError, ImportError, AttributeError):
            return False
        if spec is None:
            return False
        # Reject namespace-only matches: they can be assembled from any
        # directory on sys.path with no concrete loader.
        return bool(spec.origin) or bool(spec.has_location)

    def _run_custom(self, command: str, ctx: LinterContext) -> Iterable[LintIssue]:
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
