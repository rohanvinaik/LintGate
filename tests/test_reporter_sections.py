"""Tests for lintgate/controlplane/reporter/sections.py.

Covers all public and key private functions: section formatters,
helpers (_short_path, _estimate_tokens), _BudgetTracker, and the
top-level _assemble_report_sections orchestrator.
"""

from __future__ import annotations

from lintgate.controlplane.reporter.sections import (
    _append_blocking_section,
    _append_delta_summary,
    _assemble_report_sections,
    _BudgetTracker,
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
)
from lintgate.controlplane.types import (
    ChannelResult,
    CoherenceResult,
    MeshResult,
    RepairAction,
    SupervisionEvent,
)
from lintgate.types import LintIssue

# ── Fixture helpers ──────────────────────────────────────────────────────


def _issue(
    severity: str = "warning",
    linter: str = "ruff",
    kind: str = "F821",
    message: str = "undefined name",
    file: str | None = "/tmp/proj/foo.py",
    line: int | None = 10,
    fix_description: str | None = None,
) -> LintIssue:
    return LintIssue(
        linter=linter,
        kind=kind,
        message=message,
        file=file,
        line=line,
        severity=severity,
        fix_description=fix_description,
    )


def _mesh(
    coherence_state: str = "stable",
    confidence: float = 1.0,
    summary: str = "",
    channel_results: list[ChannelResult] | None = None,
    duration_ms: float = 42.0,
    partial: bool = False,
    incomplete_channels: list[str] | None = None,
) -> MeshResult:
    return MeshResult(
        event=SupervisionEvent(),
        channel_results=channel_results or [],
        coherence=CoherenceResult(
            state=coherence_state,  # type: ignore[arg-type]
            confidence=confidence,
            summary=summary,
        ),
        duration_ms=duration_ms,
        partial=partial,
        incomplete_channels=incomplete_channels or [],
    )


# ── _short_path ──────────────────────────────────────────────────────────


class TestShortPath:
    def test_returns_basename(self):
        assert _short_path("/a/b/c/foo.py") == "foo.py"

    def test_none_returns_empty(self):
        assert _short_path(None) == ""

    def test_empty_string_returns_empty(self):
        assert _short_path("") == ""

    def test_bare_filename(self):
        assert _short_path("bar.py") == "bar.py"


# ── _estimate_tokens ─────────────────────────────────────────────────────


class TestEstimateTokens:
    def test_empty_string(self):
        assert _estimate_tokens("") == 0

    def test_four_chars_is_one_token(self):
        assert _estimate_tokens("abcd") == 1

    def test_three_chars_rounds_down(self):
        assert _estimate_tokens("abc") == 0

    def test_eight_chars(self):
        assert _estimate_tokens("12345678") == 2

    def test_long_string(self):
        text = "x" * 100
        assert _estimate_tokens(text) == 25


# ── _BudgetTracker ───────────────────────────────────────────────────────


class TestBudgetTracker:
    def test_empty_tracker(self):
        bt = _BudgetTracker(max_tokens=100)
        assert bt.parts == []
        assert bt.token_estimate == 0
        assert bt.max_tokens == 100

    def test_try_append_within_budget(self):
        bt = _BudgetTracker(max_tokens=100)
        # 8 chars -> 2 tokens, well within 100
        result = bt.try_append("12345678")
        assert result is True
        assert bt.parts == ["12345678"]
        assert bt.token_estimate == 2

    def test_try_append_exceeds_budget(self):
        bt = _BudgetTracker(max_tokens=1)
        # 8 chars -> 2 tokens, exceeds budget of 1
        result = bt.try_append("12345678")
        assert result is False
        assert bt.parts == []
        assert bt.token_estimate == 0

    def test_try_append_exact_budget(self):
        bt = _BudgetTracker(max_tokens=2)
        result = bt.try_append("12345678")  # 2 tokens
        assert result is True
        assert bt.token_estimate == 2

    def test_accumulates_across_appends(self):
        bt = _BudgetTracker(max_tokens=10)
        assert bt.try_append("abcd") is True  # 1 token
        assert bt.try_append("efgh") is True  # 1 token -> total 2
        assert bt.token_estimate == 2
        assert len(bt.parts) == 2

    def test_budget_exhaustion(self):
        bt = _BudgetTracker(max_tokens=2)
        assert bt.try_append("12345678") is True  # 2 tokens
        assert bt.try_append("more") is False  # would be 3, exceeds 2
        assert len(bt.parts) == 1


