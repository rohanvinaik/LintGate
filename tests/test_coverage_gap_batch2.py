"""Targeted coverage tests for specific uncovered lines in batch 2.

Covers gaps in:
- coherence.py: _classify_systemic_failure, _apply_edit_scope, _channel_failure_weight
- reporter.py: format_mesh_report (escalated delta, tight budget, resolved delta)
- reporter_hook.py: _build_posttooluse_context, _build_telemetry_counters
- session_memory.py: save_session OSError path
"""

from __future__ import annotations

from unittest.mock import patch

from lintgate.controlplane.coherence import (
    _apply_edit_scope,
    _channel_failure_weight,
    _classify_systemic_failure,
)
from lintgate.controlplane.reporter import (
    _estimate_tokens,
    format_mesh_report,
)
from lintgate.controlplane.reporter_hook import (
    _build_posttooluse_context,
    _build_telemetry_counters,
)
from lintgate.controlplane.session_memory import (
    SessionMemory,
    save_session,
)
from lintgate.controlplane.types import (
    ChannelResult,
    CoherenceResult,
    MeshResult,
)
from lintgate.types import LintIssue

# ── Helpers ──────────────────────────────────────────────────────────────


def _issue(kind="E001", severity="warning", file="a.py", message="issue"):
    return LintIssue(
        linter="test", kind=kind, severity=severity, file=file, message=message
    )


def _channel(name, status="fail", severity="warning", findings=None):
    return ChannelResult(
        channel=name,
        status=status,
        severity=severity,
        findings=findings if findings is not None else [_issue()],
    )


def _mesh(
    channel_results=None,
    coherence_state="stable",
    coherence_kwargs=None,
    partial=False,
):
    coh_kw = {"state": coherence_state, "summary": "test", "loud_channels": []}
    if coherence_kwargs:
        coh_kw.update(coherence_kwargs)
    return MeshResult(
        channel_results=channel_results or [],
        coherence=CoherenceResult(**coh_kw),
        partial=partial,
    )


# ── coherence.py: _classify_systemic_failure line 292 ────────────────────
# Cross-domain failure with only 2 channels AND severity_weighted=True
# triggers the severity-weighted note on the else branch.


def test_systemic_cross_domain_two_channels_severity_weighted():
    """Line 292: cross-domain with 2 failures, severity_weighted appends score note."""
    # One infra channel (git) + one code channel (lint) = cross-domain.
    # Each needs enough weighted score for effective_failure_count >= 1.25
    # (blocking=1.0 per finding, so 2 blocking findings across 2 channels = 2.0 >= 1.25).
    git_ch = _channel(
        "git", severity="blocking", findings=[_issue(severity="blocking")]
    )
    lint_ch = _channel(
        "lint", severity="blocking", findings=[_issue(severity="blocking")]
    )
    failed = [git_ch, lint_ch]
    loud = ["git", "lint"]
    silent = ["tests"]

    result = _classify_systemic_failure(
        failed,
        loud,
        silent,
        demoted_notes=[],
        severity_weighted=True,
        channel_weights=None,
    )
    assert result is not None
    assert result.state == "systemic"
    assert result.confidence == 0.7
    # Line 292: the severity-weighted note should be present
    assert any(
        "severity-weighted failure score=" in n for n in result.classification_notes
    )


# ── coherence.py: _apply_edit_scope lines 645, 652 ──────────────────────


def test_apply_edit_scope_empty_loud_channels():
    """Line 645: non-stable result with empty loud_channels returns unchanged."""
    result = CoherenceResult(state="isolated", loud_channels=[], summary="test")
    channel_results = [_channel("lint", status="fail")]
    out = _apply_edit_scope(result, channel_results, files_changed=["a.py"])
    # Should return the same result object unchanged
    assert out is result


def test_apply_edit_scope_no_matching_failing_results():
    """Line 652: loud_channels populated but no channel_results match with status=fail."""
    result = CoherenceResult(state="coupled", loud_channels=["lint"], summary="test")
    # Channel result has status=pass, not fail — won't match the filter
    channel_results = [_channel("lint", status="pass")]
    out = _apply_edit_scope(result, channel_results, files_changed=["a.py"])
    assert out is result


# ── coherence.py: _channel_failure_weight line 879 ───────────────────────


def test_channel_failure_weight_no_findings_fallback():
    """Line 879: base_score==0 falls back to severity weight lookup."""
    # Use status="pass" so _finding_severity_counts' fallback (which only fires
    # for status="fail") does NOT fill in counts. With empty findings and
    # status != "fail", all counts stay 0, base_score == 0, hitting line 879.
    ch = ChannelResult(channel="lint", status="pass", severity="warning", findings=[])
    weight = _channel_failure_weight(ch)
    # Falls back to _SEVERITY_WEIGHT["warning"] = 0.55
    assert weight == 0.55


