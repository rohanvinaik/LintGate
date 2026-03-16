"""Mutation-targeted tests for coherence module VALUE survivors."""

from __future__ import annotations

from lintgate.controlplane.coherence import (
    _build_classification_reason,
    _has_actionable_findings,
    _partition_results,
)
from lintgate.controlplane.types import ChannelResult, CoherenceResult

# ── _has_actionable_findings ──────────────────────────────────────────


class TestHasActionableFindings:
    def test_blocking_finding(self):
        from lintgate.types import LintIssue

        cr = ChannelResult(
            channel="lint",
            status="fail",
            findings=[
                LintIssue(linter="ruff", kind="E501", message="line too long", severity="blocking")
            ],
        )
        assert _has_actionable_findings(cr) is True

    def test_warning_finding(self):
        from lintgate.types import LintIssue

        cr = ChannelResult(
            channel="lint",
            status="fail",
            findings=[
                LintIssue(
                    linter="ruff", kind="W291", message="trailing whitespace", severity="warning"
                )
            ],
        )
        assert _has_actionable_findings(cr) is True

    def test_informational_only(self):
        from lintgate.types import LintIssue

        cr = ChannelResult(
            channel="lint",
            status="fail",
            severity="informational",
            findings=[
                LintIssue(linter="ruff", kind="I001", message="unsorted", severity="informational")
            ],
        )
        assert _has_actionable_findings(cr) is False

    def test_no_findings_but_blocking_severity(self):
        cr = ChannelResult(channel="lint", status="fail", severity="blocking")
        assert _has_actionable_findings(cr) is True

    def test_empty_findings_none_severity(self):
        cr = ChannelResult(channel="lint", status="fail", severity="none")
        assert _has_actionable_findings(cr) is False


# ── _partition_results ────────────────────────────────────────────────


class TestPartitionResults:
    def test_basic_partition(self):
        results = [
            ChannelResult(channel="lint", status="fail"),
            ChannelResult(channel="tests", status="pass"),
            ChannelResult(channel="deps", status="skip"),
        ]
        partition = _partition_results(results, severity_weighted=False)
        assert len(partition["failed"]) == 1
        assert len(partition["passed"]) == 1
        assert len(partition["errored"]) == 0
        assert len(partition["enabled"]) == 2  # skip excluded

    def test_error_partitioned(self):
        results = [
            ChannelResult(channel="lint", status="error"),
            ChannelResult(channel="tests", status="timeout"),
        ]
        partition = _partition_results(results, severity_weighted=False)
        assert len(partition["errored"]) == 2

    def test_severity_weighted_demotion(self):
        from lintgate.types import LintIssue

        results = [
            ChannelResult(
                channel="lint",
                status="fail",
                severity="informational",
                findings=[
                    LintIssue(linter="ruff", kind="I001", message="x", severity="informational")
                ],
            ),
        ]
        partition = _partition_results(results, severity_weighted=True)
        # Informational-only fail is demoted to passed
        assert len(partition["failed"]) == 0
        assert len(partition["passed"]) == 1


# ── _build_classification_reason ──────────────────────────────────────


class TestBuildClassificationReason:
    def test_stable(self):
        cr = CoherenceResult(state="stable", silent_channels=["lint", "tests"])
        reason = _build_classification_reason(cr)
        assert "2 channel(s) passed" in reason

    def test_degraded(self):
        cr = CoherenceResult(state="degraded")
        reason = _build_classification_reason(cr)
        assert "errored or timed out" in reason

    def test_isolated(self):
        cr = CoherenceResult(
            state="isolated",
            loud_channels=["lint"],
            silent_channels=["tests", "deps"],
        )
        reason = _build_classification_reason(cr)
        assert "lint" in reason
        assert "2 channel(s) passed" in reason

    def test_coupled(self):
        cr = CoherenceResult(
            state="coupled",
            loud_channels=["lint", "tests"],
        )
        reason = _build_classification_reason(cr)
        assert "2 channels failed" in reason
        assert "overlapping" in reason

    def test_systemic(self):
        cr = CoherenceResult(
            state="systemic",
            loud_channels=["lint", "tests", "deps"],
        )
        reason = _build_classification_reason(cr)
        assert "3 channels failed" in reason
        assert "across domains" in reason
