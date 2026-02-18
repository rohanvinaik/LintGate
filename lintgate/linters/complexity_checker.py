"""Radon cyclomatic complexity and maintainability index checker.

Tier 2 — runs on logic and structural changes. Uses radon's JSON
output mode for structured results.

Direct descendant of ARC_AGI_3's lint.py radon integration:
- Pipeline-critical paths get strict thresholds
- RADON_CC_EXEMPTIONS pattern carried forward via config exemptions
- Separate CC (per-function) and MI (per-file) checks

CC grades: A (1-5), B (6-10), C (11-15), D (16-20), E (21-25), F (26+)
MI grades: A (20-100), B (10-19), C (0-9)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..types import LinterContext, LintIssue
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable

# Default thresholds by strictness
_CC_THRESHOLDS = {
    "relaxed": 25,   # Grade E boundary
    "normal": 20,    # Grade D boundary (same as lint.py)
    "strict": 15,    # Grade C boundary
}

_MI_THRESHOLDS = {
    "relaxed": 5,    # Below C
    "normal": 10,    # Grade B boundary
    "strict": 20,    # Grade A boundary
}


class ComplexityChecker(BaseLinter):
    """Radon CC + MI checker — catches overly complex functions and files.

    Uses radon's JSON output for structured parsing. Thresholds are
    strictness-aware: pipeline-critical code gets tighter limits.
    """

    name = "complexity_checker"
    tier = 2
    timeout_ms = 5000
    required_tool = "radon"

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Run radon CC and MI on specified files."""
        yield from self._check_cc(ctx)
        yield from self._check_mi(ctx)

    def _check_cc(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Check cyclomatic complexity per function."""
        cmd = ["radon", "cc", "-j", "-s"] + ctx.files
        result = self.run_command(cmd, ctx.project_root)

        if not result.stdout:
            return

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return

        threshold = ctx.config.get(
            "cc_threshold",
            _CC_THRESHOLDS.get(ctx.strictness, 20),
        )

        for filepath, functions in data.items():
            for func in functions:
                cc = func.get("complexity", 0)
                if cc <= threshold:
                    continue

                name = func.get("name", "unknown")
                rank = func.get("rank", "?")
                lineno = func.get("lineno", 0)
                func_type = func.get("type", "function")

                # Severity: extreme complexity is blocking
                if cc > 30:
                    severity = "blocking"
                elif cc > threshold:
                    severity = "warning"
                else:
                    severity = "informational"

                yield LintIssue(
                    linter="radon",
                    kind="complexity",
                    message=(
                        f"{func_type} '{name}' has cyclomatic complexity {cc} "
                        f"(grade {rank}, threshold={threshold})"
                    ),
                    file=filepath,
                    line=lineno,
                    severity=severity,
                    confidence=1.0,
                    evidence={"complexity": cc, "grade": rank, "threshold": threshold},
                    suggestions=[
                        f"Consider extracting helper functions to reduce complexity below {threshold}",
                    ],
                )

    def _check_mi(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Check maintainability index per file."""
        cmd = ["radon", "mi", "-j", "-s"] + ctx.files
        result = self.run_command(cmd, ctx.project_root)

        if not result.stdout:
            return

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return

        mi_threshold = ctx.config.get(
            "mi_threshold",
            _MI_THRESHOLDS.get(ctx.strictness, 10),
        )

        for filepath, info in data.items():
            mi_score = info.get("mi", 100)
            rank = info.get("rank", "A")

            if mi_score >= mi_threshold:
                continue

            yield LintIssue(
                linter="radon",
                kind="maintainability",
                message=(
                    f"Maintainability index {mi_score:.1f} (grade {rank}, "
                    f"threshold={mi_threshold})"
                ),
                file=filepath,
                severity="warning" if mi_score >= 5 else "blocking",
                confidence=1.0,
                evidence={"mi_score": mi_score, "grade": rank},
                suggestions=[
                    "File is difficult to maintain. Consider breaking into smaller modules.",
                ],
            )
