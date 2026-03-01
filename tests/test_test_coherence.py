"""Targeted coverage tests for coherence.py internal functions.

Exercises specific code paths in helper functions that are partially covered
by the scenario-based integration tests.
"""

from __future__ import annotations

from lintgate.controlplane.coherence import (
    _apply_edit_scope,
    _channel_failure_weight,
    _channel_finding_summary,
    _classify_coupled_failure,
    _classify_edit_scope,
    _classify_isolated_failure,
    _classify_systemic_failure,
    _compute_base_coherence,
    _detect_persistent_loud,
    _detect_refactoring_tradeoffs,
    _detect_resolutions,
    _effective_failure_count,
    _find_shared_files,
    _finding_severity_counts,
    _has_actionable_findings,
    _has_ambient_critical_findings,
    _is_cross_domain_failure,
    _ordered_failed_channels,
    _top_finding_kind,
    compute_coherence,
    compute_coherence_with_history,
)
from lintgate.controlplane.session_memory import SessionMemory, SessionSnapshot
from lintgate.controlplane.types import ChannelResult
from lintgate.types import LintIssue


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


# ── compute_coherence ─────────────────────────────────────────────────


def test_compute_coherence_basic():
    results = [_channel("lint", "pass"), _channel("tests", "pass")]
    r = compute_coherence(results)
    assert r.state == "stable"


def test_compute_coherence_with_files_changed():
    results = [
        _channel("lint", "fail", findings=[_issue(file="changed.py")]),
        _channel("tests", "pass"),
        _channel("deps", "pass"),
    ]
    r = compute_coherence(results, files_changed=["changed.py"])
    assert r.edit_scoped is True


def test_compute_coherence_severity_weighted():
    # Two info-only failures get demoted, leaving zero actionable → stable
    results = [
        _channel(
            "lint",
            "fail",
            severity="informational",
            findings=[_issue(severity="informational")],
        ),
        _channel(
            "tests",
            "fail",
            severity="informational",
            findings=[_issue(severity="informational")],
        ),
        _channel("deps", "pass"),
    ]
    r = compute_coherence(results, severity_weighted=True)
    assert r.state == "stable"


# ── _compute_base_coherence ───────────────────────────────────────────


def test_base_coherence_no_channels():
    r = _compute_base_coherence([])
    assert r.state == "stable"
    assert "No channels" in r.summary


def test_base_coherence_all_pass():
    results = [_channel("lint", "pass"), _channel("tests", "pass")]
    r = _compute_base_coherence(results)
    assert r.state == "stable"


def test_base_coherence_error_channel():
    results = [
        _channel("lint", "error"),
        _channel("tests", "pass"),
    ]
    r = _compute_base_coherence(results)
    assert r.state == "degraded"


def test_base_coherence_error_with_fail():
    results = [
        _channel("lint", "error"),
        _channel("tests", "fail"),
        _channel("deps", "pass"),
    ]
    r = _compute_base_coherence(results)
    assert r.state == "degraded"
    assert any("failed" in n or "failures" in n for n in r.classification_notes)


def test_base_coherence_timeout_channel():
    results = [_channel("lint", "timeout"), _channel("tests", "pass")]
    r = _compute_base_coherence(results)
    assert r.state == "degraded"


def test_base_coherence_severity_weighted_demotes_info():
    # Two info-only failures get demoted → stable with "actionable" note
    results = [
        _channel(
            "lint",
            "fail",
            severity="informational",
            findings=[_issue(severity="informational")],
        ),
        _channel(
            "tests",
            "fail",
            severity="informational",
            findings=[_issue(severity="informational")],
        ),
        _channel("deps", "pass"),
    ]
    r = _compute_base_coherence(results, severity_weighted=True)
    assert r.state == "stable"
    assert "actionable" in r.summary.lower()


def test_base_coherence_single_fail():
    results = [
        _channel("lint", "fail"),
        _channel("tests", "pass"),
        _channel("deps", "pass"),
    ]
    r = _compute_base_coherence(results)
    assert r.state == "isolated"


def test_base_coherence_two_fail_shared_files():
    findings = [_issue(file="shared.py")]
    results = [
        _channel("lint", "fail", findings=findings),
        _channel("tests", "fail", findings=findings),
        _channel("deps", "pass"),
    ]
    r = _compute_base_coherence(results)
    assert r.state == "coupled"


