"""Comprehensive tests for lintgate/agent_reporter.py covering all functions."""

from __future__ import annotations

from typing import Any

from lintgate.agent_reporter import (
    _RECENT_WINDOW,
    _add_blocking_section,
    _add_delta_section,
    _add_fixable_section,
    _add_header,
    _add_linter_status_section,
    _add_pattern_summary,
    _add_warnings_count,
    _add_warnings_section,
    _build_posttooluse_context,
    _compute_delta,
    _short_path,
    format_report,
)
from lintgate.types import AggregatedResult, LintIssue

# ─── Helpers ─────────────────────────────────────────────────────────────


def _make_issue(
    linter: str = "ruff",
    kind: str = "F821",
    message: str = "undefined name 'x'",
    file: str | None = "/src/app.py",
    line: int | None = 10,
    severity: str = "blocking",
    fixable: bool = False,
    fix_description: str | None = None,
    suggestions: list[str] | None = None,
) -> LintIssue:
    return LintIssue(
        linter=linter,
        kind=kind,
        message=message,
        file=file,
        line=line,
        severity=severity,
        fixable=fixable,
        fix_description=fix_description,
        suggestions=suggestions or [],
    )


def _make_result(
    blocking: list[LintIssue] | None = None,
    warnings: list[LintIssue] | None = None,
    informational: list[LintIssue] | None = None,
    metrics: dict[str, Any] | None = None,
    tier_used: str = "tier_2_logic",
    tier_reason: str = "logic change",
    total_duration_ms: float = 500.0,
    files_linted: list[str] | None = None,
    linter_statuses: dict[str, str] | None = None,
) -> AggregatedResult:
    return AggregatedResult(
        blocking=blocking or [],
        warnings=warnings or [],
        informational=informational or [],
        metrics=metrics or {},
        tier_used=tier_used,
        tier_reason=tier_reason,
        total_duration_ms=total_duration_ms,
        files_linted=files_linted or ["/src/app.py"],
        linter_statuses=linter_statuses or {},
    )


# ─── format_report ──────────────────────────────────────────────────────


class TestFormatReport:
    """Tests for the top-level format_report function."""

    def test_empty_when_no_issues(self) -> None:
        """Returns empty dict when total_issues is 0."""
        result = _make_result(metrics={"total_issues": 0})
        assert format_report(result) == {}

    def test_empty_when_total_issues_missing(self) -> None:
        """Returns empty dict when total_issues key is absent."""
        result = _make_result(metrics={})
        assert format_report(result) == {}

    def test_system_message_present_when_issues_exist(self) -> None:
        """Returns systemMessage when there are issues."""
        result = _make_result(
            blocking=[_make_issue()],
            metrics={"total_issues": 1, "blocking_count": 1},
        )
        output = format_report(result)
        assert "systemMessage" in output
        assert "<lint-report" in output["systemMessage"]
        assert "</lint-report>" in output["systemMessage"]

    def test_hook_specific_output_present_when_blocking(self) -> None:
        """hookSpecificOutput is included when there are blocking issues."""
        result = _make_result(
            blocking=[_make_issue()],
            metrics={"total_issues": 1, "blocking_count": 1},
        )
        output = format_report(result)
        assert "hookSpecificOutput" in output
        ctx = output["hookSpecificOutput"]
        assert ctx["hookEventName"] == "PostToolUse"
        assert "blocking_count=1" in ctx["additionalContext"]

    def test_no_hook_specific_output_when_only_warnings(self) -> None:
        """hookSpecificOutput absent when no blocking issues."""
        result = _make_result(
            warnings=[_make_issue(severity="warning")],
            metrics={"total_issues": 1, "warning_count": 1},
        )
        output = format_report(result)
        assert "hookSpecificOutput" not in output

    def test_format_report_with_all_sections(self) -> None:
        """Smoke test: all optional sections supplied."""
        result = _make_result(
            blocking=[_make_issue()],
            warnings=[_make_issue(severity="warning", kind="W123")],
            informational=[_make_issue(severity="informational", kind="I001")],
            metrics={
                "total_issues": 5,
                "blocking_count": 1,
                "warning_count": 1,
                "fixable_count": 2,
                "linters_skipped": 1,
            },
        )
        last_run = {"blocking_count": 0, "total_issues": 3}
        pattern = {
            "alerted_patterns": [
                {
                    "linter": "ruff",
                    "kind": "E501",
                    "count_this_run": 4,
                    "files_this_run": 2,
                    "alert_reason": "recurring_across_runs",
                    "recent_run_count": 3,
                }
            ]
        }
        output = format_report(result, last_run, pattern_report=pattern)
        msg = output["systemMessage"]
        assert "BLOCKING" in msg
        # When blocking exists, warnings are condensed to count
        assert "WARNINGS:" in msg
        assert "fix blocking issues first" in msg
        assert "REGRESSION" in msg
        assert "Recurring:" in msg
        assert "Auto-fixable" in msg

    def test_warnings_expanded_when_no_blocking(self) -> None:
        """When no blocking issues, warnings are shown in full."""
        result = _make_result(
            warnings=[_make_issue(severity="warning", kind=f"W{i}", line=i) for i in range(5)],
            metrics={"total_issues": 5, "warning_count": 5},
        )
        output = format_report(result)
        msg = output["systemMessage"]
        assert "WARNINGS (5):" in msg
        assert "and 2 more warnings" in msg


