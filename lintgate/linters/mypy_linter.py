"""Mypy type checker integration.

Tier 2 — runs on logic and structural changes. Parses mypy's
machine-readable output format for structured results.

Mypy is slower than ruff (~2-5s on a single file) but catches
an entirely different class of errors: type mismatches, missing
return statements, incorrect argument types, protocol violations.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..types import LinterContext, LintIssue
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable

# Mypy output pattern: file:line: severity: message  [error-code]
_MYPY_LINE_RE = re.compile(
    r"^(.+?):(\d+):\s*(\d+)?:?\s*(error|warning|note):\s*(.+?)(?:\s+\[(.+?)\])?\s*$"
)

# Error codes that should be blocking
_BLOCKING_CODES = frozenset({
    "syntax",           # Syntax errors
    "name-defined",     # Name not defined
    "attr-defined",     # Attribute not defined
    "import",           # Import error
    "valid-type",       # Invalid type
})

# Error codes that are informational
_INFORMATIONAL_CODES = frozenset({
    "no-untyped-def",   # Missing type annotations
    "type-arg",         # Missing type arguments
    "unused-ignore",    # Unused type: ignore comment
})


class MypyLinter(BaseLinter):
    """Mypy type checker — catches type errors, missing returns, bad imports.

    Uses --no-error-summary and --show-column-numbers for parseable output.
    Scoped to specific files (not whole project) for speed.
    """

    name = "mypy"
    tier = 2
    timeout_ms = 15000  # mypy can be slow on first run
    required_tool = "mypy"

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Run mypy on specified files with parseable output."""

        cmd = [
            "mypy",
            "--no-error-summary",
            "--show-column-numbers",
            "--no-color-output",
            "--no-pretty",
        ]

        # Add extra args from config
        extra_args = ctx.config.get("extra_args", [])
        if extra_args:
            cmd.extend(extra_args)

        cmd.extend(ctx.files)

        result = self.run_command(cmd, ctx.project_root)

        # mypy outputs to stdout, exit code 1 = type errors found
        output = result.stdout or ""
        for line in output.splitlines():
            match = _MYPY_LINE_RE.match(line.strip())
            if not match:
                continue

            filepath, line_no, col_no, severity_str, message, error_code = match.groups()

            yield LintIssue(
                linter="mypy",
                kind=error_code or severity_str,
                message=message.strip(),
                file=filepath,
                line=int(line_no),
                column=int(col_no) if col_no else None,
                severity=_classify_severity(severity_str, error_code, ctx.strictness),
                confidence=1.0,  # mypy is deterministic
                fixable=False,  # mypy doesn't auto-fix
            )


def _classify_severity(severity_str: str, error_code: str | None, strictness: str) -> str:
    """Map mypy severity + error code to LintGate severity."""

    # Notes are always informational
    if severity_str == "note":
        return "informational"

    if error_code:
        if error_code in _BLOCKING_CODES:
            return "blocking"
        if error_code in _INFORMATIONAL_CODES:
            return "informational"

    # In strict mode, all errors are blocking
    if strictness == "strict" and severity_str == "error":
        return "blocking"

    # Default: mypy errors are warnings, mypy warnings are informational
    if severity_str == "error":
        return "warning"

    return "informational"
