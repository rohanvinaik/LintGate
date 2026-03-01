"""Phase 5: Mesh reporter extended tests — hooks, delta, repairs, structure.

Verifies:
- PostToolUse hook schema context
- Delta report display
- Repair suggestions in report
- XML structure
- Duration in header
- Error/timeout channel reports

Fingerprint, index, delta, and compact tests are in
test_mesh_reporter_fingerprint.py. Core formatting tests are in
test_mesh_reporter.py.
"""

from __future__ import annotations

from lintgate.controlplane.reporter import (
    build_finding_index,
    format_mesh_report,
)
from lintgate.controlplane.types import (
    ChannelResult,
    CoherenceResult,
    MeshResult,
    RepairAction,
    SupervisionEvent,
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
                channel="lint",
                status="fail",
                severity="warning",
                findings=[_make_issue("warning")],
            ),
            ChannelResult(channel="tests", status="pass"),
            ChannelResult(channel="deps", status="skip"),
        ]
    )
    report = format_mesh_report(mesh)

    ctx = report["hookSpecificOutput"]["additionalContext"]
    assert "loud=" in ctx
    assert "lint:fail" in ctx
    assert "deps" not in ctx  # Skip channels excluded


# ── Delta report ─────────────────────────────────────────────────────────


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
        'duration="42ms"' in report["systemMessage"]
        or 'duration="43ms"' in report["systemMessage"]
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
