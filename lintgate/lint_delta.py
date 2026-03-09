"""Lint-level delta computation — shows finding changes between consecutive runs.

Reuses the stable fingerprinting from controlplane.reporter.delta but operates
on AggregatedResult (lint pipeline output) rather than MeshResult (controlplane).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lintgate.controlplane.reporter.delta import (
    compute_finding_delta,
    compute_finding_fingerprint,
)

if TYPE_CHECKING:
    from lintgate.types import AggregatedResult


def build_lint_finding_index(aggregated: AggregatedResult) -> dict[str, dict[str, Any]]:
    """Build fingerprint → finding-summary dict from an AggregatedResult.

    Uses channel="lint" for all findings since this is lint-pipeline output.
    """
    index: dict[str, dict[str, Any]] = {}
    severity_order = {"informational": 0, "warning": 1, "blocking": 2}

    all_issues = [*aggregated.blocking, *aggregated.warnings, *aggregated.informational]
    for issue in all_issues:
        fp = compute_finding_fingerprint(issue, "lint")
        existing = index.get(fp)
        if existing is None:
            index[fp] = {
                "channel": "lint",
                "kind": issue.kind,
                "severity": issue.severity,
                "file": issue.short_location() if hasattr(issue, "short_location") else "",
                "message": (issue.message or "")[:80],
                "line": issue.line,
                "count": 1,
            }
        else:
            existing["count"] = int(existing.get("count", 1)) + 1
            cur_rank = severity_order.get(str(existing.get("severity", "")), 0)
            new_rank = severity_order.get(issue.severity, 0)
            if new_rank > cur_rank:
                existing["severity"] = issue.severity
    return index


def compute_lint_delta(
    current: AggregatedResult,
    previous_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Compute delta between current lint results and a previous finding index.

    Returns dict with: resolved_count, new (list), still_active_count, summary.
    """
    current_index = build_lint_finding_index(current)
    delta = compute_finding_delta(current_index, previous_index)

    # Build human-readable summary
    resolved = delta["resolved_count"]
    new_count = sum(int(f.get("count", 1)) for f in delta["new"])
    still = delta["still_active_count"]
    delta["summary"] = f"{resolved} resolved, {new_count} new, {still} remaining"

    return delta