def test_base_coherence_three_fail_systemic():
    results = [
        _channel("lint", "fail"),
        _channel("tests", "fail"),
        _channel("deps", "fail"),
        _channel("git", "pass"),
    ]
    r = _compute_base_coherence(results)
    assert r.state == "systemic"


# ── _classify_isolated_failure ────────────────────────────────────────


def test_isolated_high_confidence():
    failed = [_channel("lint")]
    passed = [_channel("tests", "pass"), _channel("deps", "pass")]
    r = _classify_isolated_failure(failed, passed, [], ["lint"], ["tests", "deps"])
    assert r.state == "isolated"
    assert r.confidence >= 0.7


def test_isolated_low_confidence_one_pass():
    failed = [_channel("lint")]
    passed = [_channel("tests", "pass")]
    r = _classify_isolated_failure(failed, passed, [], ["lint"], ["tests"])
    assert r.state == "isolated"
    assert r.confidence < 0.7


def test_isolated_low_confidence_no_pass():
    failed = [_channel("lint")]
    r = _classify_isolated_failure(failed, [], [], ["lint"], [])
    assert r.state == "isolated"
    assert r.confidence <= 0.3


# ── _classify_systemic_failure ────────────────────────────────────────


def test_systemic_three_plus():
    failed = [_channel("lint"), _channel("tests"), _channel("deps")]
    r = _classify_systemic_failure(failed, ["lint", "tests", "deps"], [], [], False)
    assert r is not None
    assert r.state == "systemic"


def test_systemic_cross_domain():
    failed = [_channel("lint"), _channel("deps")]
    r = _classify_systemic_failure(failed, ["lint", "deps"], [], [], False)
    assert r is not None
    assert r.state == "systemic"


def test_systemic_severity_weighted():
    failed = [
        _channel("lint", "fail", findings=[_issue(severity="blocking")] * 5),
        _channel("deps", "fail", findings=[_issue(severity="blocking")] * 5),
    ]
    r = _classify_systemic_failure(failed, ["lint", "deps"], [], [], True)
    assert r is not None


def test_systemic_returns_none_when_not_systemic():
    failed = [_channel("lint"), _channel("tests")]
    # Two failures with no shared files and same domain — not systemic
    r = _classify_systemic_failure(failed, ["lint", "tests"], [], [], False)
    # Both are code channels, so cross-domain check fails, and only 2 failures
    assert r is None


def test_systemic_cross_domain_low_weight():
    failed = [_channel("lint"), _channel("deps")]
    r = _classify_systemic_failure(
        failed,
        ["lint", "deps"],
        [],
        [],
        True,
        channel_weights={"lint": 0.1, "deps": 0.1},
    )
    # Low weights mean effective failure count is low — may not trigger systemic
    assert r is None  # weights too low to reach 1.25 threshold


def test_systemic_with_severity_weighted_notes():
    failed = [
        _channel("lint", "fail", findings=[_issue(severity="blocking")] * 3),
        _channel("tests", "fail", findings=[_issue(severity="blocking")] * 3),
        _channel("deps", "fail", findings=[_issue(severity="blocking")] * 3),
    ]
    r = _classify_systemic_failure(failed, ["lint", "tests", "deps"], [], [], True)
    assert r is not None
    assert any("severity-weighted" in n for n in r.classification_notes)


# ── _classify_coupled_failure ─────────────────────────────────────────


def test_coupled_shared_files():
    findings = [_issue(file="shared.py")]
    failed = [
        _channel("lint", "fail", findings=findings),
        _channel("tests", "fail", findings=findings),
    ]
    r = _classify_coupled_failure(failed, ["lint", "tests"], ["deps"], [])
    assert r.state == "coupled"
    assert "shared.py" in r.recommended_action


def test_coupled_shared_files_three_channels():
    findings = [_issue(file="shared.py")]
    failed = [
        _channel("lint", "fail", findings=findings),
        _channel("tests", "fail", findings=findings),
        _channel("structure", "fail", findings=findings),
    ]
    r = _classify_coupled_failure(
        failed,
        ["lint", "tests", "structure"],
        ["deps"],
        [],
    )
    assert r.state == "coupled"


def test_coupled_no_shared_files():
    failed = [
        _channel("lint", "fail", findings=[_issue(file="a.py")]),
        _channel("tests", "fail", findings=[_issue(file="b.py")]),
    ]
    r = _classify_coupled_failure(failed, ["lint", "tests"], ["deps"], [])
    assert r.state == "coupled"
    assert "independent" in r.summary.lower()


