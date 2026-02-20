"""Tests for the v2 micro-task model calibration probe."""

from __future__ import annotations

from typing import Any

import pytest

from lintgate.controlplane.model_probe import (
    NEUTRAL_PRIOR,
    NEUTRAL_PRIOR_CONFIDENCE,
    PROBE_MAX_CONFIDENCE,
    PROBE_TASKS,
    PROBE_VERSION,
    _compute_trace_quality,
    _derive_custom_anti_patterns,
    _derive_custom_dispositions,
    _extract_features_for_task,
    build_profile_from_probe,
    compute_probe_validity,
    get_neutral_prior,
    get_probe_questions,
    get_probe_tasks,
    score_probe_responses,
)


class TestProbeTaskStructure:
    def test_task_count(self):
        assert len(PROBE_TASKS) == 5

    def test_all_tasks_have_variants(self):
        for task in PROBE_TASKS:
            assert len(task.variants) >= 2, f"Task {task.id} has <2 variants"

    def test_all_tasks_have_features(self):
        for task in PROBE_TASKS:
            assert len(task.features) >= 1, f"Task {task.id} has no features"

    def test_all_tasks_have_target_signals(self):
        for task in PROBE_TASKS:
            assert len(task.target_signals) >= 1, f"Task {task.id} has no target_signals"

    def test_unique_task_ids(self):
        ids = [t.id for t in PROBE_TASKS]
        assert len(set(ids)) == len(ids)

    def test_unique_variant_ids(self):
        all_variant_ids = []
        for task in PROBE_TASKS:
            for v in task.variants:
                all_variant_ids.append(v.variant_id)
        assert len(set(all_variant_ids)) == len(all_variant_ids)

    def test_variants_have_context_and_instruction(self):
        for task in PROBE_TASKS:
            for v in task.variants:
                assert v.context, f"Variant {v.variant_id} has no context"
                assert v.instruction, f"Variant {v.variant_id} has no instruction"

    def test_features_have_valid_signals(self):
        """All feature signals should be from the known signal set."""
        known_signals = {
            "approach_cycling",
            "failure_amnesia",
            "serial_discovery",
            "premature_action",
            "verification_debt",
            "stale_model",
            "tool_repetition",
            "brute_force_escalation",
            "consecutive_failures",
        }
        for task in PROBE_TASKS:
            for f in task.features:
                assert f.signal in known_signals, (
                    f"Feature {f.name} on task {task.id} targets unknown signal {f.signal}"
                )

    def test_probe_version_is_2(self):
        assert PROBE_VERSION == 2


class TestGetProbeTasks:
    def test_returns_all_tasks(self):
        tasks = get_probe_tasks()
        assert len(tasks) == 5

    def test_tasks_have_correct_fields(self):
        tasks = get_probe_tasks()
        for t in tasks:
            assert "id" in t
            assert "context" in t
            assert "instruction" in t
            assert "setup_files" in t
            assert "response_schema" in t

    def test_does_not_expose_features(self):
        tasks = get_probe_tasks()
        for t in tasks:
            assert "features" not in t
            assert "target_signals" not in t

    def test_variant_rotation_with_seed(self):
        """Different seeds should sometimes select different variants."""
        t1 = get_probe_tasks(seed=42)
        t2 = get_probe_tasks(seed=12345)
        # At least one task should have different context (different variant)
        contexts_differ = any(t1[i]["context"] != t2[i]["context"] for i in range(len(t1)))
        # This is probabilistic but with 5 tasks and 2 variants each,
        # the chance of all matching is (1/2)^5 = 3.1% — very unlikely
        assert contexts_differ

    def test_same_seed_same_result(self):
        t1 = get_probe_tasks(seed=42)
        t2 = get_probe_tasks(seed=42)
        for i in range(len(t1)):
            assert t1[i]["context"] == t2[i]["context"]

    def test_rejects_unsupported_probe_set(self):
        with pytest.raises(ValueError, match="Unsupported probe_set"):
            get_probe_tasks("full")

    def test_v1_compat_alias(self):
        """get_probe_questions should work as alias."""
        tasks = get_probe_questions()
        assert len(tasks) == 5