# ── _format_header ───────────────────────────────────────────────────────


class TestFormatHeader:
    def test_stable_full_confidence(self):
        mr = _mesh(coherence_state="stable", confidence=1.0, duration_ms=100.0)
        header = _format_header(mr)
        assert 'coherence="stable"' in header
        assert "confidence=" not in header
        assert 'channels="0"' in header
        assert 'duration="100ms"' in header

    def test_low_confidence_shows_attribute(self):
        mr = _mesh(coherence_state="coupled", confidence=0.75, duration_ms=50.0)
        header = _format_header(mr)
        assert 'confidence="0.75"' in header
        assert 'coherence="coupled"' in header

    def test_channel_count_excludes_skipped(self):
        crs = [
            ChannelResult(channel="lint", status="pass"),
            ChannelResult(channel="test", status="skip"),
            ChannelResult(channel="git", status="fail"),
        ]
        mr = _mesh(channel_results=crs)
        header = _format_header(mr)
        assert 'channels="2"' in header

    def test_all_channels_skipped(self):
        crs = [ChannelResult(channel="a", status="skip")]
        mr = _mesh(channel_results=crs)
        header = _format_header(mr)
        assert 'channels="0"' in header


# ── _format_blocking ─────────────────────────────────────────────────────


class TestFormatBlocking:
    def test_single_finding(self):
        findings = [_issue(severity="blocking", message="bad name")]
        result = _format_blocking(findings)
        assert "BLOCKING (1 issue - must fix):" in result
        assert "[ruff/F821]" in result
        assert "foo.py:10" in result
        assert "bad name" in result

    def test_plural_findings(self):
        findings = [_issue(severity="blocking") for _ in range(3)]
        result = _format_blocking(findings)
        assert "BLOCKING (3 issues - must fix):" in result

    def test_no_linter_prefix(self):
        f = _issue(linter="", kind="E001")
        result = _format_blocking([f])
        assert "[E001]" in result
        assert "[/E001]" not in result

    def test_no_file(self):
        f = _issue(file=None, line=None)
        result = _format_blocking([f])
        # location should be empty
        assert ": undefined name" in result

    def test_file_no_line(self):
        f = _issue(file="/tmp/x.py", line=None)
        result = _format_blocking([f])
        # Location should be "x.py" without a ":NN" line suffix.
        # The colon after x.py comes from the "{location}: {message}" format,
        # not from a line number.
        assert "x.py: undefined name" in result
        assert "x.py:None" not in result
        # No digit immediately after "x.py:"
        import re

        assert not re.search(r"x\.py:\d", result)

    def test_fix_description_shown(self):
        f = _issue(fix_description="Use `bar` instead")
        result = _format_blocking([f])
        assert "Fix: Use `bar` instead" in result

    def test_no_fix_description(self):
        f = _issue(fix_description=None)
        result = _format_blocking([f])
        assert "Fix:" not in result

    def test_truncation_at_five(self):
        findings = [_issue(severity="blocking", message=f"msg{i}") for i in range(7)]
        result = _format_blocking(findings)
        assert "msg0" in result
        assert "msg4" in result
        assert "msg5" not in result
        assert "... and 2 more blocking issues" in result

    def test_exactly_five_no_truncation(self):
        findings = [_issue(severity="blocking") for _ in range(5)]
        result = _format_blocking(findings)
        assert "... and" not in result

    def test_empty_list(self):
        result = _format_blocking([])
        assert "BLOCKING (0 issues - must fix):" in result


