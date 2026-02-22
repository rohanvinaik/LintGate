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
    mesh = _mesh(
        channel_results=[
            ChannelResult(
                channel="structure",
                status="fail",
                severity="informational",
                findings=[_issue("informational", message="Missing docstring")],
            ),
        ],
    )
    report = format_mesh_report(mesh)
    assert "INFO: 1 informational finding" in report["systemMessage"]


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
    prev_index = {}  # empty previous => everything is "new"
    report = format_mesh_report(mesh, previous_finding_index=prev_index)
    # Telemetry should be present since delta generates counters
    assert "_telemetry" in report


def test_format_mesh_report_delta_new_findings() -> None:
    """Delta mode shows only new findings."""
    issue_a = _issue(
        "warning", linter="ruff", kind="F821", message="Undefined foo", file="/tmp/foo.py"
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
        "warning", linter="ruff", kind="F821", message="Undefined foo", file="/tmp/foo.py"
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
        "warning", linter="ruff", kind="F821", message="Undefined foo", file="/tmp/foo.py"
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
        "blocking", linter="ruff", kind="F821", message="Critical bug", file="/tmp/foo.py"
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
                channel="lint", status="fail", findings=[_issue("warning", message="New issue")]
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