# ─── _build_posttooluse_context ──────────────────────────────────────────


class TestBuildPosttooluseContext:
    """Tests for _build_posttooluse_context."""

    def test_basic_counts(self) -> None:
        result = _make_result(
            blocking=[_make_issue()],
            warnings=[_make_issue(severity="warning"), _make_issue(severity="warning")],
            informational=[_make_issue(severity="informational")],
        )
        ctx = _build_posttooluse_context(result)
        assert "blocking_count=1" in ctx
        assert "warning_count=2" in ctx
        assert "informational_count=1" in ctx
        assert "top_blocking=" in ctx

    def test_top_blocking_caps_at_three(self) -> None:
        issues = [_make_issue(kind=f"E{i}", line=i) for i in range(5)]
        result = _make_result(blocking=issues)
        ctx = _build_posttooluse_context(result)
        # Only first 3 issues should appear in top_blocking
        assert "E0" in ctx
        assert "E1" in ctx
        assert "E2" in ctx
        assert "E3" not in ctx

    def test_empty_blocking_no_top(self) -> None:
        result = _make_result(blocking=[], warnings=[_make_issue(severity="warning")])
        ctx = _build_posttooluse_context(result)
        assert "top_blocking" not in ctx
        assert "blocking_count=0" in ctx


# ─── _add_header ─────────────────────────────────────────────────────────


class TestAddHeader:
    """Tests for _add_header."""

    def test_basic_header(self) -> None:
        parts: list[str] = []
        result = _make_result(files_linted=["/src/app.py"])
        _add_header(parts, result)
        assert len(parts) == 1
        assert 'tier="tier_2_logic"' in parts[0]
        assert 'reason="logic change"' in parts[0]
        assert "app.py" in parts[0]

    def test_header_with_more_than_three_files(self) -> None:
        parts: list[str] = []
        files = [f"/src/file{i}.py" for i in range(6)]
        result = _make_result(files_linted=files)
        _add_header(parts, result)
        assert "(+3 more)" in parts[0]
        # First 3 shown
        assert "file0.py" in parts[0]
        assert "file1.py" in parts[0]
        assert "file2.py" in parts[0]

    def test_header_with_exactly_three_files(self) -> None:
        parts: list[str] = []
        files = [f"/src/f{i}.py" for i in range(3)]
        result = _make_result(files_linted=files)
        _add_header(parts, result)
        assert "(+" not in parts[0]


# ─── _add_blocking_section ───────────────────────────────────────────────