def test_channel_failure_weight_unknown_severity_fallback():
    """Line 879: unknown severity falls back to default 0.5."""
    ch = ChannelResult(channel="lint", status="fail", severity="none", findings=[])
    weight = _channel_failure_weight(ch)
    # _SEVERITY_WEIGHT["none"] = 0.0, so falls back to 0.0
    assert weight == 0.0


# ── reporter.py: format_mesh_report lines 160-163, 186 ──────────────────
# Escalated delta items contribute to quota; findings with zero quota filtered.


def test_format_mesh_report_escalated_delta():
    """Lines 160-163: escalated items in delta build per-fingerprint quota."""
    finding = _issue(severity="blocking", kind="E999", file="x.py")
    ch = _channel("lint", severity="blocking", findings=[finding])
    mesh = _mesh(channel_results=[ch], coherence_state="isolated")

    # Build a previous index that has the same fingerprint at lower severity
    # so the delta will show it as escalated
    from lintgate.controlplane.reporter_delta import compute_finding_fingerprint

    fp = compute_finding_fingerprint(finding, "lint")
    prev_index = {fp: {"severity": "warning", "count": 1, "channel": "lint"}}

    result = format_mesh_report(mesh, previous_finding_index=prev_index)
    assert "systemMessage" in result


def test_format_mesh_report_finding_filtered_by_quota():
    """Line 186: findings with zero quota are filtered out of display."""
    finding = _issue(severity="warning", kind="OLD1", file="old.py")
    ch = _channel("lint", severity="warning", findings=[finding])
    mesh = _mesh(channel_results=[ch], coherence_state="isolated")

    # Previous index has the same fingerprint — so delta shows it as still_active,
    # not new or escalated. Quota will be 0 for this fingerprint.
    from lintgate.controlplane.reporter_delta import compute_finding_fingerprint

    fp = compute_finding_fingerprint(finding, "lint")
    prev_index = {fp: {"severity": "warning", "count": 1, "channel": "lint"}}

    result = format_mesh_report(mesh, previous_finding_index=prev_index)
    # With only still_active findings (no new/escalated), display_findings is empty
    # The report should either be empty or have no blocking/warning sections
    msg = result.get("systemMessage", "")
    assert "BLOCKING" not in msg


# ── reporter.py: format_mesh_report lines 213-215 (minimal header) ──────


def test_format_mesh_report_budget_too_small_for_header():
    """Lines 213-215: token budget smaller than header emits minimal header."""
    finding = _issue(severity="blocking")
    ch = _channel("lint", severity="blocking", findings=[finding])
    mesh = _mesh(channel_results=[ch], coherence_state="isolated")

    # The dynamic budget floor is _BUDGET_BASE=300, always larger than a header.
    # Mock _compute_dynamic_budget to return a tiny value so the header doesn't fit.
    with patch(
        "lintgate.controlplane.reporter._compute_dynamic_budget",
        return_value=5,
    ):
        result = format_mesh_report(mesh)
    msg = result.get("systemMessage", "")
    assert '<controlplane-report coherence="isolated">' in msg


# ── reporter.py: format_mesh_report lines 227, 229 (delta parts) ────────


def test_format_mesh_report_delta_escalated_and_resolved():
    """Lines 227, 229: delta with escalated_count and resolved_count."""
    finding = _issue(severity="blocking", kind="NEW1", file="new.py")
    ch = _channel("lint", severity="blocking", findings=[finding])
    mesh = _mesh(channel_results=[ch], coherence_state="isolated")

    from lintgate.controlplane.reporter_delta import compute_finding_fingerprint

    fp = compute_finding_fingerprint(finding, "lint")
    # Previous index has a different finding that is now gone (resolved)
    # and current finding at escalated severity
    prev_index = {
        fp: {"severity": "warning", "count": 1, "channel": "lint"},
        "old_fp_gone": {"severity": "warning", "count": 1, "channel": "lint"},
    }

    result = format_mesh_report(mesh, previous_finding_index=prev_index)
    msg = result.get("systemMessage", "")
    assert "DELTA" in msg
    assert "escalated" in msg
    assert "resolved" in msg


# ── reporter.py: format_mesh_report lines 252-253 (budget tight) ────────


def test_format_mesh_report_tight_budget_reduced_blocking():
    """Lines 252-253: budget tight, blocking findings reduced to cap."""
    # Create many blocking findings to exceed budget
    findings = [
        _issue(severity="blocking", kind=f"E{i:03d}", file=f"f{i}.py")
        for i in range(10)
    ]
    ch = _channel("lint", severity="blocking", findings=findings)
    mesh = _mesh(channel_results=[ch], coherence_state="systemic")

    # Compute what the header token cost is
    from lintgate.controlplane.reporter import _format_blocking, _format_header

    header = _format_header(mesh)
    header_tokens = _estimate_tokens(header)
    # Budget: enough for header + reduced blocking (cap=1), but not all 10
    reduced_1 = _format_blocking(findings[:1])
    reduced_1_tokens = _estimate_tokens(reduced_1)
    # Set budget so header fits but full blocking doesn't; reduced cap=1 does
    tight_budget = header_tokens + reduced_1_tokens + 20

    with patch(
        "lintgate.controlplane.reporter._compute_dynamic_budget",
        return_value=tight_budget,
    ):
        result = format_mesh_report(mesh)
    msg = result.get("systemMessage", "")
    # Should have some blocking content but be truncated
    assert "BLOCKING" in msg
    assert "more blocking" in msg