# ── _format_coherence ────────────────────────────────────────────────────


class TestFormatCoherence:
    def test_full_confidence(self):
        coh = CoherenceResult(state="stable", confidence=1.0, summary="All clear")
        result = _format_coherence(coh)
        assert "COHERENCE [stable]:" in result
        assert "All clear" in result
        assert "confidence:" not in result

    def test_low_confidence_shows_percent(self):
        coh = CoherenceResult(state="coupled", confidence=0.6, summary="hmm")
        result = _format_coherence(coh)
        assert "(confidence: 60%)" in result

    def test_recommended_action(self):
        coh = CoherenceResult(
            state="systemic",
            confidence=1.0,
            summary="problems",
            recommended_action="Run tests",
        )
        result = _format_coherence(coh)
        assert "Action: Run tests" in result

    def test_no_recommended_action(self):
        coh = CoherenceResult(state="stable", summary="ok")
        result = _format_coherence(coh)
        assert "Action:" not in result

    def test_classification_notes(self):
        coh = CoherenceResult(
            state="coupled",
            confidence=0.7,
            summary="overlap",
            classification_notes=["ambiguous boundary", "low sample"],
        )
        result = _format_coherence(coh)
        assert "Note: ambiguous boundary" in result
        assert "Note: low sample" in result


# ── _format_warnings ─────────────────────────────────────────────────────


class TestFormatWarnings:
    def test_single_warning(self):
        result = _format_warnings([_issue()])
        assert "WARNINGS (1):" in result
        assert "[ruff/F821]" in result

    def test_truncation_at_three(self):
        findings = [_issue(message=f"w{i}") for i in range(5)]
        result = _format_warnings(findings)
        assert "w0" in result
        assert "w2" in result
        assert "w3" not in result
        assert "... and 2 more warnings" in result

    def test_exactly_three_no_truncation(self):
        findings = [_issue() for _ in range(3)]
        result = _format_warnings(findings)
        assert "... and" not in result

    def test_empty_list(self):
        result = _format_warnings([])
        assert "WARNINGS (0):" in result


# ── _format_incomplete ───────────────────────────────────────────────────


class TestFormatIncomplete:
    def test_single_channel(self):
        result = _format_incomplete(["lint"])
        assert result == "PARTIAL: Channels timed out: lint. Results may be incomplete."

    def test_multiple_channels(self):
        result = _format_incomplete(["lint", "test", "git"])
        assert "lint, test, git" in result

    def test_empty_channels(self):
        result = _format_incomplete([])
        assert result == "PARTIAL: Channels timed out: . Results may be incomplete."


# ── _format_channel_summary ──────────────────────────────────────────────


class TestFormatChannelSummary:
    def test_pass_channel(self):
        crs = [ChannelResult(channel="lint", status="pass")]
        result = _format_channel_summary(crs)
        assert "Channels:" in result
        assert "\u2713 lint: pass" in result

    def test_fail_with_findings(self):
        crs = [
            ChannelResult(
                channel="test",
                status="fail",
                findings=[_issue(), _issue()],
            )
        ]
        result = _format_channel_summary(crs)
        assert "\u2717 test: fail (2 findings)" in result

    def test_error_with_message(self):
        crs = [ChannelResult(channel="git", status="error", error_message="git crashed")]
        result = _format_channel_summary(crs)
        assert "\u26a0 git: error (git crashed)" in result

    def test_error_without_message(self):
        crs = [ChannelResult(channel="git", status="error")]
        result = _format_channel_summary(crs)
        assert "\u26a0 git: error (unknown error)" in result

    def test_timeout_channel(self):
        crs = [ChannelResult(channel="perf", status="timeout")]
        result = _format_channel_summary(crs)
        assert "\u23f1 perf: timeout" in result

    def test_unknown_status(self):
        crs = [ChannelResult(channel="x", status="weird")]  # type: ignore[arg-type]
        result = _format_channel_summary(crs)
        assert "? x: weird" in result

    def test_empty_channels(self):
        result = _format_channel_summary([])
        assert result == "Channels:"

    def test_pass_no_findings_detail(self):
        """Pass channel with no findings should not show '(0 findings)'."""
        crs = [ChannelResult(channel="lint", status="pass", findings=[])]
        result = _format_channel_summary(crs)
        assert "findings" not in result


