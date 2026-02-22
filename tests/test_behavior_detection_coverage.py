"""Tests for lintgate/channels/behavior_detection.py — all 9 signal detectors."""
from __future__ import annotations

import time

import pytest

from lintgate.channels.behavior_detection import (
    _detect_amnesia_from_action_history,
    _detect_amnesia_from_error_memory,
    _detect_amnesia_from_hypotheses,
    detect_approach_cycling,
    detect_brute_force_escalation,
    detect_consecutive_failures,
    detect_failure_amnesia,
    detect_premature_action,
    detect_serial_discovery,
    detect_stale_model,
    detect_tool_repetition,
    detect_verification_debt,
)
from lintgate.channels.behavior_scoring import IntentBiasScorer, SignalCoordinator
from lintgate.controlplane.behavior_compass import error_memory_key
from lintgate.controlplane.behavior_types import (
    ApproachAttempt,
    BehaviorCompass,
    BehaviorHypothesis,
    CoverageMetrics,
)


# ── Helpers ───────────────────────────────────────────────────────────


def _fresh_compass(**overrides) -> BehaviorCompass:
    """Return a minimal BehaviorCompass with optional overrides."""
    c = BehaviorCompass()
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


def _make_coord(compass: BehaviorCompass, thresholds=None) -> SignalCoordinator:
    thresholds = thresholds or {"signal_cooldown": 0, "escalation_threshold": 100}
    return SignalCoordinator(compass, thresholds)


def _make_scorer(compass: BehaviorCompass, bias_weights=None) -> IntentBiasScorer:
    return IntentBiasScorer(compass, bias_weights or {})


# ── detect_approach_cycling ──────────────────────────────────────────


class TestDetectApproachCycling:
    def test_no_approaches_no_finding(self):
        compass = _fresh_compass()
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_approach_cycling(compass, {}, coord, scorer)
        findings, _, _ = coord.finalize()
        assert findings == []

    def test_below_threshold_no_finding(self):
        now = time.time()
        compass = _fresh_compass(
            approaches=[
                ApproachAttempt(
                    approach_sig="cmd1", outcome="failed", last_event=now, started_at=now
                ),
                ApproachAttempt(
                    approach_sig="cmd2", outcome="failed", last_event=now, started_at=now
                ),
            ],
            action_history=[{"ts": now}],
        )
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_approach_cycling(compass, {"approach_cycling_count": 3}, coord, scorer)
        findings, _, _ = coord.finalize()
        assert findings == []

    def test_fires_when_threshold_met(self):
        now = time.time()
        approaches = [
            ApproachAttempt(
                approach_sig=f"cmd{i}",
                outcome="failed",
                last_event=now,
                started_at=now - 60,
            )
            for i in range(3)
        ]
        compass = _fresh_compass(
            approaches=approaches,
            action_history=[{"ts": now}],
        )
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_approach_cycling(
            compass, {"approach_cycling_count": 3, "approach_cycling_window_min": 30}, coord, scorer
        )
        findings, actions, _ = coord.finalize()
        assert len(findings) == 1
        assert findings[0].kind == "approach_cycling"
        assert findings[0].severity == "warning"
        assert len(actions) == 1

    def test_old_failures_outside_window_ignored(self):
        now = time.time()
        old = now - 3600  # 60 min ago
        approaches = [
            ApproachAttempt(
                approach_sig=f"cmd{i}",
                outcome="failed",
                last_event=old,
                started_at=old,
            )
            for i in range(4)
        ]
        compass = _fresh_compass(
            approaches=approaches,
            action_history=[{"ts": now}],
        )
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_approach_cycling(
            compass, {"approach_cycling_count": 3, "approach_cycling_window_min": 30}, coord, scorer
        )
        findings, _, _ = coord.finalize()
        assert findings == []


# ── detect_failure_amnesia ───────────────────────────────────────────


class TestDetectFailureAmnesia:
    def test_no_history_no_finding(self):
        compass = _fresh_compass()
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_failure_amnesia(compass, {}, coord, scorer)
        findings, _, _ = coord.finalize()
        assert findings == []

    def test_repeated_error_in_history_fires(self):
        now = time.time()
        compass = _fresh_compass(
            action_history=[
                {"err": "ModuleNotFoundError: foo", "ts": now - 60},
                {"err": "", "ts": now - 30},
                {"err": "ModuleNotFoundError: foo", "ts": now},
            ],
        )
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_failure_amnesia(compass, {}, coord, scorer)
        findings, _, _ = coord.finalize()
        assert len(findings) == 1
        assert findings[0].kind == "failure_amnesia"