def test_coupled_no_shared_three_channels():
    failed = [
        _channel("lint", "fail", findings=[_issue(file="a.py")]),
        _channel("tests", "fail", findings=[_issue(file="b.py")]),
        _channel("structure", "fail", findings=[_issue(file="c.py")]),
    ]
    r = _classify_coupled_failure(
        failed,
        ["lint", "tests", "structure"],
        [],
        [],
    )
    assert r.state == "coupled"
    assert "order" in r.recommended_action.lower()


# ── _find_shared_files ────────────────────────────────────────────────


def test_find_shared_files_overlap():
    results = [
        _channel("lint", findings=[_issue(file="a.py"), _issue(file="b.py")]),
        _channel("tests", findings=[_issue(file="b.py"), _issue(file="c.py")]),
    ]
    assert _find_shared_files(results) == {"b.py"}


def test_find_shared_files_no_overlap():
    results = [
        _channel("lint", findings=[_issue(file="a.py")]),
        _channel("tests", findings=[_issue(file="b.py")]),
    ]
    assert _find_shared_files(results) == set()


def test_find_shared_files_one_channel():
    results = [_channel("lint", findings=[_issue(file="a.py")])]
    assert _find_shared_files(results) == set()


def test_find_shared_files_no_files():
    results = [
        _channel("lint", findings=[_issue(file="")]),
        _channel("tests", findings=[_issue(file="")]),
    ]
    assert _find_shared_files(results) == set()


# ── _finding_severity_counts ──────────────────────────────────────────


def test_severity_counts_mixed():
    r = _channel(
        "lint",
        findings=[
            _issue(severity="blocking"),
            _issue(severity="warning"),
            _issue(severity="informational"),
        ],
    )
    counts = _finding_severity_counts(r)
    assert counts["blocking"] == 1
    assert counts["warning"] == 1
    assert counts["informational"] == 1


def test_severity_counts_empty_findings_uses_channel():
    r = ChannelResult(channel="lint", status="fail", severity="warning", findings=[])
    counts = _finding_severity_counts(r)
    assert counts["warning"] == 1


def test_severity_counts_empty_findings_unknown_severity():
    r = ChannelResult(channel="lint", status="fail", severity="critical", findings=[])
    counts = _finding_severity_counts(r)
    assert counts["warning"] == 1  # fallback


def test_severity_counts_empty_findings_none_severity():
    r = ChannelResult(channel="lint", status="fail", severity="none", findings=[])
    counts = _finding_severity_counts(r)
    assert sum(counts.values()) == 0


# ── _effective_failure_count ──────────────────────────────────────────


def test_effective_failure_count_basic():
    failed = [_channel("lint", findings=[_issue(severity="blocking")])]
    assert _effective_failure_count(failed) >= 1.0


def test_effective_failure_count_with_weights():
    failed = [_channel("lint", findings=[_issue(severity="blocking")])]
    result = _effective_failure_count(failed, channel_weights={"lint": 0.5})
    assert result < _effective_failure_count(failed)


def test_effective_failure_count_missing_weight_defaults():
    failed = [_channel("other", findings=[_issue(severity="blocking")])]
    result = _effective_failure_count(failed, channel_weights={"lint": 1.0})
    assert result > 0  # defaults to 0.5


# ── _channel_failure_weight ───────────────────────────────────────────


def test_channel_failure_weight_blocking():
    r = _channel("lint", findings=[_issue(severity="blocking")])
    assert _channel_failure_weight(r) >= 1.0


def test_channel_failure_weight_info_only():
    r = _channel("lint", findings=[_issue(severity="informational")])
    assert _channel_failure_weight(r) > 0


def test_channel_failure_weight_no_findings_uses_channel_severity():
    r = ChannelResult(channel="lint", status="fail", severity="blocking", findings=[])
    assert _channel_failure_weight(r) == 1.0


# ── _has_actionable_findings ──────────────────────────────────────────


def test_has_actionable_blocking():
    r = _channel("lint", findings=[_issue(severity="blocking")])
    assert _has_actionable_findings(r) is True


def test_has_actionable_info_only():
    r = _channel(
        "lint", severity="informational", findings=[_issue(severity="informational")]
    )
    assert _has_actionable_findings(r) is False