class TestFeatureExtraction:
    @pytest.fixture
    def t1_task(self):
        return PROBE_TASKS[0]  # t1_error_reading

    def test_read_before_edit_from_tool_calls(self, t1_task):
        response = {
            "text": "I would fix the bug",
            "tool_calls": ["Read", "Read", "Edit", "Bash"],
        }
        features = _extract_features_for_task(t1_task, response)
        assert features["read_before_edit"] is True

    def test_no_read_before_edit_from_tool_calls(self, t1_task):
        response = {
            "text": "Fix it immediately",
            "tool_calls": ["Edit", "Bash"],
        }
        features = _extract_features_for_task(t1_task, response)
        assert features["read_before_edit"] is False

    def test_verify_points_extract(self, t1_task):
        response = {
            "text": "I would fix each bug and test",
            "tool_calls": ["Read", "Edit", "Bash", "Edit", "Bash"],
            "verify_points": [2, 4],
        }
        features = _extract_features_for_task(t1_task, response)
        assert features["verifies_after_some"] is True
        assert features["mentions_verification"] is True

    def test_exact_retry_from_retry_count(self, t1_task):
        response = {
            "text": "Try again",
            "retry_count": 2,
        }
        features = _extract_features_for_task(t1_task, response)
        assert features["exact_retry"] is True

    def test_constraint_refs_extract(self, t1_task):
        response = {
            "text": "Both prior attempts failed",
            "constraint_refs": ["SQLite ALTER TABLE", "foreign key constraint"],
        }
        features = _extract_features_for_task(t1_task, response)
        assert features["references_previous_attempts"] is True
        assert features["references_both_failures"] is True

    def test_text_only_fallback(self, t1_task):
        response = {
            "text": "First I would read the source file to understand the bug, "
            "then examine the test output, and finally edit the code to fix it.",
        }
        features = _extract_features_for_task(t1_task, response)
        assert features["read_before_edit"] is True

    def test_root_cause_identification_v1(self, t1_task):
        response = {
            "text": "The real issue is variable shadowing. The loop variable "
            "overwrites the label variable on line 5.",
        }
        features = _extract_features_for_task(t1_task, response)
        assert features["identifies_root_cause"] is True
        assert features["follows_misleading_error"] is False

    def test_follows_misleading_error(self, t1_task):
        response = {
            "text": "The error on line 15 shows a type error — str vs int. "
            "I need to fix the type conversion.",
        }
        features = _extract_features_for_task(t1_task, response)
        assert features["follows_misleading_error"] is True


class TestTraceQuality:
    def test_text_only(self):
        q = _compute_trace_quality({"text": "I would do stuff"})
        assert 0.2 <= q <= 0.4

    def test_full_structured(self):
        q = _compute_trace_quality(
            {
                "text": "approach",
                "tool_calls": ["Read"],
                "actions": ["read file"],
                "retry_count": 0,
                "verify_points": [1],
                "constraint_refs": ["err"],
            }
        )
        assert q >= 0.9

    def test_empty(self):
        q = _compute_trace_quality({})
        assert q == 0.0


class TestScoring:
    def _make_responses(self, *, include_traces: bool = True) -> dict[str, dict]:
        """Build a full set of task responses."""
        responses = {}
        for task in PROBE_TASKS:
            resp: dict[str, Any] = {
                "text": "First I would read the source file to understand the issue, "
                "then look at both error messages, and fix the root cause. "
                "I would verify after each change by running tests.",
            }
            if include_traces:
                resp["tool_calls"] = ["Read", "Read", "Edit", "Bash"]
                resp["actions"] = [
                    "Read source file to understand",
                    "Read test output carefully",
                    "Fix the root cause",
                    "Run pytest to verify",
                ]
                resp["verify_points"] = [3]
                resp["constraint_refs"] = ["variable shadowing", "prior attempt failed"]
            responses[task.id] = resp
        return responses

    def test_full_responses_produce_signal_risk(self):
        responses = self._make_responses()
        risk, confidence = score_probe_responses(responses)
        assert isinstance(risk, dict)
        assert len(risk) > 0
        assert 0.0 <= confidence <= PROBE_MAX_CONFIDENCE

    def test_all_values_clamped(self):
        responses = self._make_responses()
        risk, _ = score_probe_responses(responses)
        for val in risk.values():
            assert 0.0 <= val <= 1.0

    def test_structured_higher_confidence_than_text_only(self):
        r_structured = self._make_responses(include_traces=True)
        r_text = self._make_responses(include_traces=False)
        _, conf_structured = score_probe_responses(r_structured)
        _, conf_text = score_probe_responses(r_text)
        assert conf_structured > conf_text

    def test_partial_responses(self):
        """3/5 tasks should still produce a usable score."""
        responses = self._make_responses()
        partial = {k: v for i, (k, v) in enumerate(responses.items()) if i < 3}
        risk, confidence = score_probe_responses(partial)
        assert len(risk) > 0
        assert confidence > 0.0

    def test_single_response_low_confidence(self):
        task = PROBE_TASKS[0]
        responses = {
            task.id: {
                "text": "I would fix the bug",
            }
        }
        risk, confidence = score_probe_responses(responses)
        # 1/5 tasks, text-only → very low confidence
        assert confidence < PROBE_MAX_CONFIDENCE * 0.7

    def test_empty_responses(self):
        risk, confidence = score_probe_responses({})
        assert confidence == 0.0
        assert len(risk) == 0

    def test_invalid_task_id_ignored(self):
        responses = {
            "invalid_task": {"text": "something"},
            PROBE_TASKS[0].id: {"text": "I would read the file first"},
        }
        risk, confidence = score_probe_responses(responses)
        # Only 1 valid response
        assert confidence > 0.0

    def test_confidence_capped(self):
        responses = self._make_responses()
        _, confidence = score_probe_responses(responses)
        assert confidence <= PROBE_MAX_CONFIDENCE