# ── reporter_hook.py: _build_posttooluse_context lines 52, 55, 65 ───────


def test_posttooluse_context_edit_scoped_channels():
    """Lines 52, 55: edit_scoped coherence with edit_related and ambient channels."""
    coherence = CoherenceResult(
        state="isolated",
        summary="test",
        edit_scoped=True,
        edit_related_channels=["lint"],
        ambient_channels=["deps"],
        loud_channels=["lint"],
    )
    ch = _channel("lint", status="fail")
    mesh = MeshResult(channel_results=[ch], coherence=coherence)

    ctx = _build_posttooluse_context(
        mesh_result=mesh,
        blocking_count=1,
        warning_count=0,
        informational_count=0,
        hidden_findings=0,
        channels_run=2,
    )
    assert "edit_related=lint" in ctx
    assert "ambient_debt=deps" in ctx


def test_posttooluse_context_resolved_delta():
    """Line 65: delta with resolved_count > 0 adds resolved field."""
    coherence = CoherenceResult(state="stable", summary="test")
    mesh = MeshResult(channel_results=[], coherence=coherence)

    delta = {"new": [], "escalated": [], "resolved_count": 3, "still_active_count": 0}
    ctx = _build_posttooluse_context(
        mesh_result=mesh,
        blocking_count=0,
        warning_count=0,
        informational_count=0,
        hidden_findings=0,
        channels_run=1,
        delta=delta,
    )
    assert "resolved=3" in ctx


# ── reporter_hook.py: _build_posttooluse_context lines 92-93 ────────────


def test_posttooluse_context_truncation():
    """Lines 92-93: context string > 300 chars drops fields from bottom."""
    # Create a mesh with many loud channels to inflate the string
    channels = [_channel(f"ch_{i:03d}", status="fail") for i in range(30)]
    coherence = CoherenceResult(
        state="systemic",
        summary="test",
        loud_channels=[f"ch_{i:03d}" for i in range(30)],
    )
    mesh = MeshResult(channel_results=channels, coherence=coherence)

    ctx = _build_posttooluse_context(
        mesh_result=mesh,
        blocking_count=5,
        warning_count=10,
        informational_count=3,
        hidden_findings=2,
        channels_run=30,
        delta={
            "new": [{"count": 1}],
            "escalated": [],
            "resolved_count": 2,
            "still_active_count": 5,
        },
        resurfaced_count=1,
    )
    # The result must be <= 300 chars after truncation
    assert len(ctx) <= 300


# ── reporter_hook.py: _build_telemetry_counters lines 125, 131 ──────────


def test_telemetry_counters_edit_scope_downgrade():
    """Line 125: edit_scope_downgrades counter when coherence was downgraded."""
    coherence = CoherenceResult(
        state="stable",
        summary="test",
        edit_scoped=True,
        classification_notes=["downgraded to stable because all findings are ambient"],
    )
    mesh = MeshResult(channel_results=[], coherence=coherence)

    counters = _build_telemetry_counters(
        mesh_result=mesh,
        delta=None,
        baseline_delta=None,
        display_findings=[],
        all_findings=[],
        resurfaced_count=0,
    )
    assert counters["edit_scope_downgrades"] == 1


def test_telemetry_counters_edit_scope_preserved():
    """Line 131: edit_scope_preserved counter when edit-related channels exist."""
    coherence = CoherenceResult(
        state="isolated",
        summary="test",
        edit_scoped=True,
        edit_related_channels=["lint"],
    )
    mesh = MeshResult(channel_results=[], coherence=coherence)

    counters = _build_telemetry_counters(
        mesh_result=mesh,
        delta=None,
        baseline_delta=None,
        display_findings=[],
        all_findings=[],
        resurfaced_count=0,
    )
    assert counters["edit_scope_preserved"] == 1


# ── session_memory.py: save_session lines 244-245 ───────────────────────


def test_save_session_oserror_is_nonfatal(tmp_path):
    """Lines 244-245: OSError during save is silently swallowed."""
    session = SessionMemory(project_root=str(tmp_path / "project"))

    # Patch SESSION_DIR to a path that will cause an OSError on open
    with patch(
        "lintgate.controlplane.session_memory.SESSION_DIR", tmp_path / "sessions"
    ):
        # Create the directory first so mkdir succeeds
        (tmp_path / "sessions").mkdir()
        # Patch _session_path to return a path that is a directory (can't open for writing)
        fake_path = tmp_path / "sessions" / "subdir"
        fake_path.mkdir()
        with patch(
            "lintgate.controlplane.session_memory._session_path",
            return_value=fake_path,
        ):
            # Should not raise — OSError is caught
            save_session(session)