def test_has_actionable_channel_severity_fallback():
    # Channel severity=warning is checked as fallback after findings
    r = ChannelResult(
        channel="lint",
        status="fail",
        severity="warning",
        findings=[_issue(severity="informational")],
    )
    # Finding is info but channel severity is warning → True (channel fallback)
    assert _has_actionable_findings(r) is True


# ── _top_finding_kind ─────────────────────────────────────────────────


def test_top_finding_kind_single():
    r = _channel("lint", findings=[_issue(kind="E001")])
    assert _top_finding_kind(r) == "E001"


def test_top_finding_kind_empty():
    r = ChannelResult(channel="lint", status="fail", findings=[])
    assert _top_finding_kind(r) == ""


def test_top_finding_kind_most_common():
    r = _channel(
        "lint",
        findings=[
            _issue(kind="E001"),
            _issue(kind="E001"),
            _issue(kind="E002"),
        ],
    )
    assert _top_finding_kind(r) == "E001"


# ── _channel_finding_summary ─────────────────────────────────────────


def test_summary_zero():
    r = ChannelResult(channel="lint", status="fail", findings=[])
    assert _channel_finding_summary(r) == "0 findings"


def test_summary_one():
    r = _channel("lint", findings=[_issue(kind="E001")])
    s = _channel_finding_summary(r)
    assert "1 finding" in s
    assert "E001" in s


def test_summary_many():
    r = _channel("lint", findings=[_issue(), _issue()])
    s = _channel_finding_summary(r)
    assert "2 findings" in s


# ── _ordered_failed_channels ─────────────────────────────────────────


def test_ordered_by_weight():
    failed = [
        _channel("lint", findings=[_issue(severity="informational")]),
        _channel("tests", findings=[_issue(severity="blocking")] * 3),
    ]
    ordered = _ordered_failed_channels(failed)
    assert ordered[0] == "tests"


# ── _is_cross_domain_failure ─────────────────────────────────────────


def test_cross_domain_infra_and_code():
    failed = [_channel("lint"), _channel("deps")]
    assert _is_cross_domain_failure(failed) is True


def test_cross_domain_code_only():
    failed = [_channel("lint"), _channel("tests")]
    assert _is_cross_domain_failure(failed) is False


def test_cross_domain_with_low_effective_count():
    failed = [_channel("lint"), _channel("deps")]
    assert _is_cross_domain_failure(failed, effective_failure_count=0.5) is False


def test_cross_domain_with_high_effective_count():
    failed = [_channel("lint"), _channel("deps")]
    assert _is_cross_domain_failure(failed, effective_failure_count=2.0) is True


# ── Edit-scope functions ──────────────────────────────────────────────


def test_classify_edit_scope_basic(tmp_path):
    changed = tmp_path / "a.py"
    changed.write_text("")
    failing = [_channel("lint", findings=[_issue(file=str(changed))])]
    edit_related, ambient, unknown = _classify_edit_scope(failing, [str(changed)])
    assert "lint" in edit_related


def test_classify_edit_scope_ambient(tmp_path):
    changed = tmp_path / "a.py"
    changed.write_text("")
    other = tmp_path / "b.py"
    other.write_text("")
    failing = [_channel("lint", findings=[_issue(file=str(other))])]
    edit_related, ambient, unknown = _classify_edit_scope(failing, [str(changed)])
    assert "lint" in ambient


def test_classify_edit_scope_unknown_no_findings():
    failing = [ChannelResult(channel="lint", status="fail", findings=[])]
    edit_related, ambient, unknown = _classify_edit_scope(failing, ["a.py"])
    assert "lint" in unknown


def test_classify_edit_scope_no_file_evidence():
    failing = [_channel("lint", findings=[_issue(file="")])]
    edit_related, ambient, unknown = _classify_edit_scope(failing, ["a.py"])
    assert "lint" in unknown


def test_classify_edit_scope_basename_match(tmp_path):
    failing = [_channel("lint", findings=[_issue(file="a.py")])]
    edit_related, ambient, unknown = _classify_edit_scope(
        failing, [str(tmp_path / "a.py")]
    )
    assert "lint" in edit_related


def test_apply_edit_scope_stable_passthrough():
    from lintgate.controlplane.types import CoherenceResult

    base = CoherenceResult(
        state="stable",
        summary="ok",
        recommended_action="ok",
        silent_channels=[],
        loud_channels=[],
        confidence=1.0,
    )
    r = _apply_edit_scope(base, [], [])
    assert r.state == "stable"


