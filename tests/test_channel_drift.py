"""Tests for lintgate/channels/_test_channel_drift.py.

Covers failure classification, drift summary emission,
test result collection, and classify_test_failure logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from lintgate.channels._test_channel_drift import (
    _classify_failure,
    _classify_test_failure,
    _collect_test_findings,
    _emit_drift_summary,
)

if TYPE_CHECKING:
    from lintgate.types import LintIssue


def _make_failure(file=None, message="test failed", line=None, test_name=None):
    f = MagicMock()
    f.file = file
    f.message = message
    f.line = line
    f.test_name = test_name
    return f


def _make_test_result(failures=None, timed_out=False):
    r = MagicMock()
    r.failures = failures or []
    r.timed_out = timed_out
    return r


# ── _classify_test_failure ───────────────────────────────────────


class TestClassifyTestFailure:
    def test_modified_file_is_drift(self):
        result = _classify_test_failure(
            "tests/test_foo.py",
            modified_files={"tests/test_foo.py"},
            untracked_files=set(),
            project_root="/project",
        )
        assert result == "test_drift"

    def test_untracked_file_is_drift(self):
        result = _classify_test_failure(
            "tests/test_foo.py",
            modified_files=set(),
            untracked_files={"tests/test_foo.py"},
            project_root="/project",
        )
        assert result == "test_drift"

    def test_committed_file_is_regression(self):
        result = _classify_test_failure(
            "tests/test_foo.py",
            modified_files=set(),
            untracked_files=set(),
            project_root="/project",
        )
        assert result == "regression"

    def test_absolute_path_converted_to_relative(self):
        result = _classify_test_failure(
            "/project/tests/test_foo.py",
            modified_files={"tests/test_foo.py"},
            untracked_files=set(),
            project_root="/project",
        )
        assert result == "test_drift"


# ── _classify_failure ────────────────────────────────────────────


class TestClassifyFailure:
    def test_no_drift_context(self):
        failure = _make_failure(file="test_foo.py")
        assert _classify_failure(failure, None, "/project") == "unknown"

    def test_no_file(self):
        failure = _make_failure(file=None)
        ctx: dict[str, set[str]] = {"modified": set(), "untracked": set()}
        assert _classify_failure(failure, ctx, "/project") == "unknown"

    def test_with_context_and_file(self):
        failure = _make_failure(file="tests/test_foo.py")
        ctx = {"modified": {"tests/test_foo.py"}, "untracked": set()}
        assert _classify_failure(failure, ctx, "/project") == "test_drift"


# ── _emit_drift_summary ─────────────────────────────────────────


class TestEmitDriftSummary:
    def test_no_counts_no_finding(self):
        findings: list[LintIssue] = []
        _emit_drift_summary(0, 0, findings)
        assert findings == []

    def test_drift_only(self):
        findings: list[LintIssue] = []
        _emit_drift_summary(3, 0, findings)
        assert len(findings) == 1
        assert "3" in findings[0].message
        assert "drift" in findings[0].message
        assert findings[0].evidence["drift_count"] == 3

    def test_regression_only(self):
        findings: list[LintIssue] = []
        _emit_drift_summary(0, 2, findings)
        assert len(findings) == 1
        assert "regression" in findings[0].message
        assert findings[0].evidence["regression_count"] == 2

    def test_both_drift_and_regression(self):
        findings: list[LintIssue] = []
        _emit_drift_summary(1, 2, findings)
        assert len(findings) == 1
        assert "drift" in findings[0].message
        assert "regression" in findings[0].message


# ── _collect_test_findings ───────────────────────────────────────


class TestCollectTestFindings:
    def test_timeout_creates_finding(self):
        result = _make_test_result(timed_out=True)
        findings: list[LintIssue] = []
        _collect_test_findings(result, 5000, findings)
        assert len(findings) == 1
        assert findings[0].kind == "test_timeout"
        assert "5000" in findings[0].message

    def test_no_failures_no_findings(self):
        result = _make_test_result()
        findings: list[LintIssue] = []
        _collect_test_findings(result, 5000, findings)
        assert findings == []

    def test_failure_creates_finding(self):
        failure = _make_failure(file="test_foo.py", message="assert failed")
        result = _make_test_result(failures=[failure])
        findings: list[LintIssue] = []
        _collect_test_findings(result, 5000, findings)
        # At least one test_failure finding
        test_failures = [f for f in findings if f.kind == "test_failure"]
        assert len(test_failures) == 1
        assert test_failures[0].message == "assert failed"
