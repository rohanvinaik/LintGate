"""Phase 5: Mesh reporter fingerprint, index, delta, and compact tests.

Verifies:
- Finding fingerprint stability
- Finding index construction
- Finding delta computation
- Compact ControlPlane reporter output

Core formatting tests are in test_mesh_reporter.py.
Hook/report tests are in test_mesh_reporter_extended.py.
"""

from __future__ import annotations

from lintgate.controlplane.reporter import (
    build_finding_index,
    compute_finding_delta,
    compute_finding_fingerprint,
    format_mesh_report_compact,
)
from lintgate.controlplane.types import (
    ChannelResult,
    CoherenceResult,
    MeshResult,
    SupervisionEvent,
)
from lintgate.types import LintIssue

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_mesh(
    channel_results: list[ChannelResult],
    coherence_state: str = "stable",
    coherence_summary: str = "",
) -> MeshResult:
    """Build a MeshResult for testing."""
    return MeshResult(
        event=SupervisionEvent(project_root="/tmp/test"),
        channel_results=channel_results,
        coherence=CoherenceResult(
            state=coherence_state,
            summary=coherence_summary,
        ),
        duration_ms=42.5,
        incomplete_channels=[],
        partial=False,
    )


def _make_issue(
    severity: str = "warning",
    linter: str = "test",
    kind: str = "test_issue",
    message: str = "Test message",
    file: str | None = None,
) -> LintIssue:
    return LintIssue(
        linter=linter,
        kind=kind,
        message=message,
        severity=severity,
        file=file,
    )


# ── Finding Fingerprint ─────────────────────────────────────────────────


class TestFindingFingerprint:
    def test_stable_across_line_changes(self) -> None:
        """Same file+kind+message -> same fingerprint regardless of line."""
        issue_a = _make_issue(
            "blocking",
            linter="ruff",
            kind="F821",
            message="Undefined name 'x'",
            file="foo.py",
        )
        issue_a.line = 10
        issue_b = _make_issue(
            "blocking",
            linter="ruff",
            kind="F821",
            message="Undefined name 'x'",
            file="foo.py",
        )
        issue_b.line = 25
        fp_a = compute_finding_fingerprint(issue_a, "lint")
        fp_b = compute_finding_fingerprint(issue_b, "lint")
        assert fp_a == fp_b

    def test_different_messages_different_fingerprints(self) -> None:
        issue_a = _make_issue(
            "blocking",
            linter="ruff",
            kind="F821",
            message="Undefined name 'x'",
            file="foo.py",
        )
        issue_b = _make_issue(
            "blocking",
            linter="ruff",
            kind="F821",
            message="Undefined name 'y'",
            file="foo.py",
        )
        fp_a = compute_finding_fingerprint(issue_a, "lint")
        fp_b = compute_finding_fingerprint(issue_b, "lint")
        assert fp_a != fp_b

    def test_different_channels_different_fingerprints(self) -> None:
        issue = _make_issue("warning", linter="test", kind="test_fail", message="test_x failed")
        fp_lint = compute_finding_fingerprint(issue, "lint")
        fp_tests = compute_finding_fingerprint(issue, "tests")
        assert fp_lint != fp_tests

    def test_full_path_distinguishes_same_basename(self) -> None:
        """Fingerprint uses full normalized path -- same basename in different dirs differs."""
        issue_a = _make_issue("warning", file="/a/b/foo.py", message="some issue")
        issue_b = _make_issue("warning", file="/c/d/foo.py", message="some issue")
        fp_a = compute_finding_fingerprint(issue_a, "lint")
        fp_b = compute_finding_fingerprint(issue_b, "lint")
        assert fp_a != fp_b

    def test_empty_file_handled(self) -> None:
        issue = _make_issue("warning", message="no file")
        issue.file = None
        fp = compute_finding_fingerprint(issue, "lint")
        assert isinstance(fp, str) and len(fp) == 16


