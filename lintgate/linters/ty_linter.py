"""ty type checker integration — type integrity signal.

Tier 2 — runs on logic and structural changes. Parses ty's GitLab Code
Quality JSON output for structured results.

ty is Astral's Rust-based Python type checker, 10-100x faster than mypy.
It catches type mismatches, invalid assignments, undefined attributes,
protocol violations, and generic type errors.

Professional instinct modeled: "A senior engineer runs type checks before
committing. Type errors caught early prevent cascading failures."
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..types import LinterContext, LintIssue
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable

# ty GitLab Code Quality severity → LintGate severity (default mapping)
_SEVERITY_MAP = {
    "blocker": "blocking",
    "critical": "blocking",
    "major": "warning",
    "minor": "informational",
    "info": "informational",
}

# Check names that should always be blocking (regardless of ty severity)
_BLOCKING_CHECKS = frozenset(
    {
        "unresolved-import",
        "unresolved-reference",
        "invalid-syntax",
        "invalid-base",
        "cyclic-class-definition",
    }
)

# Check names that are informational (style/annotation issues)
_INFORMATIONAL_CHECKS = frozenset(
    {
        "unused-ignore-comment",
        "ignore-comment-unknown-rule",
        "fstring-type-annotation",
        "deprecated",
    }
)


class TyLinter(BaseLinter):
    """ty type checker — catches type errors, invalid assignments, bad imports.

    Uses --output-format gitlab for JSON output (GitLab Code Quality spec).
    Scoped to specific files for speed.
    """

    name = "ty"
    tier = 2
    timeout_ms = 10000  # ty is fast, but budget for cold starts
    required_tool = "ty"

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Run ty on specified files with GitLab JSON output."""

        cmd = [
            "ty",
            "check",
            "--output-format",
            "gitlab",
        ]

        # Add extra args from config
        extra_args = ctx.config.get("extra_args", [])
        if extra_args:
            cmd.extend(extra_args)

        cmd.extend(ctx.files)

        result = self.run_command(cmd, ctx.project_root)

        # ty outputs JSON to stdout; exit code 1 = type errors found
        output = result.stdout or ""
        if not output.strip():
            return

        try:
            diagnostics = json.loads(output)
        except json.JSONDecodeError:
            return

        if not isinstance(diagnostics, list):
            return

        for item in diagnostics:
            check_name = item.get("check_name", "")
            description = item.get("description", "")
            severity_label = item.get("severity", "minor")
            location = item.get("location", {})
            filepath = location.get("path")
            line_no = None

            # GitLab format supports lines.begin or positions.begin.line
            lines = location.get("lines", {})
            if lines:
                line_no = lines.get("begin")
            else:
                positions = location.get("positions", {})
                begin = positions.get("begin", {})
                line_no = begin.get("line")

            yield LintIssue(
                linter="ty",
                kind=check_name or "type-error",
                message=description.strip(),
                file=filepath,
                line=line_no,
                severity=_classify_severity(severity_label, check_name, ctx.strictness),
                confidence=1.0,  # ty is deterministic
                fixable=False,
                evidence={
                    "check_name": check_name,
                    "ty_severity": severity_label,
                },
            )


def _classify_severity(severity_label: str, check_name: str, strictness: str) -> str:
    """Map ty severity + check name to LintGate severity."""

    # Specific check overrides
    if check_name in _BLOCKING_CHECKS:
        return "blocking"
    if check_name in _INFORMATIONAL_CHECKS:
        return "informational"

    # In strict mode, all major+ findings are blocking
    if strictness == "strict" and severity_label in ("blocker", "critical", "major"):
        return "blocking"

    # Default: use the severity map
    return _SEVERITY_MAP.get(severity_label, "warning")