def test_apply_edit_scope_all_ambient_downgrade(tmp_path):
    from lintgate.controlplane.types import CoherenceResult

    other = tmp_path / "other.py"
    other.write_text("")
    changed = tmp_path / "changed.py"
    changed.write_text("")
    base = CoherenceResult(
        state="isolated",
        summary="Issue in lint",
        recommended_action="Fix lint",
        silent_channels=["tests"],
        loud_channels=["lint"],
        confidence=0.8,
    )
    channel_results = [
        _channel("lint", findings=[_issue(file=str(other), severity="informational")])
    ]
    r = _apply_edit_scope(base, channel_results, [str(changed)])
    assert r.edit_scoped is True
    assert r.state == "stable"  # downgraded because all ambient + non-critical


def test_apply_edit_scope_ambient_critical_stays_isolated(tmp_path):
    from lintgate.controlplane.types import CoherenceResult

    other = tmp_path / "other.py"
    other.write_text("")
    changed = tmp_path / "changed.py"
    changed.write_text("")
    base = CoherenceResult(
        state="isolated",
        summary="Issue in lint",
        recommended_action="Fix lint",
        silent_channels=["tests"],
        loud_channels=["lint"],
        confidence=0.8,
    )
    channel_results = [
        _channel("lint", findings=[_issue(file=str(other), severity="blocking")])
    ]
    r = _apply_edit_scope(base, channel_results, [str(changed)])
    assert r.edit_scoped is True
    assert r.state == "isolated"  # not downgraded because ambient has blocking


def test_apply_edit_scope_mixed():
    from lintgate.controlplane.types import CoherenceResult

    base = CoherenceResult(
        state="coupled",
        summary="Issues",
        recommended_action="Fix",
        silent_channels=[],
        loud_channels=["lint", "tests"],
        confidence=0.8,
    )
    channel_results = [
        _channel("lint", findings=[_issue(file="a.py")]),
        _channel("tests", findings=[_issue(file="b.py")]),
    ]
    r = _apply_edit_scope(base, channel_results, ["a.py"])
    assert r.edit_scoped is True
    assert "pre-existing" in r.recommended_action


# ── _has_ambient_critical_findings ────────────────────────────────────


def test_ambient_critical_blocking():
    results = [_channel("lint", findings=[_issue(severity="blocking")])]
    assert _has_ambient_critical_findings(results, ["lint"]) is True


def test_ambient_critical_security_keyword():
    results = [
        _channel("lint", findings=[_issue(kind="secret_leak", severity="warning")])
    ]
    assert _has_ambient_critical_findings(results, ["lint"]) is True


def test_ambient_critical_none():
    results = [_channel("lint", findings=[_issue(severity="informational")])]
    assert _has_ambient_critical_findings(results, ["lint"]) is False


def test_ambient_critical_wrong_channel():
    results = [_channel("lint", findings=[_issue(severity="blocking")])]
    assert _has_ambient_critical_findings(results, ["tests"]) is False


# ── compute_coherence_with_history ────────────────────────────────────


def test_history_no_session():
    results = [_channel("lint", "pass")]
    r = compute_coherence_with_history(results, session=None)
    assert r.state == "stable"


def test_history_regression():
    session = SessionMemory()
    session.coherence_trajectory = ["stable"]
    session.snapshots = [
        SessionSnapshot(loud_channels=[], silent_channels=["lint"]),
    ]
    results = [
        _channel("lint", "fail"),
        _channel("tests", "pass"),
        _channel("deps", "pass"),
    ]
    r = compute_coherence_with_history(results, session=session)
    assert "REGRESSION" in r.summary


def test_history_improvement():
    session = SessionMemory()
    session.coherence_trajectory = ["systemic"]
    session.snapshots = [
        SessionSnapshot(loud_channels=["lint", "tests", "deps"], silent_channels=[]),
    ]
    results = [_channel("lint", "pass"), _channel("tests", "pass")]
    r = compute_coherence_with_history(results, session=session)
    assert "IMPROVEMENT" in r.summary


