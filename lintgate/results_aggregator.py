"""Phase 4: Aggregate, deduplicate, and classify lint results.

Collects results from all linters, deduplicates issues (same file/line/kind
from different tools), applies severity overrides and exemptions from config,
and splits into blocking/warning/informational tiers.

Pattern from TailChasingFixer's IssueCollection: filter, deduplicate, sort.
Pattern from ARC_AGI_3's lint.py: blocking vs informational severity split.
"""

from __future__ import annotations

import os

from .linters.import_pattern_detector import (
    OptionalImportReport,
    detect_optional_imports,
    is_finding_on_guarded_import,
)
from .types import AggregatedResult, LinterResult, LintIssue, ProjectConfig


def _normalize_file_path(filepath: str, project_root: str) -> str:
    """Normalize absolute paths under project_root to relative; keep others as-is."""
    if os.path.isabs(filepath) and project_root:
        try:
            rel = os.path.relpath(filepath, project_root)
            # Only use relative if it doesn't escape the project root
            if not rel.startswith(".."):
                return rel
        except ValueError:
            pass  # Different drive on Windows
    return filepath


_EXEMPTION_ALIASES: dict[str, tuple[str, ...]] = {
    # README/docs often use "complexity", while emitted issues come from linter "radon".
    "radon": ("complexity", "complexity_checker"),
    "structure": ("structure_checker",),
    "architecture": ("architecture_checker",),
    "dead_code": ("dead_code_checker",),
}


def _collect_raw_issues(
    linter_results: list[LinterResult],
) -> tuple[list[LintIssue], dict[str, str], float]:
    """Collect issues, statuses, and total duration from linter results."""
    all_issues: list[LintIssue] = []
    linter_statuses: dict[str, str] = {}
    total_duration = 0.0
    for lr in linter_results:
        linter_statuses[lr.linter_name] = lr.status
        total_duration += lr.duration_ms
        all_issues.extend(lr.issues)
    return all_issues, linter_statuses, total_duration


def _apply_config_overrides(
    issues: list[LintIssue], config: ProjectConfig,
) -> tuple[list[LintIssue], int]:
    """Apply severity overrides, exemptions, and signal tunings. Returns (filtered, tuned_count)."""
    if config.severity_overrides:
        for issue in issues:
            override = _resolve_severity_override(issue.kind, config.severity_overrides)
            if override:
                issue.severity = override

    if config.exemptions:
        issues = [i for i in issues if not _is_exempted(i, config.exemptions)]

    tuned_count = 0
    if config.project_root:
        from .signal_tunings import filter_tuned_issues

        issues, tuned_count = filter_tuned_issues(issues, config.project_root)

    return issues, tuned_count


def _split_by_severity(issues: list[LintIssue]) -> tuple[list[LintIssue], list[LintIssue], list[LintIssue]]:
    """Split issues into blocking/warning/informational, each sorted by file+line."""
    sort_key = lambda i: (i.file or "", i.line or 0)  # noqa: E731
    blocking = sorted([i for i in issues if i.severity == "blocking"], key=sort_key)
    warnings = sorted([i for i in issues if i.severity == "warning"], key=sort_key)
    informational = sorted([i for i in issues if i.severity == "informational"], key=sort_key)
    return blocking, warnings, informational


def _compute_metrics(
    issues: list[LintIssue],
    blocking: list[LintIssue],
    warnings: list[LintIssue],
    informational: list[LintIssue],
    tuned_count: int,
    linter_statuses: dict[str, str],
) -> dict[str, int]:
    """Compute aggregate metrics dict."""
    return {
        "total_issues": len(issues),
        "blocking_count": len(blocking),
        "warning_count": len(warnings),
        "info_count": len(informational),
        "tuned_count": tuned_count,
        "fixable_count": sum(1 for i in issues if i.fixable),
        "linters_run": sum(1 for s in linter_statuses.values() if s == "ok"),
        "linters_skipped": sum(1 for s in linter_statuses.values() if s in ("skipped", "deferred")),
        "linters_errored": sum(1 for s in linter_statuses.values() if s in ("error", "timeout")),
    }