class TestNeutralPrior:
    def test_neutral_prior_values(self):
        risk, confidence = get_neutral_prior()
        assert confidence == NEUTRAL_PRIOR_CONFIDENCE
        assert all(0.0 <= v <= 0.30 for v in risk.values())

    def test_neutral_prior_is_copy(self):
        r1, _ = get_neutral_prior()
        r2, _ = get_neutral_prior()
        r1["approach_cycling"] = 99.0
        assert r2["approach_cycling"] != 99.0


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
        signal_risk = dict.fromkeys(
            [
                "approach_cycling",
                "verification_debt",
                "premature_action",
                "serial_discovery",
                "failure_amnesia",
                "stale_model",
                "tool_repetition",
            ],
            0.9,
        )
        dispositions = _derive_custom_dispositions(signal_risk, max_items=4)
        assert len(dispositions) <= 4

    def test_derive_dispositions_skips_below_threshold(self):
        signal_risk = {"approach_cycling": 0.1}
        dispositions = _derive_custom_dispositions(signal_risk, threshold=0.3)
        assert len(dispositions) == 0

    def test_dispositions_reference_constraint_check(self):
        """Dispositions should reference constraint_check, not behavior_precheck."""
        signal_risk = {"approach_cycling": 0.5, "serial_discovery": 0.5}
        dispositions = _derive_custom_dispositions(signal_risk, threshold=0.3)
        for d in dispositions:
            assert "behavior_precheck" not in d
            assert "constraint_check" in d


class TestBuildProfile:
    def _full_responses(self) -> dict:
        return {
            task.id: {
                "text": "First I would read the source file, examine the error, "
                "then fix the root cause. I would verify after the fix.",
                "tool_calls": ["Read", "Read", "Edit", "Bash"],
                "verify_points": [3],
                "constraint_refs": ["previous error", "shadowed variable"],
            }
            for task in PROBE_TASKS
        }

    def test_builds_with_canonical_key(self):
        responses = self._full_responses()
        profile = build_profile_from_probe("claude-opus-4", responses)
        assert profile.model_key == "anthropic:claude-opus-4"
        assert profile.probe_version == PROBE_VERSION
        assert profile.confidence > 0
        assert profile.probe_runs == 1

    def test_includes_custom_outputs(self):
        responses = self._full_responses()
        profile = build_profile_from_probe("claude-opus-4", responses)
        # Should have at least some anti-patterns or dispositions
        assert isinstance(profile.custom_anti_patterns, list)
        assert isinstance(profile.custom_dispositions, list)

    def test_rejects_unresolvable_model(self):
        responses = self._full_responses()
        with pytest.raises(ValueError, match="Cannot resolve"):
            build_profile_from_probe("unknown-thing", responses)

    def test_neutral_fallback_on_empty(self):
        """Empty responses should fall back to neutral prior."""
        profile = build_profile_from_probe("claude-opus-4", {})
        assert profile.confidence == NEUTRAL_PRIOR_CONFIDENCE
        assert profile.signal_risk == NEUTRAL_PRIOR

    def test_confidence_capped_at_max(self):
        responses = self._full_responses()
        profile = build_profile_from_probe("claude-opus-4", responses)
        assert profile.confidence <= PROBE_MAX_CONFIDENCE


class TestProbeValidity:
    def test_insufficient_data(self):
        result = compute_probe_validity({"approach_cycling": 0.5}, {}, 10)
        assert result["correlation_quality"] == "insufficient_data"

    def test_good_correlation(self):
        probe_risk = {"approach_cycling": 0.3, "verification_debt": 0.2}
        observed = {"approach_cycling": 6, "verification_debt": 4}
        result = compute_probe_validity(probe_risk, observed, 200)
        assert result["correlation_quality"] in ("good", "moderate")
        assert result["mean_absolute_delta"] is not None
        assert len(result["per_signal"]) == 2

    def test_poor_correlation(self):
        probe_risk = {"approach_cycling": 0.0, "verification_debt": 0.0}
        observed = {"approach_cycling": 100, "verification_debt": 100}
        result = compute_probe_validity(probe_risk, observed, 200)
        assert result["correlation_quality"] == "poor"