def test_history_persistent():
    session = SessionMemory()
    session.coherence_trajectory = ["isolated", "isolated", "isolated"]
    session.snapshots = [
        SessionSnapshot(loud_channels=["lint"], silent_channels=["tests"]),
        SessionSnapshot(loud_channels=["lint"], silent_channels=["tests"]),
        SessionSnapshot(loud_channels=["lint"], silent_channels=["tests"]),
    ]
    results = [
        _channel("lint", "fail"),
        _channel("tests", "pass"),
        _channel("deps", "pass"),
    ]
    r = compute_coherence_with_history(results, session=session)
    assert "PERSISTENT" in r.summary


def test_history_resolution():
    session = SessionMemory()
    session.coherence_trajectory = ["isolated"]
    session.snapshots = [
        SessionSnapshot(loud_channels=["lint"], silent_channels=["tests"]),
    ]
    results = [_channel("lint", "pass"), _channel("tests", "pass")]
    r = compute_coherence_with_history(results, session=session)
    assert "RESOLVED" in r.summary


def test_history_tradeoff():
    session = SessionMemory()
    session.coherence_trajectory = ["isolated"]
    session.snapshots = [
        SessionSnapshot(
            loud_channels=["lint"],
            silent_channels=["tests"],
            finding_index={
                "fp1": {"kind": "cognitive_complexity", "count": 5},
                "fp2": {"kind": "too_many_args", "count": 1},
            },
        ),
    ]
    # Current has fewer CC but more too_many_args
    results = [
        _channel(
            "lint",
            "fail",
            findings=[_issue(kind="cognitive_complexity")] * 2
            + [_issue(kind="too_many_args")] * 3,
        ),
        _channel("tests", "pass"),
        _channel("deps", "pass"),
    ]
    r = compute_coherence_with_history(results, session=session)
    assert "TRADEOFF" in r.summary


# ── _detect_persistent_loud ───────────────────────────────────────────


def test_detect_persistent_no_history():
    session = SessionMemory()
    session.snapshots = []
    assert _detect_persistent_loud(session, ["lint"]) == []


def test_detect_persistent_short_history():
    session = SessionMemory()
    session.snapshots = [
        SessionSnapshot(loud_channels=["lint"], silent_channels=[]),
    ]
    # Only 1 snapshot + current = 2 streak, need 3
    assert _detect_persistent_loud(session, ["lint"]) == []


def test_detect_persistent_streak():
    session = SessionMemory()
    session.snapshots = [
        SessionSnapshot(loud_channels=["lint"], silent_channels=[]),
        SessionSnapshot(loud_channels=["lint"], silent_channels=[]),
    ]
    result = _detect_persistent_loud(session, ["lint"])
    assert len(result) == 1
    assert result[0] == ("lint", 3)


# ── _detect_resolutions ──────────────────────────────────────────────


def test_detect_resolutions_no_snapshots():
    session = SessionMemory()
    session.snapshots = []
    assert _detect_resolutions(session, ["lint"]) == []


def test_detect_resolutions_found():
    session = SessionMemory()
    session.snapshots = [
        SessionSnapshot(loud_channels=["lint"], silent_channels=["tests"]),
    ]
    assert _detect_resolutions(session, ["lint"]) == ["lint"]


def test_detect_resolutions_not_resolved():
    session = SessionMemory()
    session.snapshots = [
        SessionSnapshot(loud_channels=["lint"], silent_channels=["tests"]),
    ]
    assert _detect_resolutions(session, ["tests"]) == []


# ── _detect_refactoring_tradeoffs ────────────────────────────────────


def test_tradeoff_detected():
    session = SessionMemory()
    session.snapshots = [
        SessionSnapshot(
            loud_channels=["lint"],
            finding_index={
                "fp1": {"kind": "cognitive_complexity", "count": 5},
                "fp2": {"kind": "too_many_args", "count": 1},
            },
        ),
    ]
    current = [
        _channel(
            "lint",
            "fail",
            findings=[_issue(kind="cognitive_complexity")] * 2
            + [_issue(kind="too_many_args")] * 3,
        ),
    ]
    result = _detect_refactoring_tradeoffs(current, session)
    assert len(result) == 1
    assert result[0]["type"] == "refactor_tradeoff_detected"


def test_tradeoff_no_history():
    session = SessionMemory()
    session.snapshots = []
    assert _detect_refactoring_tradeoffs([], session) == []


def test_tradeoff_no_finding_index():
    session = SessionMemory()
    session.snapshots = [SessionSnapshot(finding_index={})]
    assert _detect_refactoring_tradeoffs([], session) == []
