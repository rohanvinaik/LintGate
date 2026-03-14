"""Tests for mcp_tools/_behavior_impl.py — sub-module level coverage."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from mcp_tools._behavior_impl import (
    _build_constraint_recommendation,
    _compute_coverage_gap,
    _find_similar_failures,
    _seed_theory_constraints,
    impl_behavior_precheck,
    impl_constraint_check,
    impl_global_memory_reset,
    impl_global_memory_status,
    impl_hygiene_check,
    impl_prediction_register,
)

# ── helpers ────────────────────────────────────────────────────────


def _make_helpers(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "_validate_project_root": lambda p: "/test/project",
        "_json_dumps": lambda d, **kw: json.dumps(d),
        "_json_loads": lambda s: json.loads(s),
        "_build_onboarding_status": lambda p: {"config_state": "config_enabled"},
    }
    base.update(overrides)
    return base


def _make_approach(
    outcome: str = "failed",
    approach_sig: str = "pytest:run",
    error_sigs: list[str] | None = None,
    event_count: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        outcome=outcome,
        approach_sig=approach_sig,
        error_sigs=error_sigs or [],
        event_count=event_count,
    )


def _make_hypothesis(
    hyp_id: str = "h1",
    claim: str = "test hypothesis about errors",
    confidence: float = 0.5,
    source: str = "command_failure",
) -> SimpleNamespace:
    return SimpleNamespace(id=hyp_id, claim=claim, confidence=confidence, source=source)


# ══════════════════════════════════════════════════════════════════
# _build_constraint_recommendation
# ══════════════════════════════════════════════════════════════════


class TestBuildConstraintRecommendation:
    def test_good_coverage_returns_proceed_message(self):
        result = _build_constraint_recommendation(
            coverage_gap=0, recall=1.0, uncertainty=[], similar_failures=[]
        )
        assert result == "Good constraint coverage. Proceed with awareness of known constraints."

    def test_coverage_gap_singular(self):
        result = _build_constraint_recommendation(
            coverage_gap=1, recall=1.0, uncertainty=[], similar_failures=[]
        )
        assert "1 unverified constraint area." in result
        assert "1 unverified constraint areas" not in result
        assert "Consider researching" in result

    def test_coverage_gap_plural(self):
        result = _build_constraint_recommendation(
            coverage_gap=3, recall=1.0, uncertainty=[], similar_failures=[]
        )
        assert "3 unverified constraint areas" in result

    def test_low_recall(self):
        result = _build_constraint_recommendation(
            coverage_gap=0, recall=0.5, uncertainty=[], similar_failures=[]
        )
        assert "50% prediction recall" in result
        assert "Consider researching" in result

    def test_uncertainty_singular(self):
        result = _build_constraint_recommendation(
            coverage_gap=0, recall=1.0, uncertainty=["zone1"], similar_failures=[]
        )
        assert "1 uncertainty zone." in result
        assert "1 uncertainty zones" not in result

    def test_uncertainty_plural(self):
        result = _build_constraint_recommendation(
            coverage_gap=0, recall=1.0, uncertainty=["z1", "z2"], similar_failures=[]
        )
        assert "2 uncertainty zones" in result

    def test_similar_failures_singular(self):
        result = _build_constraint_recommendation(
            coverage_gap=0, recall=1.0, uncertainty=[], similar_failures=[{"sig": "x"}]
        )
        assert "1 similar past failure." in result
        assert "1 similar past failures" not in result

    def test_similar_failures_plural(self):
        result = _build_constraint_recommendation(
            coverage_gap=0,
            recall=1.0,
            uncertainty=[],
            similar_failures=[{"sig": "a"}, {"sig": "b"}],
        )
        assert "2 similar past failures" in result

    def test_all_parts_combined(self):
        result = _build_constraint_recommendation(
            coverage_gap=2,
            recall=0.75,
            uncertainty=["z1"],
            similar_failures=[{"sig": "x"}],
        )
        assert "2 unverified constraint areas" in result
        assert "75% prediction recall" in result
        assert "1 uncertainty zone" in result
        assert "1 similar past failure" in result
        assert "Consider researching" in result

    def test_zero_recall(self):
        result = _build_constraint_recommendation(
            coverage_gap=0, recall=0.0, uncertainty=[], similar_failures=[]
        )
        assert "0% prediction recall" in result

    def test_parts_joined_with_period_separator(self):
        result = _build_constraint_recommendation(
            coverage_gap=1, recall=0.5, uncertainty=[], similar_failures=[]
        )
        # Two parts joined by ". "
        assert ". " in result

    def test_coverage_gap_zero_not_included(self):
        """coverage_gap=0 should NOT add an 'unverified constraint areas' part."""
        result = _build_constraint_recommendation(
            coverage_gap=0, recall=0.5, uncertainty=[], similar_failures=[]
        )
        assert "unverified" not in result

    def test_recall_at_1_not_included(self):
        """recall=1.0 should NOT add a 'prediction recall' part."""
        result = _build_constraint_recommendation(
            coverage_gap=1, recall=1.0, uncertainty=[], similar_failures=[]
        )
        assert "prediction recall" not in result

    def test_empty_uncertainty_not_added_as_part(self):
        """When uncertainty is empty, no 'N uncertainty zone(s)' part is included."""
        result = _build_constraint_recommendation(
            coverage_gap=0, recall=1.0, uncertainty=[], similar_failures=[{"x": 1}]
        )
        # The suffix always mentions "uncertainty zones", so check for the count pattern
        import re

        assert not re.search(r"\d+ uncertainty zone", result)

    def test_empty_similar_failures_not_included(self):
        result = _build_constraint_recommendation(
            coverage_gap=0, recall=1.0, uncertainty=["z"], similar_failures=[]
        )
        assert "similar past failure" not in result


# ══════════════════════════════════════════════════════════════════
# _find_similar_failures
# ══════════════════════════════════════════════════════════════════


class TestFindSimilarFailures:
    def test_empty_approaches(self):
        result = _find_similar_failures([], "pytest:run_tests")
        assert result == []

    def test_no_colon_in_command_sig_returns_empty(self):
        approaches = [_make_approach(outcome="failed", approach_sig="pytest:run")]
        result = _find_similar_failures(approaches, "nocoherentsig")
        assert result == []

    def test_match_by_binary_prefix(self):
        approaches = [
            _make_approach(
                outcome="failed",
                approach_sig="pytest:run_tests",
                error_sigs=["ImportError"],
                event_count=3,
            ),
        ]
        result = _find_similar_failures(approaches, "pytest:other_tests")
        assert len(result) == 1
        assert result[0]["sig"] == "pytest:run_tests"
        assert result[0]["count"] == 3
        assert result[0]["error"] == "ImportError"

    def test_no_match_different_binary(self):
        approaches = [
            _make_approach(outcome="failed", approach_sig="git:commit"),
        ]
        result = _find_similar_failures(approaches, "pytest:run")
        assert result == []

    def test_skips_non_failed(self):
        approaches = [
            _make_approach(outcome="success", approach_sig="pytest:run"),
        ]
        result = _find_similar_failures(approaches, "pytest:check")
        assert result == []

    def test_skips_pending_outcome(self):
        approaches = [
            _make_approach(outcome="pending", approach_sig="pytest:run"),
        ]
        result = _find_similar_failures(approaches, "pytest:check")
        assert result == []

    def test_empty_error_sigs_returns_empty_string(self):
        approaches = [
            _make_approach(outcome="failed", approach_sig="pytest:run", error_sigs=[]),
        ]
        result = _find_similar_failures(approaches, "pytest:check")
        assert len(result) == 1
        assert result[0]["error"] == ""

    def test_long_error_truncated_to_80(self):
        long_error = "A" * 200
        approaches = [
            _make_approach(
                outcome="failed",
                approach_sig="pytest:run",
                error_sigs=[long_error],
            ),
        ]
        result = _find_similar_failures(approaches, "pytest:check")
        assert len(result) == 1
        assert len(result[0]["error"]) == 80

    def test_multiple_matches(self):
        approaches = [
            _make_approach(outcome="failed", approach_sig="pytest:a", error_sigs=["err1"]),
            _make_approach(outcome="failed", approach_sig="pytest:b", error_sigs=["err2"]),
            _make_approach(outcome="success", approach_sig="pytest:c"),
        ]
        result = _find_similar_failures(approaches, "pytest:d")
        assert len(result) == 2

    def test_approach_sig_no_colon_does_not_match(self):
        """Approach with no colon in sig should not match anything."""
        approaches = [
            _make_approach(outcome="failed", approach_sig="nocolon"),
        ]
        result = _find_similar_failures(approaches, "pytest:run")
        assert result == []

    def test_uses_last_error_sig(self):
        """Should use the last element of error_sigs."""
        approaches = [
            _make_approach(
                outcome="failed",
                approach_sig="pytest:run",
                error_sigs=["first_error", "second_error", "last_error"],
            ),
        ]
        result = _find_similar_failures(approaches, "pytest:check")
        assert result[0]["error"] == "last_error"

    def test_empty_command_sig_returns_empty(self):
        """Empty command sig should return empty list."""
        approaches = [
            _make_approach(outcome="failed", approach_sig="pytest:run"),
        ]
        result = _find_similar_failures(approaches, "")
        assert result == []


# ══════════════════════════════════════════════════════════════════
# _compute_coverage_gap
# ══════════════════════════════════════════════════════════════════


class TestComputeCoverageGap:
    def test_no_relevant_hypotheses(self):
        gap, recall, matched = _compute_coverage_gap(["some claim"], [])
        assert gap == 0
        assert recall == 1.0
        assert matched == set()

    def test_no_declared_constraints(self):
        hyps = [_make_hypothesis(hyp_id="h1", claim="test hypothesis about errors")]
        gap, recall, matched = _compute_coverage_gap([], hyps)
        assert gap == 1
        assert recall == 0.0
        assert matched == set()

    def test_full_match(self):
        hyps = [_make_hypothesis(hyp_id="h1", claim="test hypothesis about errors")]
        gap, recall, matched = _compute_coverage_gap(["hypothesis about errors"], hyps)
        assert gap == 0
        assert recall == 1.0
        assert "h1" in matched

    def test_partial_match(self):
        hyps = [
            _make_hypothesis(hyp_id="h1", claim="test hypothesis about errors"),
            _make_hypothesis(hyp_id="h2", claim="performance regression in module"),
        ]
        gap, recall, matched = _compute_coverage_gap(["hypothesis about errors"], hyps)
        assert gap == 1
        assert recall == 0.5
        assert "h1" in matched
        assert "h2" not in matched

    def test_single_word_no_match(self):
        """Need at least 2 word overlap to match."""
        hyps = [_make_hypothesis(hyp_id="h1", claim="test hypothesis about errors")]
        gap, recall, matched = _compute_coverage_gap(["errors"], hyps)
        assert gap == 1
        assert recall == 0.0

    def test_both_empty(self):
        gap, recall, matched = _compute_coverage_gap([], [])
        assert gap == 0
        assert recall == 1.0
        assert matched == set()

    def test_multiple_declared_match_same_hypothesis(self):
        """Multiple declared constraints can match the same hypothesis, but it
        only counts once."""
        hyps = [_make_hypothesis(hyp_id="h1", claim="test hypothesis about errors")]
        gap, recall, matched = _compute_coverage_gap(
            ["hypothesis about errors", "test hypothesis claim"], hyps
        )
        assert gap == 0
        assert recall == 1.0
        assert "h1" in matched

    def test_case_insensitive_matching(self):
        """Matching is case insensitive."""
        hyps = [_make_hypothesis(hyp_id="h1", claim="Test Hypothesis About Errors")]
        gap, recall, matched = _compute_coverage_gap(["test hypothesis"], hyps)
        assert recall == 1.0
        assert "h1" in matched

    def test_declared_matches_first_hypothesis_only(self):
        """Each declared claim only matches the first relevant hypothesis it overlaps with."""
        hyps = [
            _make_hypothesis(hyp_id="h1", claim="import errors in module"),
            _make_hypothesis(hyp_id="h2", claim="import failures in system"),
        ]
        # "import errors" has >=2 overlap with h1 but not h2
        gap, recall, matched = _compute_coverage_gap(["import errors"], hyps)
        assert "h1" in matched
        assert "h2" not in matched
        assert gap == 1


# ══════════════════════════════════════════════════════════════════
# _seed_theory_constraints
# ══════════════════════════════════════════════════════════════════


class TestSeedTheoryConstraints:
    def test_with_anti_patterns(self):
        output: dict[str, Any] = {}
        mock_profile = {
            "theory_profile": {
                "anti_patterns": [
                    {"claims": ["avoid global state", "prefer pure functions"]},
                ],
            },
        }
        with patch("lintgate.theory_extractor.extract_theory", return_value=mock_profile):
            _seed_theory_constraints("/test/project", output)
        assert "theory_constraints" in output
        assert len(output["theory_constraints"]) == 2
        assert "avoid global state" in output["theory_constraints"]
        assert "hint" in output

    def test_empty_anti_patterns(self):
        output: dict[str, Any] = {}
        mock_profile: dict[str, Any] = {"theory_profile": {"anti_patterns": []}}
        with patch("lintgate.theory_extractor.extract_theory", return_value=mock_profile):
            _seed_theory_constraints("/test/project", output)
        assert "theory_constraints" not in output

    def test_extraction_error_is_swallowed(self):
        output: dict[str, Any] = {}
        with patch("lintgate.theory_extractor.extract_theory", side_effect=RuntimeError("fail")):
            _seed_theory_constraints("/test/project", output)
        assert "theory_constraints" not in output

    def test_caps_at_5(self):
        output: dict[str, Any] = {}
        mock_profile = {
            "theory_profile": {
                "anti_patterns": [
                    {"claims": [f"claim_{i}" for i in range(10)]},
                ],
            },
        }
        with patch("lintgate.theory_extractor.extract_theory", return_value=mock_profile):
            _seed_theory_constraints("/test/project", output)
        assert len(output["theory_constraints"]) == 5

    def test_truncates_long_claims(self):
        output: dict[str, Any] = {}
        long_claim = "A" * 200
        mock_profile = {
            "theory_profile": {
                "anti_patterns": [{"claims": [long_claim]}],
            },
        }
        with patch("lintgate.theory_extractor.extract_theory", return_value=mock_profile):
            _seed_theory_constraints("/test/project", output)
        assert len(output["theory_constraints"][0]) == 120

    def test_no_theory_profile_key(self):
        """Missing theory_profile key should produce no output."""
        output: dict[str, Any] = {}
        with patch("lintgate.theory_extractor.extract_theory", return_value={}):
            _seed_theory_constraints("/test/project", output)
        assert "theory_constraints" not in output

    def test_multiple_anti_pattern_entries(self):
        """Claims from multiple anti-pattern entries are aggregated."""
        output: dict[str, Any] = {}
        mock_profile = {
            "theory_profile": {
                "anti_patterns": [
                    {"claims": ["claim_a", "claim_b"]},
                    {"claims": ["claim_c"]},
                ],
            },
        }
        with patch("lintgate.theory_extractor.extract_theory", return_value=mock_profile):
            _seed_theory_constraints("/test/project", output)
        assert len(output["theory_constraints"]) == 3
        assert "claim_a" in output["theory_constraints"]
        assert "claim_c" in output["theory_constraints"]

    def test_anti_pattern_entry_with_no_claims_key(self):
        """Anti-pattern entries missing 'claims' key are skipped."""
        output: dict[str, Any] = {}
        mock_profile = {
            "theory_profile": {
                "anti_patterns": [{"description": "no claims here"}],
            },
        }
        with patch("lintgate.theory_extractor.extract_theory", return_value=mock_profile):
            _seed_theory_constraints("/test/project", output)
        assert "theory_constraints" not in output


# ══════════════════════════════════════════════════════════════════
# impl_hygiene_check
# ══════════════════════════════════════════════════════════════════


class TestImplHygieneCheck:
    def _make_hygiene_result(
        self,
        command_class: str = "pip_install",
        warnings: list | None = None,
        recommendation: str = "All clear",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            command_class=command_class,
            warnings=warnings or [],
            recommendation=recommendation,
        )

    def _make_warning(
        self,
        check: str = "venv_active",
        message: str = "No venv detected",
        confidence: float = 0.9,
        actionability: str = "immediate",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            check=check,
            message=message,
            confidence=confidence,
            actionability=actionability,
        )

    @patch("lintgate.hygiene.classify_and_check")
    @patch("lintgate.state.log_feature_usage")
    def test_pass_status_when_no_warnings(self, mock_log, mock_classify):
        mock_classify.return_value = self._make_hygiene_result(
            command_class="pytest_run", warnings=[], recommendation="Looks good"
        )
        helpers = _make_helpers()
        result = json.loads(impl_hygiene_check(helpers, path="/test", planned_action="pytest"))
        assert result["status"] == "pass"
        assert result["command_class"] == "pytest_run"
        assert result["message"] == "Looks good"
        assert result["next_actions"] == []

    @patch("lintgate.hygiene.classify_and_check")
    @patch("lintgate.state.log_feature_usage")
    def test_warnings_status_with_warnings(self, mock_log, mock_classify):
        warn = self._make_warning(
            check="venv_active",
            message="No virtual environment active",
            confidence=0.95,
            actionability="immediate",
        )
        mock_classify.return_value = self._make_hygiene_result(
            command_class="pip_install",
            warnings=[warn],
            recommendation="Create venv first",
        )
        helpers = _make_helpers()
        result = json.loads(
            impl_hygiene_check(helpers, path="/test", planned_action="pip install foo")
        )
        assert result["status"] == "warnings"
        assert result["command_class"] == "pip_install"
        assert len(result["warnings"]) == 1
        assert result["warnings"][0]["check"] == "venv_active"
        assert result["warnings"][0]["confidence"] == 0.95
        assert result["recommendation"] == "Create venv first"

    @patch("lintgate.hygiene.classify_and_check")
    @patch("lintgate.state.log_feature_usage")
    def test_immediate_warnings_generate_next_actions(self, mock_log, mock_classify):
        warn = self._make_warning(actionability="immediate")
        mock_classify.return_value = self._make_hygiene_result(warnings=[warn])
        helpers = _make_helpers()
        result = json.loads(
            impl_hygiene_check(helpers, path="/test", planned_action="pip install foo")
        )
        assert len(result["next_actions"]) == 1
        assert result["next_actions"][0]["tool"] == "terminal"

    @patch("lintgate.hygiene.classify_and_check")
    @patch("lintgate.state.log_feature_usage")
    def test_advisory_warnings_generate_no_next_actions(self, mock_log, mock_classify):
        warn = self._make_warning(actionability="advisory")
        mock_classify.return_value = self._make_hygiene_result(warnings=[warn])
        helpers = _make_helpers()
        result = json.loads(
            impl_hygiene_check(helpers, path="/test", planned_action="pip install foo")
        )
        assert result["next_actions"] == []

    @patch("lintgate.hygiene.classify_and_check", side_effect=Exception("boom"))
    @patch("lintgate.state.log_feature_usage")
    def test_classify_exception_returns_no_checks_applicable(self, mock_log, mock_classify):
        helpers = _make_helpers()
        result = json.loads(impl_hygiene_check(helpers, path="/test", planned_action="ls"))
        assert result["status"] == "no_checks_applicable"
        assert "No hygiene checks applicable" in result["message"]

    @patch("lintgate.hygiene.classify_and_check", return_value=None)
    @patch("lintgate.state.log_feature_usage")
    def test_classify_returns_none(self, mock_log, mock_classify):
        helpers = _make_helpers()
        result = json.loads(impl_hygiene_check(helpers, path="/test", planned_action="ls"))
        assert result["status"] == "no_checks_applicable"

    @patch("lintgate.hygiene.classify_and_check")
    @patch("lintgate.state.log_feature_usage")
    def test_max_two_next_actions_from_warnings(self, mock_log, mock_classify):
        warns = [
            self._make_warning(check=f"check_{i}", actionability="immediate") for i in range(5)
        ]
        mock_classify.return_value = self._make_hygiene_result(warnings=warns)
        helpers = _make_helpers()
        result = json.loads(
            impl_hygiene_check(helpers, path="/test", planned_action="pip install foo")
        )
        # Only first 2 immediate warnings generate next_actions
        assert len(result["next_actions"]) <= 2

    @patch("lintgate.hygiene.classify_and_check")
    @patch("lintgate.state.log_feature_usage")
    def test_warning_confidence_is_rounded(self, mock_log, mock_classify):
        warn = self._make_warning(confidence=0.8567)
        mock_classify.return_value = self._make_hygiene_result(warnings=[warn])
        helpers = _make_helpers()
        result = json.loads(
            impl_hygiene_check(helpers, path="/test", planned_action="pip install foo")
        )
        assert result["warnings"][0]["confidence"] == 0.86


# ══════════════════════════════════════════════════════════════════
# impl_constraint_check
# ══════════════════════════════════════════════════════════════════


class TestImplConstraintCheck:
    """Tests for impl_constraint_check — mocks the entire controlplane
    stack at the I/O boundary (session_memory, config, behavior_compass)."""

    def _setup_mocks(self, relevant_hyps=None, compass_attrs=None):
        """Build a mock compass plus the standard patch dict."""
        from lintgate.controlplane.behavior.types import BehaviorCompass

        compass = BehaviorCompass()
        if compass_attrs:
            for k, v in compass_attrs.items():
                setattr(compass, k, v)

        if relevant_hyps is None:
            relevant_hyps = []

        patches = {
            "lintgate.config.load_controlplane_config": MagicMock(
                return_value=SimpleNamespace(session_max_age_hours=4.0)
            ),
            "lintgate.controlplane.session_memory.get_or_create_session": MagicMock(
                return_value=SimpleNamespace(session_id="test-session")
            ),
            "lintgate.controlplane.session_memory.load_behavior_compass": MagicMock(
                return_value=compass
            ),
            "lintgate.controlplane.session_memory.save_behavior_compass": MagicMock(),
            "lintgate.controlplane.session_memory.save_session": MagicMock(),
            "lintgate.controlplane.behavior_compass.normalize_command_sig": MagicMock(
                return_value="pytest:run"
            ),
            "lintgate.controlplane.behavior_compass.find_relevant_hypotheses": MagicMock(
                return_value=relevant_hyps
            ),
            "lintgate.controlplane.behavior_compass.add_declared_hypothesis": MagicMock(),
            "lintgate.controlplane.behavior_compass.compute_coverage": MagicMock(
                return_value=SimpleNamespace(constraints_verified=0)
            ),
            "lintgate.controlplane.behavior_compass.compute_uncertainty_zones": MagicMock(
                return_value=[]
            ),
            "lintgate.state.log_feature_usage": MagicMock(),
        }
        return compass, patches

    def _run_with_patches(self, patches, helpers=None, **kwargs):
        import contextlib

        helpers = helpers or _make_helpers()
        with contextlib.ExitStack() as stack:
            for target, mock_obj in patches.items():
                stack.enter_context(patch(target, mock_obj))
            raw = impl_constraint_check(
                helpers,
                path=kwargs.get("path", "/test"),
                planned_action=kwargs.get("planned_action", "pytest tests/"),
                known_constraints=kwargs.get("known_constraints"),
            )
        return json.loads(raw)

    def test_basic_output_structure(self):
        compass, patches = self._setup_mocks()
        result = self._run_with_patches(patches)
        assert "constraint_ledger" in result
        assert "coverage" in result
        assert "uncertainty_zones" in result
        assert "similar_failures" in result
        assert "recommendation" in result

    def test_coverage_fields(self):
        compass, patches = self._setup_mocks()
        result = self._run_with_patches(patches)
        cov = result["coverage"]
        assert "constraints_verified" in cov
        assert "agent_reported" in cov
        assert "relevant_hypotheses" in cov
        assert "coverage_gap" in cov
        assert "prediction_recall" in cov

    def test_with_known_constraints(self):
        hyps = [_make_hypothesis(hyp_id="h1", claim="test hypothesis about errors")]
        compass, patches = self._setup_mocks(relevant_hyps=hyps)
        result = self._run_with_patches(patches, known_constraints=["hypothesis about errors"])
        assert result["coverage"]["agent_reported"] == 1

    def test_first_session_hint_on_first_check(self):
        compass, patches = self._setup_mocks()
        # constraint_check_count_session starts at 0, incremented to 1 inside impl
        result = self._run_with_patches(patches)
        assert "first_session_hint" in result

    def test_no_first_session_hint_on_subsequent_check(self):
        compass, patches = self._setup_mocks(compass_attrs={"constraint_check_count_session": 1})
        # Will be incremented to 2 inside impl
        result = self._run_with_patches(patches)
        assert "first_session_hint" not in result

    def test_onboarding_included_when_not_config_enabled(self):
        compass, patches = self._setup_mocks()
        helpers = _make_helpers(
            _build_onboarding_status=lambda p: {"config_state": "config_missing"}
        )
        result = self._run_with_patches(patches, helpers=helpers)
        assert "onboarding" in result

    def test_onboarding_not_included_when_config_enabled(self):
        compass, patches = self._setup_mocks()
        result = self._run_with_patches(patches)
        # First check with config_enabled should not include onboarding
        assert "onboarding" not in result

    def test_next_actions_when_coverage_gap(self):
        hyps = [
            _make_hypothesis(hyp_id="h1", claim="test hypothesis about errors"),
            _make_hypothesis(hyp_id="h2", claim="performance regression in module"),
        ]
        compass, patches = self._setup_mocks(relevant_hyps=hyps)
        result = self._run_with_patches(patches)
        # coverage_gap should be >0 since no declared constraints match
        assert "next_actions" in result
        assert len(result["next_actions"]) > 0

    def test_no_next_actions_when_good_coverage(self):
        compass, patches = self._setup_mocks(relevant_hyps=[])
        result = self._run_with_patches(patches)
        # No relevant hyps means gap=0 and recall=1.0
        assert "next_actions" not in result

    def test_constraint_ledger_caps_at_8(self):
        hyps = [_make_hypothesis(hyp_id=f"h{i}", claim=f"hypothesis claim {i}") for i in range(15)]
        compass, patches = self._setup_mocks(relevant_hyps=hyps)
        result = self._run_with_patches(patches)
        assert len(result["constraint_ledger"]) <= 8

    def test_constraint_ledger_claim_truncated_at_100(self):
        long_claim = "a " * 80  # 160 chars
        hyps = [_make_hypothesis(hyp_id="h1", claim=long_claim)]
        compass, patches = self._setup_mocks(relevant_hyps=hyps)
        result = self._run_with_patches(patches)
        assert len(result["constraint_ledger"][0]["claim"]) <= 100

    def test_uncertainty_zones_capped_at_3(self):
        compass, patches = self._setup_mocks()
        patches["lintgate.controlplane.behavior_compass.compute_uncertainty_zones"] = MagicMock(
            return_value=["z1", "z2", "z3", "z4", "z5"]
        )
        result = self._run_with_patches(patches)
        assert len(result["uncertainty_zones"]) <= 3


# ══════════════════════════════════════════════════════════════════
# impl_prediction_register
# ══════════════════════════════════════════════════════════════════


class TestImplPredictionRegister:
    """Tests for impl_prediction_register — mocks the controlplane stack."""

    def _setup_mocks(self, relevant_hyps=None, compass_attrs=None):
        from lintgate.controlplane.behavior.types import BehaviorCompass

        compass = BehaviorCompass()
        if compass_attrs:
            for k, v in compass_attrs.items():
                setattr(compass, k, v)

        patches = {
            "lintgate.config.load_controlplane_config": MagicMock(
                return_value=SimpleNamespace(session_max_age_hours=4.0)
            ),
            "lintgate.controlplane.session_memory.get_or_create_session": MagicMock(
                return_value=SimpleNamespace(session_id="test-session")
            ),
            "lintgate.controlplane.session_memory.load_behavior_compass": MagicMock(
                return_value=compass
            ),
            "lintgate.controlplane.session_memory.save_behavior_compass": MagicMock(),
            "lintgate.controlplane.session_memory.save_session": MagicMock(),
            "lintgate.controlplane.behavior_compass.normalize_command_sig": MagicMock(
                return_value="pytest:run"
            ),
            "lintgate.controlplane.behavior_compass.find_relevant_hypotheses": MagicMock(
                return_value=relevant_hyps or []
            ),
            "lintgate.controlplane.behavior_compass.compute_prediction_accuracy": MagicMock(
                return_value=None
            ),
            "lintgate.state.log_feature_usage": MagicMock(),
        }
        return compass, patches

    def _run(self, patches, **kwargs):
        import contextlib

        helpers = _make_helpers()
        with contextlib.ExitStack() as stack:
            for target, mock_obj in patches.items():
                stack.enter_context(patch(target, mock_obj))
            raw = impl_prediction_register(
                helpers,
                path=kwargs.get("path", "/test"),
                planned_action=kwargs.get("planned_action", "run pytest tests/"),
                prediction=kwargs.get("prediction", "tests pass"),
                prediction_type=kwargs.get("prediction_type", "exit_code"),
                prediction_value=kwargs.get("prediction_value", 0),
            )
        return json.loads(raw)

    def test_invalid_prediction_type_returns_error(self):
        compass, patches = self._setup_mocks()
        result = self._run(patches, prediction_type="invalid_type")
        assert "error" in result
        assert "Invalid prediction_type" in result["error"]
        assert sorted(result["valid_types"]) == ["error_signature", "exit_code", "stdout_contains"]

    def test_non_bash_action_returns_not_applicable(self):
        compass, patches = self._setup_mocks()
        patches["lintgate.controlplane.behavior_compass.normalize_command_sig"] = MagicMock(
            return_value="unknown:unknown"
        )
        result = self._run(
            patches,
            planned_action="read a file",
        )
        assert result["status"] == "not_applicable"
        assert "Predictions apply to Bash" in result["message"]

    def test_successful_registration(self):
        compass, patches = self._setup_mocks()
        result = self._run(patches)
        assert result["status"] == "registered"
        assert "prediction_id" in result
        assert result["command_sig"] == "pytest:run"
        assert result["prediction_type"] == "exit_code"
        assert result["prediction_value"] == 0

    def test_linked_hypothesis_id_when_relevant_exists(self):
        hyp = _make_hypothesis(hyp_id="h42", claim="test error hypothesis")
        compass, patches = self._setup_mocks(relevant_hyps=[hyp])
        result = self._run(patches)
        assert result["linked_hypothesis_id"] == "h42"

    def test_no_linked_hypothesis_when_none_relevant(self):
        compass, patches = self._setup_mocks(relevant_hyps=[])
        result = self._run(patches)
        assert result["linked_hypothesis_id"] is None

    def test_prediction_tracking_section_present(self):
        compass, patches = self._setup_mocks()
        result = self._run(patches)
        tracking = result["prediction_tracking"]
        assert "pending_count" in tracking
        assert "checked_count" in tracking

    def test_accuracy_included_when_available(self):
        compass, patches = self._setup_mocks()
        patches["lintgate.controlplane.behavior_compass.compute_prediction_accuracy"] = MagicMock(
            return_value=0.85
        )
        result = self._run(patches)
        assert result["prediction_tracking"]["accuracy"] == 0.85

    def test_accuracy_note_when_not_enough_predictions(self):
        from lintgate.controlplane.behavior.types import BehaviorCompass

        compass = BehaviorCompass()
        # Add 2 checked predictions to prediction_log
        compass.prediction_log = [
            {"prediction_id": "p1", "status": "confirmed"},
            {"prediction_id": "p2", "status": "falsified"},
        ]
        patches = {
            "lintgate.config.load_controlplane_config": MagicMock(
                return_value=SimpleNamespace(session_max_age_hours=4.0)
            ),
            "lintgate.controlplane.session_memory.get_or_create_session": MagicMock(
                return_value=SimpleNamespace(session_id="s")
            ),
            "lintgate.controlplane.session_memory.load_behavior_compass": MagicMock(
                return_value=compass
            ),
            "lintgate.controlplane.session_memory.save_behavior_compass": MagicMock(),
            "lintgate.controlplane.session_memory.save_session": MagicMock(),
            "lintgate.controlplane.behavior_compass.normalize_command_sig": MagicMock(
                return_value="pytest:run"
            ),
            "lintgate.controlplane.behavior_compass.find_relevant_hypotheses": MagicMock(
                return_value=[]
            ),
            "lintgate.controlplane.behavior_compass.compute_prediction_accuracy": MagicMock(
                return_value=None
            ),
            "lintgate.state.log_feature_usage": MagicMock(),
        }
        helpers = _make_helpers()
        import contextlib

        with contextlib.ExitStack() as stack:
            for target, mock_obj in patches.items():
                stack.enter_context(patch(target, mock_obj))
            raw = impl_prediction_register(
                helpers,
                path="/test",
                planned_action="run pytest tests/",
                prediction="tests pass",
                prediction_type="exit_code",
                prediction_value=0,
            )
        result = json.loads(raw)
        assert "accuracy_note" in result["prediction_tracking"]
        assert "3 more" in result["prediction_tracking"]["accuracy_note"]

    def test_recent_outcomes_included(self):
        from lintgate.controlplane.behavior.types import BehaviorCompass

        compass = BehaviorCompass()
        compass.prediction_log = [
            {"prediction_id": "p1", "status": "confirmed"},
        ]
        patches = {
            "lintgate.config.load_controlplane_config": MagicMock(
                return_value=SimpleNamespace(session_max_age_hours=4.0)
            ),
            "lintgate.controlplane.session_memory.get_or_create_session": MagicMock(
                return_value=SimpleNamespace(session_id="s")
            ),
            "lintgate.controlplane.session_memory.load_behavior_compass": MagicMock(
                return_value=compass
            ),
            "lintgate.controlplane.session_memory.save_behavior_compass": MagicMock(),
            "lintgate.controlplane.session_memory.save_session": MagicMock(),
            "lintgate.controlplane.behavior_compass.normalize_command_sig": MagicMock(
                return_value="pytest:run"
            ),
            "lintgate.controlplane.behavior_compass.find_relevant_hypotheses": MagicMock(
                return_value=[]
            ),
            "lintgate.controlplane.behavior_compass.compute_prediction_accuracy": MagicMock(
                return_value=None
            ),
            "lintgate.state.log_feature_usage": MagicMock(),
        }
        helpers = _make_helpers()
        import contextlib

        with contextlib.ExitStack() as stack:
            for target, mock_obj in patches.items():
                stack.enter_context(patch(target, mock_obj))
            raw = impl_prediction_register(
                helpers,
                path="/test",
                planned_action="run pytest tests/",
                prediction="tests pass",
                prediction_type="exit_code",
                prediction_value=0,
            )
        result = json.loads(raw)
        assert "recent_outcomes" in result["prediction_tracking"]
        assert result["prediction_tracking"]["recent_outcomes"][0]["id"] == "p1"

    def test_next_actions_always_present(self):
        compass, patches = self._setup_mocks()
        result = self._run(patches)
        assert "next_actions" in result
        assert len(result["next_actions"]) == 1
        assert result["next_actions"][0]["tool"] == "terminal"

    def test_exit_code_prediction_type(self):
        compass, patches = self._setup_mocks()
        result = self._run(patches, prediction_type="exit_code", prediction_value=1)
        assert result["prediction_type"] == "exit_code"
        assert result["prediction_value"] == 1

    def test_error_signature_prediction_type(self):
        compass, patches = self._setup_mocks()
        result = self._run(
            patches, prediction_type="error_signature", prediction_value="ImportError"
        )
        assert result["prediction_type"] == "error_signature"
        assert result["prediction_value"] == "ImportError"

    def test_stdout_contains_prediction_type(self):
        compass, patches = self._setup_mocks()
        result = self._run(patches, prediction_type="stdout_contains", prediction_value="passed")
        assert result["prediction_type"] == "stdout_contains"
        assert result["prediction_value"] == "passed"

    def test_bash_keyword_detection_case_insensitive(self):
        compass, patches = self._setup_mocks()
        patches["lintgate.controlplane.behavior_compass.normalize_command_sig"] = MagicMock(
            return_value="python:script"
        )
        result = self._run(patches, planned_action="PYTHON script.py")
        assert result["status"] == "registered"

    def test_non_recognizable_sig_returns_not_applicable(self):
        compass, patches = self._setup_mocks()
        patches["lintgate.controlplane.behavior_compass.normalize_command_sig"] = MagicMock(
            return_value=""
        )
        result = self._run(patches, planned_action="run bash test")
        assert result["status"] == "not_applicable"


# ══════════════════════════════════════════════════════════════════
# impl_behavior_precheck
# ══════════════════════════════════════════════════════════════════


class TestImplBehaviorPrecheck:
    def test_deprecation_message_present(self):
        constraint_output = json.dumps(
            {
                "recommendation": "Good constraint coverage.",
                "coverage": {"constraints_verified": 0, "agent_reported": 0},
            }
        )
        hygiene_output = json.dumps({"status": "pass"})

        tools = {
            "constraint_check": MagicMock(return_value=constraint_output),
            "hygiene_check": MagicMock(return_value=hygiene_output),
        }
        helpers = _make_helpers()

        result_raw = impl_behavior_precheck(helpers, tools, path="/test", planned_action="pytest")
        result = json.loads(result_raw)
        assert "deprecation" in result
        assert "behavior_precheck is deprecated" in result["deprecation"]["message"]
        assert "migration" in result["deprecation"]

    def test_with_hygiene_warnings(self):
        constraint_output = json.dumps({"recommendation": "ok", "coverage": {}})
        hygiene_output = json.dumps(
            {
                "status": "warnings",
                "command_class": "pip_install",
                "warnings": [{"check": "venv", "message": "no venv"}],
                "recommendation": "create venv",
            }
        )

        tools = {
            "constraint_check": MagicMock(return_value=constraint_output),
            "hygiene_check": MagicMock(return_value=hygiene_output),
        }
        helpers = _make_helpers()

        result_raw = impl_behavior_precheck(
            helpers, tools, path="/test", planned_action="pip install foo"
        )
        result = json.loads(result_raw)
        assert "hygiene" in result
        assert result["hygiene"]["command_class"] == "pip_install"
        assert result["hygiene"]["recommendation"] == "create venv"

    def test_no_hygiene_key_when_status_pass(self):
        constraint_output = json.dumps({"recommendation": "ok", "coverage": {}})
        hygiene_output = json.dumps({"status": "pass"})

        tools = {
            "constraint_check": MagicMock(return_value=constraint_output),
            "hygiene_check": MagicMock(return_value=hygiene_output),
        }
        helpers = _make_helpers()

        result_raw = impl_behavior_precheck(helpers, tools, path="/test", planned_action="ls")
        result = json.loads(result_raw)
        assert "hygiene" not in result

    def test_prediction_missing_type(self):
        constraint_output = json.dumps({"recommendation": "ok", "coverage": {}})
        hygiene_output = json.dumps({"status": "pass"})

        tools = {
            "constraint_check": MagicMock(return_value=constraint_output),
            "hygiene_check": MagicMock(return_value=hygiene_output),
        }
        helpers = _make_helpers()

        result_raw = impl_behavior_precheck(
            helpers,
            tools,
            path="/test",
            planned_action="pytest",
            prediction="will pass",
            prediction_type=None,
            prediction_value=0,
        )
        result = json.loads(result_raw)
        assert "prediction_error" in result
        assert "prediction_type is required" in result["prediction_error"]["errors"][0]

    def test_prediction_missing_value(self):
        constraint_output = json.dumps({"recommendation": "ok", "coverage": {}})
        hygiene_output = json.dumps({"status": "pass"})

        tools = {
            "constraint_check": MagicMock(return_value=constraint_output),
            "hygiene_check": MagicMock(return_value=hygiene_output),
        }
        helpers = _make_helpers()

        result_raw = impl_behavior_precheck(
            helpers,
            tools,
            path="/test",
            planned_action="pytest",
            prediction="will pass",
            prediction_type="exit_code",
            prediction_value=None,
        )
        result = json.loads(result_raw)
        assert "prediction_error" in result
        assert "prediction_value is required" in result["prediction_error"]["errors"][0]

    def test_prediction_invalid_type(self):
        constraint_output = json.dumps({"recommendation": "ok", "coverage": {}})
        hygiene_output = json.dumps({"status": "pass"})

        tools = {
            "constraint_check": MagicMock(return_value=constraint_output),
            "hygiene_check": MagicMock(return_value=hygiene_output),
        }
        helpers = _make_helpers()

        result_raw = impl_behavior_precheck(
            helpers,
            tools,
            path="/test",
            planned_action="pytest",
            prediction="will pass",
            prediction_type="invalid_type",
            prediction_value=0,
        )
        result = json.loads(result_raw)
        assert "prediction_error" in result
        assert "invalid" in result["prediction_error"]["errors"][0]

    def test_prediction_registered_successfully(self):
        constraint_output = json.dumps({"recommendation": "ok", "coverage": {}})
        hygiene_output = json.dumps({"status": "pass"})
        pred_output = json.dumps(
            {
                "status": "registered",
                "prediction_tracking": {"pending_count": 1},
            }
        )

        tools = {
            "constraint_check": MagicMock(return_value=constraint_output),
            "hygiene_check": MagicMock(return_value=hygiene_output),
            "prediction_register": MagicMock(return_value=pred_output),
        }
        helpers = _make_helpers()

        result_raw = impl_behavior_precheck(
            helpers,
            tools,
            path="/test",
            planned_action="pytest",
            prediction="exit 0",
            prediction_type="exit_code",
            prediction_value=0,
        )
        result = json.loads(result_raw)
        assert result["prediction_tracking"]["prediction_registered"] is True
        assert result["prediction_tracking"]["pending_count"] == 1
        tools["prediction_register"].assert_called_once_with(
            path="/test",
            planned_action="pytest",
            prediction="exit 0",
            prediction_type="exit_code",
            prediction_value=0,
        )
        assert "prediction_error" not in result

    def test_no_prediction_param_skips_registration(self):
        constraint_output = json.dumps({"recommendation": "ok", "coverage": {}})
        hygiene_output = json.dumps({"status": "pass"})

        tools = {
            "constraint_check": MagicMock(return_value=constraint_output),
            "hygiene_check": MagicMock(return_value=hygiene_output),
        }
        helpers = _make_helpers()

        result_raw = impl_behavior_precheck(helpers, tools, path="/test", planned_action="ls")
        result = json.loads(result_raw)
        # No prediction_tracking or prediction_error
        assert "prediction_error" not in result

    def test_prediction_not_registered_no_flag(self):
        """When prediction_register returns status != 'registered', the flag is not set."""
        constraint_output = json.dumps({"recommendation": "ok", "coverage": {}})
        hygiene_output = json.dumps({"status": "pass"})
        pred_output = json.dumps({"status": "not_applicable", "message": "not bash"})

        tools = {
            "constraint_check": MagicMock(return_value=constraint_output),
            "hygiene_check": MagicMock(return_value=hygiene_output),
            "prediction_register": MagicMock(return_value=pred_output),
        }
        helpers = _make_helpers()

        result_raw = impl_behavior_precheck(
            helpers,
            tools,
            path="/test",
            planned_action="pytest",
            prediction="will pass",
            prediction_type="exit_code",
            prediction_value=0,
        )
        result = json.loads(result_raw)
        # prediction_registered flag should NOT be present
        pt = result.get("prediction_tracking", {})
        assert pt.get("prediction_registered") is not True

    def test_both_type_and_value_errors(self):
        """When both prediction_type is None and prediction_value is None, both errors show."""
        constraint_output = json.dumps({"recommendation": "ok", "coverage": {}})
        hygiene_output = json.dumps({"status": "pass"})

        tools = {
            "constraint_check": MagicMock(return_value=constraint_output),
            "hygiene_check": MagicMock(return_value=hygiene_output),
        }
        helpers = _make_helpers()

        result_raw = impl_behavior_precheck(
            helpers,
            tools,
            path="/test",
            planned_action="pytest",
            prediction="will pass",
            prediction_type=None,
            prediction_value=None,
        )
        result = json.loads(result_raw)
        assert len(result["prediction_error"]["errors"]) == 2

    def test_constraint_check_forwarded_with_known_constraints(self):
        constraint_output = json.dumps({"recommendation": "ok", "coverage": {}})
        hygiene_output = json.dumps({"status": "pass"})

        tools = {
            "constraint_check": MagicMock(return_value=constraint_output),
            "hygiene_check": MagicMock(return_value=hygiene_output),
        }
        helpers = _make_helpers()

        impl_behavior_precheck(
            helpers,
            tools,
            path="/test",
            planned_action="pytest",
            known_constraints=["constraint_a"],
        )
        tools["constraint_check"].assert_called_once_with(
            path="/test",
            planned_action="pytest",
            known_constraints=["constraint_a"],
        )


# ══════════════════════════════════════════════════════════════════
# impl_global_memory_status
# ══════════════════════════════════════════════════════════════════


class TestImplGlobalMemoryStatus:
    def _make_cp_config(self, **overrides):
        defaults = {
            "global_memory_enabled": True,
            "global_memory_ttl_days": 90,
            "global_memory_alpha": 0.6,
            "global_memory_decay_horizon": 50,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _make_profile(self, **overrides):
        defaults = {
            "session_count": 5,
            "updated_at": "2026-03-13T00:00:00",
            "signal_priors": {},
            "intent_ratios": {},
            "nudge_outcomes": {},
            "computed_bias_adjustments": {},
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    @patch(
        "lintgate.controlplane.global_behavior_profile.GLOBAL_PROFILE_PATH", "/test/profile.json"
    )
    @patch("lintgate.controlplane.global_behavior_profile.load_global_profile")
    @patch("lintgate.controlplane.session_memory.load_session", return_value=None)
    @patch("lintgate.config.load_controlplane_config")
    def test_basic_structure(self, mock_config, mock_session, mock_profile):
        mock_config.return_value = self._make_cp_config()
        mock_profile.return_value = self._make_profile()
        helpers = _make_helpers()
        raw = impl_global_memory_status(helpers, path="/test")
        result = json.loads(raw)

        assert result["scope"] == "project"
        assert result["enabled"] is True
        assert result["session_count"] == 5
        assert "alpha_config" in result
        assert result["alpha_config"]["initial"] == 0.6
        assert result["alpha_config"]["decay_horizon"] == 50
        assert result["alpha_config"]["ttl_days"] == 90

    @patch("lintgate.config.load_controlplane_config", return_value=None)
    def test_returns_error_when_no_config(self, mock_config):
        helpers = _make_helpers()
        raw = impl_global_memory_status(helpers, path="/test")
        result = json.loads(raw)
        assert "error" in result
        assert "not configured" in result["error"]

    @patch(
        "lintgate.controlplane.global_behavior_profile.GLOBAL_PROFILE_PATH", "/test/profile.json"
    )
    @patch("lintgate.controlplane.global_behavior_profile.load_global_profile")
    @patch("lintgate.controlplane.session_memory.load_session", return_value=None)
    @patch("lintgate.config.load_controlplane_config")
    def test_nudge_rates_computed(self, mock_config, mock_session, mock_profile):
        mock_config.return_value = self._make_cp_config()
        mock_profile.return_value = self._make_profile(
            nudge_outcomes={
                "approach_cycle": {"accepted": 3, "ignored": 1},
                "verification_debt": {"accepted": 0, "ignored": 0},
            }
        )
        helpers = _make_helpers()
        raw = impl_global_memory_status(helpers, path="/test")
        result = json.loads(raw)
        assert "approach_cycle" in result["nudge_outcomes"]
        assert result["nudge_outcomes"]["approach_cycle"]["acceptance_rate"] == 0.75
        # zero-total nudge outcomes should be excluded
        assert "verification_debt" not in result["nudge_outcomes"]

    @patch(
        "lintgate.controlplane.global_behavior_profile.GLOBAL_PROFILE_PATH", "/test/profile.json"
    )
    @patch("lintgate.controlplane.global_behavior_profile.load_global_profile")
    @patch("lintgate.controlplane.session_memory.load_session", return_value=None)
    @patch("lintgate.config.load_controlplane_config")
    def test_intent_ratios_normalized(self, mock_config, mock_session, mock_profile):
        mock_config.return_value = self._make_cp_config()
        mock_profile.return_value = self._make_profile(intent_ratios={"debug": 30, "implement": 70})
        helpers = _make_helpers()
        raw = impl_global_memory_status(helpers, path="/test")
        result = json.loads(raw)
        ratios = result["intent_ratios_normalized"]
        assert ratios["implement"] == 0.7
        assert ratios["debug"] == 0.3

    @patch(
        "lintgate.controlplane.global_behavior_profile.GLOBAL_PROFILE_PATH", "/test/profile.json"
    )
    @patch("lintgate.controlplane.global_behavior_profile.load_global_profile")
    @patch("lintgate.controlplane.session_memory.load_session", return_value=None)
    @patch("lintgate.config.load_controlplane_config")
    def test_empty_intent_ratios(self, mock_config, mock_session, mock_profile):
        mock_config.return_value = self._make_cp_config()
        mock_profile.return_value = self._make_profile(intent_ratios={})
        helpers = _make_helpers()
        raw = impl_global_memory_status(helpers, path="/test")
        result = json.loads(raw)
        assert result["intent_ratios_normalized"] == {}

    @patch(
        "lintgate.controlplane.global_behavior_profile.GLOBAL_PROFILE_PATH", "/test/profile.json"
    )
    @patch("lintgate.controlplane.global_behavior_profile.load_global_profile")
    @patch("lintgate.controlplane.session_memory.load_session")
    @patch("lintgate.config.load_controlplane_config")
    def test_transfer_info_from_session(self, mock_config, mock_session_load, mock_profile):
        mock_config.return_value = self._make_cp_config()
        mock_profile.return_value = self._make_profile()
        import time as _time

        mock_session_load.return_value = SimpleNamespace(
            latest_transfer_packet={"type": "handoff"},
            last_active=_time.time() - 3600,  # 1 hour ago
            resolution_repertoire=[{"id": "r1"}, {"id": "r2"}],
            delivery_health_summary={"skipped": 2},
        )
        helpers = _make_helpers()
        raw = impl_global_memory_status(helpers, path="/test")
        result = json.loads(raw)
        transfer = result["transfer_telemetry"]
        assert transfer["latest_transfer_packet"] == {"type": "handoff"}
        assert transfer["resolutions_available"] == 2
        assert transfer["suppressed_nudges"] == 2
        assert abs(transfer["packet_age_hours"] - 1.0) < 0.1

    @patch(
        "lintgate.controlplane.global_behavior_profile.GLOBAL_PROFILE_PATH", "/test/profile.json"
    )
    @patch("lintgate.controlplane.global_behavior_profile.load_global_profile")
    @patch("lintgate.controlplane.session_memory.load_session")
    @patch("lintgate.config.load_controlplane_config")
    def test_transfer_info_no_packet(self, mock_config, mock_session_load, mock_profile):
        mock_config.return_value = self._make_cp_config()
        mock_profile.return_value = self._make_profile()
        mock_session_load.return_value = SimpleNamespace(
            latest_transfer_packet=None,
            last_active=0.0,
            resolution_repertoire=[],
            delivery_health_summary={},
        )
        helpers = _make_helpers()
        raw = impl_global_memory_status(helpers, path="/test")
        result = json.loads(raw)
        transfer = result["transfer_telemetry"]
        assert transfer["latest_transfer_packet"] is None
        assert transfer["packet_age_hours"] is None

    @patch(
        "lintgate.controlplane.global_behavior_profile.GLOBAL_PROFILE_PATH", "/test/profile.json"
    )
    @patch("lintgate.controlplane.global_behavior_profile.load_global_profile")
    @patch("lintgate.controlplane.session_memory.load_session", return_value=None)
    @patch("lintgate.config.load_controlplane_config")
    def test_no_session_returns_empty_transfer(self, mock_config, mock_session, mock_profile):
        mock_config.return_value = self._make_cp_config()
        mock_profile.return_value = self._make_profile()
        helpers = _make_helpers()
        raw = impl_global_memory_status(helpers, path="/test")
        result = json.loads(raw)
        assert result["transfer_telemetry"] == {}

    @patch(
        "lintgate.controlplane.global_behavior_profile.GLOBAL_PROFILE_PATH", "/test/profile.json"
    )
    @patch("lintgate.controlplane.global_behavior_profile.load_global_profile")
    @patch("lintgate.controlplane.session_memory.load_session", return_value=None)
    @patch("lintgate.config.load_controlplane_config")
    def test_bias_adjustments_rounded(self, mock_config, mock_session, mock_profile):
        mock_config.return_value = self._make_cp_config()
        mock_profile.return_value = self._make_profile(
            computed_bias_adjustments={"approach_cycle": 0.12345678}
        )
        helpers = _make_helpers()
        raw = impl_global_memory_status(helpers, path="/test")
        result = json.loads(raw)
        assert result["computed_bias_adjustments"]["approach_cycle"] == 0.1235


# ══════════════════════════════════════════════════════════════════
# impl_global_memory_reset
# ══════════════════════════════════════════════════════════════════


class TestImplGlobalMemoryReset:
    @patch(
        "lintgate.controlplane.global_behavior_profile.GLOBAL_PROFILE_PATH", "/test/profile.json"
    )
    @patch("lintgate.controlplane.global_behavior_profile.save_global_profile")
    def test_returns_reset_status(self, mock_save):
        helpers = _make_helpers()
        raw = impl_global_memory_reset(helpers, path="/test")
        result = json.loads(raw)
        assert result["status"] == "reset"
        assert result["scope"] == "project"
        assert "reset to empty state" in result["message"]
        mock_save.assert_called_once()

    @patch(
        "lintgate.controlplane.global_behavior_profile.GLOBAL_PROFILE_PATH", "/test/profile.json"
    )
    @patch("lintgate.controlplane.global_behavior_profile.save_global_profile")
    def test_profile_path_in_output(self, mock_save):
        helpers = _make_helpers()
        raw = impl_global_memory_reset(helpers, path="/test")
        result = json.loads(raw)
        assert result["profile_path"] == "/test/profile.json"

    @patch(
        "lintgate.controlplane.global_behavior_profile.GLOBAL_PROFILE_PATH", "/test/profile.json"
    )
    @patch("lintgate.controlplane.global_behavior_profile.save_global_profile")
    def test_saves_fresh_profile(self, mock_save):
        from lintgate.controlplane.global_behavior_profile import GlobalBehaviorProfile

        helpers = _make_helpers()
        impl_global_memory_reset(helpers, path="/test")
        saved_arg = mock_save.call_args[0][0]
        assert isinstance(saved_arg, GlobalBehaviorProfile)
        assert saved_arg.session_count == 0

    @patch(
        "lintgate.controlplane.global_behavior_profile.GLOBAL_PROFILE_PATH", "/test/profile.json"
    )
    @patch("lintgate.controlplane.global_behavior_profile.save_global_profile")
    def test_scope_note_present(self, mock_save):
        helpers = _make_helpers()
        raw = impl_global_memory_reset(helpers, path="/test")
        result = json.loads(raw)
        assert "scope_note" in result
        assert "Cross-session" in result["scope_note"]

    @patch(
        "lintgate.controlplane.global_behavior_profile.GLOBAL_PROFILE_PATH", "/test/profile.json"
    )
    @patch("lintgate.controlplane.global_behavior_profile.save_global_profile")
    def test_project_root_is_absolute(self, mock_save):
        helpers = _make_helpers()
        raw = impl_global_memory_reset(helpers, path=".")
        result = json.loads(raw)
        # project_root should be an absolute path
        import os

        assert os.path.isabs(result["project_root"])