class TestAddBlockingSection:
    """Tests for _add_blocking_section."""

    def test_empty_blocking_adds_nothing(self) -> None:
        parts: list[str] = []
        _add_blocking_section(parts, [])
        assert parts == []

    def test_single_blocking_singular(self) -> None:
        parts: list[str] = []
        _add_blocking_section(parts, [_make_issue()])
        assert "1 issue -" in parts[0]
        assert "issues" not in parts[0]

    def test_multiple_blocking_plural(self) -> None:
        parts: list[str] = []
        issues = [_make_issue(line=i) for i in range(3)]
        _add_blocking_section(parts, issues)
        assert "3 issues -" in parts[0]

    def test_blocking_capped_at_five(self) -> None:
        parts: list[str] = []
        issues = [_make_issue(line=i) for i in range(8)]
        _add_blocking_section(parts, issues)
        joined = "\n".join(parts)
        assert "and 3 more blocking issues" in joined

    def test_fix_description_shown(self) -> None:
        parts: list[str] = []
        issue = _make_issue(fix_description="Use 'y' instead of 'x'")
        _add_blocking_section(parts, [issue])
        joined = "\n".join(parts)
        assert "Fix: Use 'y' instead of 'x'" in joined

    def test_suggestion_shown_when_no_fix_description(self) -> None:
        parts: list[str] = []
        issue = _make_issue(suggestions=["Try importing the module"])
        _add_blocking_section(parts, [issue])
        joined = "\n".join(parts)
        assert "Suggestion: Try importing the module" in joined

    def test_fix_description_preferred_over_suggestion(self) -> None:
        parts: list[str] = []
        issue = _make_issue(
            fix_description="Auto-fix available",
            suggestions=["Manual fix suggestion"],
        )
        _add_blocking_section(parts, [issue])
        joined = "\n".join(parts)
        assert "Fix: Auto-fix available" in joined
        assert "Suggestion:" not in joined


# ─── _add_warnings_section ───────────────────────────────────────────────


class TestAddWarningsSection:
    """Tests for _add_warnings_section."""

    def test_empty_warnings_adds_nothing(self) -> None:
        parts: list[str] = []
        _add_warnings_section(parts, [])
        assert parts == []

    def test_warnings_capped_at_three(self) -> None:
        parts: list[str] = []
        issues = [_make_issue(severity="warning", kind=f"W{i}", line=i) for i in range(6)]
        _add_warnings_section(parts, issues)
        joined = "\n".join(parts)
        assert "WARNINGS (6):" in joined
        assert "and 3 more warnings" in joined

    def test_warnings_count_header(self) -> None:
        parts: list[str] = []
        _add_warnings_section(parts, [_make_issue(severity="warning")])
        assert "WARNINGS (1):" in parts[0]


# ─── _add_warnings_count ────────────────────────────────────────────────


class TestAddWarningsCount:
    """Tests for _add_warnings_count (condensed warnings when blocking exists)."""

    def test_empty_warnings_adds_nothing(self) -> None:
        parts: list[str] = []
        _add_warnings_count(parts, [])
        assert parts == []

    def test_shows_count_with_focus_hint(self) -> None:
        parts: list[str] = []
        issues = [_make_issue(severity="warning", line=i) for i in range(22)]
        _add_warnings_count(parts, issues)
        assert parts == ["WARNINGS: 22 (fix blocking issues first)"]


# ─── _add_pattern_summary ──────────────────────────────────────────────


class TestAddPatternSummary:
    """Tests for _add_pattern_summary (condensed pattern alerts)."""

    def test_none_pattern_adds_nothing(self) -> None:
        parts: list[str] = []
        _add_pattern_summary(parts, None)
        assert parts == []

    def test_empty_alerts_adds_nothing(self) -> None:
        parts: list[str] = []
        _add_pattern_summary(parts, {"alerted_patterns": []})
        assert parts == []

    def test_single_run_volume_ignored(self) -> None:
        parts: list[str] = []
        _add_pattern_summary(parts, {"alerted_patterns": [
            {"alert_reason": "single_run_volume", "kind": "E501", "recent_run_count": 0},
        ]})
        assert parts == []

    def test_recurring_condensed_to_one_line(self) -> None:
        parts: list[str] = []
        _add_pattern_summary(parts, {"alerted_patterns": [
            {"alert_reason": "recurring_across_runs", "kind": "cognitive-complexity", "recent_run_count": 5},
            {"alert_reason": "recurring_across_runs", "kind": "too-many-locals", "recent_run_count": 3},
        ]})
        assert len(parts) == 1
        assert "Recurring:" in parts[0]
        assert "cognitive-complexity (5/5 runs)" in parts[0]
        assert "too-many-locals (3/5 runs)" in parts[0]


