"""Bandit security scanner integration.

Tier 3 — runs on architectural changes to pipeline-critical code.
Catches common security issues: hardcoded passwords, shell injection,
insecure deserialization, weak crypto, etc.

Uses bandit's JSON output for structured results.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from ..types import LinterContext, LintIssue
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable

# Directories where B105 (hardcoded password) has low signal
_B105_LOW_SIGNAL_DIRS = frozenset({
    "test", "tests", "testing", "docs", "doc",
    "examples", "fixtures", "conftest",
})


def _is_test_or_docs_context(filepath: str, project_root: str) -> bool:
    """Check if file is in a test/docs directory (lower B105 signal).

    B105 hardcoded-password findings in test/docs directories are almost
    always false positives (test fixtures, example values, UI symbols).
    Only suppress B105 in these contexts — never in production code paths.
    """
    try:
        rel = os.path.relpath(filepath, project_root)
    except ValueError:
        return False
    parts = Path(rel).parts
    return any(p.lower() in _B105_LOW_SIGNAL_DIRS for p in parts[:-1])

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
            filename = item.get("filename")

            # B105 scope-aware filtering: suppress in test/docs only
            if test_id == "B105" and filename and _is_test_or_docs_context(
                filename, ctx.project_root
            ):
                continue

            yield LintIssue(
                linter="bandit",
                kind=f"{test_id}/{test_name}" if test_id else test_name,
                message=item.get("issue_text", ""),
                file=filename,
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
