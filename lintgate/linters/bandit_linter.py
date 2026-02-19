"""Bandit security scanner integration.

Tier 3 — runs on architectural changes to pipeline-critical code.
Catches common security issues: hardcoded passwords, shell injection,
insecure deserialization, weak crypto, etc.

Uses bandit's JSON output for structured results.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..types import LinterContext, LintIssue
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable

# Bandit severity mapping
_SEVERITY_MAP = {
    "HIGH": "blocking",
    "MEDIUM": "warning",
    "LOW": "informational",
}

# Bandit confidence mapping — high confidence issues are more actionable
_CONFIDENCE_MAP = {
    "HIGH": 1.0,
    "MEDIUM": 0.8,
    "LOW": 0.6,
}


class BanditLinter(BaseLinter):
    """Bandit security scanner — catches common security anti-patterns.

    Only runs at Tier 3 (architectural changes). Uses JSON output.
    """

    name = "bandit"
    tier = 3
    timeout_ms = 10000
    required_tool = "bandit"

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Run bandit on specified files with JSON output."""

        cmd = [
            "bandit",
            "-f",
            "json",  # JSON output
            "-q",  # Quiet (no progress)
        ]

        # Add config file if specified
        extra_args = ctx.config.get("extra_args", [])
        if extra_args:
            cmd.extend(extra_args)

        cmd.extend(ctx.files)

        result = self.run_command(cmd, ctx.project_root)

        # bandit outputs JSON to stdout; exit code 1 = issues found
        output = result.stdout or ""
        if not output:
            return

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return

        results = data.get("results", [])
        for item in results:
            severity_label = item.get("issue_severity", "LOW")
            confidence_label = item.get("issue_confidence", "LOW")
            test_id = item.get("test_id", "")
            test_name = item.get("test_name", "")

            yield LintIssue(
                linter="bandit",
                kind=f"{test_id}/{test_name}" if test_id else test_name,
                message=item.get("issue_text", ""),
                file=item.get("filename"),
                line=item.get("line_number"),
                severity=_SEVERITY_MAP.get(severity_label, "warning"),
                confidence=_CONFIDENCE_MAP.get(confidence_label, 0.6),
                evidence={
                    "severity": severity_label,
                    "confidence": confidence_label,
                    "cwe": item.get("issue_cwe", {}),
                    "more_info": item.get("more_info", ""),
                },
                suggestions=[
                    f"See: {item['more_info']}"
                    if item.get("more_info")
                    else f"Review {test_name} finding for security implications",
                ],
            )