# ─── _add_delta_section ──────────────────────────────────────────────────


class TestAddDeltaSection:
    """Tests for _add_delta_section."""

    def test_no_last_run_adds_nothing(self) -> None:
        parts: list[str] = []
        result = _make_result(metrics={"blocking_count": 1, "total_issues": 1})
        _add_delta_section(parts, result, None)
        assert parts == []

    def test_regression_detected(self) -> None:
        parts: list[str] = []
        result = _make_result(metrics={"blocking_count": 3, "total_issues": 5})
        last_run = {"blocking_count": 1, "total_issues": 3}
        _add_delta_section(parts, result, last_run)
        joined = "\n".join(parts)
        assert "REGRESSION: +2 blocking issues" in joined

    def test_regression_singular(self) -> None:
        parts: list[str] = []
        result = _make_result(metrics={"blocking_count": 2, "total_issues": 3})
        last_run = {"blocking_count": 1, "total_issues": 3}
        _add_delta_section(parts, result, last_run)
        joined = "\n".join(parts)
        assert "REGRESSION: +1 blocking issue" in joined
        assert "issues" not in joined.split("REGRESSION")[1].split("\n")[0]

    def test_improvement_detected(self) -> None:
        parts: list[str] = []
        result = _make_result(metrics={"blocking_count": 0, "total_issues": 1})
        last_run = {"blocking_count": 2, "total_issues": 4}
        _add_delta_section(parts, result, last_run)
        joined = "\n".join(parts)
        assert "IMPROVEMENT: 2 fewer blocking issues" in joined

    def test_improvement_singular(self) -> None:
        parts: list[str] = []
        result = _make_result(metrics={"blocking_count": 0, "total_issues": 1})
        last_run = {"blocking_count": 1, "total_issues": 2}
        _add_delta_section(parts, result, last_run)
        joined = "\n".join(parts)
        assert "IMPROVEMENT: 1 fewer blocking issue" in joined
        # Ensure singular (no trailing 's')
        improvement_line = [line for line in joined.split("\n") if "IMPROVEMENT" in line][0]
        assert improvement_line.endswith("issue")

    def test_total_delta_shown_when_different_from_blocking(self) -> None:
        parts: list[str] = []
        result = _make_result(metrics={"blocking_count": 1, "total_issues": 10})
        last_run = {"blocking_count": 1, "total_issues": 5}
        _add_delta_section(parts, result, last_run)
        joined = "\n".join(parts)
        # blocking_delta == 0, so no REGRESSION/IMPROVEMENT
        assert "REGRESSION" not in joined
        assert "Total: +5 issues vs last run" in joined

    def test_total_delta_suppressed_when_same_as_blocking(self) -> None:
        parts: list[str] = []
        result = _make_result(metrics={"blocking_count": 2, "total_issues": 2})
        last_run = {"blocking_count": 0, "total_issues": 0}
        _add_delta_section(parts, result, last_run)
        joined = "\n".join(parts)
        assert "Total:" not in joined

    def test_no_change_adds_nothing(self) -> None:
        parts: list[str] = []
        result = _make_result(metrics={"blocking_count": 1, "total_issues": 3})
        last_run = {"blocking_count": 1, "total_issues": 3}
        _add_delta_section(parts, result, last_run)
        assert parts == []


# ─── _add_fixable_section ────────────────────────────────────────────────


class TestAddFixableSection:
    """Tests for _add_fixable_section."""

    def test_no_fixable_adds_nothing(self) -> None:
        parts: list[str] = []
        _add_fixable_section(parts, {"fixable_count": 0})
        assert parts == []

    def test_fixable_count_missing_adds_nothing(self) -> None:
        parts: list[str] = []
        _add_fixable_section(parts, {})
        assert parts == []

    def test_fixable_singular(self) -> None:
        parts: list[str] = []
        _add_fixable_section(parts, {"fixable_count": 1})
        assert "1 issue (run: ruff check --fix)" in parts[0]

    def test_fixable_plural(self) -> None:
        parts: list[str] = []
        _add_fixable_section(parts, {"fixable_count": 5})
        assert "5 issues (run: ruff check --fix)" in parts[0]


