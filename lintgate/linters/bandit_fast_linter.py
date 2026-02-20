"""Bandit fast path — high-confidence security checks at Tier 2.

Runs a focused subset of bandit checks that have high signal-to-noise
ratio on every structural change, not just Tier 3 deep scans.

Professional instinct modeled: "Critical security checks run on every
structural change, not just deep scans."

The full bandit suite (bandit_linter.py, Tier 3) includes all checks.
This Tier 2 fast path covers:
- Hardcoded passwords and binds (B105, B106, B107)
- Swallowed errors via try/except/pass (B110)
- Unsafe deserialization: pickle, marshal (B301, B302)
- Weak hashing: md5, sha1 (B303)
- Requests without SSL verify (B501), unsafe SSL (B502)
- Shell injection: subprocess with shell=True variants (B602-B605)
- SQL injection via string formatting (B608)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..types import LinterContext, LintIssue
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable

# High-confidence, low-noise security checks
_FAST_TESTS = "B105,B106,B107,B110,B301,B302,B303,B501,B502,B602,B603,B604,B605,B608"

# Bandit severity → LintGate severity (same mapping as bandit_linter.py)
_SEVERITY_MAP = {
    "HIGH": "blocking",
    "MEDIUM": "warning",
    "LOW": "informational",
}

# Bandit confidence → LintGate confidence (same as bandit_linter.py)
_CONFIDENCE_MAP = {
    "HIGH": 1.0,
    "MEDIUM": 0.8,
    "LOW": 0.6,
}


class BanditFastLinter(BaseLinter):
    """Bandit fast path — high-confidence security checks at Tier 2.

    Runs only the highest-signal bandit checks for fast feedback on
    every structural change. The full bandit suite runs at Tier 3.
    """

    name = "bandit_fast"
    tier = 2
    timeout_ms = 8000
    required_tool = "bandit"

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Run bandit with focused test set and JSON output."""

        cmd = [
            "bandit",
            "-t",
            _FAST_TESTS,
            "-f",
            "json",
            "-q",
        ]

        # Add extra args from config
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
                linter="bandit_fast",
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
                    "test_set": "fast",
                },
                suggestions=[
                    f"See: {item['more_info']}"
                    if item.get("more_info")
                    else f"Review {test_name} finding for security implications",
                ],
            )
