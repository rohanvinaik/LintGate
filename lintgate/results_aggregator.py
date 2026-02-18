"""Phase 4: Aggregate, deduplicate, and classify lint results.

Collects results from all linters, deduplicates issues (same file/line/kind
from different tools), applies severity overrides and exemptions from config,
and splits into blocking/warning/informational tiers.

Pattern from TailChasingFixer's IssueCollection: filter, deduplicate, sort.
Pattern from ARC_AGI_3's lint.py: blocking vs informational severity split.
"""

from __future__ import annotations

import os

from .types import AggregatedResult, LinterResult, LintIssue, ProjectConfig

_EXEMPTION_ALIASES: dict[str, tuple[str, ...]] = {
    # README/docs often use "complexity", while emitted issues come from linter "radon".
    "radon": ("complexity", "complexity_checker"),
    "structure": ("structure_checker",),
    "architecture": ("architecture_checker",),
    "dead_code": ("dead_code_checker",),
}


def aggregate_results(
    linter_results: list[LinterResult],
    config: ProjectConfig,
    tier_name: str = "",
    tier_reason: str = "",
) -> AggregatedResult:
    """Aggregate all linter results into a single structured report.

    Args:
        linter_results: Results from lint_runner.py
        config: Project config (for severity overrides and exemptions)
        tier_name: Which tier was selected
        tier_reason: Why this tier was selected

    Returns:
        AggregatedResult with blocking/warning/informational splits
    """
    all_issues: list[LintIssue] = []
    linter_statuses: dict[str, str] = {}
    total_duration = 0.0

    for lr in linter_results:
        linter_statuses[lr.linter_name] = lr.status
        total_duration += lr.duration_ms
        all_issues.extend(lr.issues)

    # Deduplicate (same file + line + kind from different linters)
    unique = _deduplicate(all_issues)

    # Assign deterministic issue IDs after dedup
    for issue in unique:
        issue.issue_id = issue.compute_issue_id()

    # Apply severity overrides from config
    if config.severity_overrides:
        for issue in unique:
            override = config.severity_overrides.get(issue.kind)
            if override:
                issue.severity = override

    # Apply exemptions
    if config.exemptions:
        unique = [i for i in unique if not _is_exempted(i, config.exemptions)]

    # Split by severity
    blocking = sorted(
        [i for i in unique if i.severity == "blocking"],
        key=lambda i: (i.file or "", i.line or 0),
    )
    warnings = sorted(
        [i for i in unique if i.severity == "warning"],
        key=lambda i: (i.file or "", i.line or 0),
    )
    informational = sorted(
        [i for i in unique if i.severity == "informational"],
        key=lambda i: (i.file or "", i.line or 0),
    )

    # Compute metrics
    metrics = {
        "total_issues": len(unique),
        "blocking_count": len(blocking),
        "warning_count": len(warnings),
        "info_count": len(informational),
        "fixable_count": sum(1 for i in unique if i.fixable),
        "linters_run": sum(1 for s in linter_statuses.values() if s == "ok"),
        "linters_skipped": sum(1 for s in linter_statuses.values() if s == "skipped"),
        "linters_errored": sum(1 for s in linter_statuses.values() if s in ("error", "timeout")),
    }

    # Collect all files that were linted
    files_linted = sorted({i.file for i in unique if i.file})

    return AggregatedResult(
        blocking=blocking,
        warnings=warnings,
        informational=informational,
        metrics=metrics,
        linter_statuses=linter_statuses,
        tier_used=tier_name,
        tier_reason=tier_reason,
        total_duration_ms=total_duration,
        files_linted=files_linted,
    )


def _deduplicate(issues: list[LintIssue]) -> list[LintIssue]:
    """Remove duplicate issues (same file + line + kind).

    When multiple linters report the same issue, keep the one with
    the highest severity, then highest confidence.
    """
    seen: dict[tuple, LintIssue] = {}
    severity_order = {"blocking": 3, "warning": 2, "informational": 1}

    for issue in issues:
        key = (issue.file, issue.line, issue.kind)
        if key in seen:
            existing = seen[key]
            # Keep the one with higher severity, then higher confidence
            existing_rank = (severity_order.get(existing.severity, 0), existing.confidence)
            new_rank = (severity_order.get(issue.severity, 0), issue.confidence)
            if new_rank > existing_rank:
                seen[key] = issue
        else:
            seen[key] = issue

    return list(seen.values())


def _is_exempted(issue: LintIssue, exemptions: dict) -> bool:
    """Check if an issue is exempted by config.

    Exemption format in lintgate.yaml:
    ```yaml
    exemptions:
      complexity:
        "filename.py":
          reason: "Pre-existing CC=48"
          ticket: "BACKLOG-42"
      ruff:
        "src/legacy/old_module.py":
          codes: ["C901", "PLR0904"]
          reason: "Legacy module"
    ```
    """
    for section in _exemption_sections(issue):
        linter_exemptions = exemptions.get(section, {})
        if not linter_exemptions:
            continue

        # Check by filename
        if issue.file:
            basename = os.path.basename(issue.file)
            file_exemption = linter_exemptions.get(basename) or linter_exemptions.get(issue.file)
            if file_exemption:
                # If codes are specified, check if this specific code is exempted
                exempt_codes = file_exemption.get("codes", [])
                if exempt_codes:
                    return issue.kind in exempt_codes
                # No codes specified = exempt all issues from this file
                return True

    return False


def _exemption_sections(issue: LintIssue) -> list[str]:
    """Return config section names that can match this issue."""
    sections: list[str] = [issue.linter]
    sections.extend(_EXEMPTION_ALIASES.get(issue.linter, ()))

    if issue.kind and issue.kind not in sections:
        sections.append(issue.kind)

    return sections
