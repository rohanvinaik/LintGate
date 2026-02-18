"""Ruff linter integration.

Tier 0 — always runs. Uses ruff's JSON output mode for structured results.
Ruff replaces the entire flake8/isort/pyflakes/pycodestyle ecosystem.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..types import LinterContext, LintIssue
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable

# Ruff error codes that should be blocking (prevent proceeding)
_BLOCKING_CODES = frozenset({
    # Pyflakes errors (undefined names, unused imports in certain contexts)
    "F821",  # Undefined name
    "F811",  # Redefinition of unused name
    "F841",  # Local variable assigned but never used (in strict mode)
    # Syntax errors
    "E999",  # Syntax error
})

# Ruff error codes that are informational (learning signal, not actionable)
_INFORMATIONAL_CODES = frozenset({
    "E501",  # Line too long
    "W291",  # Trailing whitespace
    "W292",  # No newline at end of file
    "W293",  # Whitespace before ':'
    "D100",  # Missing docstring in public module
    "D101",  # Missing docstring in public class
    "D102",  # Missing docstring in public method
    "D103",  # Missing docstring in public function
})


class RuffLinter(BaseLinter):
    """Ruff linter — fast Python linting with JSON output.

    Always available as tier 0. Uses --output-format json for structured
    results that the agent can parse without ANSI scraping.
    """

    name = "ruff_check"
    tier = 0
    timeout_ms = 5000
    required_tool = "ruff"

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Run ruff check with JSON output on specified files."""

        cmd = [
            "ruff", "check",
            "--output-format", "json",
            "--no-fix",  # Don't auto-fix, just report
        ]

        # Add extra args from config
        extra_args = ctx.config.get("extra_args", [])
        if extra_args:
            cmd.extend(extra_args)

        cmd.extend(ctx.files)

        result = self.run_command(cmd, ctx.project_root)

        # Ruff outputs JSON to stdout even on failure (exit code 1 = issues found)
        if result.stdout:
            try:
                items = json.loads(result.stdout)
            except json.JSONDecodeError:
                return  # Malformed output, skip

            for item in items:
                code = item.get("code", "unknown")
                location = item.get("location", {})
                end_location = item.get("end_location", {})
                fix = item.get("fix")

                yield LintIssue(
                    linter="ruff",
                    kind=code,
                    message=item.get("message", ""),
                    file=item.get("filename"),
                    line=location.get("row"),
                    column=location.get("column"),
                    end_line=end_location.get("row"),
                    end_column=end_location.get("column"),
                    severity=_classify_severity(code, ctx.strictness),
                    confidence=1.0,  # Ruff is deterministic
                    fixable=fix is not None,
                    fix_description=fix.get("message") if fix else None,
                )


class RuffFormatLinter(BaseLinter):
    """Ruff format checker — checks formatting without fixing.

    Tier 0 complement to RuffLinter. Only checks, doesn't format.
    """

    name = "ruff_format"
    tier = 0
    timeout_ms = 3000
    required_tool = "ruff"

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Run ruff format --check on specified files."""

        cmd = ["ruff", "format", "--check", "--diff"]
        cmd.extend(ctx.files)

        result = self.run_command(cmd, ctx.project_root)

        # ruff format --check exits 1 if files need formatting
        if result.returncode != 0 and result.stdout:
            # Emit one issue per file that needs formatting
            seen_files: set[str] = set()
            for line in result.stdout.splitlines():
                if line.startswith("+++ ") and line != "+++ /dev/null":
                    filepath = line.split("\t")[0].removeprefix("+++ ")
                    if filepath not in seen_files:
                        seen_files.add(filepath)
                        yield LintIssue(
                            linter="ruff_format",
                            kind="format",
                            message="File needs formatting",
                            file=filepath,
                            severity="informational",
                            confidence=1.0,
                            fixable=True,
                            fix_description="Run: ruff format",
                        )


def _classify_severity(code: str, strictness: str) -> str:
    """Map ruff error code to LintGate severity.

    In strict mode (pipeline-critical paths), more codes become blocking.
    """
    if code in _BLOCKING_CODES:
        return "blocking"

    if code in _INFORMATIONAL_CODES:
        return "informational"

    # In strict mode, unused imports and variables are warnings
    if strictness == "strict" and (code.startswith("F4") or code.startswith("F8")):
        return "warning"

    return "warning"