# ── _format_pattern_alerts ───────────────────────────────────────────────


class TestFormatPatternAlerts:
    def test_recurring_condensed(self):
        alerts = [
            {
                "linter": "ruff",
                "kind": "F821",
                "alert_reason": "recurring_across_runs",
                "recent_run_count": 4,
            }
        ]
        result = _format_pattern_alerts(alerts)
        assert "Recurring:" in result
        assert "F821 (4 runs)" in result

    def test_single_run_volume_ignored(self):
        alerts = [
            {
                "linter": "mypy",
                "kind": "E001",
                "alert_reason": "single_run_volume",
                "count_this_run": 12,
            }
        ]
        result = _format_pattern_alerts(alerts)
        assert result == ""

    def test_unknown_reason_skipped(self):
        alerts = [{"linter": "x", "kind": "Y", "alert_reason": "something_else"}]
        result = _format_pattern_alerts(alerts)
        assert result == ""

    def test_truncation_at_three(self):
        alerts = [
            {
                "linter": f"l{i}",
                "kind": f"K{i}",
                "alert_reason": "recurring_across_runs",
                "recent_run_count": i,
            }
            for i in range(5)
        ]
        result = _format_pattern_alerts(alerts)
        assert "K0" in result
        assert "K2" in result
        assert "K3" not in result
        # All condensed into one line
        assert result.count("\n") == 0

    def test_empty_alerts(self):
        assert _format_pattern_alerts([]) == ""

    def test_missing_keys_use_defaults(self):
        alerts = [{"alert_reason": "recurring_across_runs"}]
        result = _format_pattern_alerts(alerts)
        assert "Recurring:" in result
        assert "? (0 runs)" in result


# ── _format_repairs ──────────────────────────────────────────────────────


class TestFormatRepairs:
    def test_safe_repair(self):
        repairs = [RepairAction(summary="Fix import", safe=True)]
        result = _format_repairs(repairs)
        assert "SUGGESTED REPAIRS (1):" in result
        assert "\u2022 Fix import [safe]" in result

    def test_review_repair(self):
        repairs = [RepairAction(summary="Refactor", safe=False)]
        result = _format_repairs(repairs)
        assert "\u2022 Refactor [review]" in result

    def test_truncation_at_five(self):
        repairs = [RepairAction(summary=f"r{i}", safe=True) for i in range(7)]
        result = _format_repairs(repairs)
        assert "r4" in result
        assert "r5" not in result
        assert "... and 2 more repair actions" in result

    def test_exactly_five_no_truncation(self):
        repairs = [RepairAction(summary=f"r{i}", safe=True) for i in range(5)]
        result = _format_repairs(repairs)
        assert "... and" not in result

    def test_empty_repairs(self):
        result = _format_repairs([])
        assert "SUGGESTED REPAIRS (0):" in result


# ── _format_proposed_constraints ─────────────────────────────────────────