# ─── _add_linter_status_section ──────────────────────────────────────────


class TestAddLinterStatusSection:
    """Tests for _add_linter_status_section."""

    def test_nothing_interesting_adds_nothing(self) -> None:
        parts: list[str] = []
        result = _make_result(metrics={"linters_skipped": 0, "linters_errored": 0})
        _add_linter_status_section(parts, result)
        assert parts == []

    def test_skipped_linters_shown(self) -> None:
        parts: list[str] = []
        result = _make_result(
            metrics={"linters_skipped": 2, "linters_errored": 0, "linters_run": 3}
        )
        _add_linter_status_section(parts, result)
        assert "2 skipped" in parts[0]
        assert "3 ran" in parts[0]

    def test_errored_linters_shown(self) -> None:
        parts: list[str] = []
        result = _make_result(
            metrics={"linters_skipped": 0, "linters_errored": 1, "linters_run": 4}
        )
        _add_linter_status_section(parts, result)
        assert "1 errored" in parts[0]

    def test_both_skipped_and_errored(self) -> None:
        parts: list[str] = []
        result = _make_result(
            metrics={"linters_skipped": 1, "linters_errored": 2, "linters_run": 5}
        )
        _add_linter_status_section(parts, result)
        text = parts[0]
        assert "1 skipped" in text
        assert "2 errored" in text

    def test_duration_shown_when_above_threshold(self) -> None:
        parts: list[str] = []
        result = _make_result(
            total_duration_ms=3000.0,
            metrics={"linters_skipped": 0, "linters_errored": 0},
        )
        _add_linter_status_section(parts, result)
        assert len(parts) == 1
        assert "Duration: 3000ms" in parts[0]

    def test_duration_hidden_when_below_threshold(self) -> None:
        parts: list[str] = []
        result = _make_result(
            total_duration_ms=1500.0,
            metrics={"linters_skipped": 0, "linters_errored": 0},
        )
        _add_linter_status_section(parts, result)
        assert parts == []


# ─── _short_path ─────────────────────────────────────────────────────────


class TestShortPath:
    """Tests for _short_path."""

    def test_extracts_basename(self) -> None:
        assert _short_path("/a/b/c/app.py") == "app.py"

    def test_bare_filename(self) -> None:
        assert _short_path("app.py") == "app.py"

    def test_empty_string(self) -> None:
        assert _short_path("") == ""


# ─── _compute_delta ──────────────────────────────────────────────────────


class TestComputeDelta:
    """Tests for _compute_delta."""

    def test_computes_all_deltas(self) -> None:
        result = _make_result(metrics={"blocking_count": 5, "warning_count": 3, "total_issues": 10})
        last_run = {"blocking_count": 2, "warning_count": 1, "total_issues": 4}
        delta = _compute_delta(result, last_run)
        assert delta["blocking_delta"] == 3
        assert delta["warning_delta"] == 2
        assert delta["total_delta"] == 6

    def test_negative_deltas(self) -> None:
        result = _make_result(metrics={"blocking_count": 0, "warning_count": 0, "total_issues": 0})
        last_run = {"blocking_count": 5, "warning_count": 3, "total_issues": 10}
        delta = _compute_delta(result, last_run)
        assert delta["blocking_delta"] == -5
        assert delta["warning_delta"] == -3
        assert delta["total_delta"] == -10

    def test_missing_keys_default_to_zero(self) -> None:
        result = _make_result(metrics={})
        last_run: dict[str, Any] = {}
        delta = _compute_delta(result, last_run)
        assert delta["blocking_delta"] == 0
        assert delta["warning_delta"] == 0
        assert delta["total_delta"] == 0


# ─── _RECENT_WINDOW constant ────────────────────────────────────────────


class TestConstants:
    """Tests for module-level constants."""

    def test_recent_window_value(self) -> None:
        assert _RECENT_WINDOW == 5
