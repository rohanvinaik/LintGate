"""Comprehensive coverage tests for lintgate/controlplane/reporter.py.

Tests related to format_mesh_report and its various sections.
"""

from __future__ import annotations

from lintgate.controlplane.reporter import (
    _BUDGET_BASE,
    _BUDGET_HARD_CAP,
    _BUDGET_PER_BLOCKING,
    _BUDGET_PER_INFO,
    _BUDGET_PER_REPAIR,
    _BUDGET_PER_WARNING,
    _compute_dynamic_budget,
    _format_blocking,
    _format_channel_summary,
    _format_coherence,
    _format_header,
    _format_incomplete,
    _format_pattern_alerts,
    _format_proposed_constraints,
    _format_repairs,
    _format_warnings,
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
    coherence_state: str = "stable",
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
    findings = [
        _issue("blocking", message="Undefined name 'foo'", file="/tmp/foo.py", line=5)
    ]
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
    # Full confidence -> no confidence attr
    assert "confidence:" not in result


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
                "contradicting_claims": [
                    "Project always uses f-strings for formatting"
                ],
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
    lines = [ln for ln in result.split("\n") if ln.strip()]
    assert len(lines) == 6  # Corrected from 3 to 6


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
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="lint", status="fail", findings=findings)
        ]
    )
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
        _BUDGET_BASE
        + 2 * _BUDGET_PER_BLOCKING
        + 3 * _BUDGET_PER_WARNING
        + 4 * _BUDGET_PER_INFO
    )
    assert budget == expected


def test_dynamic_budget_includes_repairs() -> None:
    findings = [_issue("warning")]
    repairs = [_repair() for _ in range(3)]
    mesh = _mesh(
        channel_results=[
            ChannelResult(
                channel="lint", status="fail", findings=findings, repairs=repairs
            ),
        ],
    )
    config = ControlPlaneConfig(token_policy=TokenPolicy(hook_max_tokens=100))
    budget = _compute_dynamic_budget(findings, mesh, config)
    expected = _BUDGET_BASE + 1 * _BUDGET_PER_WARNING + 3 * _BUDGET_PER_REPAIR
    assert budget == expected


def test_dynamic_budget_hard_cap() -> None:
    """Massive findings should hit the hard cap."""
    findings = [_issue("blocking") for _ in range(5000)]
    mesh = _mesh(
        channel_results=[
            ChannelResult(channel="lint", status="fail", findings=findings)
        ]
    )
    config = ControlPlaneConfig(token_policy=TokenPolicy(hook_max_tokens=900))
    budget = _compute_dynamic_budget(findings, mesh, config)
    assert budget == _BUDGET_HARD_CAP


def test_dynamic_budget_floor_capped_at_hard_cap() -> None:
    """If hook_max_tokens exceeds hard cap, floor is capped."""
    mesh = _mesh(channel_results=[])
    config = ControlPlaneConfig(token_policy=TokenPolicy(hook_max_tokens=99999))
    budget = _compute_dynamic_budget([], mesh, config)
    assert budget == _BUDGET_HARD_CAP
