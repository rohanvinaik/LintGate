"""Tests for lintgate/lint_delta.py — incremental delta reporting (#196)."""

from __future__ import annotations

from lintgate.lint_delta import build_lint_finding_index, compute_lint_delta
from lintgate.types import AggregatedResult, LintIssue


def _issue(kind: str, msg: str, file: str = "mod.py", line: int = 10, severity: str = "warning"):
    return LintIssue(linter="ruff", kind=kind, message=msg, file=file, line=line, severity=severity)


class TestBuildLintFindingIndex:
    def test_empty_result(self):
        agg = AggregatedResult()
        index = build_lint_finding_index(agg)
        assert index == {}

    def test_single_issue(self):
        agg = AggregatedResult(warnings=[_issue("E501", "Line too long")])
        index = build_lint_finding_index(agg)
        assert len(index) == 1
        entry = next(iter(index.values()))
        assert entry["kind"] == "E501"
        assert entry["severity"] == "warning"
        assert entry["count"] == 1

    def test_multiple_severities(self):
        agg = AggregatedResult(
            blocking=[_issue("F821", "Undefined name", severity="blocking")],
            warnings=[_issue("E501", "Line too long")],
            informational=[_issue("I001", "Import order", severity="informational")],
        )
        index = build_lint_finding_index(agg)
        assert len(index) == 3
        severities = {v["severity"] for v in index.values()}
        assert severities == {"blocking", "warning", "informational"}

    def test_duplicate_fingerprints_counted(self):
        issue1 = _issue("E501", "Line too long", file="a.py")
        issue2 = _issue("E501", "Line too long", file="a.py", line=20)
        agg = AggregatedResult(warnings=[issue1, issue2])
        index = build_lint_finding_index(agg)
        # Same kind+file+message → same fingerprint (line excluded)
        assert len(index) == 1
        assert next(iter(index.values()))["count"] == 2

    def test_different_files_different_fingerprints(self):
        issue1 = _issue("E501", "Line too long", file="a.py")
        issue2 = _issue("E501", "Line too long", file="b.py")
        agg = AggregatedResult(warnings=[issue1, issue2])
        index = build_lint_finding_index(agg)
        assert len(index) == 2


class TestComputeLintDelta:
    def test_no_previous_findings(self):
        agg = AggregatedResult(warnings=[_issue("E501", "Line too long")])
        delta = compute_lint_delta(agg, {})
        assert len(delta["new"]) == 1
        assert delta["resolved_count"] == 0
        assert delta["still_active_count"] == 0
        assert "1 new" in delta["summary"]

    def test_all_resolved(self):
        prev_agg = AggregatedResult(warnings=[_issue("E501", "Line too long")])
        prev_index = build_lint_finding_index(prev_agg)

        curr_agg = AggregatedResult()  # empty — all fixed
        delta = compute_lint_delta(curr_agg, prev_index)
        assert delta["resolved_count"] == 1
        assert len(delta["new"]) == 0
        assert "1 resolved" in delta["summary"]

    def test_some_resolved_some_new(self):
        prev_agg = AggregatedResult(
            warnings=[
                _issue("E501", "Line too long", file="a.py"),
                _issue("F841", "Unused var", file="a.py"),
            ]
        )
        prev_index = build_lint_finding_index(prev_agg)

        curr_agg = AggregatedResult(
            warnings=[
                _issue("E501", "Line too long", file="a.py"),  # still present
                _issue("SIM102", "Nested if", file="b.py"),  # new
            ]
        )
        delta = compute_lint_delta(curr_agg, prev_index)
        assert delta["resolved_count"] == 1  # F841 resolved
        assert len(delta["new"]) == 1  # SIM102 new
        assert delta["still_active_count"] == 1  # E501 remains

    def test_no_change(self):
        agg = AggregatedResult(warnings=[_issue("E501", "Line too long")])
        index = build_lint_finding_index(agg)
        delta = compute_lint_delta(agg, index)
        assert delta["resolved_count"] == 0
        assert len(delta["new"]) == 0
        assert delta["still_active_count"] == 1
        assert delta["summary"] == "0 resolved, 0 new, 1 remaining"

    def test_summary_format(self):
        prev_agg = AggregatedResult(
            warnings=[
                _issue("E501", "Line too long"),
                _issue("F841", "Unused var"),
                _issue("SIM102", "Nested if"),
            ]
        )
        prev_index = build_lint_finding_index(prev_agg)

        curr_agg = AggregatedResult(warnings=[_issue("E501", "Line too long")])
        delta = compute_lint_delta(curr_agg, prev_index)
        assert delta["summary"] == "2 resolved, 0 new, 1 remaining"