def aggregate_results(
    linter_results: list[LinterResult],
    config: ProjectConfig,
    tier_name: str = "",
    tier_reason: str = "",
) -> AggregatedResult:
    """Aggregate all linter results into a single structured report."""
    all_issues, linter_statuses, total_duration = _collect_raw_issues(linter_results)

    unique = _deduplicate(all_issues)
    for issue in unique:
        issue.issue_id = issue.compute_issue_id()

    _downgrade_optional_import_findings(unique, config.project_root)
    unique, tuned_count = _apply_config_overrides(unique, config)

    for issue in unique:
        if issue.estimated_effort_minutes is None:
            issue.estimated_effort_minutes = estimate_effort(issue)

    blocking, warnings, informational = _split_by_severity(unique)
    metrics = _compute_metrics(unique, blocking, warnings, informational, tuned_count, linter_statuses)

    project_root = config.project_root
    files_linted = sorted({_normalize_file_path(f, project_root) for i in unique if (f := i.file)})

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


def _downgrade_optional_import_findings(issues: list[LintIssue], project_root: str) -> None:
    """Downgrade findings that reference guarded optional imports.

    Scans each unique source file for ``try/except ImportError`` patterns.
    Any finding whose line or message references a guarded import name is
    downgraded from blocking/warning → informational with evidence attached.
    """
    by_file: dict[str, list[LintIssue]] = {}
    for issue in issues:
        if issue.file:
            by_file.setdefault(issue.file, []).append(issue)

    for filepath, file_issues in by_file.items():
        report = _parse_optional_imports(filepath, project_root)
        if report is None:
            continue
        _apply_optional_import_downgrades(file_issues, report)


def _parse_optional_imports(filepath: str, project_root: str) -> OptionalImportReport | None:
    """Read and parse a file for optional-import patterns."""
    full_path = filepath
    if not os.path.isabs(filepath) and project_root:
        full_path = os.path.join(project_root, filepath)
    try:
        with open(full_path) as f:
            source = f.read()
    except OSError:
        return None
    report = detect_optional_imports(source)
    return report if report.optional_imports else None


def _apply_optional_import_downgrades(
    issues: list[LintIssue], report: OptionalImportReport
) -> None:
    """Downgrade issues that reference guarded imports to informational."""
    fallback = _first_fallback(report)
    for issue in issues:
        if issue.severity == "informational":
            continue
        if is_finding_on_guarded_import(issue.line, issue.kind, issue.message, report):
            issue.severity = "informational"
            issue.evidence["optional_import"] = True
            if fallback:
                issue.evidence["fallback_value"] = fallback


def _first_fallback(report: OptionalImportReport) -> str | None:
    """Return the first fallback value found in the report, if any."""
    for oi in report.optional_imports:
        if oi.fallback_value:
            return oi.fallback_value
    return None


def _resolve_severity_override(kind: str, overrides: dict[str, str]) -> str | None:
    """Match issue kind against severity overrides with prefix support.

    Bandit emits composite kinds like "B603/subprocess_without_shell_equals_true".
    This allows an override key of "B603" to match "B603/..." via prefix.
    Exact matches take priority over prefix matches.
    """
    # Exact match first
    if kind in overrides:
        return overrides[kind]
    # Prefix match: "B603" matches "B603/subprocess_without_shell_equals_true"
    if "/" in kind:
        prefix = kind.split("/", 1)[0]
        if prefix in overrides:
            return overrides[prefix]
    return None


# ── Effort estimation + ROI scoring ────────────────────────────────────

_EFFORT_HEURISTICS: dict[str, float] = {
    "ruff": 2.0,       # auto-fixable style/import issues
    "mypy": 10.0,      # type errors require understanding
    "radon": 15.0,     # complexity reduction = refactoring
    "bandit": 20.0,    # security issues need careful review
    "vulture": 5.0,    # dead code removal
    "structure": 15.0,  # architectural issues
}

_SEVERITY_WEIGHT: dict[str, float] = {
    "blocking": 3.0,
    "warning": 2.0,
    "informational": 1.0,
}


def estimate_effort(issue: LintIssue) -> float:
    """Estimate fix effort in minutes using linter-based heuristics."""
    base = _EFFORT_HEURISTICS.get(issue.linter, 10.0)
    if issue.fixable:
        base = min(base, 2.0)  # auto-fixable = ~2 min regardless of linter
    return base


def compute_roi(issue: LintIssue) -> float:
    """ROI = severity_weight * confidence / estimated_effort.

    Higher ROI = higher value per minute of effort spent.
    """
    weight = _SEVERITY_WEIGHT.get(issue.severity, 1.0)
    effort = issue.estimated_effort_minutes or estimate_effort(issue)
    return round(weight * issue.confidence / max(effort, 0.1), 3)


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
            existing_rank = (
                severity_order.get(existing.severity, 0),
                existing.confidence,
            )
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
