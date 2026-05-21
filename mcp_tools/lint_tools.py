"""Lint tools — thin subprocess wrappers around scripts/lint_run.py.

All computation lives in scripts/lint_run.py. This module registers MCP
tools that invoke the script via subprocess and relay its stdout. Pure
helper functions (_tool_package_name, _project_venv_python, etc.) are
re-exported from lintgate.lint_helpers for test-import compatibility.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Literal

from lintgate.lint_helpers import (  # re-exported for tests
    _format_cmd,
    _linter_available,
    _missing_tool_hints,
    _project_venv_python,
    _tool_package_name,
)

__all__ = [
    "_format_cmd",
    "_linter_available",
    "_missing_tool_hints",
    "_project_venv_python",
    "_tool_package_name",
    "register",
]

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "lint_run.py",
)

# Error prefixes that should become ValueError in the MCP layer
# (preserves the pre-subprocess contract for callers).
_VALUE_ERROR_PREFIXES = (
    "No lint run found",
    "Invalid severity",
    "No specified files exist",
    "No files specified",
    "Either files or path",
    "Invalid tier",
    "No Python files found",
    "No valid Python files",
)


def _run_script(path: str, *args: str) -> str:
    """Invoke scripts/lint_run.py as a subprocess and relay stdout.

    Raises ValueError when the script emits a known validation error
    (preserves the pre-subprocess MCP contract).
    """
    try:
        proc = subprocess.run(
            [sys.executable, _SCRIPT, path, *args],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "lint_run subprocess timed out"})
    except OSError as exc:
        return json.dumps({"error": f"lint_run subprocess failed: {exc}"})

    stdout = (proc.stdout or "").strip()
    if not stdout:
        return json.dumps({
            "error": f"lint_run exit {proc.returncode}",
            "stderr": (proc.stderr or "").strip()[-500:],
        })

    # Peek at the last-line JSON for ValueError-worthy errors
    last = stdout.splitlines()[-1]
    try:
        parsed = json.loads(last)
    except json.JSONDecodeError:
        return stdout
    if isinstance(parsed, dict) and "error" in parsed and "analysis_id" not in parsed:
        msg = str(parsed["error"])
        for prefix in _VALUE_ERROR_PREFIXES:
            if msg.startswith(prefix):
                raise ValueError(msg)
    return stdout


def register(mcp, helpers):
    """Register lint tools on the shared MCP instance."""
    del helpers  # unused — the script handles validation and state

    @mcp.tool()
    def lint_files(
        files: list[str],
        tier: int = 2,
        project_root: str | None = None,
        strictness: Literal["relaxed", "normal", "strict"] = "normal",
        scope: Literal["compact", "surgical"] = "compact",
    ) -> str:
        """Lint specific files at a given tier level.

        WHEN TO USE: After editing Python files. This is the most common tool —
        call it after every code change to catch issues early.

        Example: lint_files(files=["/my/project/src/main.py"])

        Args:
            scope: "compact" (default) returns flat issue counts.
                   "surgical" splits into edit_scope (your changes) and
                   baseline (pre-existing project state from last full run).

        Returns compact JSON with run_id, issue counts, and next_actions.
        """
        if tier not in (0, 1, 2, 3):
            raise ValueError(f"Invalid tier {tier}; expected one of: 0, 1, 2, 3")
        if strictness not in ("relaxed", "normal", "strict"):
            raise ValueError(
                f"Invalid strictness '{strictness}'; expected one of: relaxed, normal, strict"
            )
        if not files:
            raise ValueError("No files specified")

        path_arg = project_root or os.path.dirname(os.path.abspath(files[0]))
        args = ["files", "--tier", str(tier), "--strictness", strictness, "--scope", scope, "--files", *files]
        if project_root:
            args.extend(["--project-root", project_root])
        return _run_script(path_arg, *args)

    @mcp.tool()
    def lint_project(
        path: str,
        tier: int = 2,
        strictness: Literal["relaxed", "normal", "strict"] = "normal",
    ) -> str:
        """Lint all Python files in a project at a given tier level.

        WHEN TO USE: For a full project scan — run at the start of a session
        or before committing. For checking specific files after edits, use
        lint_files instead.

        Example: lint_project(path="/my/project")
        """
        if tier not in (0, 1, 2, 3):
            raise ValueError(f"Invalid tier {tier}; expected one of: 0, 1, 2, 3")
        return _run_script(path, "project", "--tier", str(tier), "--strictness", strictness)

    @mcp.tool()
    def lint_get_details(
        run_id: str,
        severity: str | None = None,
        max_issues: int = 10,
        include_recurrence: bool = False,
    ) -> str:
        """Drill into a previous lint run by run_id.

        Use after lint_files/lint_project (which return a run_id in compact mode)
        to retrieve full issue details without re-running linters.

        Args:
            run_id: The run_id from a previous lint_files/lint_project response.
            severity: Filter by severity: "blocking", "warning", "informational",
                      or None for all.
            max_issues: Maximum issues to return (default 10).
            include_recurrence: Include recurrence data from issue memory.
        """
        valid_severities = {"blocking", "warning", "informational", None}
        if severity not in valid_severities:
            raise ValueError(
                f"Invalid severity '{severity}'; expected one of: blocking, warning, informational"
            )
        args = ["details", "--run-id", run_id, "--max-issues", str(max_issues)]
        if severity:
            args.extend(["--severity", severity])
        if include_recurrence:
            args.append("--include-recurrence")
        return _run_script(os.getcwd(), *args)

    @mcp.tool()
    def lint_status(path: str | None = None) -> str:
        """Show LintGate status: linters, run history, context, version audits, and today's metrics."""
        return _run_script(path or os.getcwd(), "status")

    @mcp.tool()
    def audit_tool_versions(
        path: str,
        auto_fix: bool = False,
        verify_after_fix: bool = True,
    ) -> str:
        """Audit lint tool version compatibility and optionally repair mismatches.

        Compares installed tool versions against requirements in lintgate.yaml.
        Set auto_fix=True to attempt automatic upgrades via pip/uv.
        """
        args = ["audit"]
        if auto_fix:
            args.append("--auto-fix")
        if not verify_after_fix:
            args.append("--no-verify-after-fix")
        return _run_script(path, *args)

    @mcp.tool()
    def lint_fix(
        files: list[str] | None = None,
        path: str | None = None,
        dry_run: bool = True,
        safe_only: bool = True,
    ) -> str:
        """Auto-fix safe lint issues found by lint_files or lint_project.

        WHEN TO USE: After lint_files/lint_project reports fixable issues.
        Applies ruff's safe auto-fix rules (formatting, import sorting, simple corrections).

        Example: lint_fix(path="/my/project", dry_run=False)

        Default is dry_run=True which previews changes without modifying files.
        Set dry_run=False to apply fixes.
        """
        if not files and not path:
            raise ValueError("Either files or path must be provided")
        path_arg = path or (os.path.dirname(os.path.abspath(files[0])) if files else os.getcwd())
        args = ["fix"]
        if not dry_run:
            args.append("--no-dry-run")
        if not safe_only:
            args.append("--no-safe-only")
        if files:
            args.extend(["--files", *files])
        return _run_script(path_arg, *args)

    return {
        "lint_files": lint_files,
        "lint_project": lint_project,
        "lint_get_details": lint_get_details,
        "lint_status": lint_status,
        "audit_tool_versions": audit_tool_versions,
        "lint_fix": lint_fix,
    }