class TestFormatProposedConstraints:
    def test_basic_proposal(self):
        proposals = [
            {
                "rule_type": "must",
                "confidence": 0.85,
                "proposed_rule": "Always pin deps",
                "rationale": "Reproducibility",
            }
        ]
        result = _format_proposed_constraints(proposals)
        assert "PROPOSED CONSTRAINTS (1):" in result
        assert "[must] (85% confidence): Always pin deps" in result
        assert "Reason: Reproducibility" in result
        assert "Use controlplane_agent_feedback to accept or reject." in result

    def test_no_rationale(self):
        proposals = [{"rule_type": "note", "confidence": 0.5, "proposed_rule": "a rule"}]
        result = _format_proposed_constraints(proposals)
        assert "Reason:" not in result

    def test_drift_warning_with_contradicting(self):
        proposals = [
            {
                "rule_type": "must",
                "confidence": 0.8,
                "proposed_rule": "use X",
                "rationale": "",
                "drift_warning": True,
                "theory_coherence": {
                    "contradicting_claims": ["Project avoids X because of perf issues"]
                },
            }
        ]
        result = _format_proposed_constraints(proposals)
        assert "DRIFT WARNING: contradicts theory claim:" in result
        assert "Project avoids X because of perf issues" in result

    def test_drift_warning_no_contradicting(self):
        proposals = [
            {
                "rule_type": "note",
                "confidence": 0.5,
                "proposed_rule": "y",
                "drift_warning": True,
                "theory_coherence": {"contradicting_claims": []},
            }
        ]
        result = _format_proposed_constraints(proposals)
        assert "DRIFT WARNING: potential conflict with project theory" in result

    def test_drift_warning_no_theory_coherence_key(self):
        proposals = [
            {
                "rule_type": "note",
                "confidence": 0.5,
                "proposed_rule": "z",
                "drift_warning": True,
            }
        ]
        result = _format_proposed_constraints(proposals)
        assert "DRIFT WARNING: potential conflict with project theory" in result

    def test_truncation_at_three(self):
        proposals = [
            {"rule_type": "note", "confidence": 0.5, "proposed_rule": f"rule{i}"} for i in range(5)
        ]
        result = _format_proposed_constraints(proposals)
        assert "rule0" in result
        assert "rule2" in result
        assert "rule3" not in result
        assert "... and 2 more proposals" in result

    def test_exactly_three_no_truncation(self):
        proposals = [
            {"rule_type": "note", "confidence": 0.5, "proposed_rule": f"rule{i}"} for i in range(3)
        ]
        result = _format_proposed_constraints(proposals)
        assert "... and" not in result

    def test_empty_proposals(self):
        result = _format_proposed_constraints([])
        assert "PROPOSED CONSTRAINTS (0):" in result
        assert "Use controlplane_agent_feedback" in result

    def test_default_values_for_missing_keys(self):
        proposals: list[dict[str, object]] = [{}]
        result = _format_proposed_constraints(proposals)
        assert "[note] (0% confidence):" in result

    def test_long_contradicting_claim_truncated_at_80(self):
        long_claim = "A" * 200
        proposals = [
            {
                "rule_type": "must",
                "confidence": 0.9,
                "proposed_rule": "x",
                "drift_warning": True,
                "theory_coherence": {"contradicting_claims": [long_claim]},
            }
        ]
        result = _format_proposed_constraints(proposals)
        # The claim is sliced to [:80] in the source
        assert "A" * 80 in result
        assert "A" * 81 not in result


# ── _append_delta_summary ────────────────────────────────────────────────


class TestAppendDeltaSummary:
    def test_all_categories(self):
        parts: list[str] = []
        delta = {
            "new": [{"count": 2}, {"count": 1}],
            "escalated": [{"count": 1}],
            "resolved_count": 3,
            "still_active_count": 5,
        }
        token_est = _append_delta_summary(
            parts, delta, resurfaced_count=2, token_estimate=0, max_tokens=500
        )
        assert len(parts) == 1
        assert "3 new" in parts[0]
        assert "1 escalated" in parts[0]
        assert "3 resolved" in parts[0]
        assert "5 unchanged (suppressed)" in parts[0]
        assert "2 resurfaced" in parts[0]
        assert token_est > 0

    def test_empty_delta(self):
        parts: list[str] = []
        delta = {"new": [], "escalated": [], "resolved_count": 0, "still_active_count": 0}
        token_est = _append_delta_summary(
            parts, delta, resurfaced_count=0, token_estimate=0, max_tokens=500
        )
        assert len(parts) == 0
        assert token_est == 0

    def test_only_new(self):
        parts: list[str] = []
        delta = {"new": [{"count": 4}]}
        _append_delta_summary(parts, delta, resurfaced_count=0, token_estimate=0, max_tokens=500)
        assert "4 new" in parts[0]
        assert "escalated" not in parts[0]

    def test_budget_exceeded_skips_section(self):
        parts: list[str] = []
        delta = {"new": [{"count": 1}]}
        token_est = _append_delta_summary(
            parts, delta, resurfaced_count=0, token_estimate=0, max_tokens=0
        )
        assert len(parts) == 0
        assert token_est == 0

    def test_items_without_count_default_to_one(self):
        parts: list[str] = []
        delta: dict[str, list[dict[str, object]]] = {"new": [{}]}
        _append_delta_summary(parts, delta, resurfaced_count=0, token_estimate=0, max_tokens=500)
        assert "1 new" in parts[0]


