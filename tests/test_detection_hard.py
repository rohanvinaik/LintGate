"""Tests for lintgate.channels.behavior.detection_hard.

Covers all three hard signal detectors and their private helpers:
- detect_approach_cycling
- detect_failure_amnesia (+ _detect_amnesia_from_action_history,
  _detect_amnesia_from_error_memory, _detect_amnesia_from_hypotheses)
- detect_brute_force_escalation
"""

from __future__ import annotations

import time

from lintgate.channels.behavior.detection_hard import (
    _detect_amnesia_from_action_history,
    _detect_amnesia_from_error_memory,
    _detect_amnesia_from_hypotheses,
    detect_approach_cycling,
    detect_brute_force_escalation,
    detect_failure_amnesia,
)
from lintgate.channels.behavior.scoring import IntentBiasScorer, SignalCoordinator
from lintgate.controlplane.behavior_types import (
    ApproachAttempt,
    BehaviorCompass,
    BehaviorHypothesis,
    CoverageMetrics,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_compass(**overrides) -> BehaviorCompass:
    """Build a BehaviorCompass with sensible defaults, accepting overrides."""
    defaults = {
        "hypotheses": [],
        "approaches": [],
        "coverage": CoverageMetrics(),
        "action_history": [],
        "error_memory": {},
    }
    defaults.update(overrides)
    return BehaviorCompass(**defaults)  # type: ignore[arg-type]  # dict unpacking


def _make_coord(compass: BehaviorCompass, thresholds: dict | None = None) -> SignalCoordinator:
    """Build a SignalCoordinator for testing (no theory profile)."""
    return SignalCoordinator(
        compass=compass,
        thresholds=thresholds or _default_thresholds(),
    )


def _make_scorer(compass: BehaviorCompass) -> IntentBiasScorer:
    """Build an IntentBiasScorer with empty bias weights."""
    return IntentBiasScorer(compass=compass, bias_weights={})


def _default_thresholds() -> dict:
    return {
        "approach_cycling_count": 3,
        "approach_cycling_window_min": 30,
        "failure_amnesia_lookback": 30,
        "brute_force_approach_gap": 0,
        "signal_cooldown": 10,
    }


# ══════════════════════════════════════════════════════════════════════════
# detect_approach_cycling
# ══════════════════════════════════════════════════════════════════════════


class TestDetectApproachCycling:
    """Tests for detect_approach_cycling — hard signal for repeated failures."""

    def test_no_approaches_produces_no_findings(self) -> None:
        compass = _make_compass(approaches=[], action_history=[])
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_approach_cycling(compass, _default_thresholds(), coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert findings == []

    def test_approaches_below_threshold_no_finding(self) -> None:
        now = time.time()
        approaches = [
            ApproachAttempt(
                approach_sig="cmd:a", outcome="failed", last_event=now, started_at=now - 60
            ),
            ApproachAttempt(
                approach_sig="cmd:b", outcome="failed", last_event=now, started_at=now - 30
            ),
        ]
        compass = _make_compass(
            approaches=approaches,
            action_history=[{"ts": now}],
        )
        thresholds = _default_thresholds()
        thresholds["approach_cycling_count"] = 3
        coord = _make_coord(compass, thresholds)
        scorer = _make_scorer(compass)
        detect_approach_cycling(compass, thresholds, coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert findings == []

    def test_at_threshold_fires(self) -> None:
        now = time.time()
        approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:{i}",
                outcome="failed",
                last_event=now - 60,
                started_at=now - 120,
            )
            for i in range(3)
        ]
        compass = _make_compass(
            approaches=approaches,
            action_history=[{"ts": now}],
        )
        thresholds = _default_thresholds()
        coord = _make_coord(compass, thresholds)
        scorer = _make_scorer(compass)
        detect_approach_cycling(compass, thresholds, coord, scorer)
        findings, next_actions, nudge_signals, _ = coord.finalize()
        assert len(findings) == 1
        assert findings[0].kind == "approach_cycling"
        assert "3 approaches" in findings[0].message
        assert "constraint_check" in next_actions[0]["tool"]
        assert "approach_cycling" in nudge_signals

    def test_above_threshold_fires(self) -> None:
        now = time.time()
        approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:{i}",
                outcome="failed",
                last_event=now - 30,
                started_at=now - 300,
            )
            for i in range(5)
        ]
        compass = _make_compass(
            approaches=approaches,
            action_history=[{"ts": now}],
        )
        thresholds = _default_thresholds()
        coord = _make_coord(compass, thresholds)
        scorer = _make_scorer(compass)
        detect_approach_cycling(compass, thresholds, coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert len(findings) == 1
        assert "5 approaches" in findings[0].message

    def test_successful_approaches_not_counted(self) -> None:
        now = time.time()
        approaches = [
            ApproachAttempt(
                approach_sig="cmd:a", outcome="success", last_event=now, started_at=now - 60
            ),
            ApproachAttempt(
                approach_sig="cmd:b", outcome="success", last_event=now, started_at=now - 30
            ),
            ApproachAttempt(
                approach_sig="cmd:c", outcome="failed", last_event=now, started_at=now - 10
            ),
        ]
        compass = _make_compass(
            approaches=approaches,
            action_history=[{"ts": now}],
        )
        thresholds = _default_thresholds()
        coord = _make_coord(compass, thresholds)
        scorer = _make_scorer(compass)
        detect_approach_cycling(compass, thresholds, coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert findings == []

    def test_old_failures_outside_window_ignored(self) -> None:
        now = time.time()
        # All failures happened > 30 min ago (outside the default 30-min window)
        old_time = now - 3600
        approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:{i}",
                outcome="failed",
                last_event=old_time,
                started_at=old_time - 60,
            )
            for i in range(5)
        ]
        compass = _make_compass(
            approaches=approaches,
            action_history=[{"ts": now}],
        )
        thresholds = _default_thresholds()
        coord = _make_coord(compass, thresholds)
        scorer = _make_scorer(compass)
        detect_approach_cycling(compass, thresholds, coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert findings == []

    def test_approach_sigs_truncated_to_four(self) -> None:
        """Only the first 4 approach sigs should appear in the message."""
        now = time.time()
        approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:sig{i}",
                outcome="failed",
                last_event=now - 10,
                started_at=now - 300,
            )
            for i in range(6)
        ]
        compass = _make_compass(
            approaches=approaches,
            action_history=[{"ts": now}],
        )
        thresholds = _default_thresholds()
        coord = _make_coord(compass, thresholds)
        scorer = _make_scorer(compass)
        detect_approach_cycling(compass, thresholds, coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert len(findings) == 1
        msg = findings[0].message
        assert "cmd:sig0" in msg
        assert "cmd:sig3" in msg
        # sig4 and sig5 should NOT appear (truncated at 4)
        assert "cmd:sig4" not in msg

    def test_count_threshold_min_clamped_to_one(self) -> None:
        """approach_cycling_count of 0 or negative is clamped to 1."""
        now = time.time()
        approaches = [
            ApproachAttempt(
                approach_sig="cmd:a",
                outcome="failed",
                last_event=now,
                started_at=now - 60,
            ),
        ]
        compass = _make_compass(
            approaches=approaches,
            action_history=[{"ts": now}],
        )
        thresholds = _default_thresholds()
        thresholds["approach_cycling_count"] = 0  # Should be clamped to 1
        coord = _make_coord(compass, thresholds)
        scorer = _make_scorer(compass)
        detect_approach_cycling(compass, thresholds, coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert len(findings) == 1

    def test_uses_action_history_ts_for_now(self) -> None:
        """When action_history exists, 'now' comes from the last entry."""
        base = 1_000_000.0
        approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:{i}",
                outcome="failed",
                last_event=base - 60,
                started_at=base - 120,
            )
            for i in range(3)
        ]
        compass = _make_compass(
            approaches=approaches,
            action_history=[{"ts": base}],
        )
        thresholds = _default_thresholds()
        coord = _make_coord(compass, thresholds)
        scorer = _make_scorer(compass)
        detect_approach_cycling(compass, thresholds, coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert len(findings) == 1
        # The window_actual should be computed relative to base
        assert "min" in findings[0].message

    def test_finding_has_linter_and_severity(self) -> None:
        now = time.time()
        approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:{i}",
                outcome="failed",
                last_event=now,
                started_at=now - 60,
            )
            for i in range(3)
        ]
        compass = _make_compass(
            approaches=approaches,
            action_history=[{"ts": now}],
        )
        thresholds = _default_thresholds()
        coord = _make_coord(compass, thresholds)
        scorer = _make_scorer(compass)
        detect_approach_cycling(compass, thresholds, coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert findings[0].linter == "behavior_channel"


# ══════════════════════════════════════════════════════════════════════════
# _detect_amnesia_from_action_history
# ══════════════════════════════════════════════════════════════════════════


class TestDetectAmnesiaFromActionHistory:
    """Tests for the action-history source of failure_amnesia."""

    def test_empty_history_returns_false(self) -> None:
        compass = _make_compass()
        coord = _make_coord(compass)
        result = _detect_amnesia_from_action_history([], {}, coord)
        assert result is False

    def test_no_errors_returns_false(self) -> None:
        recent = [{"ts": 100, "err": ""}, {"ts": 200, "err": ""}]
        compass = _make_compass()
        coord = _make_coord(compass)
        result = _detect_amnesia_from_action_history(recent, {}, coord)
        assert result is False

    def test_single_error_returns_false(self) -> None:
        recent = [{"ts": 100, "err": "ModuleNotFoundError"}, {"ts": 200, "err": ""}]
        compass = _make_compass()
        coord = _make_coord(compass)
        result = _detect_amnesia_from_action_history(recent, {}, coord)
        assert result is False

    def test_repeated_error_returns_true(self) -> None:
        recent = [
            {"ts": 100, "err": "ModuleNotFoundError: no module named foo"},
            {"ts": 200, "err": ""},
            {"ts": 300, "err": "ModuleNotFoundError: no module named foo"},
        ]
        compass = _make_compass()
        coord = _make_coord(compass)
        result = _detect_amnesia_from_action_history(recent, {}, coord)
        assert result is True
        findings, _, _, _ = coord.finalize()
        assert len(findings) == 1
        assert findings[0].kind == "failure_amnesia"
        assert "ModuleNotFoundError" in findings[0].message

    def test_gap_minutes_computed(self) -> None:
        recent = [
            {"ts": 1000, "err": "SomeError"},
            {"ts": 1600, "err": "SomeError"},  # 10 min later
        ]
        compass = _make_compass()
        coord = _make_coord(compass)
        _detect_amnesia_from_action_history(recent, {}, coord)
        findings, _, _, _ = coord.finalize()
        assert len(findings) == 1
        assert "10min" in findings[0].message

    def test_gap_zero_when_same_ts(self) -> None:
        ts = 1000.0
        recent = [
            {"ts": ts, "err": "SameError"},
            {"ts": ts, "err": "SameError"},
        ]
        compass = _make_compass()
        coord = _make_coord(compass)
        _detect_amnesia_from_action_history(recent, {}, coord)
        findings, _, _, _ = coord.finalize()
        assert "0min" in findings[0].message

    def test_error_sig_truncated_in_message_at_80(self) -> None:
        long_err = "X" * 200
        recent = [
            {"ts": 100, "err": long_err},
            {"ts": 200, "err": long_err},
        ]
        compass = _make_compass()
        coord = _make_coord(compass)
        _detect_amnesia_from_action_history(recent, {}, coord)
        findings, _, _, _ = coord.finalize()
        # The message truncates the error at 80 chars
        assert "X" * 80 in findings[0].message
        assert "X" * 81 not in findings[0].message

    def test_different_errors_no_finding(self) -> None:
        recent = [
            {"ts": 100, "err": "ErrorA"},
            {"ts": 200, "err": "ErrorB"},
        ]
        compass = _make_compass()
        coord = _make_coord(compass)
        result = _detect_amnesia_from_action_history(recent, {}, coord)
        assert result is False

    def test_three_occurrences_pattern_score_capped(self) -> None:
        recent = [
            {"ts": 100, "err": "RepeatErr"},
            {"ts": 200, "err": "RepeatErr"},
            {"ts": 300, "err": "RepeatErr"},
        ]
        compass = _make_compass()
        coord = _make_coord(compass)
        _detect_amnesia_from_action_history(recent, {}, coord)
        findings, _, _, _ = coord.finalize()
        assert len(findings) == 1
        # pattern_score = min(1.0, 3/2.0) = 1.0 -> reflected in confidence
        assert findings[0].confidence > 0


# ══════════════════════════════════════════════════════════════════════════
# _detect_amnesia_from_error_memory
# ══════════════════════════════════════════════════════════════════════════


class TestDetectAmnesiaFromErrorMemory:
    """Tests for the error-memory source of failure_amnesia."""

    def test_empty_latest_err_returns_false(self) -> None:
        compass = _make_compass()
        coord = _make_coord(compass)
        result = _detect_amnesia_from_error_memory(compass, "", {}, coord)
        assert result is False

    def test_no_matching_memory_returns_false(self) -> None:
        compass = _make_compass(error_memory={})
        coord = _make_coord(compass)
        result = _detect_amnesia_from_error_memory(compass, "SomeError", {}, coord)
        assert result is False

    def test_memory_count_below_two_returns_false(self) -> None:
        from lintgate.controlplane.command_normalization import error_memory_key

        err = "ModuleNotFoundError"
        key = error_memory_key(err)
        compass = _make_compass(
            error_memory={key: {"count": 1, "first_seen": 100.0, "last_seen": 200.0}}
        )
        coord = _make_coord(compass)
        result = _detect_amnesia_from_error_memory(compass, err, {}, coord)
        assert result is False

    def test_memory_count_two_fires(self) -> None:
        from lintgate.controlplane.command_normalization import error_memory_key

        err = "ModuleNotFoundError"
        key = error_memory_key(err)
        compass = _make_compass(
            error_memory={key: {"count": 2, "first_seen": 100.0, "last_seen": 700.0}}
        )
        coord = _make_coord(compass)
        result = _detect_amnesia_from_error_memory(compass, err, {}, coord)
        assert result is True
        findings, _, _, _ = coord.finalize()
        assert len(findings) == 1
        assert findings[0].kind == "failure_amnesia"
        assert "10min" in findings[0].message  # (700-100)/60 = 10

    def test_memory_count_large_fires(self) -> None:
        from lintgate.controlplane.command_normalization import error_memory_key

        err = "SomeError"
        key = error_memory_key(err)
        compass = _make_compass(
            error_memory={key: {"count": 5, "first_seen": 0.0, "last_seen": 3600.0}}
        )
        coord = _make_coord(compass)
        result = _detect_amnesia_from_error_memory(compass, err, {}, coord)
        assert result is True
        findings, _, _, _ = coord.finalize()
        assert "5 times" in findings[0].message

    def test_precheck_nudge_included(self) -> None:
        from lintgate.controlplane.command_normalization import error_memory_key

        err = "SomeError"
        key = error_memory_key(err)
        compass = _make_compass(
            error_memory={key: {"count": 3, "first_seen": 0.0, "last_seen": 60.0}}
        )
        coord = _make_coord(compass)
        _detect_amnesia_from_error_memory(compass, err, {}, coord)
        _, next_actions, nudge_signals, _ = coord.finalize()
        assert len(next_actions) == 1
        assert "constraint_check" in next_actions[0]["tool"]
        assert "failure_amnesia" in nudge_signals


# ══════════════════════════════════════════════════════════════════════════
# _detect_amnesia_from_hypotheses
# ══════════════════════════════════════════════════════════════════════════


class TestDetectAmnesiaFromHypotheses:
    """Tests for the hypothesis source of failure_amnesia."""

    def test_no_hypotheses_no_finding(self) -> None:
        compass = _make_compass(hypotheses=[])
        coord = _make_coord(compass)
        _detect_amnesia_from_hypotheses(compass, "SomeError", {}, coord)
        findings, _, _, _ = coord.finalize()
        assert findings == []

    def test_expired_hypothesis_skipped(self) -> None:
        hyp = BehaviorHypothesis(
            id="h1",
            claim="test claim",
            confidence=0.8,
            status="expired",
            evidence_for=["exit!=0 with: SomeError occurred"],
        )
        compass = _make_compass(hypotheses=[hyp])
        coord = _make_coord(compass)
        _detect_amnesia_from_hypotheses(compass, "SomeError occurred", {}, coord)
        findings, _, _, _ = coord.finalize()
        assert findings == []

    def test_matching_hypothesis_fires(self) -> None:
        hyp = BehaviorHypothesis(
            id="h1",
            claim="connection fails without VPN",
            confidence=0.75,
            status="active",
            evidence_for=["exit!=0 with: ConnectionRefusedError"],
        )
        compass = _make_compass(hypotheses=[hyp])
        coord = _make_coord(compass)
        _detect_amnesia_from_hypotheses(compass, "ConnectionRefusedError", {}, coord)
        findings, _, _, _ = coord.finalize()
        assert len(findings) == 1
        assert "connection fails without VPN" in findings[0].message
        assert "0.75" in findings[0].message

    def test_non_matching_hypothesis_no_finding(self) -> None:
        hyp = BehaviorHypothesis(
            id="h1",
            claim="some claim",
            confidence=0.8,
            status="active",
            evidence_for=["exit!=0 with: TimeoutError"],
        )
        compass = _make_compass(hypotheses=[hyp])
        coord = _make_coord(compass)
        # "ZeroDivisionError" doesn't match "TimeoutError"
        _detect_amnesia_from_hypotheses(compass, "ZeroDivisionError", {}, coord)
        findings, _, _, _ = coord.finalize()
        assert findings == []

    def test_only_first_match_fires(self) -> None:
        """Once a match is found, the function returns — only one finding."""
        hyp1 = BehaviorHypothesis(
            id="h1",
            claim="first claim",
            confidence=0.6,
            status="active",
            evidence_for=["exit!=0 with: RuntimeError occurred"],
        )
        hyp2 = BehaviorHypothesis(
            id="h2",
            claim="second claim",
            confidence=0.9,
            status="active",
            evidence_for=["exit!=0 with: RuntimeError occurred"],
        )
        compass = _make_compass(hypotheses=[hyp1, hyp2])
        coord = _make_coord(compass)
        _detect_amnesia_from_hypotheses(compass, "RuntimeError occurred", {}, coord)
        findings, _, _, _ = coord.finalize()
        assert len(findings) == 1
        assert "first claim" in findings[0].message

    def test_hypothesis_without_error_evidence_skipped(self) -> None:
        hyp = BehaviorHypothesis(
            id="h1",
            claim="a claim",
            confidence=0.5,
            status="active",
            evidence_for=["some non-error evidence"],
        )
        compass = _make_compass(hypotheses=[hyp])
        coord = _make_coord(compass)
        _detect_amnesia_from_hypotheses(compass, "SomeError", {}, coord)
        findings, _, _, _ = coord.finalize()
        assert findings == []

    def test_confirmed_hypothesis_included(self) -> None:
        """Non-expired hypotheses (including confirmed) should be checked."""
        hyp = BehaviorHypothesis(
            id="h1",
            claim="confirmed hypothesis",
            confidence=0.95,
            status="confirmed",
            evidence_for=["exit!=0 with: PermissionDenied error accessing file"],
        )
        compass = _make_compass(hypotheses=[hyp])
        coord = _make_coord(compass)
        _detect_amnesia_from_hypotheses(compass, "PermissionDenied error accessing file", {}, coord)
        findings, _, _, _ = coord.finalize()
        assert len(findings) == 1


# ══════════════════════════════════════════════════════════════════════════
# detect_failure_amnesia (orchestrator)
# ══════════════════════════════════════════════════════════════════════════


class TestDetectFailureAmnesia:
    """Tests for detect_failure_amnesia — orchestrates all three sources."""

    def test_empty_compass_no_findings(self) -> None:
        compass = _make_compass(action_history=[])
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_failure_amnesia(compass, _default_thresholds(), coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert findings == []

    def test_source1_action_history_takes_priority(self) -> None:
        """If action_history detects amnesia, sources 2 and 3 are skipped."""
        from lintgate.controlplane.command_normalization import error_memory_key

        err = "DuplicateError"
        key = error_memory_key(err)
        recent = [
            {"ts": 100, "err": err},
            {"ts": 200, "err": err},
        ]
        hyp = BehaviorHypothesis(
            id="h1",
            claim="hyp claim",
            confidence=0.8,
            status="active",
            evidence_for=[f"exit!=0 with: {err}"],
        )
        compass = _make_compass(
            action_history=recent,
            error_memory={key: {"count": 3, "first_seen": 100.0, "last_seen": 200.0}},
            hypotheses=[hyp],
        )
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_failure_amnesia(compass, _default_thresholds(), coord, scorer)
        findings, _, _, _ = coord.finalize()
        # Only source 1 fires (action history), not source 2 or 3
        assert len(findings) == 1
        assert "action history" in findings[0].message

    def test_source2_error_memory_when_source1_misses(self) -> None:
        """If no repeated error in action_history, falls through to error_memory."""
        from lintgate.controlplane.command_normalization import error_memory_key

        err = "UniqueLatestError"
        key = error_memory_key(err)
        # action_history has this error only once at the end
        recent = [
            {"ts": 100, "err": ""},
            {"ts": 200, "err": err},
        ]
        compass = _make_compass(
            action_history=recent,
            error_memory={key: {"count": 3, "first_seen": 0.0, "last_seen": 200.0}},
        )
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_failure_amnesia(compass, _default_thresholds(), coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert len(findings) == 1
        assert "session memory" in findings[0].message

    def test_source3_hypothesis_when_source12_miss(self) -> None:
        """Falls through to hypothesis matching when sources 1 and 2 miss."""
        err = "HypMatchError detail"
        hyp = BehaviorHypothesis(
            id="h1",
            claim="hypothesis about HypMatch",
            confidence=0.7,
            status="active",
            evidence_for=[f"exit!=0 with: {err}"],
        )
        recent = [
            {"ts": 100, "err": ""},
            {"ts": 200, "err": err},
        ]
        compass = _make_compass(
            action_history=recent,
            hypotheses=[hyp],
            error_memory={},
        )
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_failure_amnesia(compass, _default_thresholds(), coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert len(findings) == 1
        assert "hypothesis" in findings[0].message.lower()

    def test_no_latest_err_short_circuits(self) -> None:
        """If latest action has no error, sources 2 and 3 are not checked."""
        recent = [
            {"ts": 100, "err": ""},
            {"ts": 200, "err": ""},
        ]
        compass = _make_compass(action_history=recent)
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_failure_amnesia(compass, _default_thresholds(), coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert findings == []

    def test_lookback_limits_history(self) -> None:
        """Only the last N events (from lookback) are considered for source 1."""
        err = "RepeatedErr"
        # Put the repeated error outside the lookback window
        recent = [{"ts": i * 10, "err": err if i < 2 else ""} for i in range(50)]
        compass = _make_compass(action_history=recent)
        thresholds = _default_thresholds()
        thresholds["failure_amnesia_lookback"] = 5  # Only look at last 5
        coord = _make_coord(compass, thresholds)
        scorer = _make_scorer(compass)
        detect_failure_amnesia(compass, thresholds, coord, scorer)
        findings, _, _, _ = coord.finalize()
        # The repeated errors at positions 0,1 are outside lookback=5 (last 5 items)
        assert findings == []


# ══════════════════════════════════════════════════════════════════════════
# detect_brute_force_escalation
# ══════════════════════════════════════════════════════════════════════════


class TestDetectBruteForceEscalation:
    """Tests for detect_brute_force_escalation — hard signal."""

    def test_zero_approaches_no_finding(self) -> None:
        compass = _make_compass(coverage=CoverageMetrics(approaches_attempted=0))
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_brute_force_escalation(compass, _default_thresholds(), coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert findings == []

    def test_approaches_equal_constraints_no_finding(self) -> None:
        """gap = 0 with gap_threshold = 0 means gap > threshold is False."""
        compass = _make_compass(
            coverage=CoverageMetrics(approaches_attempted=3, constraints_verified=3)
        )
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_brute_force_escalation(compass, _default_thresholds(), coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert findings == []

    def test_approaches_exceed_constraints_fires(self) -> None:
        compass = _make_compass(
            coverage=CoverageMetrics(approaches_attempted=5, constraints_verified=2)
        )
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_brute_force_escalation(compass, _default_thresholds(), coord, scorer)
        findings, next_actions, nudge_signals, _ = coord.finalize()
        assert len(findings) == 1
        assert findings[0].kind == "brute_force_escalation"
        assert "5 approaches" in findings[0].message
        assert "2 constraints" in findings[0].message
        assert "gap: 3" in findings[0].message.lower()
        assert "constraint_check" in next_actions[0]["tool"]
        assert "brute_force_escalation" in nudge_signals

    def test_gap_exactly_at_threshold_fires(self) -> None:
        """With default gap_threshold=0, gap=1 is > 0 and should fire."""
        compass = _make_compass(
            coverage=CoverageMetrics(approaches_attempted=1, constraints_verified=0)
        )
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_brute_force_escalation(compass, _default_thresholds(), coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert len(findings) == 1

    def test_custom_gap_threshold(self) -> None:
        """With higher gap_threshold, small gaps don't fire."""
        compass = _make_compass(
            coverage=CoverageMetrics(approaches_attempted=3, constraints_verified=1)
        )
        thresholds = _default_thresholds()
        thresholds["brute_force_approach_gap"] = 5
        coord = _make_coord(compass, thresholds)
        scorer = _make_scorer(compass)
        detect_brute_force_escalation(compass, thresholds, coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert findings == []

    def test_large_gap_pattern_score_capped(self) -> None:
        compass = _make_compass(
            coverage=CoverageMetrics(approaches_attempted=20, constraints_verified=0)
        )
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_brute_force_escalation(compass, _default_thresholds(), coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert len(findings) == 1
        # Confidence should be computed and capped
        assert 0.0 < findings[0].confidence <= 1.0

    def test_constraints_exceed_approaches_no_finding(self) -> None:
        compass = _make_compass(
            coverage=CoverageMetrics(approaches_attempted=2, constraints_verified=5)
        )
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_brute_force_escalation(compass, _default_thresholds(), coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert findings == []

    def test_message_format(self) -> None:
        compass = _make_compass(
            coverage=CoverageMetrics(approaches_attempted=7, constraints_verified=1)
        )
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_brute_force_escalation(compass, _default_thresholds(), coord, scorer)
        findings, _, _, _ = coord.finalize()
        msg = findings[0].message
        assert "7 approaches" in msg
        assert "1 constraints" in msg
        assert "6" in msg  # gap = 7 - 1


# ══════════════════════════════════════════════════════════════════════════
# Signal cooldown integration
# ══════════════════════════════════════════════════════════════════════════


class TestSignalCooldown:
    """Verify that cooldown prevents duplicate firings within a window."""

    def test_second_firing_suppressed_by_cooldown(self) -> None:
        now = time.time()
        approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:{i}",
                outcome="failed",
                last_event=now,
                started_at=now - 60,
            )
            for i in range(3)
        ]
        compass = _make_compass(
            approaches=approaches,
            action_history=[{"ts": now}],
        )
        thresholds = _default_thresholds()
        thresholds["signal_cooldown"] = 10
        coord = _make_coord(compass, thresholds)
        scorer = _make_scorer(compass)

        # First detection fires
        detect_approach_cycling(compass, thresholds, coord, scorer)
        # Second detection is suppressed by cooldown
        detect_approach_cycling(compass, thresholds, coord, scorer)

        findings, _, _, suppressed = coord.finalize()
        assert len(findings) == 1
        assert suppressed == 1

    def test_firing_after_cooldown_succeeds(self) -> None:
        now = time.time()
        approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:{i}",
                outcome="failed",
                last_event=now,
                started_at=now - 60,
            )
            for i in range(3)
        ]
        compass = _make_compass(
            approaches=approaches,
            action_history=[{"ts": now}],
        )
        thresholds = _default_thresholds()
        thresholds["signal_cooldown"] = 2
        coord = _make_coord(compass, thresholds)
        scorer = _make_scorer(compass)

        # First detection fires at event_counter=0
        detect_approach_cycling(compass, thresholds, coord, scorer)
        # Advance event_counter past cooldown
        compass.event_counter = 15
        # Second detection should succeed
        detect_approach_cycling(compass, thresholds, coord, scorer)

        findings, _, _, suppressed = coord.finalize()
        assert len(findings) == 2
        assert suppressed == 0


# ══════════════════════════════════════════════════════════════════════════
# Decomposition attribution
# ══════════════════════════════════════════════════════════════════════════


class TestDecompositionAttribution:
    """Verify that decomposition scores are applied to findings."""

    def test_approach_cycling_has_attribution(self) -> None:
        now = time.time()
        approaches = [
            ApproachAttempt(
                approach_sig=f"cmd:{i}",
                outcome="failed",
                last_event=now,
                started_at=now - 60,
            )
            for i in range(4)
        ]
        compass = _make_compass(
            approaches=approaches,
            action_history=[{"ts": now}],
        )
        thresholds = _default_thresholds()
        coord = _make_coord(compass, thresholds)
        scorer = _make_scorer(compass)
        detect_approach_cycling(compass, thresholds, coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert len(findings) == 1
        assert "attribution" in findings[0].evidence
        attr = findings[0].evidence["attribution"]
        # pattern_score = min(1.0, 4/3) = 1.0
        assert attr["pattern"] == 1.0
        # outcome_score = 1.0
        assert attr["outcome"] == 1.0

    def test_brute_force_has_attribution(self) -> None:
        compass = _make_compass(
            coverage=CoverageMetrics(approaches_attempted=4, constraints_verified=0)
        )
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_brute_force_escalation(compass, _default_thresholds(), coord, scorer)
        findings, _, _, _ = coord.finalize()
        assert len(findings) == 1
        attr = findings[0].evidence["attribution"]
        # pattern_score = min(1.0, 4/2.0) = 1.0
        assert attr["pattern"] == 1.0
        # outcome_score = min(1.0, 4/10.0) = 0.4
        assert abs(attr["outcome"] - 0.4) < 0.01

    def test_amnesia_action_history_has_attribution(self) -> None:
        recent = [
            {"ts": 100, "err": "RepeatErr"},
            {"ts": 200, "err": "RepeatErr"},
            {"ts": 300, "err": "RepeatErr"},
        ]
        compass = _make_compass()
        coord = _make_coord(compass)
        _detect_amnesia_from_action_history(recent, {}, coord)
        findings, _, _, _ = coord.finalize()
        assert len(findings) == 1
        attr = findings[0].evidence["attribution"]
        # pattern_score = min(1.0, 3/2.0) = 1.0
        assert attr["pattern"] == 1.0

    def test_amnesia_hypothesis_has_theory_score(self) -> None:
        hyp = BehaviorHypothesis(
            id="h1",
            claim="hypothesis claim",
            confidence=0.65,
            status="active",
            evidence_for=["exit!=0 with: SpecificError"],
        )
        compass = _make_compass(hypotheses=[hyp])
        coord = _make_coord(compass)
        _detect_amnesia_from_hypotheses(compass, "SpecificError", {}, coord)
        findings, _, _, _ = coord.finalize()
        assert len(findings) == 1
        attr = findings[0].evidence["attribution"]
        # theory_score = hyp.confidence = 0.65
        assert attr["theory"] == 0.65