# ── _detect_amnesia_from_action_history ─────────────────────────────


class TestDetectAmnesiaFromActionHistory:
    def test_no_errors_returns_false(self):
        recent = [{"ts": 1}, {"ts": 2}]
        coord = _make_coord(_fresh_compass())
        result = _detect_amnesia_from_action_history(recent, {}, coord)
        assert result is False

    def test_single_error_returns_false(self):
        recent = [{"err": "SomeError", "ts": 1}]
        coord = _make_coord(_fresh_compass())
        result = _detect_amnesia_from_action_history(recent, {}, coord)
        assert result is False

    def test_repeated_error_returns_true_and_adds_finding(self):
        recent = [
            {"err": "ImportError: x", "ts": 100},
            {"err": "ImportError: x", "ts": 200},
        ]
        compass = _fresh_compass()
        coord = _make_coord(compass)
        evidence = {"extra": "data"}
        result = _detect_amnesia_from_action_history(recent, evidence, coord)
        assert result is True
        findings, _, _ = coord.finalize()
        assert len(findings) == 1
        assert "action_history" in evidence["source"]


# ── _detect_amnesia_from_error_memory ────────────────────────────────


class TestDetectAmnesiaFromErrorMemory:
    def test_returns_false_when_no_key(self):
        compass = _fresh_compass()
        coord = _make_coord(compass)
        result = _detect_amnesia_from_error_memory(compass, "", {}, coord)
        assert result is False

    def test_returns_false_when_count_below_2(self):
        err = "ImportError: missing module"
        key = error_memory_key(err)
        compass = _fresh_compass(error_memory={key: {"count": 1, "first_seen": 0, "last_seen": 0}})
        coord = _make_coord(compass)
        result = _detect_amnesia_from_error_memory(compass, err, {}, coord)
        assert result is False

    def test_returns_true_when_count_gte_2(self):
        err = "ImportError: missing module"
        key = error_memory_key(err)
        now = time.time()
        compass = _fresh_compass(
            error_memory={key: {"count": 3, "first_seen": now - 300, "last_seen": now}}
        )
        coord = _make_coord(compass)
        evidence = {}
        result = _detect_amnesia_from_error_memory(compass, err, evidence, coord)
        assert result is True
        findings, _, _ = coord.finalize()
        assert len(findings) == 1
        assert evidence["source"] == "error_memory"


# ── _detect_amnesia_from_hypotheses ─────────────────────────────────


class TestDetectAmnesiaFromHypotheses:
    def test_no_hypotheses_no_finding(self):
        compass = _fresh_compass()
        coord = _make_coord(compass)
        _detect_amnesia_from_hypotheses(compass, "some error", {}, coord)
        findings, _, _ = coord.finalize()
        assert findings == []

    def test_expired_hypothesis_skipped(self):
        hyp = BehaviorHypothesis(
            id="h1",
            claim="some constraint",
            confidence=0.5,
            status="expired",
            evidence_for=["exit!=0 with: some error"],
        )
        compass = _fresh_compass(hypotheses=[hyp])
        coord = _make_coord(compass)
        _detect_amnesia_from_hypotheses(compass, "some error", {}, coord)
        findings, _, _ = coord.finalize()
        assert findings == []

    def test_matching_hypothesis_fires(self):
        hyp = BehaviorHypothesis(
            id="h1",
            claim="test constraint claim",
            confidence=0.7,
            status="active",
            evidence_for=["exit!=0 with: connection refused timeout"],
        )
        compass = _fresh_compass(hypotheses=[hyp])
        coord = _make_coord(compass)
        evidence = {}
        _detect_amnesia_from_hypotheses(
            compass, "connection refused timeout", evidence, coord
        )
        findings, _, _ = coord.finalize()
        assert len(findings) == 1
        assert findings[0].kind == "failure_amnesia"
        assert evidence["source"] == "hypothesis_evidence"


# ── detect_brute_force_escalation ───────────────────────────────────