# ── Build Finding Index ──────────────────────────────────────────────────


class TestBuildFindingIndex:
    def test_indexes_findings_from_mesh(self) -> None:
        mesh = _make_mesh(
            [
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="blocking",
                    findings=[
                        _make_issue("blocking", message="Error A", file="a.py"),
                        _make_issue("warning", message="Warn B", file="b.py"),
                    ],
                ),
            ]
        )
        index = build_finding_index(mesh)
        assert len(index) == 2
        for _fp, info in index.items():
            assert "channel" in info
            assert "kind" in info
            assert "severity" in info

    def test_multi_channel_findings(self) -> None:
        mesh = _make_mesh(
            [
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="blocking",
                    findings=[_make_issue("blocking", message="lint error")],
                ),
                ChannelResult(
                    channel="tests",
                    status="fail",
                    severity="warning",
                    findings=[_make_issue("warning", linter="test_ch", message="test failed")],
                ),
            ]
        )
        index = build_finding_index(mesh)
        channels = {info["channel"] for info in index.values()}
        assert "lint" in channels
        assert "tests" in channels

    def test_empty_mesh_empty_index(self) -> None:
        mesh = _make_mesh(
            [
                ChannelResult(channel="lint", status="pass"),
            ]
        )
        index = build_finding_index(mesh)
        assert index == {}

    def test_duplicate_findings_are_counted(self) -> None:
        """Multiple matching findings aggregate with a count field."""
        mesh = _make_mesh(
            [
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="warning",
                    findings=[
                        _make_issue(
                            "warning",
                            kind="E501",
                            message="Line too long",
                            file="foo.py",
                        ),
                        _make_issue(
                            "warning",
                            kind="E501",
                            message="Line too long",
                            file="foo.py",
                        ),
                    ],
                ),
            ]
        )
        index = build_finding_index(mesh)
        assert len(index) == 1
        only = next(iter(index.values()))
        assert only["count"] == 2


# ── Compute Finding Delta ────────────────────────────────────────────────


class TestComputeFindingDelta:
    def _idx(self, fp: str, severity: str = "warning", channel: str = "lint") -> dict:
        return {
            fp: {
                "channel": channel,
                "kind": "test",
                "severity": severity,
                "message": "m",
            }
        }

    def test_new_findings_detected(self) -> None:
        prev = self._idx("fp1")
        curr = {**self._idx("fp1"), **self._idx("fp2")}
        delta = compute_finding_delta(curr, prev)
        assert len(delta["new"]) == 1
        assert delta["new"][0]["fingerprint"] == "fp2"
        assert delta["still_active_count"] == 1

    def test_resolved_findings_detected(self) -> None:
        prev = {**self._idx("fp1"), **self._idx("fp2")}
        curr = self._idx("fp1")
        delta = compute_finding_delta(curr, prev)
        assert delta["resolved_count"] == 1
        assert delta["still_active_count"] == 1

    def test_escalated_severity_detected(self) -> None:
        prev = self._idx("fp1", severity="warning")
        curr = self._idx("fp1", severity="blocking")
        delta = compute_finding_delta(curr, prev)
        assert len(delta["escalated"]) == 1
        assert delta["escalated"][0]["previous_severity"] == "warning"

    def test_still_active_counted(self) -> None:
        prev = {**self._idx("fp1"), **self._idx("fp2"), **self._idx("fp3")}
        curr = {**self._idx("fp1"), **self._idx("fp2"), **self._idx("fp3")}
        delta = compute_finding_delta(curr, prev)
        assert delta["still_active_count"] == 3
        assert len(delta["new"]) == 0
        assert delta["resolved_count"] == 0

    def test_empty_previous_all_new(self) -> None:
        curr = {**self._idx("fp1"), **self._idx("fp2")}
        delta = compute_finding_delta(curr, {})
        assert len(delta["new"]) == 2
        assert delta["resolved_count"] == 0

    def test_empty_current_all_resolved(self) -> None:
        prev = {**self._idx("fp1"), **self._idx("fp2")}
        delta = compute_finding_delta({}, prev)
        assert delta["resolved_count"] == 2
        assert len(delta["new"]) == 0

    def test_count_increase_reported_as_new_delta(self) -> None:
        prev = {
            "fp1": {
                "channel": "lint",
                "kind": "k",
                "severity": "warning",
                "message": "m",
                "count": 1,
            }
        }
        curr = {
            "fp1": {
                "channel": "lint",
                "kind": "k",
                "severity": "warning",
                "message": "m",
                "count": 3,
            }
        }
        delta = compute_finding_delta(curr, prev)
        assert delta["still_active_count"] == 1
        assert len(delta["new"]) == 1
        assert delta["new"][0]["fingerprint"] == "fp1"
        assert delta["new"][0]["count"] == 2