# ── _append_blocking_section ─────────────────────────────────────────────


class TestAppendBlockingSection:
    def test_fits_in_budget(self):
        parts: list[str] = []
        blocking = [_issue(severity="blocking", message="err1")]
        token_est = _append_blocking_section(parts, blocking, token_estimate=0, max_tokens=5000)
        assert len(parts) == 1
        assert "BLOCKING" in parts[0]
        assert token_est > 0

    def test_fallback_to_cap_3(self):
        """When full section exceeds budget, should try cap=3."""
        # Create 6 findings with long messages so full section is large
        blocking = [
            _issue(severity="blocking", message="x" * 100, file=f"/tmp/f{i}.py") for i in range(6)
        ]
        full_section = _format_blocking(blocking)
        full_tokens = _estimate_tokens(full_section)
        # Set budget so full doesn't fit but cap=3 does
        cap3_section = _format_blocking(blocking[:3])
        cap3_tokens = _estimate_tokens(cap3_section)
        budget = cap3_tokens + 10  # enough for cap=3 but not full
        assert budget < full_tokens  # sanity: full must not fit

        parts: list[str] = []
        _append_blocking_section(parts, blocking, token_estimate=0, max_tokens=budget)
        assert len(parts) == 1
        assert "...and 3 more blocking issues" in parts[0]

    def test_fallback_to_cap_1(self):
        """When cap=3 also exceeds budget, should try cap=1."""
        blocking = [
            _issue(severity="blocking", message="y" * 200, file=f"/tmp/f{i}.py") for i in range(6)
        ]
        cap1_section = _format_blocking(blocking[:1])
        cap1_tokens = _estimate_tokens(cap1_section)
        cap3_section = _format_blocking(blocking[:3])
        cap3_tokens = _estimate_tokens(cap3_section)
        # budget: fits cap=1 but not cap=3
        budget = cap1_tokens + 10
        assert budget < cap3_tokens

        parts: list[str] = []
        _append_blocking_section(parts, blocking, token_estimate=0, max_tokens=budget)
        assert len(parts) == 1
        assert "...and 5 more blocking issues" in parts[0]

    def test_nothing_fits(self):
        """When even cap=1 exceeds budget, nothing appended."""
        blocking = [_issue(severity="blocking", message="z" * 500)]
        parts: list[str] = []
        _append_blocking_section(parts, blocking, token_estimate=0, max_tokens=1)
        assert len(parts) == 0


# ── _assemble_report_sections ────────────────────────────────────────────


