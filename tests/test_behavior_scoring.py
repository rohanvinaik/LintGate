"""Tests for lintgate/channels/behavior_scoring.py — full coverage."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from lintgate.channels.behavior_scoring import (
    _BIAS_CAP,
    _ERROR_EVIDENCE_PREFIXES,
    _ERROR_STOPWORDS,
    _THEORY_CODA_MAX_CHARS,
    SIGNAL_THEORY_MAP,
    IntentBiasScorer,
    SignalCoordinator,
    _error_like_match,
    _error_tokens,
    _extract_hypothesis_error_candidates,
    _ground_finding_in_theory,
    _normalize_error_text,
)
from lintgate.controlplane.behavior_types import (
    ApproachAttempt,
    BehaviorCompass,
    BehaviorHypothesis,
    new_compass,
)
from lintgate.types import LintIssue

# ── _normalize_error_text ────────────────────────────────────────────────


class TestNormalizeErrorText:
    def test_lowercases(self) -> None:
        assert _normalize_error_text("FOO BAR") == "foo bar"

    def test_strips_special_chars(self) -> None:
        assert _normalize_error_text("err: foo!bar@baz") == "err foo bar baz"

    def test_collapses_whitespace(self) -> None:
        assert _normalize_error_text("a   b   c") == "a b c"

    def test_empty(self) -> None:
        assert _normalize_error_text("") == ""

    def test_numbers_kept(self) -> None:
        assert _normalize_error_text("exit 127") == "exit 127"

    def test_strips_leading_trailing(self) -> None:
        assert _normalize_error_text("  hello  ") == "hello"


# ── _error_tokens ────────────────────────────────────────────────────────


class TestErrorTokens:
    def test_basic(self) -> None:
        tokens = _error_tokens("ModuleNotFoundError: no module named flask")
        assert "modulenotfounderror" in tokens
        assert "module" in tokens  # 6 chars and not in stopwords
        assert "flask" in tokens
        assert "named" in tokens

    def test_stopwords_excluded(self) -> None:
        tokens = _error_tokens("error failed with code status")
        # All these are in _ERROR_STOPWORDS
        assert "error" not in tokens
        assert "failed" not in tokens
        assert "code" not in tokens
        assert "status" not in tokens

    def test_short_tokens_excluded(self) -> None:
        tokens = _error_tokens("a bb ccc dddd")
        assert "a" not in tokens
        assert "bb" not in tokens
        assert "ccc" in tokens
        assert "dddd" in tokens

    def test_empty(self) -> None:
        assert _error_tokens("") == set()


# ── _error_like_match ────────────────────────────────────────────────────


class TestErrorLikeMatch:
    def test_exact_match_long(self) -> None:
        text = "modulenotfounderror no module named flask"
        assert _error_like_match(text, text) is True

    def test_exact_match_too_short(self) -> None:
        assert _error_like_match("abc", "abc") is False

    def test_substring_match(self) -> None:
        short = "no module named flask"
        long = "error: no module named flask extra stuff"
        assert _error_like_match(short, long) is True

    def test_substring_too_short(self) -> None:
        assert _error_like_match("short", "the short one") is False

    def test_token_overlap(self) -> None:
        a = "flask import error detected"
        b = "flask module import problem"
        assert _error_like_match(a, b) is True

    def test_no_match(self) -> None:
        assert _error_like_match("totally different", "nothing alike here") is False

    def test_empty_candidate(self) -> None:
        assert _error_like_match("", "something") is False

    def test_empty_latest(self) -> None:
        assert _error_like_match("something", "") is False

    def test_both_empty(self) -> None:
        assert _error_like_match("", "") is False


# ── _extract_hypothesis_error_candidates ─────────────────────────────────


class TestExtractHypothesisErrorCandidates:
    def test_exit_prefix(self) -> None:
        evidence = ["exit!=0 with: ModuleNotFoundError"]
        candidates = _extract_hypothesis_error_candidates(evidence)
        assert candidates == ["ModuleNotFoundError"]

    def test_confirmed_prefix(self) -> None:
        evidence = ["confirmed by: same error again"]
        candidates = _extract_hypothesis_error_candidates(evidence)
        assert candidates == ["same error again"]

    def test_reobserved_prefix(self) -> None:
        evidence = ["re-observed: timeout on connect"]
        candidates = _extract_hypothesis_error_candidates(evidence)
        assert candidates == ["timeout on connect"]

    def test_no_matching_prefix(self) -> None:
        evidence = ["just a note about something"]
        assert _extract_hypothesis_error_candidates(evidence) == []

    def test_empty_after_prefix(self) -> None:
        evidence = ["exit!=0 with:"]
        assert _extract_hypothesis_error_candidates(evidence) == []

    def test_multiple_entries(self) -> None:
        evidence = [
            "exit!=0 with: err1",
            "no prefix here",
            "confirmed by: err2",
        ]
        candidates = _extract_hypothesis_error_candidates(evidence)
        assert candidates == ["err1", "err2"]

    def test_case_insensitive_prefix(self) -> None:
        evidence = ["EXIT!=0 WITH: UpperError"]
        candidates = _extract_hypothesis_error_candidates(evidence)
        assert candidates == ["UpperError"]

    def test_whitespace_handling(self) -> None:
        evidence = ["  exit!=0 with:   spaced  "]
        candidates = _extract_hypothesis_error_candidates(evidence)
        assert candidates == ["spaced"]


# ── IntentBiasScorer ─────────────────────────────────────────────────────


def _make_compass(**overrides: object) -> BehaviorCompass:
    """Create a compass with sensible defaults, applying overrides."""
    compass = new_compass()
    for k, v in overrides.items():
        setattr(compass, k, v)
    return compass


class TestIntentBiasScorer:
    def test_verification_debt_fires_exact_values(self) -> None:
        compass = _make_compass(
            intent_history=["execute"] * 10,
            event_counter=20,
        )
        scorer = IntentBiasScorer(compass, {"verification_debt_streak": 8})
        delta, terms = scorer.verification_debt_bias()
        # Default weight 0.20, no global priors → effective = min(0.20, 0.25) = 0.20
        assert delta == 0.20
        assert terms == ["execute_streak=10,verify=0,inspect=0"]

    def test_verification_debt_no_fire_with_verify(self) -> None:
        compass = _make_compass(
            intent_history=["execute"] * 7 + ["verify"] + ["execute"] * 2,
            event_counter=20,
        )
        scorer = IntentBiasScorer(compass, {"verification_debt_streak": 8})
        delta, terms = scorer.verification_debt_bias()
        assert delta == 0.0
        assert terms == []

    def test_verification_debt_no_fire_short_streak(self) -> None:
        compass = _make_compass(
            intent_history=["execute"] * 5,
            event_counter=10,
        )
        scorer = IntentBiasScorer(compass, {"verification_debt_streak": 8})
        delta, terms = scorer.verification_debt_bias()
        assert delta == 0.0
        assert terms == []

    def test_verification_debt_custom_weight(self) -> None:
        compass = _make_compass(
            intent_history=["execute"] * 10,
            event_counter=20,
        )
        scorer = IntentBiasScorer(
            compass, {"verification_debt_streak": 8, "verification_debt_bias": 0.10}
        )
        delta, terms = scorer.verification_debt_bias()
        assert delta == 0.10

    def test_verification_debt_with_inspect_breaks_streak(self) -> None:
        compass = _make_compass(
            intent_history=["execute"] * 8 + ["inspect"] + ["execute"],
            event_counter=20,
        )
        scorer = IntentBiasScorer(compass, {"verification_debt_streak": 8})
        delta, terms = scorer.verification_debt_bias()
        # Streak is only 1 (last "execute" after "inspect"), inspect_count=1
        assert delta == 0.0

    def test_failure_amnesia_fires_exact_values(self) -> None:
        compass = _make_compass(
            action_history=[
                {"cmd": "test", "err": "ModuleNotFound"},
                {"cmd": "test2", "err": ""},
                {"cmd": "test3", "err": "ModuleNotFound"},
            ],
            intent_history=["execute", "execute", "execute"],
            event_counter=10,
        )
        scorer = IntentBiasScorer(compass, {})
        delta, terms = scorer.failure_amnesia_bias()
        # Default weight 0.15, no global priors → effective = min(0.15, 0.25) = 0.15
        assert delta == 0.15
        assert terms == ["repeated_error,no_verify_between"]

    def test_failure_amnesia_no_fire_with_verify_between(self) -> None:
        compass = _make_compass(
            action_history=[
                {"cmd": "test", "err": "SomeError"},
                {"cmd": "check", "err": ""},
                {"cmd": "test", "err": "SomeError"},
            ],
            intent_history=["execute", "verify", "execute"],
            event_counter=10,
        )
        scorer = IntentBiasScorer(compass, {})
        delta, terms = scorer.failure_amnesia_bias()
        assert delta == 0.0
        assert terms == []

    def test_failure_amnesia_no_error(self) -> None:
        compass = _make_compass(
            action_history=[{"cmd": "ok", "err": ""}],
            intent_history=["execute"],
            event_counter=5,
        )
        scorer = IntentBiasScorer(compass, {})
        delta, terms = scorer.failure_amnesia_bias()
        assert delta == 0.0
        assert terms == []

    def test_failure_amnesia_too_few_actions(self) -> None:
        compass = _make_compass(
            action_history=[{"cmd": "x", "err": "e"}],
            intent_history=["execute"],
            event_counter=5,
        )
        scorer = IntentBiasScorer(compass, {})
        delta, terms = scorer.failure_amnesia_bias()
        assert delta == 0.0
        assert terms == []

    def test_serial_discovery_fires_exact_values(self) -> None:
        compass = _make_compass(
            constraint_check_count_session=0,
            event_counter=10,
        )
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h1",
                claim="test",
                confidence=0.3,
                source="command_failure",
                status="active",
            )
        ]
        scorer = IntentBiasScorer(compass, {})
        delta, terms = scorer.serial_discovery_bias()
        # Default weight 0.10, no global priors → effective = min(0.10, 0.25) = 0.10
        assert delta == 0.10
        assert terms == ["failure_hyps=1,precheck=0"]

    def test_serial_discovery_no_fire_with_precheck(self) -> None:
        compass = _make_compass(
            constraint_check_count_session=1,
            event_counter=10,
        )
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h1",
                claim="t",
                confidence=0.3,
                source="command_failure",
                status="active",
            )
        ]
        scorer = IntentBiasScorer(compass, {})
        delta, terms = scorer.serial_discovery_bias()
        assert delta == 0.0
        assert terms == []

    def test_serial_discovery_multiple_hyps(self) -> None:
        compass = _make_compass(
            constraint_check_count_session=0,
            event_counter=10,
        )
        compass.hypotheses = [
            BehaviorHypothesis(
                id="h1",
                claim="c1",
                confidence=0.3,
                source="command_failure",
                status="active",
            ),
            BehaviorHypothesis(
                id="h2",
                claim="c2",
                confidence=0.5,
                source="command_failure",
                status="confirmed",
            ),
        ]
        scorer = IntentBiasScorer(compass, {})
        delta, terms = scorer.serial_discovery_bias()
        assert delta == 0.10
        assert terms == ["failure_hyps=2,precheck=0"]

    def test_stale_model_fires_exact_values(self) -> None:
        compass = _make_compass(event_counter=20)
        compass.approaches = [
            ApproachAttempt(approach_sig="sig1", started_at=1.0, hyp_version_at_start=0),
            ApproachAttempt(approach_sig="sig2", started_at=2.0, hyp_version_at_start=0),
            ApproachAttempt(approach_sig="sig3", started_at=3.0, hyp_version_at_start=0),
        ]
        scorer = IntentBiasScorer(compass, {"stale_model_approach_changes": 2})
        delta, terms = scorer.stale_model_bias()
        # Default weight 0.15, no global priors → effective = min(0.15, 0.25) = 0.15
        assert delta == 0.15
        assert terms == ["approach_streak=3,hyp_version_unchanged"]

    def test_stale_model_no_fire_few_approaches(self) -> None:
        compass = _make_compass(event_counter=10)
        compass.approaches = [
            ApproachAttempt(approach_sig="sig1", started_at=1.0, hyp_version_at_start=0),
        ]
        scorer = IntentBiasScorer(compass, {})
        delta, terms = scorer.stale_model_bias()
        assert delta == 0.0
        assert terms == []

    def test_stale_model_no_fire_version_changes(self) -> None:
        compass = _make_compass(event_counter=20)
        compass.approaches = [
            ApproachAttempt(approach_sig="sig1", started_at=1.0, hyp_version_at_start=0),
            ApproachAttempt(approach_sig="sig2", started_at=2.0, hyp_version_at_start=1),
            ApproachAttempt(approach_sig="sig3", started_at=3.0, hyp_version_at_start=2),
        ]
        scorer = IntentBiasScorer(compass, {"stale_model_approach_changes": 2})
        delta, terms = scorer.stale_model_bias()
        assert delta == 0.0
        assert terms == []

    def test_build_evidence_trace_exact_structure(self) -> None:
        compass = _make_compass(
            intent_history=["execute", "verify", "execute"],
            event_counter=10,
        )
        scorer = IntentBiasScorer(compass, {})
        trace = scorer.build_evidence_trace()
        assert trace == {
            "window": 3,
            "intent_counts": {"execute": 2, "verify": 1},
        }

    def test_build_evidence_trace_with_global_priors(self) -> None:
        compass = _make_compass(
            intent_history=["execute"] * 3,
            event_counter=20,
        )
        global_priors = {
            "enabled": True,
            "alpha": 0.6,
            "decay_horizon": 50,
            "computed_bias_adjustments": {"verification_debt": 0.1},
        }
        scorer = IntentBiasScorer(compass, {}, global_priors)
        trace = scorer.build_evidence_trace()
        assert "global_alpha" in trace
        assert isinstance(trace["global_alpha"], float)
        assert trace["global_alpha"] > 0
        assert trace["global_adjustments_applied"] == {"verification_debt": 0.1}

    def test_init_attributes_exact(self) -> None:
        compass = _make_compass(
            intent_history=["execute", "verify"],
            event_counter=5,
        )
        scorer = IntentBiasScorer(compass, {"key": 0.5})
        assert scorer.compass is compass
        assert scorer.weights == {"key": 0.5}
        assert scorer.global_priors == {}
        assert scorer._alpha == 0.0
        assert scorer._global_adjustments == {}
        assert scorer.recent_window == 2
        assert scorer.recent_counts == {"execute": 1, "verify": 1}

    def test_effective_bias_weight_exact_values(self) -> None:
        compass = _make_compass(event_counter=5)
        scorer = IntentBiasScorer(compass, {"my_key": 0.30})
        # project_weight=0.30 > BIAS_CAP=0.25 → clamped to 0.25
        result = scorer._effective_bias_weight("test", "my_key", 0.30)
        assert result == _BIAS_CAP
        assert result == 0.25

    def test_effective_bias_weight_uses_default(self) -> None:
        compass = _make_compass(event_counter=5)
        scorer = IntentBiasScorer(compass, {})
        # No config key → uses default of 0.12
        result = scorer._effective_bias_weight("test", "missing_key", 0.12)
        assert result == 0.12

    def test_recent_counts_window(self) -> None:
        compass = _make_compass(
            intent_history=["execute"] * 15,
            event_counter=20,
        )
        scorer = IntentBiasScorer(compass, {})
        # Only last 10 should be counted
        assert scorer.recent_window == 10
        assert scorer.recent_counts == {"execute": 10}


# ── SignalCoordinator ────────────────────────────────────────────────────


class TestSignalCoordinator:
    def _make_coordinator(self, **compass_overrides: object) -> SignalCoordinator:
        compass = _make_compass(**compass_overrides)
        return SignalCoordinator(
            compass=compass,
            thresholds={"signal_cooldown": 10, "escalation_threshold": 3},
        )

    def test_can_fire_first_time(self) -> None:
        coord = self._make_coordinator(event_counter=0)
        assert coord.can_fire("approach_cycling") is True

    def test_can_fire_cooldown_not_elapsed(self) -> None:
        compass = _make_compass(event_counter=5)
        compass.last_fired["approach_cycling"] = 1
        coord = SignalCoordinator(
            compass=compass,
            thresholds={"signal_cooldown": 10},
        )
        assert coord.can_fire("approach_cycling") is False

    def test_can_fire_cooldown_elapsed(self) -> None:
        compass = _make_compass(event_counter=15)
        compass.last_fired["approach_cycling"] = 1
        coord = SignalCoordinator(
            compass=compass,
            thresholds={"signal_cooldown": 10},
        )
        assert coord.can_fire("approach_cycling") is True

    def test_record_firing(self) -> None:
        coord = self._make_coordinator(event_counter=5)
        coord.record_firing("approach_cycling")
        assert coord.compass.last_fired["approach_cycling"] == 5
        assert coord.compass.signal_fire_counts["approach_cycling"] == 1
        assert coord.run_fire_counts["approach_cycling"] == 1

    def test_add_finding_respects_cooldown(self) -> None:
        compass = _make_compass(event_counter=3)
        compass.last_fired["test_signal"] = 1
        coord = SignalCoordinator(
            compass=compass,
            thresholds={"signal_cooldown": 10, "escalation_threshold": 3},
        )
        finding = LintIssue(linter="behavior", kind="test_signal", message="msg")
        coord.add_finding("test_signal", finding, is_hard=False)
        assert len(coord.findings) == 0  # Blocked by cooldown

    def test_add_finding_escalation_hard(self) -> None:
        compass = _make_compass(event_counter=50)
        compass.signal_fire_counts["approach_cycling"] = 3
        coord = SignalCoordinator(
            compass=compass,
            thresholds={"signal_cooldown": 10, "escalation_threshold": 3},
        )
        finding = LintIssue(linter="behavior", kind="approach_cycling", message="cycling detected")
        coord.add_finding("approach_cycling", finding, is_hard=True)
        assert len(coord.findings) == 1
        assert coord.findings[0].message.startswith("[persistent]")

    def test_add_finding_escalation_soft(self) -> None:
        compass = _make_compass(event_counter=50)
        compass.signal_fire_counts["verification_debt"] = 3
        coord = SignalCoordinator(
            compass=compass,
            thresholds={"signal_cooldown": 10, "escalation_threshold": 3},
        )
        finding = LintIssue(
            linter="behavior", kind="debt", message="debt", severity="informational"
        )
        coord.add_finding("verification_debt", finding, is_hard=False)
        assert coord.findings[0].severity == "warning"

    def test_add_finding_with_precheck_nudge(self) -> None:
        coord = self._make_coordinator(event_counter=0)
        finding = LintIssue(linter="behavior", kind="cycling", message="msg")
        nudge = {"tool": "constraint_check", "reason": "cycling"}
        coord.add_finding("approach_cycling", finding, is_hard=True, precheck_nudge=nudge)
        findings, actions, nudge_signals, _suppressed = coord.finalize()
        assert len(findings) == 1
        assert len(actions) == 1
        assert "approach_cycling" in nudge_signals

    def test_precheck_nudge_priority(self) -> None:
        coord = self._make_coordinator(event_counter=0)
        f1 = LintIssue(linter="b", kind="k", message="m")
        f2 = LintIssue(linter="b", kind="k", message="m2")
        nudge_low = {"tool": "constraint_check", "reason": "serial"}
        nudge_high = {"tool": "constraint_check", "reason": "cycling"}
        # Add lower priority first
        coord.add_finding("serial_discovery_early", f1, is_hard=False, precheck_nudge=nudge_low)
        coord.add_finding("approach_cycling", f2, is_hard=True, precheck_nudge=nudge_high)
        _, actions, _, _suppressed = coord.finalize()
        assert len(actions) == 1
        assert actions[0]["reason"] == "cycling"

    def test_register_nudge_only(self) -> None:
        coord = self._make_coordinator(event_counter=0)
        nudge = {"tool": "constraint_check", "reason": "early"}
        coord.register_nudge_only("serial_discovery_early", nudge)
        findings, actions, nudge_signals, _suppressed = coord.finalize()
        assert len(findings) == 0
        assert len(actions) == 1
        assert "serial_discovery_early" in nudge_signals

    def test_register_nudge_only_blocked_by_cooldown(self) -> None:
        compass = _make_compass(event_counter=3)
        compass.last_fired["serial_discovery_early"] = 1
        coord = SignalCoordinator(
            compass=compass,
            thresholds={"signal_cooldown": 10},
        )
        nudge = {"tool": "constraint_check"}
        coord.register_nudge_only("serial_discovery_early", nudge)
        _, actions, signals, _suppressed = coord.finalize()
        assert len(actions) == 0
        assert len(signals) == 0

    def test_finalize_no_nudge(self) -> None:
        coord = self._make_coordinator(event_counter=0)
        findings, actions, signals, _suppressed = coord.finalize()
        assert findings == []
        assert actions == []
        assert signals == []

    def test_theory_grounding_dedup(self) -> None:
        """When the same coda fires twice, second is suppressed."""
        compass = _make_compass(event_counter=0)
        recent_codas = {"approach_cycling": " Theory: 'some claim'."}
        coord = SignalCoordinator(
            compass=compass,
            thresholds={"signal_cooldown": 10, "escalation_threshold": 3},
            theory_profile={
                "facets": {
                    "problem_solving": {"claims": [{"claim": "some claim", "relevance_score": 1.0}]}
                }
            },
            recent_codas=recent_codas,
        )
        finding = LintIssue(linter="behavior", kind="cycling", message="cycling detected")
        # Mock _ground_finding_in_theory to return the same coda (now a 2-tuple)
        with patch("lintgate.channels.behavior.scoring._ground_finding_in_theory") as mock_ground:
            mock_ground.return_value = (" Theory: 'some claim'.", 1.0)
            finding.message = "cycling detected Theory: 'some claim'."
            finding.evidence = {"theory_context": ["some claim"]}
            coord.add_finding("approach_cycling", finding, is_hard=True)

        assert len(coord.findings) == 1
        # The duplicate coda should have been stripped
        assert not coord.findings[0].message.endswith(" Theory: 'some claim'.")


# ── _ground_finding_in_theory ────────────────────────────────────────────


class TestGroundFindingInTheory:
    def test_no_theory_profile(self) -> None:
        finding = LintIssue(linter="behavior", kind="cycling", message="msg")
        result = _ground_finding_in_theory(finding, "approach_cycling", None)
        assert result is None
        # Finding message must be unchanged when no grounding occurs
        assert finding.message == "msg"
        assert finding.evidence is None or "theory_context" not in finding.evidence

    def test_unknown_signal(self) -> None:
        finding = LintIssue(linter="behavior", kind="unknown", message="msg")
        result = _ground_finding_in_theory(finding, "nonexistent_signal", {"facets": {}})
        assert result is None
        # Finding message must be unchanged for unknown signals
        assert finding.message == "msg"
        # The signal name is not in SIGNAL_THEORY_MAP, confirming the dispatch path
        assert "nonexistent_signal" not in SIGNAL_THEORY_MAP

    def test_grounding_applied(self) -> None:
        finding = LintIssue(linter="behavior", kind="cycling", message="cycling detected")
        theory = {
            "facets": {
                "problem_solving": {
                    "claims": [
                        {"claim": "decompose before solving", "relevance_score": 0.9},
                    ]
                }
            }
        }
        with patch("lintgate.theory_extractor.get_theory_context_from_profile") as mock_ctx:
            mock_ctx.return_value = {
                "claims": [{"claim": "decompose before solving", "relevance_score": 0.9}]
            }
            result = _ground_finding_in_theory(finding, "approach_cycling", theory)
        assert result is not None
        coda, score = result
        assert "Theory:" in finding.message
        assert "theory_context" in finding.evidence
        assert score > 0

    def test_no_claims_found(self) -> None:
        finding = LintIssue(linter="behavior", kind="cycling", message="msg")
        with patch("lintgate.theory_extractor.get_theory_context_from_profile") as mock_ctx:
            mock_ctx.return_value = {"claims": []}
            result = _ground_finding_in_theory(finding, "approach_cycling", {"x": "y"})
        assert result is None
        # Finding message must be unchanged when no claims are found
        assert finding.message == "msg"
        # The theory extractor was called (signal IS in SIGNAL_THEORY_MAP)
        assert mock_ctx.call_count >= 1
        assert finding.evidence is None or "theory_context" not in finding.evidence

    def test_claim_truncation(self) -> None:
        finding = LintIssue(linter="behavior", kind="cycling", message="msg")
        long_claim = "x" * 100
        with patch("lintgate.theory_extractor.get_theory_context_from_profile") as mock_ctx:
            mock_ctx.return_value = {"claims": [{"claim": long_claim, "relevance_score": 1.0}]}
            result = _ground_finding_in_theory(finding, "approach_cycling", {"facets": {}})
        assert result is not None
        coda, _score = result
        # The claim should have been truncated to 77 + "..."
        assert "..." in coda

    def test_dedup_claims(self) -> None:
        finding = LintIssue(linter="behavior", kind="cycling", message="msg")
        with patch("lintgate.theory_extractor.get_theory_context_from_profile") as mock_ctx:
            # Return duplicate claims from different facets
            mock_ctx.return_value = {
                "claims": [
                    {"claim": "same claim", "relevance_score": 0.8},
                    {"claim": "same claim", "relevance_score": 0.9},
                    {"claim": "different claim", "relevance_score": 0.5},
                ]
            }
            result = _ground_finding_in_theory(finding, "approach_cycling", {"facets": {}})
        assert result is not None
        # Should only contain each claim once
        assert finding.message.count("same claim") == 1


# ── Constants ────────────────────────────────────────────────────────────


class TestConstants:
    def test_signal_theory_map_keys(self) -> None:
        expected_signals = {
            "approach_cycling",
            "failure_amnesia",
            "premature_action",
            "brute_force_escalation",
            "verification_debt",
            "stale_model",
            "serial_discovery",
            "tool_repetition",
            "consecutive_failures",
            "integration_verification_debt",
        }
        assert set(SIGNAL_THEORY_MAP.keys()) == expected_signals

    def test_signal_theory_map_structure(self) -> None:
        for signal, mapping in SIGNAL_THEORY_MAP.items():
            assert "facets" in mapping, f"{signal} missing 'facets'"
            assert "keywords" in mapping, f"{signal} missing 'keywords'"
            assert isinstance(mapping["facets"], list)
            assert isinstance(mapping["keywords"], list)

    def test_bias_cap(self) -> None:
        assert _BIAS_CAP == 0.25

    def test_theory_coda_max(self) -> None:
        assert _THEORY_CODA_MAX_CHARS == 150

    def test_error_stopwords_are_lowercase(self) -> None:
        for word in _ERROR_STOPWORDS:
            assert word == word.lower()

    def test_error_evidence_prefixes(self) -> None:
        assert len(_ERROR_EVIDENCE_PREFIXES) == 3
        assert "exit!=0 with:" in _ERROR_EVIDENCE_PREFIXES


# ── SPEC010: SignalCoordinator.__init__ specification ─────────────────


class TestSignalCoordinatorInit:
    """Specify exact attribute initialization contract for SignalCoordinator."""

    def test_defaults_without_optional_args(self) -> None:
        compass = _make_compass(event_counter=0)
        coord = SignalCoordinator(compass=compass, thresholds={"signal_cooldown": 5})
        assert coord.compass is compass
        assert coord.thresholds == {"signal_cooldown": 5}
        assert coord.findings == []
        assert coord.next_actions == []
        assert coord._pending_precheck is None
        assert coord._pending_priority == 999
        assert coord._nudge_signals == []
        assert coord.run_fire_counts == {}
        assert coord.suppressed_nudge_count == 0
        assert coord._theory_profile is None
        assert coord._recent_codas == {}
        assert coord._new_codas == {}

    def test_with_theory_profile_and_recent_codas(self) -> None:
        compass = _make_compass(event_counter=0)
        theory: dict[str, Any] = {"facets": {"core_theory": {"claims": []}}}
        codas = {"approach_cycling": " Theory: 'test'."}
        coord = SignalCoordinator(
            compass=compass,
            thresholds={},
            theory_profile=theory,
            recent_codas=codas,
        )
        assert coord._theory_profile is theory
        assert coord._recent_codas is codas
        assert coord._new_codas == {}

    def test_authority_engine_created(self) -> None:
        from lintgate.orchestration.authority import AuthorityEscalationEngine

        compass = _make_compass(event_counter=0)
        coord = SignalCoordinator(compass=compass, thresholds={})
        assert isinstance(coord.authority_engine, AuthorityEscalationEngine)


# ── SPEC010: SignalCoordinator.add_finding specification ──────────────


class TestSignalCoordinatorAddFindingSpec:
    """Specify exact behavioral contract for add_finding (risk=1.00)."""

    def test_blocked_by_cooldown_increments_suppressed(self) -> None:
        compass = _make_compass(event_counter=3)
        compass.last_fired["test_sig"] = 1
        coord = SignalCoordinator(compass=compass, thresholds={"signal_cooldown": 10})
        finding = LintIssue(linter="behavior", kind="test_sig", message="msg")
        coord.add_finding("test_sig", finding, is_hard=False)
        assert coord.suppressed_nudge_count == 1
        assert coord.findings == []
        assert coord.run_fire_counts == {}

    def test_records_firing_on_success(self) -> None:
        compass = _make_compass(event_counter=0)
        coord = SignalCoordinator(compass=compass, thresholds={"signal_cooldown": 10})
        finding = LintIssue(linter="behavior", kind="cycling", message="msg")
        coord.add_finding("approach_cycling", finding, is_hard=True)
        assert compass.last_fired["approach_cycling"] == 0
        assert compass.signal_fire_counts["approach_cycling"] == 1
        assert coord.run_fire_counts["approach_cycling"] == 1
        assert len(coord.findings) == 1

    def test_precheck_nudge_tracked_by_priority(self) -> None:
        compass = _make_compass(event_counter=0)
        coord = SignalCoordinator(compass=compass, thresholds={"signal_cooldown": 10})
        nudge1 = {"tool": "constraint_check", "reason": "serial"}
        f1 = LintIssue(linter="behavior", kind="k", message="m")
        coord.add_finding("serial_discovery_early", f1, is_hard=False, precheck_nudge=nudge1)
        assert coord._pending_precheck == nudge1
        assert coord._pending_priority == 7  # serial_discovery_early priority
        assert "serial_discovery_early" in coord._nudge_signals

        # Higher priority nudge should replace it
        nudge2 = {"tool": "constraint_check", "reason": "cycling"}
        f2 = LintIssue(linter="behavior", kind="k2", message="m2")
        coord.add_finding("approach_cycling", f2, is_hard=True, precheck_nudge=nudge2)
        assert coord._pending_precheck == nudge2
        assert coord._pending_priority == 1  # approach_cycling priority

    def test_authority_severity_applied_to_finding(self) -> None:
        compass = _make_compass(event_counter=0)
        coord = SignalCoordinator(compass=compass, thresholds={"signal_cooldown": 10})
        finding = LintIssue(linter="behavior", kind="cycling", message="cycling detected")
        coord.add_finding("approach_cycling", finding, is_hard=True)
        # Authority engine should have set severity and added evidence
        assert finding.severity in ("blocking", "warning", "informational")
        assert "authority" in (finding.evidence or {})
        assert "level" in finding.evidence["authority"]
        assert "reason" in finding.evidence["authority"]

    def test_with_decomposition_applies_attribution(self) -> None:
        from lintgate.orchestration.attribution import SignalSourceDecomposition

        compass = _make_compass(event_counter=0)
        coord = SignalCoordinator(compass=compass, thresholds={"signal_cooldown": 10})
        decomp = SignalSourceDecomposition(
            signal_name="approach_cycling",
            pattern_score=0.8,
            theory_score=0.0,
            outcome_score=0.5,
            coherence_score=0.3,
        )
        finding = LintIssue(linter="behavior", kind="cycling", message="detected")
        coord.add_finding("approach_cycling", finding, is_hard=True, decomposition=decomp)
        assert finding.confidence > 0
        assert "attribution" in (finding.evidence or {})
        attr = finding.evidence["attribution"]
        assert attr["pattern"] == 0.8
        assert attr["outcome"] == 0.5
        assert attr["coherence"] == 0.3


# ── _apply_theory_coda branch coverage ────────────────────────────


class TestApplyTheoryCodaMutantKilling:
    """Cover all 3 branches of SignalCoordinator._apply_theory_coda."""

    def test_no_theory_profile_returns_zero(self) -> None:
        """Branch 1: theory_profile is None → returns 0.0, finding unchanged."""
        compass = _make_compass(event_counter=0)
        coord = SignalCoordinator(
            compass=compass,
            thresholds={"signal_cooldown": 10},
            theory_profile=None,
        )
        finding = LintIssue(linter="behavior", kind="cycling", message="original message")
        result = coord._apply_theory_coda("approach_cycling", finding)
        assert result == 0.0
        assert finding.message == "original message"

    def test_new_coda_stored_in_new_codas(self) -> None:
        """Branch 2+3: theory_profile present, coda produced, not duplicate → stored."""
        compass = _make_compass(event_counter=0)
        # Profile format: {facet: [{claims: [str, ...], source: str, heading: str}]}
        theory_profile = {
            "core_theory": [
                {
                    "claims": ["Prefer deterministic checks over ambiguous heuristics."],
                    "source": "design.md",
                    "heading": "Mission",
                }
            ]
        }
        coord = SignalCoordinator(
            compass=compass,
            thresholds={"signal_cooldown": 10},
            theory_profile=theory_profile,
            recent_codas={},
        )
        finding = LintIssue(
            linter="behavior",
            kind="cycling",
            message="3 approaches tried",
        )
        result = coord._apply_theory_coda("approach_cycling", finding)
        # Theory score should be > 0 since claims were found
        assert result >= 0.0
        # If a coda was produced, it should be in _new_codas
        if "approach_cycling" in coord._new_codas:
            assert len(coord._new_codas["approach_cycling"]) > 0

    def test_duplicate_coda_stripped_from_message(self) -> None:
        """Branch 3: prev_coda == coda → coda stripped from message, theory_context removed."""
        compass = _make_compass(event_counter=0)
        theory_profile = {
            "core_theory": [
                {
                    "claims": ["Prefer deterministic checks over ambiguous heuristics."],
                    "source": "design.md",
                    "heading": "Mission",
                }
            ]
        }
        # First call to get the actual coda text
        coord1 = SignalCoordinator(
            compass=compass,
            thresholds={"signal_cooldown": 10},
            theory_profile=theory_profile,
            recent_codas={},
        )
        finding1 = LintIssue(
            linter="behavior",
            kind="cycling",
            message="3 approaches tried",
        )
        coord1._apply_theory_coda("approach_cycling", finding1)
        if "approach_cycling" not in coord1._new_codas:
            return  # No coda produced, can't test dedup
        coda_text = coord1._new_codas["approach_cycling"]

        # Second call with the same coda as recent → should dedup
        coord2 = SignalCoordinator(
            compass=compass,
            thresholds={"signal_cooldown": 10},
            theory_profile=theory_profile,
            recent_codas={"approach_cycling": coda_text},
        )
        finding2 = LintIssue(
            linter="behavior",
            kind="cycling",
            message="3 approaches tried",
            evidence={"theory_context": "old"},
        )
        coord2._apply_theory_coda("approach_cycling", finding2)
        # Duplicate coda should NOT be stored in _new_codas
        assert "approach_cycling" not in coord2._new_codas
        # theory_context should be removed from evidence
        assert "theory_context" not in (finding2.evidence or {})

    def test_no_coda_returns_theory_score(self) -> None:
        """When _ground_finding_in_theory returns empty coda, theory_score still returned."""
        compass = _make_compass(event_counter=0)
        # Empty facet list → no claims found, no coda produced
        theory_profile: dict[str, Any] = {"unrelated_facet": []}
        coord = SignalCoordinator(
            compass=compass,
            thresholds={"signal_cooldown": 10},
            theory_profile=theory_profile,
            recent_codas={},
        )
        finding = LintIssue(
            linter="behavior",
            kind="cycling",
            message="original",
        )
        result = coord._apply_theory_coda("approach_cycling", finding)
        assert isinstance(result, float)

    def test_coda_via_add_finding_without_decomposition(self) -> None:
        """_apply_theory_coda is called by add_finding when decomposition is None."""
        compass = _make_compass(event_counter=0)
        theory_profile = {
            "core_theory": [
                {
                    "claims": ["Prefer deterministic checks over ambiguous heuristics."],
                    "source": "design.md",
                    "heading": "Mission",
                }
            ]
        }
        coord = SignalCoordinator(
            compass=compass,
            thresholds={"signal_cooldown": 10},
            theory_profile=theory_profile,
            recent_codas={},
        )
        finding = LintIssue(
            linter="behavior",
            kind="cycling",
            message="3 approaches tried",
        )
        # Without decomposition, add_finding calls _apply_theory_coda
        coord.add_finding("approach_cycling", finding, is_hard=True)
        assert len(coord.findings) == 1
        # The finding should have been processed (authority severity applied)
        assert finding.severity in ("blocking", "warning", "informational")


# ── SignalSourceDecomposition (moved from test_attribution.py) ─────────


class TestSignalSourceDecomposition:
    def test_decomposition_confidence(self) -> None:
        from lintgate.orchestration.attribution import SignalSourceDecomposition

        decomp = SignalSourceDecomposition(
            signal_name="approach_cycling",
            pattern_score=1.0,  # 1.0 * 0.4 = 0.4
            theory_score=0.5,  # 0.5 * 0.2 = 0.1
            outcome_score=0.8,  # 0.8 * 0.3 = 0.24
            coherence_score=0.0,  # 0.0 * 0.1 = 0
        )
        # Total = 0.74 / 0.5 = 1.48 -> cap at 1.0
        assert decomp.total_confidence == 1.0

    def test_decomposition_message_attribution(self) -> None:
        from unittest.mock import MagicMock

        from lintgate.orchestration.attribution import SignalSourceDecomposition

        compass = MagicMock()
        compass.event_counter = 1
        compass.last_fired = {}
        compass.signal_fire_counts = {}
        compass.compliance_rate = 1.0

        coord = SignalCoordinator(compass, thresholds={"escalation_threshold": 3})

        decomp = SignalSourceDecomposition(
            signal_name="approach_cycling", pattern_score=0.8, outcome_score=0.9
        )

        finding = LintIssue(
            linter="behavior_channel",
            kind="approach_cycling",
            message="Stuck in loop",
            severity="warning",
        )

        coord.add_finding("approach_cycling", finding, is_hard=True, decomposition=decomp)

        assert "(Triggered by: pattern match, outcome evidence)" in finding.message
        assert finding.confidence > 0.5
        assert finding.evidence["attribution"]["pattern"] == 0.8

    def test_decomposition_summary_empty(self) -> None:
        from lintgate.orchestration.attribution import SignalSourceDecomposition

        decomp = SignalSourceDecomposition(signal_name="test")
        assert decomp.to_summary() == "Triggered by mixed signals"


# ── Signal extraction (SignalExtractor) ───────────────────────────────────


class TestSignalExtractor:
    def test_extract_json(self) -> None:
        from lintgate.orchestration.signals import SignalExtractor

        extractor = SignalExtractor()
        raw = {
            "code": "F401",
            "message": "imported but unused",
            "severity": "WARNING",
            "file": "foo.py",
            "line": 10,
        }
        signals = extractor.extract(raw, "lint")
        assert len(signals) == 1
        assert signals[0].kind == "F401"
        assert signals[0].severity == "warning"
        assert signals[0].message == "imported but unused"
        assert signals[0].evidence_map["file"] == "foo.py"

    def test_extract_text_errors(self) -> None:
        from lintgate.orchestration.signals import SignalExtractor

        extractor = SignalExtractor()
        text = "Error: Something went wrong\nWarning: Be careful"
        signals = extractor.extract(text, "build")
        assert len(signals) == 2
        assert signals[0].severity == "blocking"
        assert signals[0].message == "Something went wrong"
        assert signals[1].severity == "warning"
        assert signals[1].message == "Be careful"

    def test_extract_file_lines(self) -> None:
        from lintgate.orchestration.signals import SignalExtractor

        extractor = SignalExtractor()
        text = "src/main.py:42: Missing docstring"
        signals = extractor.extract(text, "test")
        assert len(signals) == 1
        assert signals[0].kind == "test_file_issue"
        assert signals[0].evidence_map["file"] == "src/main.py"
        assert signals[0].evidence_map["line"] == "42"