# ── Compact ControlPlane Reporter ────────────────────────────────────────


class TestFormatMeshReportCompact:
    def test_first_run_inline_blocking(self) -> None:
        """First run (no previous) -> inline blocking_issues."""
        mesh = _make_mesh(
            [
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="blocking",
                    findings=[_make_issue("blocking", message="Undefined 'x'", file="foo.py")],
                ),
            ]
        )
        compact = format_mesh_report_compact(mesh)

        assert "run_id" in compact
        assert "blocking_issues" in compact
        assert "delta" not in compact
        assert len(compact["blocking_issues"]) == 1
        assert compact["counts"]["blocking"] == 1

    def test_delta_run_has_delta_section(self) -> None:
        """Delta run -> delta section with new/resolved/still_active."""
        mesh = _make_mesh(
            [
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="blocking",
                    findings=[
                        _make_issue("blocking", message="Error A", file="a.py"),
                        _make_issue("warning", message="Warn B", file="b.py"),
                    ],
                ),
            ]
        )
        previous_index = build_finding_index(
            _make_mesh(
                [
                    ChannelResult(
                        channel="lint",
                        status="fail",
                        severity="warning",
                        findings=[_make_issue("warning", message="Warn B", file="b.py")],
                    ),
                ]
            )
        )

        compact = format_mesh_report_compact(mesh, previous_finding_index=previous_index)

        assert "delta" in compact
        assert "blocking_issues" not in compact
        assert compact["delta"]["still_active_count"] >= 0

    def test_next_actions_always_present(self) -> None:
        mesh = _make_mesh(
            [
                ChannelResult(channel="lint", status="pass"),
            ]
        )
        compact = format_mesh_report_compact(mesh)
        assert "next_actions" in compact
        assert isinstance(compact["next_actions"], list)

    def test_coherence_always_present(self) -> None:
        mesh = _make_mesh(
            [ChannelResult(channel="lint", status="pass")],
            coherence_state="isolated",
            coherence_summary="Lint channel failing",
        )
        compact = format_mesh_report_compact(mesh)
        assert compact["coherence"]["state"] == "isolated"
        assert "summary" in compact["coherence"]

    def test_channels_summary(self) -> None:
        mesh = _make_mesh(
            [
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="blocking",
                    findings=[_make_issue("blocking")],
                ),
                ChannelResult(channel="tests", status="pass"),
                ChannelResult(channel="deps", status="skip"),
            ]
        )
        compact = format_mesh_report_compact(mesh)
        assert "lint" in compact["channels"]
        assert compact["channels"]["tests"] == "pass"
        assert "deps" not in compact["channels"]  # Skip channels excluded

    def test_finding_index_in_output(self) -> None:
        """finding_index is included in compact output (for session storage)."""
        mesh = _make_mesh(
            [
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="warning",
                    findings=[_make_issue("warning", message="test")],
                ),
            ]
        )
        compact = format_mesh_report_compact(mesh)
        assert "finding_index" in compact
        assert len(compact["finding_index"]) == 1
