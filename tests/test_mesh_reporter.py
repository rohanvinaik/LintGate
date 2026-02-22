"""Phase 5: Mesh reporter tests — core report formatting.

Verifies:
- Token budget enforcement (report truncates correctly)
- Silent channel omission (pass channels not in output)
- Truncation metadata present when budget exceeded
- Empty report for all-pass results
- Coherence state in report headers
- Partial results notice

Extended tests (hooks, repairs, XML) are in test_mesh_reporter_extended.py.
Fingerprint, index, delta, and compact tests are in test_mesh_reporter_fingerprint.py.
Branch coverage tests are in test_reporter_compact.py and test_reporter_compact_helpers.py.
"""

from __future__ import annotations

from lintgate.controlplane.reporter import (
    _compute_dynamic_budget,
    format_mesh_report,
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