class TestDetectBruteForceEscalation:
    def test_no_approaches_no_finding(self):
        compass = _fresh_compass()
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_brute_force_escalation(compass, {}, coord, scorer)
        findings, _, _ = coord.finalize()
        assert findings == []

    def test_fires_when_gap_exceeds_threshold(self):
        compass = _fresh_compass()
        compass.coverage = CoverageMetrics(approaches_attempted=4, constraints_verified=1)
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_brute_force_escalation(compass, {"brute_force_approach_gap": 2}, coord, scorer)
        findings, _, _ = coord.finalize()
        assert len(findings) == 1
        assert findings[0].kind == "brute_force_escalation"

    def test_no_gap_no_finding(self):
        compass = _fresh_compass()
        compass.coverage = CoverageMetrics(approaches_attempted=3, constraints_verified=3)
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_brute_force_escalation(compass, {"brute_force_approach_gap": 0}, coord, scorer)
        findings, _, _ = coord.finalize()
        assert findings == []


# ── detect_premature_action ─────────────────────────────────────────


class TestDetectPrematureAction:
    def test_no_bash_no_finding(self):
        compass = _fresh_compass()
        compass.coverage = CoverageMetrics(bash_count_recent=0, read_count_recent=5)
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_premature_action(compass, {}, coord, scorer)
        findings, _, _ = coord.finalize()
        assert findings == []

    def test_fires_with_high_ratio_and_failures(self):
        now = time.time()
        history = [{"tool": "Bash", "exit": 1, "ts": now}] * 8
        compass = _fresh_compass(action_history=history)
        compass.coverage = CoverageMetrics(bash_count_recent=8, read_count_recent=1)
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_premature_action(
            compass,
            {"premature_action_ratio": 3.0, "premature_action_failure_rate": 0.5},
            coord,
            scorer,
        )
        findings, actions, nudge_sigs = coord.finalize()
        assert len(findings) == 1
        assert findings[0].kind == "premature_action"
        assert findings[0].severity == "informational"

    def test_low_failure_rate_no_finding(self):
        now = time.time()
        history = [{"tool": "Bash", "exit": 0, "ts": now}] * 8
        compass = _fresh_compass(action_history=history)
        compass.coverage = CoverageMetrics(bash_count_recent=8, read_count_recent=1)
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_premature_action(
            compass,
            {"premature_action_ratio": 3.0, "premature_action_failure_rate": 0.5},
            coord,
            scorer,
        )
        findings, _, _ = coord.finalize()
        assert findings == []


# ── detect_serial_discovery ─────────────────────────────────────────


class TestDetectSerialDiscovery:
    def test_no_hypotheses_no_finding(self):
        compass = _fresh_compass()
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_serial_discovery(compass, {}, coord, scorer)
        findings, _, _ = coord.finalize()
        assert findings == []

    def test_stage1_early_nudge(self):
        hyp = BehaviorHypothesis(
            id="h1",
            claim="test",
            confidence=0.3,
            source="command_failure",
            status="active",
        )
        compass = _fresh_compass(hypotheses=[hyp])
        compass.constraint_check_count_session = 0
        compass.early_nudge_emitted = False
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_serial_discovery(compass, {}, coord, scorer)
        findings, _, _ = coord.finalize()
        assert len(findings) == 1
        assert findings[0].kind == "serial_discovery"
        assert compass.early_nudge_emitted is True

    def test_stage2_three_failure_sourced(self):
        hyps = [
            BehaviorHypothesis(
                id=f"h{i}",
                claim=f"constraint {i}",
                confidence=0.3,
                source="command_failure",
                status="active",
            )
            for i in range(3)
        ]
        compass = _fresh_compass(hypotheses=hyps)
        compass.constraint_check_count_session = 0
        compass.early_nudge_emitted = True  # already emitted
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_serial_discovery(compass, {}, coord, scorer)
        findings, _, _ = coord.finalize()
        assert len(findings) == 1
        assert findings[0].kind == "serial_discovery"


# ── detect_tool_repetition ──────────────────────────────────────────


