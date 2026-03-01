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
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from ..types import LinterContext, LintIssue
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable

# High-confidence, low-noise security checks
_FAST_TESTS = "B105,B106,B107,B110,B301,B302,B303,B501,B502,B602,B603,B604,B605,B608"

# Directories where B105 (hardcoded password) has low signal
_B105_LOW_SIGNAL_DIRS = frozenset(
    {
        "test",
        "tests",
        "testing",
        "docs",
        "doc",
        "examples",
        "fixtures",
        "conftest",
    }
)


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


_NOQA_RE = re.compile(r"#\s*noqa:\s*([\w,\s]+)")


def _is_noqa_suppressed(filepath: str, lineno: int, test_id: str) -> bool:
    """Check if a source line has a ``# noqa: BXXX`` suppression for test_id.

    Bandit natively handles ``# nosec`` — this covers the Ruff-compatible
    ``# noqa: B608`` / ``# noqa: S608, B608`` pattern that Bandit ignores.
    """
    if not filepath or not lineno or not test_id:
        return False
    try:
        with open(filepath) as f:
            for i, line in enumerate(f, 1):
                if i == lineno:
                    m = _NOQA_RE.search(line)
                    if m:
                        codes = {c.strip() for c in m.group(1).split(",")}
                        if test_id in codes:
                            return True
                        # Also check Sxxx alias (Ruff maps B608 → S608)
                        s_alias = "S" + test_id[1:] if test_id.startswith("B") else None
                        if s_alias and s_alias in codes:
                            return True
                    return False
    except OSError:
        pass
    return False


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
            filename = item.get("filename")
            lineno = item.get("line_number")

            # B105 scope-aware filtering: suppress in test/docs only
            if (
                test_id == "B105"
                and filename
                and _is_test_or_docs_context(filename, ctx.project_root)
            ):
                continue

            # Ruff-compatible noqa suppression (Bandit handles # nosec natively)
            if _is_noqa_suppressed(filename, lineno, test_id):
                continue

            yield LintIssue(
                linter="bandit_fast",
                kind=f"{test_id}/{test_name}" if test_id else test_name,
                message=item.get("issue_text", ""),
                file=filename,
                line=lineno,
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