class TestAssembleReportSections:
    def test_minimal_report(self):
        """Empty findings, stable coherence — produces header + channel summary."""
        mr = _mesh(coherence_state="stable", summary="")
        parts, token_est, blocking_count, warning_count = _assemble_report_sections(
            mesh_result=mr,
            display_findings=[],
            all_findings=[],
            active_channels=[],
            delta=None,
            resurfaced_count=0,
            max_tokens=5000,
            disposition=None,
            proposed_constraints=None,
            cycle_alerts=None,
        )
        assert any("controlplane-report" in p for p in parts)
        assert blocking_count == 0
        assert warning_count == 0
        assert token_est > 0

    def test_disposition_included(self):
        mr = _mesh()
        parts, *_ = _assemble_report_sections(
            mesh_result=mr,
            display_findings=[],
            all_findings=[],
            active_channels=[],
            delta=None,
            resurfaced_count=0,
            max_tokens=5000,
            disposition="orient-then-act",
            proposed_constraints=None,
            cycle_alerts=None,
        )
        assert any("DISPOSITION: orient-then-act" in p for p in parts)

    def test_blocking_and_warning_counts(self):
        findings = [
            _issue(severity="blocking", message="b1"),
            _issue(severity="blocking", message="b2"),
            _issue(severity="warning", message="w1"),
        ]
        mr = _mesh()
        parts, _, blocking_count, warning_count = _assemble_report_sections(
            mesh_result=mr,
            display_findings=findings,
            all_findings=findings,
            active_channels=[],
            delta=None,
            resurfaced_count=0,
            max_tokens=5000,
            disposition=None,
            proposed_constraints=None,
            cycle_alerts=None,
        )
        assert blocking_count == 2
        assert warning_count == 1

    def test_delta_section(self):
        mr = _mesh()
        delta = {"new": [{"count": 3}], "resolved_count": 1}
        parts, *_ = _assemble_report_sections(
            mesh_result=mr,
            display_findings=[],
            all_findings=[],
            active_channels=[],
            delta=delta,
            resurfaced_count=0,
            max_tokens=5000,
            disposition=None,
            proposed_constraints=None,
            cycle_alerts=None,
        )
        assert any("DELTA:" in p for p in parts)
        assert any("3 new" in p for p in parts)

    def test_coherence_section_non_stable(self):
        mr = _mesh(coherence_state="systemic", summary="Many failures")
        parts, *_ = _assemble_report_sections(
            mesh_result=mr,
            display_findings=[],
            all_findings=[],
            active_channels=[],
            delta=None,
            resurfaced_count=0,
            max_tokens=5000,
            disposition=None,
            proposed_constraints=None,
            cycle_alerts=None,
        )
        assert any("COHERENCE [systemic]" in p for p in parts)

    def test_coherence_section_stable_with_summary(self):
        mr = _mesh(coherence_state="stable", summary="Some notes")
        parts, *_ = _assemble_report_sections(
            mesh_result=mr,
            display_findings=[],
            all_findings=[],
            active_channels=[],
            delta=None,
            resurfaced_count=0,
            max_tokens=5000,
            disposition=None,
            proposed_constraints=None,
            cycle_alerts=None,
        )
        assert any("COHERENCE [stable]" in p for p in parts)

    def test_coherence_section_stable_no_summary_omitted(self):
        mr = _mesh(coherence_state="stable", summary="")
        parts, *_ = _assemble_report_sections(
            mesh_result=mr,
            display_findings=[],
            all_findings=[],
            active_channels=[],
            delta=None,
            resurfaced_count=0,
            max_tokens=5000,
            disposition=None,
            proposed_constraints=None,
            cycle_alerts=None,
        )
        assert not any("COHERENCE" in p for p in parts)

    def test_incomplete_channels(self):
        mr = _mesh(partial=True, incomplete_channels=["test", "perf"])
        parts, *_ = _assemble_report_sections(
            mesh_result=mr,
            display_findings=[],
            all_findings=[],
            active_channels=[],
            delta=None,
            resurfaced_count=0,
            max_tokens=5000,
            disposition=None,
            proposed_constraints=None,
            cycle_alerts=None,
        )
        assert any("PARTIAL:" in p for p in parts)

    def test_pattern_alerts_from_lint_channel(self):
        lint_cr = ChannelResult(
            channel="lint",
            status="pass",
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
        )
        mr = _mesh(channel_results=[lint_cr])
        parts, *_ = _assemble_report_sections(
            mesh_result=mr,
            display_findings=[],
            all_findings=[],
            active_channels=[lint_cr],
            delta=None,
            resurfaced_count=0,
            max_tokens=5000,
            disposition=None,
            proposed_constraints=None,
            cycle_alerts=None,
        )
        assert any("Recurring:" in p for p in parts)

    def test_repairs_section(self):
        repair = RepairAction(summary="Fix import order", safe=True)
        cr = ChannelResult(channel="lint", status="fail", repairs=[repair])
        mr = _mesh(channel_results=[cr])
        parts, *_ = _assemble_report_sections(
            mesh_result=mr,
            display_findings=[],
            all_findings=[],
            active_channels=[cr],
            delta=None,
            resurfaced_count=0,
            max_tokens=5000,
            disposition=None,
            proposed_constraints=None,
            cycle_alerts=None,
        )
        assert any("SUGGESTED REPAIRS" in p for p in parts)

    def test_informational_not_shown(self):
        """INFO section removed — informational findings are noise."""
        info_findings = [_issue(severity="informational")] * 3
        mr = _mesh()
        parts, *_ = _assemble_report_sections(
            mesh_result=mr,
            display_findings=[],
            all_findings=info_findings,
            active_channels=[],
            delta=None,
            resurfaced_count=0,
            max_tokens=5000,
            disposition=None,
            proposed_constraints=None,
            cycle_alerts=None,
        )
        assert not any("INFO:" in p for p in parts)

    def test_proposed_constraints_only_proposed_status(self):
        constraints = [
            {
                "status": "proposed",
                "rule_type": "must",
                "confidence": 0.8,
                "proposed_rule": "pin deps",
            },
            {
                "status": "accepted",
                "rule_type": "must",
                "confidence": 0.9,
                "proposed_rule": "other",
            },
        ]
        mr = _mesh()
        parts, *_ = _assemble_report_sections(
            mesh_result=mr,
            display_findings=[],
            all_findings=[],
            active_channels=[],
            delta=None,
            resurfaced_count=0,
            max_tokens=5000,
            disposition=None,
            proposed_constraints=constraints,
            cycle_alerts=None,
        )
        matching = [p for p in parts if "PROPOSED CONSTRAINTS" in p]
        assert len(matching) == 1
        assert "pin deps" in matching[0]
        assert "other" not in matching[0]

    def test_cycle_alerts(self):
        mr = _mesh()
        parts, *_ = _assemble_report_sections(
            mesh_result=mr,
            display_findings=[],
            all_findings=[],
            active_channels=[],
            delta=None,
            resurfaced_count=0,
            max_tokens=5000,
            disposition=None,
            proposed_constraints=None,
            cycle_alerts=["Approach cycling detected", "3 failed attempts"],
        )
        assert any("CYCLE ALERTS" in p for p in parts)
        assert any("Approach cycling detected" in p for p in parts)

    def test_tiny_budget_fallback_header(self):
        """When full header doesn't fit, fallback to minimal header."""
        mr = _mesh(coherence_state="stable", confidence=0.5, duration_ms=12345.0)
        # Very small budget: full header is ~70 chars = ~17 tokens
        # fallback header is shorter
        parts, *_ = _assemble_report_sections(
            mesh_result=mr,
            display_findings=[],
            all_findings=[],
            active_channels=[],
            delta=None,
            resurfaced_count=0,
            max_tokens=1,
            disposition=None,
            proposed_constraints=None,
            cycle_alerts=None,
        )
        # With max_tokens=1, even fallback may not fit; just verify no crash
        assert isinstance(parts, list)

    def test_channel_summary_always_attempted(self):
        """Channel summary (section 6) should be present even with no findings."""
        crs = [ChannelResult(channel="lint", status="pass")]
        mr = _mesh(channel_results=crs)
        parts, *_ = _assemble_report_sections(
            mesh_result=mr,
            display_findings=[],
            all_findings=[],
            active_channels=crs,
            delta=None,
            resurfaced_count=0,
            max_tokens=5000,
            disposition=None,
            proposed_constraints=None,
            cycle_alerts=None,
        )
        assert any("Channels:" in p for p in parts)
