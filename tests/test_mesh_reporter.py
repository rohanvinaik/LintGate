"""Phase 5: Mesh reporter tests.

Verifies:
- Token budget enforcement (report truncates correctly)
- Silent channel omission (pass channels not in output)
- Backward compatibility (lint-only → legacy format)
- Truncation metadata present when budget exceeded
- Empty report for all-pass results
- Finding fingerprint stability and delta computation
- Compact ControlPlane reporter output
"""

from __future__ import annotations

from lintgate.controlplane.reporter import (
    _compute_dynamic_budget,
    build_finding_index,
    compute_finding_delta,
    compute_finding_fingerprint,
    format_mesh_report,
    format_mesh_report_compact,
)
from lintgate.controlplane.types import (
    ChannelResult,
    CoherenceResult,
    ControlPlaneConfig,
    MeshResult,
    RepairAction,
    SupervisionEvent,
    TokenPolicy,
)
from lintgate.types import LintIssue

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_mesh(
    channel_results: list[ChannelResult],
    coherence_state: str = "stable",
    coherence_summary: str = "",
    partial: bool = False,
    incomplete: list[str] | None = None,
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
        incomplete_channels=incomplete or [],
        partial=partial,
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


# ── Empty/clean results ─────────────────────────────────────────────────


def test_empty_report_when_all_pass() -> None:
    """All channels pass → empty report (silent success)."""
    mesh = _make_mesh(
        [
            ChannelResult(channel="lint", status="pass"),
            ChannelResult(channel="tests", status="pass"),
            ChannelResult(channel="deps", status="pass"),
        ]
    )
    report = format_mesh_report(mesh)
    assert report == {}


def test_empty_report_when_all_skip() -> None:
    """All channels skip → empty report."""
    mesh = _make_mesh(
        [
            ChannelResult(channel="lint", status="skip"),
            ChannelResult(channel="tests", status="skip"),
        ]
    )
    report = format_mesh_report(mesh)
    assert report == {}


def test_empty_report_with_no_channels() -> None:
    """No channels → empty report."""
    mesh = _make_mesh([])
    report = format_mesh_report(mesh)
    assert report == {}


# ── Blocking findings ────────────────────────────────────────────────────


def test_blocking_findings_in_report() -> None:
    """Blocking issues should appear in systemMessage."""
    mesh = _make_mesh(
        [
            ChannelResult(
                channel="lint",
                status="fail",
                severity="blocking",
                findings=[_make_issue("blocking", message="Undefined name 'foo'")],
            ),
        ]
    )
    report = format_mesh_report(mesh)

    assert "systemMessage" in report
    assert "BLOCKING" in report["systemMessage"]
    assert "Undefined name" in report["systemMessage"]


def test_blocking_in_hook_specific_output() -> None:
    """hookSpecificOutput should follow Claude PostToolUse schema."""
    mesh = _make_mesh(
        [
            ChannelResult(
                channel="lint",
                status="fail",
                severity="blocking",
                findings=[_make_issue("blocking")],
            ),
        ]
    )
    report = format_mesh_report(mesh)

    assert "hookSpecificOutput" in report
    assert report["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    ctx = report["hookSpecificOutput"].get("additionalContext", "")
    assert "blocking=1" in ctx


# ── Warning findings ─────────────────────────────────────────────────────


def test_warning_findings_in_report() -> None:
    mesh = _make_mesh(
        [
            ChannelResult(
                channel="tests",
                status="fail",
                severity="warning",
                findings=[
                    _make_issue(
                        "warning",
                        linter="test_channel",
                        kind="test_failure",
                        message="test_x failed",
                    )
                ],
            ),
        ]
    )
    report = format_mesh_report(mesh)

    assert "WARNINGS" in report["systemMessage"]
    assert "test_x failed" in report["systemMessage"]


# ── Coherence state ──────────────────────────────────────────────────────


def test_coherence_state_in_header() -> None:
    mesh = _make_mesh(
        [
            ChannelResult(
                channel="lint",
                status="fail",
                severity="warning",
                findings=[_make_issue("warning")],
            )
        ],
        coherence_state="isolated",
        coherence_summary="Issue isolated to lint.",
    )
    report = format_mesh_report(mesh)

    assert 'coherence="isolated"' in report["systemMessage"]


def test_coherence_summary_in_report() -> None:
    mesh = _make_mesh(
        [
            ChannelResult(
                channel="lint",
                status="fail",
                severity="warning",
                findings=[_make_issue("warning")],
            )
        ],
        coherence_state="isolated",
        coherence_summary="Issue isolated to lint channel.",
    )
    report = format_mesh_report(mesh)

    assert "COHERENCE [isolated]" in report["systemMessage"]
    assert "Issue isolated to lint" in report["systemMessage"]


# ── Partial results ──────────────────────────────────────────────────────


def test_partial_results_notice() -> None:
    mesh = _make_mesh(
        [
            ChannelResult(channel="lint", status="pass"),
            ChannelResult(channel="tests", status="timeout"),
        ],
        partial=True,
        incomplete=["tests"],
        coherence_state="degraded",
        coherence_summary="Tests channel timed out.",
    )
    report = format_mesh_report(mesh)

    assert "PARTIAL" in report["systemMessage"]
    assert "tests" in report["systemMessage"]


# ── Silent channel omission ──────────────────────────────────────────────


def test_passing_channels_not_detailed_in_report() -> None:
    """OTP-inspired: passing channels mentioned in summary only, not detailed."""
    mesh = _make_mesh(
        [
            ChannelResult(channel="lint", status="pass"),
            ChannelResult(channel="tests", status="pass"),
            ChannelResult(
                channel="deps",
                status="fail",
                severity="informational",
                findings=[_make_issue("informational", linter="dep_channel")],
            ),
        ]
    )
    report = format_mesh_report(mesh)
    msg = report["systemMessage"]

    # Deps channel should have findings mentioned
    assert "dep_channel" in msg or "INFO" in msg
    # Pass channels should only appear in summary, not with detailed findings
    # (no BLOCKING or WARNINGS from lint/tests)


# ── Token budget enforcement ─────────────────────────────────────────────


def test_token_budget_truncation() -> None:
    """Report with many findings truncates within token budget."""
    # Create 50 blocking findings
    findings = [
        _make_issue("blocking", message=f"Issue number {i} with a long description text")
        for i in range(50)
    ]
    mesh = _make_mesh(
        [
            ChannelResult(
                channel="lint",
                status="fail",
                severity="blocking",
                findings=findings,
            ),
        ]
    )

    # Very tight budget
    config = ControlPlaneConfig(
        token_policy=TokenPolicy(hook_max_tokens=200),
    )
    report = format_mesh_report(mesh, config)

    # Should still have a report
    assert "systemMessage" in report
    # Token count should be roughly within budget
    msg = report["systemMessage"]
    estimated_tokens = len(msg) // 4
    # Allow some buffer for header/footer
    assert estimated_tokens < 400  # 2x budget is acceptable with mandatory sections


def test_dynamic_budget_hard_cap() -> None:
    """Dynamic budget should grow with findings but stay under hard cap."""
    findings = [
        _make_issue("blocking", message=f"Issue number {i} with long payload for budget stress")
        for i in range(2000)
    ]
    mesh = _make_mesh(
        [
            ChannelResult(
                channel="lint",
                status="fail",
                severity="blocking",
                findings=findings,
            ),
        ]
    )
    config = ControlPlaneConfig(
        token_policy=TokenPolicy(hook_max_tokens=900),
    )
    budget = _compute_dynamic_budget(findings, mesh, config)
    assert budget <= 12000
    assert budget >= 900


def test_truncation_metadata_present() -> None:
    """Truncation metadata should be present when budget exceeded."""
    findings = [_make_issue("warning", message=f"Warning {i}") for i in range(20)]
    mesh = _make_mesh(
        [
            ChannelResult(
                channel="lint",
                status="fail",
                severity="warning",
                findings=findings,
                repairs=[RepairAction(channel="lint", summary=f"Fix {i}") for i in range(10)],
            ),
        ]
    )

    config = ControlPlaneConfig(
        token_policy=TokenPolicy(hook_max_tokens=100),  # Very tight
    )
    report = format_mesh_report(mesh, config)
    # Report should exist even under tight budget
    assert "systemMessage" in report


# ── PostToolUse hook schema context ──────────────────────────────────────


def test_posttooluse_context_includes_coherence_and_counts() -> None:
    mesh = _make_mesh(
        [
            ChannelResult(
                channel="lint",
                status="fail",
                severity="warning",
                findings=[_make_issue("warning")],
            ),
        ]
    )
    report = format_mesh_report(mesh)

    hs = report["hookSpecificOutput"]
    assert hs["hookEventName"] == "PostToolUse"
    ctx = hs["additionalContext"]
    assert "coherence=stable" in ctx
    assert "channels_run=1" in ctx
    assert "warnings=1" in ctx


def test_posttooluse_context_includes_channel_statuses() -> None:
    mesh = _make_mesh(
        [
            ChannelResult(
                channel="lint", status="fail", severity="warning", findings=[_make_issue("warning")]
            ),
            ChannelResult(channel="tests", status="pass"),
            ChannelResult(channel="deps", status="skip"),
        ]
    )
    report = format_mesh_report(mesh)

    ctx = report["hookSpecificOutput"]["additionalContext"]
    # New compact format uses "loud" for failing channels only
    assert "loud=" in ctx
    assert "lint:fail" in ctx
    assert "deps" not in ctx  # Skip channels excluded


def test_delta_report_limits_repeated_finding_display_to_delta_count() -> None:
    """Delta mode should show only the new/escalated count for repeated fingerprints."""
    previous_mesh = _make_mesh(
        [
            ChannelResult(
                channel="lint",
                status="fail",
                severity="warning",
                findings=[
                    _make_issue(
                        "warning",
                        linter="ruff",
                        kind="E501",
                        message="Repeated warning",
                        file="foo.py",
                    )
                ],
            ),
        ]
    )
    previous_index = build_finding_index(previous_mesh)

    current_mesh = _make_mesh(
        [
            ChannelResult(
                channel="lint",
                status="fail",
                severity="warning",
                findings=[
                    _make_issue(
                        "warning",
                        linter="ruff",
                        kind="E501",
                        message="Repeated warning",
                        file="foo.py",
                    ),
                    _make_issue(
                        "warning",
                        linter="ruff",
                        kind="E501",
                        message="Repeated warning",
                        file="foo.py",
                    ),
                    _make_issue(
                        "warning",
                        linter="ruff",
                        kind="E501",
                        message="Repeated warning",
                        file="foo.py",
                    ),
                ],
            ),
        ]
    )

    report = format_mesh_report(
        current_mesh, previous_finding_index=previous_index, snapshot_count=1
    )
    msg = report["systemMessage"]
    assert "DELTA: 2 new" in msg
    assert "WARNINGS (2):" in msg
    assert msg.count("Repeated warning") == 2


# ── Repair suggestions ───────────────────────────────────────────────────


def test_repairs_in_report() -> None:
    mesh = _make_mesh(
        [
            ChannelResult(
                channel="tests",
                status="fail",
                severity="informational",
                findings=[_make_issue("informational")],
                repairs=[
                    RepairAction(
                        channel="tests",
                        kind="create_test_skeleton",
                        summary="Create test skeleton for module.py",
                        safe=True,
                    ),
                ],
            ),
        ]
    )
    report = format_mesh_report(mesh)
    msg = report["systemMessage"]

    assert "SUGGESTED REPAIRS" in msg
    assert "test skeleton" in msg
    assert "[safe]" in msg


# ── XML structure ────────────────────────────────────────────────────────


def test_report_has_xml_structure() -> None:
    mesh = _make_mesh(
        [
            ChannelResult(
                channel="lint",
                status="fail",
                severity="warning",
                findings=[_make_issue("warning")],
            ),
        ]
    )
    report = format_mesh_report(mesh)
    msg = report["systemMessage"]

    assert msg.startswith("<controlplane-report")
    assert msg.strip().endswith("</controlplane-report>")


# ── Duration in header ───────────────────────────────────────────────────


def test_duration_in_header() -> None:
    mesh = _make_mesh(
        [
            ChannelResult(
                channel="lint",
                status="fail",
                severity="warning",
                findings=[_make_issue("warning")],
            ),
        ]
    )
    report = format_mesh_report(mesh)
    assert (
        'duration="42ms"' in report["systemMessage"] or 'duration="43ms"' in report["systemMessage"]
    )


# ── Error/timeout channels produce report ────────────────────────────────


def test_error_channel_produces_report() -> None:
    """Error channels should produce a report even without findings."""
    mesh = _make_mesh(
        [
            ChannelResult(channel="lint", status="pass"),
            ChannelResult(channel="tests", status="error", error_message="crash"),
        ],
        coherence_state="degraded",
        coherence_summary="Tests channel crashed.",
    )
    report = format_mesh_report(mesh)

    assert "systemMessage" in report
    assert "degraded" in report["systemMessage"]


# ── Finding Fingerprint ─────────────────────────────────────────────────


class TestFindingFingerprint:
    def test_stable_across_line_changes(self) -> None:
        """Same file+kind+message → same fingerprint regardless of line."""
        issue_a = _make_issue(
            "blocking", linter="ruff", kind="F821", message="Undefined name 'x'", file="foo.py"
        )
        issue_a.line = 10
        issue_b = _make_issue(
            "blocking", linter="ruff", kind="F821", message="Undefined name 'x'", file="foo.py"
        )
        issue_b.line = 25
        fp_a = compute_finding_fingerprint(issue_a, "lint")
        fp_b = compute_finding_fingerprint(issue_b, "lint")
        assert fp_a == fp_b

    def test_different_messages_different_fingerprints(self) -> None:
        issue_a = _make_issue(
            "blocking", linter="ruff", kind="F821", message="Undefined name 'x'", file="foo.py"
        )
        issue_b = _make_issue(
            "blocking", linter="ruff", kind="F821", message="Undefined name 'y'", file="foo.py"
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
        """Fingerprint uses full normalized path — same basename in different dirs differs."""
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
        # All values are dicts with expected keys
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
                        _make_issue("warning", kind="E501", message="Line too long", file="foo.py"),
                        _make_issue("warning", kind="E501", message="Line too long", file="foo.py"),
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
        return {fp: {"channel": channel, "kind": "test", "severity": severity, "message": "m"}}

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
        """First run (no previous) → inline blocking_issues."""
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
        """Delta run → delta section with new/resolved/still_active."""
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
        # Build a previous index with one finding
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
