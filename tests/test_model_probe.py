"""Tests for the deterministic model calibration probe."""

from __future__ import annotations

import pytest

from lintgate.controlplane.model_probe import (
    PROBE_QUESTIONS,
    PROBE_VERSION,
    _derive_custom_anti_patterns,
    _derive_custom_dispositions,
    build_profile_from_probe,
    get_probe_questions,
    score_probe_responses,
)


class TestProbeQuestions:
    def test_question_count(self):
        assert len(PROBE_QUESTIONS) == 5

    def test_all_questions_have_4_choices(self):
        for q in PROBE_QUESTIONS:
            assert len(q.choices) == 4, f"Question {q.id} has {len(q.choices)} choices"
            assert set(q.choices.keys()) == {"A", "B", "C", "D"}

    def test_all_choices_have_signal_map(self):
        for q in PROBE_QUESTIONS:
            for choice in ("A", "B", "C", "D"):
                assert choice in q.signal_map, (
                    f"Question {q.id} missing signal_map for choice {choice}"
                )

    def test_unique_question_ids(self):
        ids = [q.id for q in PROBE_QUESTIONS]
        assert len(set(ids)) == len(ids)

    def test_get_probe_questions_hides_signal_map(self):
        questions = get_probe_questions()
        for q in questions:
            assert "signal_map" not in q
            assert "id" in q
            assert "question" in q
            assert "choices" in q

    def test_get_probe_questions_rejects_unsupported_probe_set(self):
        with pytest.raises(ValueError, match="Unsupported probe_set"):
            get_probe_questions("full")


class TestScoring:
    def test_full_answers_all_a(self):
        responses = {q.id: "A" for q in PROBE_QUESTIONS}
        risk, confidence = score_probe_responses(responses)
        assert isinstance(risk, dict)
        assert 0.0 <= confidence <= 1.0
        assert len(risk) > 0
        # All-A should produce high risk for approach_cycling
        assert risk.get("approach_cycling", 0.0) > 0

    def test_full_answers_all_d(self):
        responses = {q.id: "D" for q in PROBE_QUESTIONS}
        risk, confidence = score_probe_responses(responses)
        # All-D (diagnostic/cautious choices) should have lower risk
        assert confidence >= 0.75

    def test_partial_answers_one(self):
        responses = {PROBE_QUESTIONS[0].id: "B"}
        risk, confidence = score_probe_responses(responses)
        # 1/5 answers -> low confidence
        assert confidence < 0.55

    def test_three_answers_meets_threshold(self):
        responses = {q.id: "C" for q in PROBE_QUESTIONS[:3]}
        risk, confidence = score_probe_responses(responses)
        # 3/5 = 0.62 confidence (above 0.55 threshold)
        assert confidence >= 0.55

    def test_clamping_to_bounds(self):
        responses = {q.id: "A" for q in PROBE_QUESTIONS}
        risk, _ = score_probe_responses(responses)
        for val in risk.values():
            assert 0.0 <= val <= 1.0

    def test_invalid_question_id_ignored(self):
        responses = {"invalid_q": "A", PROBE_QUESTIONS[0].id: "B"}
        risk, confidence = score_probe_responses(responses)
        # Only 1 valid answer
        assert confidence < 0.55

    def test_invalid_choice_letter_ignored(self):
        responses = {PROBE_QUESTIONS[0].id: "Z"}
        risk, confidence = score_probe_responses(responses)
        assert confidence < 0.55

    def test_empty_responses(self):
        risk, confidence = score_probe_responses({})
        assert confidence < 0.55
        assert len(risk) == 0

    def test_case_insensitive_choices(self):
        responses = {PROBE_QUESTIONS[0].id: "a"}
        risk1, _ = score_probe_responses(responses)
        responses2 = {PROBE_QUESTIONS[0].id: "A"}
        risk2, _ = score_probe_responses(responses2)
        assert risk1 == risk2


class TestDerivedOutputs:
    def test_derive_anti_patterns_from_high_risk(self):
        signal_risk = {"approach_cycling": 0.8, "verification_debt": 0.6}
        patterns = _derive_custom_anti_patterns(signal_risk)
        assert len(patterns) >= 2
        assert any("4th approach" in p for p in patterns)

    def test_derive_anti_patterns_skips_low_risk(self):
        signal_risk = {"approach_cycling": 0.05}
        patterns = _derive_custom_anti_patterns(signal_risk)
        assert len(patterns) == 0

    def test_derive_dispositions_from_high_risk(self):
        signal_risk = {"approach_cycling": 0.5, "premature_action": 0.4}
        dispositions = _derive_custom_dispositions(signal_risk, threshold=0.3)
        assert len(dispositions) >= 2
        assert all("MUST" in d for d in dispositions)

    def test_derive_dispositions_caps_at_max(self):
        signal_risk = dict.fromkeys(["approach_cycling", "verification_debt", "premature_action", "serial_discovery", "failure_amnesia", "stale_model", "tool_repetition"], 0.9)
        dispositions = _derive_custom_dispositions(signal_risk, max_items=4)
        assert len(dispositions) <= 4

    def test_derive_dispositions_skips_below_threshold(self):
        signal_risk = {"approach_cycling": 0.1}
        dispositions = _derive_custom_dispositions(signal_risk, threshold=0.3)
        assert len(dispositions) == 0


class TestBuildProfile:
    def test_builds_with_canonical_key(self):
        responses = {q.id: "B" for q in PROBE_QUESTIONS}
        profile = build_profile_from_probe("claude-opus-4", responses)
        assert profile.model_key == "anthropic:claude-opus-4"
        assert profile.probe_version == PROBE_VERSION
        assert profile.confidence >= 0.55
        assert profile.probe_runs == 1

    def test_includes_custom_outputs(self):
        # Use all-A responses (high risk)
        responses = {q.id: "A" for q in PROBE_QUESTIONS}
        profile = build_profile_from_probe("claude-opus-4", responses)
        assert len(profile.custom_anti_patterns) > 0
        # With high risk, should also have dispositions
        assert len(profile.custom_dispositions) > 0

    def test_rejects_unresolvable_model(self):
        responses = {q.id: "A" for q in PROBE_QUESTIONS}
        with pytest.raises(ValueError, match="Cannot resolve"):
            build_profile_from_probe("unknown-thing", responses)
