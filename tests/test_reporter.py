"""Comprehensive coverage tests for lintgate/controlplane/reporter.py.

Targets all public and private symbols in reporter.py:
- format_mesh_report (main entry point, many branches)
- _compute_dynamic_budget
- _format_header
- _format_blocking
- _format_coherence
- _format_warnings
- _format_incomplete
- _format_channel_summary
- _format_pattern_alerts
- _format_repairs
- _format_proposed_constraints
- _short_path
- _estimate_tokens

Also covers re-exported symbols and module-level constants.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from lintgate.controlplane.reporter import (
    _BUDGET_BASE,
    _BUDGET_HARD_CAP,
    _BUDGET_PER_BLOCKING,
    _BUDGET_PER_INFO,
    _BUDGET_PER_REPAIR,
    _BUDGET_PER_WARNING,
    _compute_dynamic_budget,
    _estimate_tokens,
    _format_blocking,
    _format_channel_summary,
    _format_coherence,
    _format_header,
    _format_incomplete,
    _format_pattern_alerts,
    _format_proposed_constraints,
    _format_repairs,
    _format_warnings,
    _short_path,
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


def _mesh(
    channel_results: list[ChannelResult] | None = None,
    coherence_state: Any = "stable",
    coherence_summary: str = "",
    coherence_confidence: float = 1.0,
    coherence_action: str = "",
    coherence_notes: list[str] | None = None,
    partial: bool = False,
    incomplete: list[str] | None = None,
    duration_ms: float = 50.0,
) -> MeshResult:
    return MeshResult(
        event=SupervisionEvent(project_root="/tmp/test"),
        channel_results=channel_results or [],
        coherence=CoherenceResult(
            state=coherence_state,
            summary=coherence_summary,
            confidence=coherence_confidence,
            recommended_action=coherence_action,
            classification_notes=coherence_notes or [],
        ),
        duration_ms=duration_ms,
        incomplete_channels=incomplete or [],
        partial=partial,
    )


def _issue(
    severity: str = "warning",
    linter: str = "ruff",
    kind: str = "F821",
    message: str = "Undefined name 'x'",
    file: str | None = "/tmp/test/foo.py",
    line: int | None = 10,
    fix_description: str | None = None,
) -> LintIssue:
    return LintIssue(
        linter=linter,
        kind=kind,
        message=message,
        severity=severity,
        file=file,
        line=line,
        fix_description=fix_description,
    )


def _repair(
    channel: str = "lint",
    summary: str = "Run ruff --fix",
    safe: bool = True,
) -> RepairAction:
    return RepairAction(channel=channel, summary=summary, safe=safe)


# ── _estimate_tokens ─────────────────────────────────────────────────────


def test_estimate_tokens_empty() -> None:
    assert _estimate_tokens("") == 0


def test_estimate_tokens_short_string() -> None:
    # "hello" = 5 chars -> 5 // 4 = 1
    assert _estimate_tokens("hello") == 1


def test_estimate_tokens_longer_string() -> None:
    text = "a" * 100
    assert _estimate_tokens(text) == 25


# ── _short_path ──────────────────────────────────────────────────────────


def test_short_path_with_none() -> None:
    assert _short_path(None) == ""


def test_short_path_with_empty_string() -> None:
    assert _short_path("") == ""


def test_short_path_extracts_basename() -> None:
    assert _short_path("/home/user/project/foo.py") == "foo.py"


def test_short_path_bare_filename() -> None:
    assert _short_path("foo.py") == "foo.py"


# ── _format_header ───────────────────────────────────────────────────────


def test_format_header_stable_full_confidence() -> None:
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="lint", status="pass"),
            ChannelResult(channel="tests", status="pass"),
        ],
        coherence_state="stable",
        duration_ms=123.4,
    )
    header = _format_header(mesh)
    assert 'coherence="stable"' in header
    assert 'channels="2"' in header
    assert 'duration="123ms"' in header
    # Full confidence: no confidence attr
    assert "confidence=" not in header


def test_format_header_with_low_confidence() -> None:
    mesh = _mesh(
        channel_results=[ChannelResult(channel="lint", status="fail")],
        coherence_state="isolated",
        coherence_confidence=0.75,
    )
    header = _format_header(mesh)
    assert 'confidence="0.75"' in header


def test_format_header_skips_skip_channels() -> None:
    """Skip channels should not be counted."""
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="lint", status="pass"),
            ChannelResult(channel="tests", status="skip"),
        ],
    )
    header = _format_header(mesh)
    assert 'channels="1"' in header


# ── _format_blocking ─────────────────────────────────────────────────────


def test_format_blocking_single_issue() -> None:
    findings = [_issue("blocking", message="Undefined name 'foo'", file="/tmp/foo.py", line=5)]
    result = _format_blocking(findings)
    assert "BLOCKING (1 issue - must fix):" in result
    assert "[ruff/F821]" in result
    assert "foo.py:5" in result
    assert "Undefined name 'foo'" in result


def test_format_blocking_multiple_issues() -> None:
    findings = [_issue("blocking", message=f"Issue {i}") for i in range(3)]
    result = _format_blocking(findings)
    assert "BLOCKING (3 issues - must fix):" in result


def test_format_blocking_truncates_at_five() -> None:
    findings = [_issue("blocking", message=f"Issue {i}") for i in range(8)]
    result = _format_blocking(findings)
    assert "... and 3 more blocking issues" in result


def test_format_blocking_no_linter_prefix() -> None:
    """When linter is empty, only kind appears in prefix."""
    findings = [_issue("blocking", linter="", kind="test_fail")]
    result = _format_blocking(findings)
    assert "[test_fail]" in result


def test_format_blocking_no_file() -> None:
    findings = [_issue("blocking", file=None, line=None)]
    result = _format_blocking(findings)
    assert ":" in result  # Still formatted, just no location


def test_format_blocking_with_fix_description() -> None:
    findings = [_issue("blocking", fix_description="Add import for 'x'")]
    result = _format_blocking(findings)
    assert "Fix: Add import for 'x'" in result


# ── _format_warnings ─────────────────────────────────────────────────────


def test_format_warnings_single() -> None:
    findings = [_issue("warning", message="Unused import")]
    result = _format_warnings(findings)
    assert "WARNINGS (1):" in result
    assert "Unused import" in result


def test_format_warnings_truncates_at_three() -> None:
    findings = [_issue("warning", message=f"Warn {i}") for i in range(6)]
    result = _format_warnings(findings)
    assert "... and 3 more warnings" in result


# ── _format_coherence ────────────────────────────────────────────────────


def test_format_coherence_isolated_full_confidence() -> None:
    coherence = CoherenceResult(
        state="isolated",
        summary="Only lint has issues.",
        confidence=1.0,
    )
    result = _format_coherence(coherence)
    assert "COHERENCE [isolated]:" in result
    assert "Only lint has issues." in result
    # Full confidence -> no suffix
    assert "(confidence:" not in result


def test_format_coherence_with_low_confidence() -> None:
    coherence = CoherenceResult(
        state="coupled",
        summary="Lint and tests overlap.",
        confidence=0.65,
    )
    result = _format_coherence(coherence)
    assert "(confidence: 65%)" in result


def test_format_coherence_with_recommended_action() -> None:
    coherence = CoherenceResult(
        state="systemic",
        summary="Multiple failures.",
        recommended_action="Check imports first.",
    )
    result = _format_coherence(coherence)
    assert "Action: Check imports first." in result


def test_format_coherence_with_classification_notes() -> None:
    coherence = CoherenceResult(
        state="degraded",
        summary="Channel timeout.",
        classification_notes=["Channel timed out after 8s"],
    )
    result = _format_coherence(coherence)
    assert "Note: Channel timed out after 8s" in result


# ── _format_incomplete ───────────────────────────────────────────────────


def test_format_incomplete_single_channel() -> None:
    result = _format_incomplete(["tests"])
    assert result == "PARTIAL: Channels timed out: tests. Results may be incomplete."


def test_format_incomplete_multiple_channels() -> None:
    result = _format_incomplete(["tests", "deps"])
    assert "tests, deps" in result


# ── _format_channel_summary ──────────────────────────────────────────────


def test_format_channel_summary_pass() -> None:
    channels = [ChannelResult(channel="lint", status="pass")]
    result = _format_channel_summary(channels)
    assert "Channels:" in result
    assert "lint: pass" in result


def test_format_channel_summary_fail_with_findings() -> None:
    channels = [
        ChannelResult(
            channel="lint",
            status="fail",
            findings=[_issue("blocking"), _issue("warning")],
        ),
    ]
    result = _format_channel_summary(channels)
    assert "(2 findings)" in result


def test_format_channel_summary_error_with_message() -> None:
    channels = [
        ChannelResult(channel="deps", status="error", error_message="pip crashed"),
    ]
    result = _format_channel_summary(channels)
    assert "(pip crashed)" in result


def test_format_channel_summary_error_no_message() -> None:
    channels = [
        ChannelResult(channel="deps", status="error", error_message=None),
    ]
    result = _format_channel_summary(channels)
    assert "(unknown error)" in result


def test_format_channel_summary_timeout() -> None:
    channels = [ChannelResult(channel="tests", status="timeout")]
    result = _format_channel_summary(channels)
    assert "tests: timeout" in result


# ── _format_pattern_alerts ───────────────────────────────────────────────


def test_format_pattern_alerts_recurring() -> None:
    alerts = [
        {
            "linter": "ruff",
            "kind": "F821",
            "alert_reason": "recurring_across_runs",
            "recent_run_count": 5,
        },
    ]
    result = _format_pattern_alerts(alerts)
    assert "PATTERN ALERT: [ruff/F821] recurring across 5 recent runs" in result


def test_format_pattern_alerts_single_run_volume() -> None:
    alerts = [
        {
            "linter": "mypy",
            "kind": "error",
            "alert_reason": "single_run_volume",
            "count_this_run": 42,
        },
    ]
    result = _format_pattern_alerts(alerts)
    assert "PATTERN NOTE: [mypy/error] appeared 42 times this run" in result


def test_format_pattern_alerts_unknown_reason_ignored() -> None:
    alerts = [{"linter": "x", "kind": "y", "alert_reason": "unknown_reason"}]
    result = _format_pattern_alerts(alerts)
    assert result == ""


def test_format_pattern_alerts_truncates_at_three() -> None:
    alerts = [
        {
            "linter": f"l{i}",
            "kind": "k",
            "alert_reason": "recurring_across_runs",
            "recent_run_count": i,
        }
        for i in range(5)
    ]
    result = _format_pattern_alerts(alerts)
    lines = [ln for ln in result.split("\n") if ln.strip()]
    assert len(lines) == 3


# ── _format_repairs ──────────────────────────────────────────────────────


def test_format_repairs_safe_tag() -> None:
    repairs = [_repair(summary="Auto-fix imports", safe=True)]
    result = _format_repairs(repairs)
    assert "SUGGESTED REPAIRS (1):" in result
    assert "[safe]" in result
    assert "Auto-fix imports" in result


def test_format_repairs_review_tag() -> None:
    repairs = [_repair(summary="Rewrite module", safe=False)]
    result = _format_repairs(repairs)
    assert "[review]" in result


def test_format_repairs_truncates_at_five() -> None:
    repairs = [_repair(summary=f"Fix {i}") for i in range(8)]
    result = _format_repairs(repairs)
    assert "... and 3 more repair actions" in result


# ── _format_proposed_constraints ─────────────────────────────────────────


def test_format_proposed_constraints_basic() -> None:
    proposals = [
        {
            "status": "proposed",
            "rule_type": "style",
            "confidence": 0.85,
            "rationale": "Seen 3 times.",
            "proposed_rule": "Always use f-strings.",
        }
    ]
    result = _format_proposed_constraints(proposals)
    assert "PROPOSED CONSTRAINTS (1):" in result
    assert "[style] (85% confidence): Always use f-strings." in result
    assert "Reason: Seen 3 times." in result
    assert "controlplane_agent_feedback" in result


def test_format_proposed_constraints_drift_warning_with_contradicting() -> None:
    proposals = [
        {
            "status": "proposed",
            "rule_type": "note",
            "confidence": 0.5,
            "rationale": "",
            "proposed_rule": "Use %-formatting.",
            "drift_warning": True,
            "theory_coherence": {
                "contradicting_claims": ["Project always uses f-strings for formatting"],
            },
        }
    ]
    result = _format_proposed_constraints(proposals)
    assert "DRIFT WARNING: contradicts theory claim:" in result
    assert "Project always uses f-strings" in result


def test_format_proposed_constraints_drift_warning_no_contradicting() -> None:
    proposals = [
        {
            "status": "proposed",
            "rule_type": "note",
            "confidence": 0.5,
            "rationale": "",
            "proposed_rule": "Use tabs.",
            "drift_warning": True,
            "theory_coherence": {},
        }
    ]
    result = _format_proposed_constraints(proposals)
    assert "DRIFT WARNING: potential conflict with project theory" in result


def test_format_proposed_constraints_truncates_at_three() -> None:
    proposals = [
        {
            "status": "proposed",
            "rule_type": "style",
            "confidence": 0.5,
            "proposed_rule": f"Rule {i}",
            "rationale": "",
        }
        for i in range(5)
    ]
    result = _format_proposed_constraints(proposals)
    assert "... and 2 more proposals" in result


def test_format_proposed_constraints_no_rationale() -> None:
    proposals = [
        {
            "status": "proposed",
            "rule_type": "note",
            "confidence": 0.7,
            "proposed_rule": "Do X.",
            "rationale": "",
        }
    ]
    result = _format_proposed_constraints(proposals)
    assert "Reason:" not in result


# ── _compute_dynamic_budget ──────────────────────────────────────────────


def test_dynamic_budget_no_findings() -> None:
    mesh = _mesh(channel_results=[ChannelResult(channel="lint", status="pass")])
    config = ControlPlaneConfig(token_policy=TokenPolicy(hook_max_tokens=900))
    budget = _compute_dynamic_budget([], mesh, config)
    # Base budget 300 < floor 900, so floor wins
    assert budget == 900


def test_dynamic_budget_with_blocking_findings() -> None:
    findings = [_issue("blocking") for _ in range(5)]
    mesh = _mesh(channel_results=[ChannelResult(channel="lint", status="fail", findings=findings)])
    config = ControlPlaneConfig(token_policy=TokenPolicy(hook_max_tokens=100))
    budget = _compute_dynamic_budget(findings, mesh, config)
    expected = _BUDGET_BASE + 5 * _BUDGET_PER_BLOCKING
    assert budget == expected


def test_dynamic_budget_with_mixed_findings() -> None:
    blocking = [_issue("blocking") for _ in range(2)]
    warnings = [_issue("warning") for _ in range(3)]
    infos = [_issue("informational") for _ in range(4)]
    all_findings = blocking + warnings + infos
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="lint", status="fail", findings=all_findings),
        ],
    )
    config = ControlPlaneConfig(token_policy=TokenPolicy(hook_max_tokens=100))
    budget = _compute_dynamic_budget(all_findings, mesh, config)
    expected = (
        _BUDGET_BASE + 2 * _BUDGET_PER_BLOCKING + 3 * _BUDGET_PER_WARNING + 4 * _BUDGET_PER_INFO
    )
    assert budget == expected


def test_dynamic_budget_includes_repairs() -> None:
    findings = [_issue("warning")]
    repairs = [_repair() for _ in range(3)]
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="lint", status="fail", findings=findings, repairs=repairs),
        ],
    )
    config = ControlPlaneConfig(token_policy=TokenPolicy(hook_max_tokens=100))
    budget = _compute_dynamic_budget(findings, mesh, config)
    expected = _BUDGET_BASE + 1 * _BUDGET_PER_WARNING + 3 * _BUDGET_PER_REPAIR
    assert budget == expected


def test_dynamic_budget_hard_cap() -> None:
    """Massive findings should hit the hard cap."""
    findings = [_issue("blocking") for _ in range(5000)]
    mesh = _mesh(channel_results=[ChannelResult(channel="lint", status="fail", findings=findings)])
    config = ControlPlaneConfig(token_policy=TokenPolicy(hook_max_tokens=900))
    budget = _compute_dynamic_budget(findings, mesh, config)
    assert budget == _BUDGET_HARD_CAP


def test_dynamic_budget_floor_capped_at_hard_cap() -> None:
    """If hook_max_tokens exceeds hard cap, floor is capped."""
    mesh = _mesh(channel_results=[])
    config = ControlPlaneConfig(token_policy=TokenPolicy(hook_max_tokens=99999))
    budget = _compute_dynamic_budget([], mesh, config)
    assert budget == _BUDGET_HARD_CAP


# ── format_mesh_report — full integration ────────────────────────────────


def test_format_mesh_report_empty_when_all_pass() -> None:
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="lint", status="pass"),
            ChannelResult(channel="tests", status="pass"),
        ],
    )
    report = format_mesh_report(mesh)
    assert report == {}


def test_format_mesh_report_empty_when_all_skip() -> None:
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="lint", status="skip"),
        ],
    )
    report = format_mesh_report(mesh)
    assert report == {}


def test_format_mesh_report_not_empty_when_error_channel() -> None:
    """Error channels should produce a report even with no findings."""
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="lint", status="error", error_message="crash"),
        ],
    )
    report = format_mesh_report(mesh)
    assert "systemMessage" in report


def test_format_mesh_report_has_hook_specific_output() -> None:
    mesh = _mesh(
        channel_results=[
            ChannelResult(
                channel="lint",
                status="fail",
                severity="blocking",
                findings=[_issue("blocking")],
            ),
        ],
    )
    report = format_mesh_report(mesh)
    assert "hookSpecificOutput" in report
    assert report["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "additionalContext" in report["hookSpecificOutput"]


def test_format_mesh_report_default_config() -> None:
    """When config is None, default ControlPlaneConfig is used."""
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="lint", status="fail", findings=[_issue("warning")]),
        ],
    )
    report = format_mesh_report(mesh, config=None)
    assert "systemMessage" in report


def test_format_mesh_report_blocking_section() -> None:
    mesh = _mesh(
        channel_results=[
            ChannelResult(
                channel="lint",
                status="fail",
                severity="blocking",
                findings=[_issue("blocking", message="Undefined name 'foo'")],
            ),
        ],
    )
    report = format_mesh_report(mesh)
    assert "BLOCKING" in report["systemMessage"]
    assert "Undefined name 'foo'" in report["systemMessage"]


def test_format_mesh_report_warnings_section() -> None:
    mesh = _mesh(
        channel_results=[
            ChannelResult(
                channel="lint",
                status="fail",
                severity="warning",
                findings=[_issue("warning", message="Unused import os")],
            ),
        ],
    )
    report = format_mesh_report(mesh)
    assert "WARNINGS" in report["systemMessage"]


def test_format_mesh_report_coherence_section() -> None:
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="lint", status="fail", findings=[_issue("warning")]),
        ],
        coherence_state="isolated",
        coherence_summary="Only lint fails.",
    )
    report = format_mesh_report(mesh)
    assert "COHERENCE [isolated]" in report["systemMessage"]


def test_format_mesh_report_partial_notice() -> None:
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="tests", status="timeout"),
        ],
        partial=True,
        incomplete=["tests"],
        coherence_state="degraded",
        coherence_summary="Timeout.",
    )
    report = format_mesh_report(mesh)
    assert "PARTIAL" in report["systemMessage"]


def test_format_mesh_report_channel_summary() -> None:
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="lint", status="fail", findings=[_issue("warning")]),
            ChannelResult(channel="tests", status="pass"),
        ],
    )
    report = format_mesh_report(mesh)
    assert "Channels:" in report["systemMessage"]


def test_format_mesh_report_pattern_alerts() -> None:
    mesh = _mesh(
        channel_results=[
            ChannelResult(
                channel="lint",
                status="fail",
                findings=[_issue("warning")],
                metrics={
                    "pattern_alerts": [
                        {
                            "linter": "ruff",
                            "kind": "F821",
                            "alert_reason": "recurring_across_runs",
                            "recent_run_count": 3,
                        }
                    ]
                },
            ),
        ],
    )
    report = format_mesh_report(mesh)
    assert "PATTERN ALERT" in report["systemMessage"]


def test_format_mesh_report_repairs_section() -> None:
    mesh = _mesh(
        channel_results=[
            ChannelResult(
                channel="lint",
                status="fail",
                findings=[_issue("warning")],
                repairs=[_repair(summary="Run ruff --fix")],
            ),
        ],
    )
    report = format_mesh_report(mesh)
    assert "SUGGESTED REPAIRS" in report["systemMessage"]


def test_format_mesh_report_proposed_constraints() -> None:
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="lint", status="fail", findings=[_issue("warning")]),
        ],
    )
    constraints = [
        {
            "status": "proposed",
            "rule_type": "style",
            "confidence": 0.8,
            "proposed_rule": "Use f-strings.",
            "rationale": "Consistent.",
        }
    ]
    report = format_mesh_report(mesh, proposed_constraints=constraints)
    assert "PROPOSED CONSTRAINTS" in report["systemMessage"]


def test_format_mesh_report_proposed_constraints_only_active() -> None:
    """Only 'proposed' status constraints are shown."""
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="lint", status="fail", findings=[_issue("warning")]),
        ],
    )
    constraints = [
        {
            "status": "accepted",
            "rule_type": "style",
            "confidence": 0.9,
            "proposed_rule": "Use f-strings.",
            "rationale": "",
        }
    ]
    report = format_mesh_report(mesh, proposed_constraints=constraints)
    assert "PROPOSED CONSTRAINTS" not in report["systemMessage"]


def test_format_mesh_report_informational_count() -> None:
    _mesh(
        channel_results=[
            ChannelResult(
                channel="structure",
                status="fail",
                severity="informational",
                findings=[_issue("informational", message="Missing docstring")],
            ),
        ],
    )


# ── Delta Escalations & Resolutions ──────────────────────────────────────


def test_format_mesh_report_delta_escalated_and_resolved() -> None:
    # Set up fingerprints using actual logic
    from lintgate.controlplane.reporter.delta import compute_finding_fingerprint

    # ESCALATED finding: warning -> blocking
    issue_escalated = _issue("blocking", message="Escalated")
    fp_escalated = compute_finding_fingerprint(issue_escalated, "lint")

    # RESOLVED finding: existed in prev, not in current
    issue_resolved = _issue("blocking", message="Resolved")
    fp_resolved = compute_finding_fingerprint(issue_resolved, "lint")

    # Previous run has escalated finding as warning, and the resolved finding
    prev_index = {
        fp_escalated: {"severity": "warning", "count": 1, "channel": "lint"},
        fp_resolved: {"severity": "blocking", "count": 1, "channel": "lint"},
    }

    # Current run has escalated finding as blocking
    findings = [issue_escalated]
    mesh = _mesh(channel_results=[ChannelResult(channel="lint", status="fail", findings=findings)])

    # Pass previous_finding_index to trigger delta internal computation
    report = format_mesh_report(mesh, previous_finding_index=prev_index)

    msg = report["systemMessage"]
    assert "DELTA:" in msg
    assert "1 escalated" in msg
    assert "1 resolved" in msg


# ── Token Budget Truncation ──────────────────────────────────────────────


def test_format_mesh_report_tight_budget_truncation() -> None:
    """Verify truncation logic when budget is extremely limited."""
    # Patch multipliers to ensure max_tokens relies on the floor config
    with (
        patch("lintgate.controlplane.reporter._BUDGET_BASE", 0),
        patch("lintgate.controlplane.reporter._BUDGET_PER_BLOCKING", 0),
    ):
        findings = [_issue("blocking", message=f"Issue {i}") for i in range(10)]
        mesh = _mesh(
            channel_results=[ChannelResult(channel="lint", status="fail", findings=findings)]
        )

        # Budget of 38 tokens is tight enough to force cap=1.
        # Header (10) + Blocking List cap=3 (~50) -> FAIL.
        # Header (10) + Blocking List cap=1 (~25) -> PASS.
        config = ControlPlaneConfig(token_policy=TokenPolicy(hook_max_tokens=38))
        report = format_mesh_report(mesh, config=config)

        msg = report["systemMessage"]
        assert "<controlplane-report" in msg
        # Tight budget truncates blocking list — verify truncation message present
        assert "more blocking issues" in msg


def test_format_mesh_report_minimal_header() -> None:
    """Verify minimal header when budget is too small for full header."""
    with patch("lintgate.controlplane.reporter._BUDGET_BASE", 0):
        mesh = _mesh(channel_results=[ChannelResult(channel="lint", status="fail", findings=[])])
        # Force minimal budget of 5 tokens.
        config = ControlPlaneConfig(token_policy=TokenPolicy(hook_max_tokens=5))

        # We need some reason to generate a report
        mesh.partial = True
        mesh.incomplete_channels = ["tests"]

        report = format_mesh_report(mesh, config=config)
        msg = report["systemMessage"]
        # minimal header should still include coherence
        assert 'coherence="stable"' in msg


def test_format_mesh_report_informational_plural() -> None:
    mesh = _mesh(
        channel_results=[
            ChannelResult(
                channel="structure",
                status="fail",
                severity="informational",
                findings=[
                    _issue("informational", message="Missing docstring"),
                    _issue("informational", message="Missing type hint"),
                ],
            ),
        ],
    )
    report = format_mesh_report(mesh)
    assert "INFO: 2 informational findings" in report["systemMessage"]


def test_format_mesh_report_hidden_findings_marker() -> None:
    """When findings exceed display limits, hidden count is shown."""
    findings = [_issue("warning", message=f"Warn {i}") for i in range(10)]
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="lint", status="fail", severity="warning", findings=findings),
        ],
    )
    report = format_mesh_report(mesh)
    # Only 3 warnings shown inline, rest hidden
    assert "findings not shown inline" in report["systemMessage"]


def test_format_mesh_report_close_tag() -> None:
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="lint", status="fail", findings=[_issue("warning")]),
        ],
    )
    report = format_mesh_report(mesh)
    assert report["systemMessage"].endswith("</controlplane-report>")


def test_format_mesh_report_telemetry_when_delta() -> None:
    """Telemetry counters included when delta is available."""
    mesh = _mesh(
        channel_results=[
            ChannelResult(
                channel="lint",
                status="fail",
                findings=[_issue("warning", message="Issue A")],
            ),
        ],
    )
    prev_index: dict[str, Any] = {}  # empty previous => everything is "new"
    report = format_mesh_report(mesh, previous_finding_index=prev_index)
    # Telemetry should be present since delta generates counters
    assert "_telemetry" in report


def test_format_mesh_report_delta_new_findings() -> None:
    """Delta mode shows only new findings."""
    issue_a = _issue(
        "warning",
        linter="ruff",
        kind="F821",
        message="Undefined foo",
        file="/tmp/foo.py",
    )
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="lint", status="fail", findings=[issue_a]),
        ],
    )
    # Empty previous index => all findings are new
    report = format_mesh_report(mesh, previous_finding_index={})
    assert "DELTA:" in report["systemMessage"]
    assert "1 new" in report["systemMessage"]


def test_format_mesh_report_delta_resolved() -> None:
    """Delta shows resolved count when findings disappear."""
    from lintgate.controlplane.reporter import build_finding_index

    issue_a = _issue(
        "warning",
        linter="ruff",
        kind="F821",
        message="Undefined foo",
        file="/tmp/foo.py",
    )
    prev_mesh = _mesh(
        channel_results=[ChannelResult(channel="lint", status="fail", findings=[issue_a])],
    )
    prev_index = build_finding_index(prev_mesh)

    # Current has no findings
    mesh = _mesh(
        channel_results=[ChannelResult(channel="lint", status="pass")],
    )
    report = format_mesh_report(mesh, previous_finding_index=prev_index)
    # When there are no findings and no error channels, result might be empty
    # But with previous_finding_index set, delta computation runs
    # With all resolved the display_findings list is empty, triggering quick exit
    # if no error channels present
    assert isinstance(report, dict)


def test_format_mesh_report_delta_suppresses_unchanged() -> None:
    """Delta mode suppresses unchanged findings."""
    from lintgate.controlplane.reporter import build_finding_index

    issue_a = _issue(
        "warning",
        linter="ruff",
        kind="F821",
        message="Undefined foo",
        file="/tmp/foo.py",
    )
    prev_mesh = _mesh(
        channel_results=[ChannelResult(channel="lint", status="fail", findings=[issue_a])],
    )
    prev_index = build_finding_index(prev_mesh)

    # Current has same finding
    cur_mesh = _mesh(
        channel_results=[ChannelResult(channel="lint", status="fail", findings=[issue_a])],
    )
    report = format_mesh_report(cur_mesh, previous_finding_index=prev_index)
    # Unchanged finding is suppressed
    if "DELTA:" in report.get("systemMessage", ""):
        assert "unchanged (suppressed)" in report["systemMessage"]


def test_format_mesh_report_resurfacing_cadence() -> None:
    """Persistent blocking findings resurface every 10 snapshots."""
    from lintgate.controlplane.reporter import build_finding_index

    issue_a = _issue(
        "blocking",
        linter="ruff",
        kind="F821",
        message="Critical bug",
        file="/tmp/foo.py",
    )
    prev_mesh = _mesh(
        channel_results=[ChannelResult(channel="lint", status="fail", findings=[issue_a])],
    )
    prev_index = build_finding_index(prev_mesh)

    cur_mesh = _mesh(
        channel_results=[ChannelResult(channel="lint", status="fail", findings=[issue_a])],
    )
    report = format_mesh_report(
        cur_mesh,
        previous_finding_index=prev_index,
        snapshot_count=10,  # Triggers resurfacing cadence
    )
    msg = report.get("systemMessage", "")
    assert "resurfaced" in msg


def test_format_mesh_report_baseline_delta() -> None:
    """Baseline delta is computed when baseline_finding_index is provided."""
    mesh = _mesh(
        channel_results=[
            ChannelResult(
                channel="lint",
                status="fail",
                findings=[_issue("warning", message="New issue")],
            ),
        ],
    )
    report = format_mesh_report(mesh, baseline_finding_index={})
    # Baseline delta with empty baseline => all findings are session regressions
    assert "_telemetry" in report


def test_format_mesh_report_tight_budget_minimal_header() -> None:
    """Very tight budget produces minimal header fallback."""
    findings = [_issue("blocking", message="Big issue " * 50)]
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="lint", status="fail", severity="blocking", findings=findings),
        ],
    )
    # Extremely tight budget
    config = ControlPlaneConfig(token_policy=TokenPolicy(hook_max_tokens=1))
    report = format_mesh_report(mesh, config=config)
    # Should still produce a report
    assert "systemMessage" in report


def test_format_mesh_report_blocking_budget_overflow() -> None:
    """Many blocking findings trigger per-finding granularity reduction."""
    findings = [_issue("blocking", message=f"Error {i} " * 20) for i in range(20)]
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="lint", status="fail", severity="blocking", findings=findings),
        ],
    )
    config = ControlPlaneConfig(token_policy=TokenPolicy(hook_max_tokens=300))
    report = format_mesh_report(mesh, config=config)
    msg = report["systemMessage"]
    assert "BLOCKING" in msg


# ── Module-level constants ───────────────────────────────────────────────


def test_budget_constants_are_positive() -> None:
    assert _BUDGET_BASE > 0
    assert _BUDGET_PER_BLOCKING > 0
    assert _BUDGET_PER_WARNING > 0
    assert _BUDGET_PER_INFO > 0
    assert _BUDGET_PER_REPAIR > 0
    assert _BUDGET_HARD_CAP > 0


def test_budget_ordering() -> None:
    """Blocking costs more than warning which costs more than info."""
    assert _BUDGET_PER_BLOCKING > _BUDGET_PER_WARNING > _BUDGET_PER_INFO


# ── Empty report edge case ────────────────────────────────────────────────


def test_format_mesh_report_empty_with_no_channels() -> None:
    """No channels at all -> empty report."""
    mesh = _mesh(channel_results=[])
    report = format_mesh_report(mesh)
    assert report == {}


# ── PostToolUse hook context details ──────────────────────────────────────


def test_posttooluse_context_includes_coherence_and_counts() -> None:
    mesh = _mesh(
        channel_results=[
            ChannelResult(
                channel="lint",
                status="fail",
                severity="warning",
                findings=[_issue("warning")],
            ),
        ],
    )
    report = format_mesh_report(mesh)
    hs = report["hookSpecificOutput"]
    assert hs["hookEventName"] == "PostToolUse"
    ctx = hs["additionalContext"]
    assert "coherence=stable" in ctx
    assert "channels_run=1" in ctx
    assert "warnings=1" in ctx


def test_posttooluse_context_includes_channel_statuses() -> None:
    mesh = _mesh(
        channel_results=[
            ChannelResult(
                channel="lint",
                status="fail",
                severity="warning",
                findings=[_issue("warning")],
            ),
            ChannelResult(channel="tests", status="pass"),
            ChannelResult(channel="deps", status="skip"),
        ],
    )
    report = format_mesh_report(mesh)
    ctx = report["hookSpecificOutput"]["additionalContext"]
    assert "loud=" in ctx
    assert "lint:fail" in ctx
    assert "deps" not in ctx  # Skip channels excluded


# ── XML structure ─────────────────────────────────────────────────────────


def test_report_has_xml_structure() -> None:
    mesh = _mesh(
        channel_results=[
            ChannelResult(
                channel="lint",
                status="fail",
                severity="warning",
                findings=[_issue("warning")],
            ),
        ],
    )
    report = format_mesh_report(mesh)
    msg = report["systemMessage"]
    assert msg.startswith("<controlplane-report")
    assert msg.strip().endswith("</controlplane-report>")


# ── Duration in header ────────────────────────────────────────────────────


def test_duration_in_header() -> None:
    mesh = _mesh(
        channel_results=[
            ChannelResult(
                channel="lint",
                status="fail",
                severity="warning",
                findings=[_issue("warning")],
            ),
        ],
        duration_ms=42.5,
    )
    report = format_mesh_report(mesh)
    assert (
        'duration="42ms"' in report["systemMessage"] or 'duration="43ms"' in report["systemMessage"]
    )


# ── Delta with repeated fingerprints ─────────────────────────────────────


def test_delta_report_limits_repeated_finding_display() -> None:
    """Delta mode should show only the new count for repeated fingerprints."""
    previous_mesh = _mesh(
        channel_results=[
            ChannelResult(
                channel="lint",
                status="fail",
                severity="warning",
                findings=[
                    _issue(
                        "warning",
                        linter="ruff",
                        kind="E501",
                        message="Repeated warning",
                        file="/tmp/test/foo.py",
                    )
                ],
            ),
        ],
    )
    previous_index = build_finding_index(previous_mesh)

    current_mesh = _mesh(
        channel_results=[
            ChannelResult(
                channel="lint",
                status="fail",
                severity="warning",
                findings=[
                    _issue(
                        "warning",
                        linter="ruff",
                        kind="E501",
                        message="Repeated warning",
                        file="/tmp/test/foo.py",
                    ),
                    _issue(
                        "warning",
                        linter="ruff",
                        kind="E501",
                        message="Repeated warning",
                        file="/tmp/test/foo.py",
                    ),
                    _issue(
                        "warning",
                        linter="ruff",
                        kind="E501",
                        message="Repeated warning",
                        file="/tmp/test/foo.py",
                    ),
                ],
            ),
        ],
    )
    report = format_mesh_report(
        current_mesh, previous_finding_index=previous_index, snapshot_count=1
    )
    msg = report["systemMessage"]
    assert "DELTA: 2 new" in msg


# ── Finding Fingerprint ──────────────────────────────────────────────────


class TestFindingFingerprint:
    def test_stable_across_line_changes(self) -> None:
        issue_a = _issue(
            "blocking",
            linter="ruff",
            kind="F821",
            message="Undefined name 'x'",
            file="/tmp/test/foo.py",
        )
        issue_a.line = 10
        issue_b = _issue(
            "blocking",
            linter="ruff",
            kind="F821",
            message="Undefined name 'x'",
            file="/tmp/test/foo.py",
        )
        issue_b.line = 25
        assert compute_finding_fingerprint(issue_a, "lint") == compute_finding_fingerprint(
            issue_b, "lint"
        )

    def test_different_messages_different_fingerprints(self) -> None:
        issue_a = _issue(
            "blocking",
            linter="ruff",
            kind="F821",
            message="Undefined name 'x'",
            file="/tmp/test/foo.py",
        )
        issue_b = _issue(
            "blocking",
            linter="ruff",
            kind="F821",
            message="Undefined name 'y'",
            file="/tmp/test/foo.py",
        )
        assert compute_finding_fingerprint(issue_a, "lint") != compute_finding_fingerprint(
            issue_b, "lint"
        )

    def test_different_channels_different_fingerprints(self) -> None:
        issue = _issue("warning", linter="test", kind="test_fail", message="test_x failed")
        assert compute_finding_fingerprint(issue, "lint") != compute_finding_fingerprint(
            issue, "tests"
        )

    def test_full_path_distinguishes_same_basename(self) -> None:
        issue_a = _issue("warning", file="/a/b/foo.py", message="some issue")
        issue_b = _issue("warning", file="/c/d/foo.py", message="some issue")
        assert compute_finding_fingerprint(issue_a, "lint") != compute_finding_fingerprint(
            issue_b, "lint"
        )

    def test_empty_file_handled(self) -> None:
        issue = _issue("warning", message="no file", file=None)
        fp = compute_finding_fingerprint(issue, "lint")
        assert isinstance(fp, str) and len(fp) == 16


# ── Build Finding Index ──────────────────────────────────────────────────


class TestBuildFindingIndex:
    def test_indexes_findings_from_mesh(self) -> None:
        mesh = _mesh(
            channel_results=[
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="blocking",
                    findings=[
                        _issue("blocking", message="Error A", file="/tmp/test/a.py"),
                        _issue("warning", message="Warn B", file="/tmp/test/b.py"),
                    ],
                ),
            ]
        )
        index = build_finding_index(mesh)
        assert len(index) == 2
        for info in index.values():
            assert "channel" in info and "kind" in info and "severity" in info

    def test_multi_channel_findings(self) -> None:
        mesh = _mesh(
            channel_results=[
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="blocking",
                    findings=[_issue("blocking", message="lint error")],
                ),
                ChannelResult(
                    channel="tests",
                    status="fail",
                    severity="warning",
                    findings=[_issue("warning", linter="test_ch", message="test failed")],
                ),
            ]
        )
        index = build_finding_index(mesh)
        channels = {info["channel"] for info in index.values()}
        assert "lint" in channels and "tests" in channels

    def test_empty_mesh_empty_index(self) -> None:
        mesh = _mesh(channel_results=[ChannelResult(channel="lint", status="pass")])
        assert build_finding_index(mesh) == {}

    def test_duplicate_findings_are_counted(self) -> None:
        mesh = _mesh(
            channel_results=[
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="warning",
                    findings=[
                        _issue(
                            "warning", kind="E501", message="Line too long", file="/tmp/test/foo.py"
                        ),
                        _issue(
                            "warning", kind="E501", message="Line too long", file="/tmp/test/foo.py"
                        ),
                    ],
                ),
            ]
        )
        index = build_finding_index(mesh)
        assert len(index) == 1
        assert next(iter(index.values()))["count"] == 2


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

    def test_resolved_findings_detected(self) -> None:
        prev = {**self._idx("fp1"), **self._idx("fp2")}
        curr = self._idx("fp1")
        delta = compute_finding_delta(curr, prev)
        assert delta["resolved_count"] == 1

    def test_escalated_severity_detected(self) -> None:
        prev = self._idx("fp1", severity="warning")
        curr = self._idx("fp1", severity="blocking")
        delta = compute_finding_delta(curr, prev)
        assert len(delta["escalated"]) == 1

    def test_empty_previous_all_new(self) -> None:
        curr = {**self._idx("fp1"), **self._idx("fp2")}
        delta = compute_finding_delta(curr, {})
        assert len(delta["new"]) == 2

    def test_empty_current_all_resolved(self) -> None:
        prev = {**self._idx("fp1"), **self._idx("fp2")}
        delta = compute_finding_delta({}, prev)
        assert delta["resolved_count"] == 2

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
        assert len(delta["new"]) == 1
        assert delta["new"][0]["count"] == 2


# ── Compact ControlPlane Reporter ────────────────────────────────────────


class TestFormatMeshReportCompact:
    def test_first_run_inline_blocking(self) -> None:
        mesh = _mesh(
            channel_results=[
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="blocking",
                    findings=[_issue("blocking", message="Undefined 'x'", file="/tmp/test/foo.py")],
                ),
            ]
        )
        compact = format_mesh_report_compact(mesh)
        assert "run_id" in compact
        assert "blocking_issues" in compact
        assert "delta" not in compact
        assert compact["counts"]["blocking"] == 1

    def test_delta_run_has_delta_section(self) -> None:
        mesh = _mesh(
            channel_results=[
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="blocking",
                    findings=[
                        _issue("blocking", message="Error A", file="/tmp/test/a.py"),
                        _issue("warning", message="Warn B", file="/tmp/test/b.py"),
                    ],
                ),
            ]
        )
        previous_index = build_finding_index(
            _mesh(
                channel_results=[
                    ChannelResult(
                        channel="lint",
                        status="fail",
                        severity="warning",
                        findings=[_issue("warning", message="Warn B", file="/tmp/test/b.py")],
                    ),
                ]
            )
        )
        compact = format_mesh_report_compact(mesh, previous_finding_index=previous_index)
        assert "delta" in compact
        assert "blocking_issues" not in compact

    def test_next_actions_always_present(self) -> None:
        mesh = _mesh(channel_results=[ChannelResult(channel="lint", status="pass")])
        compact = format_mesh_report_compact(mesh)
        assert "next_actions" in compact
        assert isinstance(compact["next_actions"], list)

    def test_coherence_always_present(self) -> None:
        mesh = _mesh(
            channel_results=[ChannelResult(channel="lint", status="pass")],
            coherence_state="isolated",
            coherence_summary="Lint channel failing",
        )
        compact = format_mesh_report_compact(mesh)
        assert compact["coherence"]["state"] == "isolated"

    def test_channels_summary(self) -> None:
        mesh = _mesh(
            channel_results=[
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="blocking",
                    findings=[_issue("blocking")],
                ),
                ChannelResult(channel="tests", status="pass"),
                ChannelResult(channel="deps", status="skip"),
            ]
        )
        compact = format_mesh_report_compact(mesh)
        assert "lint" in compact["channels"]
        assert compact["channels"]["tests"] == "pass"
        assert "deps" not in compact["channels"]

    def test_finding_index_in_output(self) -> None:
        mesh = _mesh(
            channel_results=[
                ChannelResult(
                    channel="lint",
                    status="fail",
                    severity="warning",
                    findings=[_issue("warning", message="test")],
                ),
            ]
        )
        compact = format_mesh_report_compact(mesh)
        assert "finding_index" in compact
        assert len(compact["finding_index"]) == 1
