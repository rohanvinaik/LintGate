"""Phase 5: Format lint results for LLM agent consumption.

Produces JSON for the PostToolUse hook's systemMessage field.
The output is designed to be concise, actionable, and parseable
by the LLM agent without ANSI color scraping.

Key decisions:
- Empty {} when no issues (silent success, zero noise)
- Blocking issues capped at 5 (most important first)
- Warnings capped at 3 (avoid flooding)
- Informational shown as count only
- Delta from last run when available
- Auto-fixable count as actionable suggestion
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .types import AggregatedResult, LintIssue


def format_report(
    result: AggregatedResult,
    last_run: dict[str, Any] | None = None,
    recurrence_summary: dict[str, Any] | None = None,  # kept for API compat
    pattern_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Format aggregated results as JSON for Claude Code systemMessage.

    Args:
        result: Aggregated lint results (from results_aggregator.py)
        last_run: Previous run metrics for delta computation (from state.py)
        recurrence_summary: Repeated-issue summary from state.update_issue_memory()
        pattern_report: Categorical pattern alerts from pattern_bank.update_pattern_bank()

    Returns:
        JSON dict with systemMessage key. Empty dict {} if no issues.
    """
    # Quick exit: nothing to report
    if result.metrics.get("total_issues", 0) == 0:
        return {}

    parts: list[str] = []

    _add_header(parts, result)
    _add_blocking_section(parts, result.blocking)
    # When blocking issues exist, show only a count for warnings to keep
    # the model focused on what must be fixed NOW.  When nothing is blocking,
    # expand warnings so the model has something actionable.
    if result.blocking:
        _add_warnings_count(parts, result.warnings)
    else:
        _add_warnings_section(parts, result.warnings)
    _add_delta_section(parts, result, last_run)
    _add_fixable_section(parts, result.metrics)
    # Condense pattern alerts to a single line instead of verbose multi-line blocks
    _add_pattern_summary(parts, pattern_report)
    _add_linter_status_section(parts, result)
    parts.append("</lint-report>")

    message = "\n".join(parts)
    output: dict[str, Any] = {"systemMessage": message}

    # Claude PostToolUse hook schema allows optional additionalContext only.
    if result.blocking:
        output["hookSpecificOutput"] = {
            "hookEventName": "PostToolUse",
            "additionalContext": _build_posttooluse_context(result),
        }

    return output


def _build_posttooluse_context(result: AggregatedResult) -> str:
    """Build compact additional context for PostToolUse schema."""
    blocking = len(result.blocking)
    warnings = len(result.warnings)
    informational = len(result.informational)
    top = ", ".join(
        f"{issue.linter}/{issue.kind}:{issue.short_location()}" for issue in result.blocking[:3]
    )
    parts = [
        f"blocking_count={blocking}",
        f"warning_count={warnings}",
        f"informational_count={informational}",
    ]
    if top:
        parts.append(f"top_blocking={top}")
    return "; ".join(parts)


# ─── Section formatters ─────────────────────────────────────────────────


def _add_header(parts: list[str], result: AggregatedResult) -> None:
    """Add the XML header with tier and file info."""
    files_desc = ", ".join(_short_path(f) for f in result.files_linted[:3])
    if len(result.files_linted) > 3:
        files_desc += f" (+{len(result.files_linted) - 3} more)"

    parts.append(
        f'<lint-report tier="{result.tier_used}" '
        f'reason="{result.tier_reason}" '
        f'files="{files_desc}">'
    )


def _add_blocking_section(parts: list[str], blocking: list[LintIssue]) -> None:
    """Add blocking issues (must fix) — capped at 5."""
    if not blocking:
        return

    count = len(blocking)
    parts.append(f"BLOCKING ({count} issue{'s' if count != 1 else ''} - must fix):")
    for issue in blocking[:5]:
        parts.append(f"  [{issue.linter}/{issue.kind}] {issue.short_location()}: {issue.message}")
        if issue.fix_description:
            parts.append(f"    Fix: {issue.fix_description}")
        elif issue.suggestions:
            parts.append(f"    Suggestion: {issue.suggestions[0]}")
    if count > 5:
        parts.append(f"  ... and {count - 5} more blocking issues")