class TestDetectToolRepetition:
    def test_no_history_no_finding(self):
        compass = _fresh_compass()
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_tool_repetition(compass, {}, coord, scorer)
        findings, _, _ = coord.finalize()
        assert findings == []

    def test_fires_when_sig_repeated(self):
        now = time.time()
        history = [{"sig": "git:status", "ts": now}] * 5
        compass = _fresh_compass(action_history=history)
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_tool_repetition(
            compass, {"tool_repetition_count": 4, "tool_repetition_window_min": 30}, coord, scorer
        )
        findings, _, _ = coord.finalize()
        assert len(findings) == 1
        assert findings[0].kind == "tool_repetition"
        assert "git:status" in findings[0].message

    def test_below_count_threshold(self):
        now = time.time()
        history = [{"sig": "git:status", "ts": now}] * 2
        compass = _fresh_compass(action_history=history)
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_tool_repetition(
            compass, {"tool_repetition_count": 4, "tool_repetition_window_min": 30}, coord, scorer
        )
        findings, _, _ = coord.finalize()
        assert findings == []


# ── detect_consecutive_failures ─────────────────────────────────────


class TestDetectConsecutiveFailures:
    def test_no_history_no_nudge(self):
        compass = _fresh_compass()
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_consecutive_failures(compass, {}, coord, scorer)
        findings, actions, _ = coord.finalize()
        assert findings == []

    def test_fires_nudge_on_consecutive_bash_failures(self):
        history = [
            {"tool": "Bash", "exit": 1},
            {"tool": "Bash", "exit": 2},
            {"tool": "Bash", "exit": 1},
        ]
        compass = _fresh_compass(action_history=history)
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_consecutive_failures(compass, {"consecutive_bash_failures": 3}, coord, scorer)
        findings, actions, _ = coord.finalize()
        # consecutive_failures produces nudge only, no finding
        assert findings == []
        assert len(actions) == 1

    def test_success_breaks_streak(self):
        history = [
            {"tool": "Bash", "exit": 1},
            {"tool": "Bash", "exit": 0},
            {"tool": "Bash", "exit": 1},
        ]
        compass = _fresh_compass(action_history=history)
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_consecutive_failures(compass, {"consecutive_bash_failures": 3}, coord, scorer)
        findings, actions, _ = coord.finalize()
        assert actions == []


# ── detect_verification_debt ────────────────────────────────────────


class TestDetectVerificationDebt:
    def test_short_streak_no_finding(self):
        compass = _fresh_compass()
        compass.intent_history = ["execute"] * 5
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_verification_debt(
            compass, {"verification_debt_streak": 8}, coord, scorer
        )
        findings, _, _ = coord.finalize()
        assert findings == []

    def test_fires_when_streak_exceeds_threshold(self):
        compass = _fresh_compass()
        compass.intent_history = ["execute"] * 10
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_verification_debt(
            compass, {"verification_debt_streak": 8}, coord, scorer
        )
        findings, actions, _ = coord.finalize()
        assert len(findings) == 1
        assert findings[0].kind == "verification_debt"
        assert len(actions) == 1

    def test_verify_breaks_streak(self):
        compass = _fresh_compass()
        compass.intent_history = ["execute"] * 5 + ["verify"] + ["execute"] * 5
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_verification_debt(
            compass, {"verification_debt_streak": 8}, coord, scorer
        )
        findings, _, _ = coord.finalize()
        assert findings == []


# ── detect_stale_model ──────────────────────────────────────────────


class TestDetectStaleModel:
    def test_no_approaches_no_finding(self):
        compass = _fresh_compass()
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_stale_model(compass, {}, coord, scorer)
        findings, _, _ = coord.finalize()
        assert findings == []

    def test_fires_when_approaches_at_same_hyp_version(self):
        now = time.time()
        approaches = [
            ApproachAttempt(
                approach_sig=f"cmd{i}",
                hyp_version_at_start=0,
                started_at=now + i,
            )
            for i in range(3)
        ]
        compass = _fresh_compass(approaches=approaches)
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_stale_model(
            compass, {"stale_model_approach_changes": 2}, coord, scorer
        )
        findings, _, _ = coord.finalize()
        assert len(findings) == 1
        assert findings[0].kind == "stale_model"

    def test_different_hyp_versions_no_finding(self):
        now = time.time()
        approaches = [
            ApproachAttempt(
                approach_sig=f"cmd{i}",
                hyp_version_at_start=i,
                started_at=now + i,
            )
            for i in range(3)
        ]
        compass = _fresh_compass(approaches=approaches)
        coord = _make_coord(compass)
        scorer = _make_scorer(compass)
        detect_stale_model(
            compass, {"stale_model_approach_changes": 4}, coord, scorer
        )
        findings, _, _ = coord.finalize()
        assert findings == []