def _add_warnings_section(parts: list[str], warnings: list[LintIssue]) -> None:
    """Add warnings — capped at 3."""
    if not warnings:
        return

    count = len(warnings)
    parts.append(f"WARNINGS ({count}):")
    for issue in warnings[:3]:
        parts.append(f"  [{issue.linter}/{issue.kind}] {issue.short_location()}: {issue.message}")
    if count > 3:
        parts.append(f"  ... and {count - 3} more warnings")


def _add_warnings_count(parts: list[str], warnings: list[LintIssue]) -> None:
    """Add warnings as a single count line when blocking issues need focus."""
    if not warnings:
        return
    parts.append(f"WARNINGS: {len(warnings)} (fix blocking issues first)")


def _add_pattern_summary(parts: list[str], pattern_report: dict[str, Any] | None) -> None:
    """Condense pattern alerts to a single line instead of verbose multi-line blocks."""
    if not pattern_report:
        return
    alerts = pattern_report.get("alerted_patterns", [])
    recurring = [
        a for a in alerts if a.get("alert_reason") == "recurring_across_runs"
    ]
    if not recurring:
        return
    tags = [
        f"{a.get('kind', '?')} ({a.get('recent_run_count', 0)}/{_RECENT_WINDOW} runs)"
        for a in recurring[:3]
    ]
    parts.append(f"Recurring: {', '.join(tags)}")


def _add_delta_section(
    parts: list[str],
    result: AggregatedResult,
    last_run: dict[str, Any] | None,
) -> None:
    """Add delta from last run (regression/improvement tracking)."""
    if not last_run:
        return

    delta = _compute_delta(result, last_run)

    if delta["blocking_delta"] > 0:
        n = delta["blocking_delta"]
        parts.append(f"REGRESSION: +{n} blocking issue{'s' if n != 1 else ''} vs last run")
    elif delta["blocking_delta"] < 0:
        n = abs(delta["blocking_delta"])
        parts.append(f"IMPROVEMENT: {n} fewer blocking issue{'s' if n != 1 else ''}")

    if delta["total_delta"] != 0 and delta["total_delta"] != delta["blocking_delta"]:
        parts.append(f"Total: {delta['total_delta']:+d} issues vs last run")


def _add_fixable_section(parts: list[str], metrics: dict[str, Any]) -> None:
    """Add auto-fixable count as actionable suggestion."""
    fixable = metrics.get("fixable_count", 0)
    if fixable > 0:
        parts.append(
            f"Auto-fixable: {fixable} issue{'s' if fixable != 1 else ''} (run: ruff check --fix)"
        )


_RECENT_WINDOW = 5  # Mirror the constant from pattern_bank.py


def _add_linter_status_section(parts: list[str], result: AggregatedResult) -> None:
    """Add linter status summary and duration (only if interesting)."""
    skipped = result.metrics.get("linters_skipped", 0)
    errored = result.metrics.get("linters_errored", 0)

    if skipped > 0 or errored > 0:
        status_parts = []
        if skipped:
            status_parts.append(f"{skipped} skipped")
        if errored:
            status_parts.append(f"{errored} errored")
        parts.append(
            f"Linters: {result.metrics.get('linters_run', 0)} ran, {', '.join(status_parts)}"
        )

    if result.total_duration_ms > 2000:
        parts.append(f"Duration: {result.total_duration_ms:.0f}ms")


# ─── Helpers ────────────────────────────────────────────────────────────


def _short_path(filepath: str) -> str:
    """Shorten a file path for display."""
    return os.path.basename(filepath)


def _compute_delta(
    current: AggregatedResult,
    last_run: dict[str, Any],
) -> dict[str, int]:
    """Compute the delta between current and previous run."""
    return {
        "blocking_delta": (
            current.metrics.get("blocking_count", 0) - last_run.get("blocking_count", 0)
        ),
        "warning_delta": (
            current.metrics.get("warning_count", 0) - last_run.get("warning_count", 0)
        ),
        "total_delta": (current.metrics.get("total_issues", 0) - last_run.get("total_issues", 0)),
    }
