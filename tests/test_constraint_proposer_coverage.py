"""Comprehensive tests for lintgate/controlplane/constraint_proposer.py.

Targets uncovered symbols: ProposedConstraint, TheoryCoherenceResult,
check_theory_coherence, propose_constraints_from_patterns,
store_proposals_in_session, update_constraint_status, and all private helpers.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lintgate.controlplane.constraint_proposer import (
    _BEHAVIOR_CONSTRAINT_MAP,
    _COHERENCE_STOPWORDS,
    _DEFAULT_PROPOSAL_THRESHOLD,
    _NEGATIVE_POLARITY,
    _PATTERN_CONSTRAINT_MAP,
    _POSITIVE_POLARITY,
    ProposedConstraint,
    TheoryCoherenceResult,
    _apply_coherence_check,
    _build_generic_template,
    _compute_proposal_confidence,
    _extract_coherence_keywords,
    _is_contradicting,
    _resolve_constraint_template,
    check_theory_coherence,
    propose_constraints_from_patterns,
    store_proposals_in_session,
    update_constraint_status,
)
from lintgate.controlplane.session_memory import SessionMemory
from lintgate.controlplane.types import ControlPlaneConfig, InquiryConfig

# ── TheoryCoherenceResult ──────────────────────────────────────────


class TestTheoryCoherenceResult:
    def test_default_values(self) -> None:
        result = TheoryCoherenceResult()
        assert result.aligned is None
        assert result.supporting_claims == []
        assert result.contradicting_claims == []
        assert result.coherence_score == 0.0

    def test_to_dict(self) -> None:
        result = TheoryCoherenceResult(
            aligned=True,
            supporting_claims=["claim1", "claim2"],
            contradicting_claims=["claim3"],
            coherence_score=0.5,
        )
        d = result.to_dict()
        assert d == {
            "aligned": True,
            "supporting_claims": ["claim1", "claim2"],
            "contradicting_claims": ["claim3"],
            "coherence_score": 0.5,
        }

    def test_from_dict_full(self) -> None:
        data = {
            "aligned": False,
            "supporting_claims": ["s1"],
            "contradicting_claims": ["c1", "c2"],
            "coherence_score": -0.333,
        }
        result = TheoryCoherenceResult.from_dict(data)
        assert result.aligned is False
        assert result.supporting_claims == ["s1"]
        assert result.contradicting_claims == ["c1", "c2"]
        assert result.coherence_score == -0.333

    def test_from_dict_missing_keys_uses_defaults(self) -> None:
        result = TheoryCoherenceResult.from_dict({})
        assert result.aligned is None
        assert result.supporting_claims == []
        assert result.contradicting_claims == []
        assert result.coherence_score == 0.0

    def test_roundtrip(self) -> None:
        original = TheoryCoherenceResult(
            aligned=True,
            supporting_claims=["a"],
            contradicting_claims=[],
            coherence_score=1.0,
        )
        restored = TheoryCoherenceResult.from_dict(original.to_dict())
        assert restored.aligned == original.aligned
        assert restored.supporting_claims == original.supporting_claims
        assert restored.contradicting_claims == original.contradicting_claims
        assert restored.coherence_score == original.coherence_score


# ── ProposedConstraint ─────────────────────────────────────────────


class TestProposedConstraint:
    def test_default_values(self) -> None:
        pc = ProposedConstraint()
        assert pc.source == "pattern_bank"
        assert pc.pattern_key == ""
        assert pc.proposed_rule == ""
        assert pc.rule_type == ""
        assert pc.rationale == ""
        assert pc.confidence == 0.0
        assert pc.status == "proposed"
        assert pc.theory_coherence is None
        assert pc.drift_warning is False

    def test_to_dict_without_theory_coherence(self) -> None:
        pc = ProposedConstraint(
            source="pattern_bank",
            pattern_key="ruff|F821",
            proposed_rule="# LINTGATE_FORBID_REGEX: undef",
            rule_type="LINTGATE_FORBID_REGEX",
            rationale="appeared 5 times",
            confidence=0.8,
            status="proposed",
        )
        d = pc.to_dict()
        assert d["theory_coherence"] is None
        assert d["pattern_key"] == "ruff|F821"
        assert d["drift_warning"] is False

    def test_to_dict_with_theory_coherence(self) -> None:
        tc = TheoryCoherenceResult(aligned=True, coherence_score=0.5)
        pc = ProposedConstraint(theory_coherence=tc, drift_warning=True)
        d = pc.to_dict()
        assert d["theory_coherence"]["aligned"] is True
        assert d["theory_coherence"]["coherence_score"] == 0.5
        assert d["drift_warning"] is True

    def test_from_dict_without_theory_coherence(self) -> None:
        data = {
            "source": "pattern_bank",
            "pattern_key": "ruff|F401",
            "proposed_rule": "cleanup imports",
            "rule_type": "theory_note",
            "rationale": "appeared 3 times",
            "confidence": 0.6,
            "status": "accepted",
            "theory_coherence": None,
            "drift_warning": False,
        }
        pc = ProposedConstraint.from_dict(data)
        assert pc.pattern_key == "ruff|F401"
        assert pc.status == "accepted"
        assert pc.theory_coherence is None

    def test_from_dict_with_theory_coherence(self) -> None:
        data = {
            "theory_coherence": {
                "aligned": False,
                "supporting_claims": [],
                "contradicting_claims": ["bad claim"],
                "coherence_score": -1.0,
            },
            "drift_warning": True,
        }
        pc = ProposedConstraint.from_dict(data)
        assert pc.theory_coherence is not None
        assert pc.theory_coherence.aligned is False
        assert pc.theory_coherence.contradicting_claims == ["bad claim"]
        assert pc.drift_warning is True

    def test_from_dict_missing_keys_uses_defaults(self) -> None:
        pc = ProposedConstraint.from_dict({})
        assert pc.source == "pattern_bank"
        assert pc.pattern_key == ""
        assert pc.confidence == 0.0
        assert pc.status == "proposed"

    def test_from_dict_theory_coherence_non_dict_ignored(self) -> None:
        """Non-dict theory_coherence values should result in None."""
        pc = ProposedConstraint.from_dict({"theory_coherence": "not_a_dict"})
        assert pc.theory_coherence is None

    def test_roundtrip(self) -> None:
        tc = TheoryCoherenceResult(
            aligned=True,
            supporting_claims=["s1"],
            contradicting_claims=["c1"],
            coherence_score=0.333,
        )
        original = ProposedConstraint(
            source="pattern_bank",
            pattern_key="ruff|F821",
            proposed_rule="# forbid undef",
            rule_type="LINTGATE_FORBID_REGEX",
            rationale="appeared 6/5 runs",
            confidence=0.875,
            status="proposed",
            theory_coherence=tc,
            drift_warning=True,
        )
        restored = ProposedConstraint.from_dict(original.to_dict())
        assert restored.pattern_key == original.pattern_key
        assert restored.confidence == original.confidence
        assert restored.theory_coherence is not None
        assert restored.theory_coherence.coherence_score == tc.coherence_score
        assert restored.drift_warning is True


# ── _extract_coherence_keywords ────────────────────────────────────


class TestExtractCoherenceKeywords:
    def test_extracts_4plus_char_words(self) -> None:
        result = _extract_coherence_keywords("The quick brown fox jumps")
        assert "quick" in result
        assert "brown" in result
        assert "jumps" in result
        # "fox" is 3 chars, excluded
        assert "fox" not in result

    def test_excludes_stopwords(self) -> None:
        result = _extract_coherence_keywords("this should have been detected from using runs")
        # All of these are stopwords
        assert "this" not in result
        assert "have" not in result
        assert "been" not in result
        assert "detected" not in result
        assert "runs" not in result

    def test_empty_string(self) -> None:
        assert _extract_coherence_keywords("") == []

    def test_only_short_words(self) -> None:
        assert _extract_coherence_keywords("a on to in of") == []

    def test_case_insensitive(self) -> None:
        result = _extract_coherence_keywords("COMPLEXITY Functions")
        assert "complexity" in result
        assert "functions" in result

    def test_non_alpha_chars_stripped(self) -> None:
        result = _extract_coherence_keywords("import_123 symbols; annotations!")
        assert "import" in result
        assert "symbols" in result
        assert "annotations" in result


# ── _is_contradicting ──────────────────────────────────────────────


class TestIsContradicting:
    def test_no_overlapping_nouns_returns_false(self) -> None:
        assert _is_contradicting("avoid using imports", "prefer small functions") is False

    def test_same_polarity_returns_false(self) -> None:
        # Both negative polarity with overlapping nouns
        assert (
            _is_contradicting(
                "avoid complexity always",
                "never create complexity",
            )
            is False
        )

    def test_opposite_polarity_with_overlap_returns_true(self) -> None:
        # Proposal: negative polarity, Claim: positive polarity, overlap on "complexity"
        assert (
            _is_contradicting(
                "avoid complexity patterns",
                "prefer complexity patterns always",
            )
            is True
        )

    def test_neutral_polarity_returns_false(self) -> None:
        # No polarity words means neutral, so no contradiction
        assert (
            _is_contradicting(
                "complexity patterns here",
                "complexity patterns there",
            )
            is False
        )

    def test_proposal_neutral_returns_false(self) -> None:
        # Proposal has no polarity words
        assert (
            _is_contradicting(
                "complexity patterns here",
                "avoid complexity patterns",
            )
            is False
        )


# ── _build_generic_template ────────────────────────────────────────


class TestBuildGenericTemplate:
    def test_returns_theory_note(self) -> None:
        result = _build_generic_template("somelinter", "somekind")
        assert result["rule_type"] == "theory_note"
        assert "somelinter/somekind" in result["template"]
        assert result["base_confidence"] == 0.4

    def test_rationale_template_has_placeholders(self) -> None:
        result = _build_generic_template("ruff", "E501")
        formatted = result["rationale_template"].format(count=3, window=5)
        assert "3" in formatted
        assert "5" in formatted


# ── _resolve_constraint_template ───────────────────────────────────


class TestResolveConstraintTemplate:
    def test_behavior_channel_maps_to_behavior_map(self) -> None:
        result = _resolve_constraint_template("behavior_channel", "approach_cycling")
        assert result == _BEHAVIOR_CONSTRAINT_MAP["approach_cycling"]

    def test_behavior_channel_unknown_kind_falls_to_pattern_map(self) -> None:
        result = _resolve_constraint_template("behavior_channel", "F821")
        assert result == _PATTERN_CONSTRAINT_MAP["F821"]

    def test_behavior_channel_unknown_both_falls_to_generic(self) -> None:
        result = _resolve_constraint_template("behavior_channel", "unknown_xyz")
        assert result["rule_type"] == "theory_note"
        assert "behavior_channel/unknown_xyz" in result["template"]

    def test_regular_linter_maps_to_pattern_map(self) -> None:
        result = _resolve_constraint_template("ruff", "F401")
        assert result == _PATTERN_CONSTRAINT_MAP["F401"]

    def test_unknown_linter_kind_falls_to_generic(self) -> None:
        result = _resolve_constraint_template("pylint", "C0123")
        assert result["rule_type"] == "theory_note"
        assert "pylint/C0123" in result["template"]

    def test_all_known_behavior_kinds_resolve(self) -> None:
        for kind in _BEHAVIOR_CONSTRAINT_MAP:
            result = _resolve_constraint_template("behavior_channel", kind)
            assert result["rule_type"] == "theory_note"

    def test_all_known_pattern_kinds_resolve(self) -> None:
        for kind in _PATTERN_CONSTRAINT_MAP:
            result = _resolve_constraint_template("ruff", kind)
            assert "template" in result


# ── _compute_proposal_confidence ───────────────────────────────────


class TestComputeProposalConfidence:
    def test_at_threshold_no_bonus(self) -> None:
        template = {"base_confidence": 0.8}
        # recent_count=3 means no bonus (bonus only when > 3)
        assert _compute_proposal_confidence(template, 3) == 0.8

    def test_above_threshold_adds_bonus(self) -> None:
        template = {"base_confidence": 0.8}
        # recent_count=4: (4-3)*0.075 = 0.075 bonus
        result = _compute_proposal_confidence(template, 4)
        assert abs(result - 0.875) < 1e-9

    def test_bonus_capped_at_015(self) -> None:
        template = {"base_confidence": 0.8}
        # recent_count=100: bonus would be (100-3)*0.075 = 7.275, capped at 0.15
        result = _compute_proposal_confidence(template, 100)
        assert abs(result - 0.95) < 1e-9

    def test_total_capped_at_1(self) -> None:
        template = {"base_confidence": 0.95}
        result = _compute_proposal_confidence(template, 10)
        assert result == 1.0

    def test_missing_base_confidence_defaults_to_05(self) -> None:
        template: dict = {}
        result = _compute_proposal_confidence(template, 3)
        assert result == 0.5

    def test_below_threshold_no_bonus(self) -> None:
        template = {"base_confidence": 0.4}
        result = _compute_proposal_confidence(template, 1)
        assert result == 0.4


# ── check_theory_coherence ─────────────────────────────────────────


class TestCheckTheoryCoherence:
    def test_no_profile_returns_none(self) -> None:
        proposal = ProposedConstraint(proposed_rule="some rule", rationale="some rationale")
        assert check_theory_coherence(proposal, None) is None

    def test_empty_profile_returns_none(self) -> None:
        proposal = ProposedConstraint(proposed_rule="some rule", rationale="some rationale")
        assert check_theory_coherence(proposal, {}) is None

    def test_no_keywords_returns_none(self) -> None:
        # All words are short or stopwords
        proposal = ProposedConstraint(proposed_rule="the and for", rationale="has was")
        assert check_theory_coherence(proposal, {"facets": {}}) is None

    @patch("lintgate.theory_extractor.get_theory_context_from_profile")
    def test_no_claims_returns_neutral(self, mock_get: MagicMock) -> None:
        mock_get.return_value = {"claims": []}
        proposal = ProposedConstraint(
            proposed_rule="complexity patterns detected",
            rationale="appeared many times",
        )
        result = check_theory_coherence(proposal, {"facets": {"core_theory": {}}})
        assert result is not None
        assert result.aligned is None
        assert result.coherence_score == 0.0

    @patch("lintgate.theory_extractor.get_theory_context_from_profile")
    def test_all_supporting_claims(self, mock_get: MagicMock) -> None:
        mock_get.return_value = {
            "claims": [
                {"claim": "prefer smaller composable functions always"},
                {"claim": "require annotations types always"},
            ]
        }
        proposal = ProposedConstraint(
            proposed_rule="prefer smaller functions always",
            rationale="complexity appeared often",
        )
        result = check_theory_coherence(proposal, {"facets": {}})
        assert result is not None
        # Both claims should be supporting (same polarity as proposal)
        assert result.coherence_score > 0
        assert result.aligned is True

    @patch("lintgate.theory_extractor.get_theory_context_from_profile")
    def test_contradicting_claims_set_negative_score(self, mock_get: MagicMock) -> None:
        mock_get.return_value = {
            "claims": [
                {"claim": "always prefer complexity patterns"},
            ]
        }
        # Proposal says "avoid" (negative), claim says "prefer always" (positive)
        proposal = ProposedConstraint(
            proposed_rule="avoid complexity patterns",
            rationale="complexity appeared often",
        )
        result = check_theory_coherence(proposal, {"facets": {}})
        assert result is not None
        assert len(result.contradicting_claims) > 0
        assert result.coherence_score < 0

    @patch("lintgate.theory_extractor.get_theory_context_from_profile")
    def test_mixed_claims(self, mock_get: MagicMock) -> None:
        mock_get.return_value = {
            "claims": [
                {"claim": "avoid complexity patterns always"},
                {"claim": "always prefer complexity patterns"},
            ]
        }
        proposal = ProposedConstraint(
            proposed_rule="avoid complexity patterns",
            rationale="complexity appeared often",
        )
        result = check_theory_coherence(proposal, {"facets": {}})
        assert result is not None
        # One supporting, one contradicting
        assert len(result.supporting_claims) + len(result.contradicting_claims) == 2

    @patch("lintgate.theory_extractor.get_theory_context_from_profile")
    def test_coherence_score_rounded(self, mock_get: MagicMock) -> None:
        mock_get.return_value = {
            "claims": [
                {"claim": "prefer complexity patterns always"},
                {"claim": "prefer complexity patterns always"},
                {"claim": "avoid complexity patterns never"},
            ]
        }
        proposal = ProposedConstraint(
            proposed_rule="prefer complexity patterns always",
            rationale="complexity appeared often",
        )
        result = check_theory_coherence(proposal, {"facets": {}})
        assert result is not None
        # Score should be rounded to 3 decimals
        score_str = str(result.coherence_score)
        if "." in score_str:
            decimals = len(score_str.split(".")[1])
            assert decimals <= 3


# ── _apply_coherence_check ─────────────────────────────────────────


class TestApplyCoherenceCheck:
    def test_no_config_is_noop(self) -> None:
        proposal = ProposedConstraint()
        _apply_coherence_check(proposal, None, None)
        assert proposal.theory_coherence is None

    def test_coherence_check_disabled_is_noop(self) -> None:
        config = ControlPlaneConfig(
            inquiry=InquiryConfig(theory_coherence_check=False),
        )
        proposal = ProposedConstraint()
        _apply_coherence_check(proposal, config, SessionMemory())
        assert proposal.theory_coherence is None

    def test_no_session_is_noop(self) -> None:
        config = ControlPlaneConfig(
            inquiry=InquiryConfig(theory_coherence_check=True),
        )
        proposal = ProposedConstraint()
        _apply_coherence_check(proposal, config, None)
        assert proposal.theory_coherence is None

    def test_no_theory_profile_cache_is_noop(self) -> None:
        config = ControlPlaneConfig(
            inquiry=InquiryConfig(theory_coherence_check=True),
        )
        session = SessionMemory()
        session.theory_profile_cache = None
        proposal = ProposedConstraint()
        _apply_coherence_check(proposal, config, session)
        assert proposal.theory_coherence is None

    @patch("lintgate.controlplane.constraint_proposer.check_theory_coherence")
    def test_applies_coherence_and_sets_drift_warning(self, mock_check: MagicMock) -> None:
        mock_check.return_value = TheoryCoherenceResult(
            aligned=False,
            contradicting_claims=["contradicts something"],
            coherence_score=-0.5,
        )
        config = ControlPlaneConfig(
            inquiry=InquiryConfig(theory_coherence_check=True),
        )
        session = SessionMemory()
        session.theory_profile_cache = {"facets": {"core_theory": {}}}
        proposal = ProposedConstraint(
            proposed_rule="avoid something",
            rationale="appeared 5 times",
        )
        _apply_coherence_check(proposal, config, session)
        assert proposal.theory_coherence is not None
        assert proposal.drift_warning is True

    @patch("lintgate.controlplane.constraint_proposer.check_theory_coherence")
    def test_no_contradictions_no_drift_warning(self, mock_check: MagicMock) -> None:
        mock_check.return_value = TheoryCoherenceResult(
            aligned=True,
            supporting_claims=["supports something"],
            contradicting_claims=[],
            coherence_score=1.0,
        )
        config = ControlPlaneConfig(
            inquiry=InquiryConfig(theory_coherence_check=True),
        )
        session = SessionMemory()
        session.theory_profile_cache = {"facets": {}}
        proposal = ProposedConstraint()
        _apply_coherence_check(proposal, config, session)
        assert proposal.theory_coherence is not None
        assert proposal.drift_warning is False

    @patch("lintgate.controlplane.constraint_proposer.check_theory_coherence")
    def test_coherence_returns_none_no_assignment(self, mock_check: MagicMock) -> None:
        mock_check.return_value = None
        config = ControlPlaneConfig(
            inquiry=InquiryConfig(theory_coherence_check=True),
        )
        session = SessionMemory()
        session.theory_profile_cache = {"facets": {}}
        proposal = ProposedConstraint()
        _apply_coherence_check(proposal, config, session)
        assert proposal.theory_coherence is None
        assert proposal.drift_warning is False


# ── propose_constraints_from_patterns ──────────────────────────────


def _make_alert(
    linter: str = "ruff",
    kind: str = "F821",
    alert_reason: str = "recurring_across_runs",
    recent_run_count: int = 5,
) -> dict:
    return {
        "linter": linter,
        "kind": kind,
        "alert_reason": alert_reason,
        "recent_run_count": recent_run_count,
    }


class TestProposeConstraintsFromPatterns:
    def test_empty_alerts_returns_empty(self) -> None:
        result = propose_constraints_from_patterns({"alerted_patterns": []})
        assert result == []

    def test_no_alerted_patterns_key_returns_empty(self) -> None:
        result = propose_constraints_from_patterns({})
        assert result == []

    def test_known_pattern_produces_proposal(self) -> None:
        report = {"alerted_patterns": [_make_alert("ruff", "F821", recent_run_count=5)]}
        proposals = propose_constraints_from_patterns(report)
        assert len(proposals) == 1
        assert proposals[0].pattern_key == "ruff|F821"
        assert proposals[0].rule_type == "LINTGATE_FORBID_REGEX"
        assert proposals[0].status == "proposed"

    def test_below_threshold_excluded(self) -> None:
        report = {"alerted_patterns": [_make_alert(recent_run_count=3)]}
        proposals = propose_constraints_from_patterns(report, threshold=5)
        assert len(proposals) == 0

    def test_non_recurring_alert_reason_excluded(self) -> None:
        report = {"alerted_patterns": [_make_alert(alert_reason="single_occurrence")]}
        proposals = propose_constraints_from_patterns(report)
        assert len(proposals) == 0

    def test_dedup_within_session(self) -> None:
        session = SessionMemory()
        session.proposed_constraints = [{"pattern_key": "ruff|F821"}]
        report = {"alerted_patterns": [_make_alert("ruff", "F821")]}
        proposals = propose_constraints_from_patterns(report, session=session)
        assert len(proposals) == 0

    def test_dedup_against_existing_rules(self) -> None:
        template = _PATTERN_CONSTRAINT_MAP["F821"]["template"]
        report = {"alerted_patterns": [_make_alert("ruff", "F821")]}
        proposals = propose_constraints_from_patterns(report, existing_rules=[template])
        assert len(proposals) == 0

    def test_unknown_pattern_uses_generic_template(self) -> None:
        report = {"alerted_patterns": [_make_alert("pylint", "C999", recent_run_count=6)]}
        proposals = propose_constraints_from_patterns(report)
        assert len(proposals) == 1
        assert proposals[0].rule_type == "theory_note"
        assert "pylint/C999" in proposals[0].proposed_rule

    def test_proposals_sorted_by_confidence_descending(self) -> None:
        report = {
            "alerted_patterns": [
                _make_alert("pylint", "C999", recent_run_count=5),  # base 0.4
                _make_alert("ruff", "F821", recent_run_count=5),  # base 0.8
                _make_alert("ruff", "F401", recent_run_count=5),  # base 0.6
            ]
        }
        proposals = propose_constraints_from_patterns(report)
        assert len(proposals) == 3
        confidences = [p.confidence for p in proposals]
        assert confidences == sorted(confidences, reverse=True)

    def test_behavioral_pattern_proposal(self) -> None:
        report = {
            "alerted_patterns": [
                _make_alert("behavior_channel", "approach_cycling", recent_run_count=5)
            ]
        }
        proposals = propose_constraints_from_patterns(report)
        assert len(proposals) == 1
        assert proposals[0].rule_type == "theory_note"
        assert (
            "constraint_check" in proposals[0].proposed_rule.lower()
            or "constraint" in proposals[0].proposed_rule.lower()
        )

    def test_dedup_within_single_run(self) -> None:
        """Two alerts with same pattern_key in one report should produce only one proposal."""
        report = {
            "alerted_patterns": [
                _make_alert("ruff", "F821", recent_run_count=5),
                _make_alert("ruff", "F821", recent_run_count=7),
            ]
        }
        proposals = propose_constraints_from_patterns(report)
        assert len(proposals) == 1

    def test_custom_threshold(self) -> None:
        report = {"alerted_patterns": [_make_alert(recent_run_count=3)]}
        proposals = propose_constraints_from_patterns(report, threshold=2)
        assert len(proposals) == 1

    def test_session_none_no_dedup(self) -> None:
        """When session is None, dedup against session doesn't happen."""
        report = {"alerted_patterns": [_make_alert("ruff", "F821")]}
        proposals = propose_constraints_from_patterns(report, session=None)
        assert len(proposals) == 1


# ── store_proposals_in_session ─────────────────────────────────────


class TestStoreProposalsInSession:
    def test_stores_new_proposals(self) -> None:
        session = SessionMemory()
        proposals = [
            ProposedConstraint(pattern_key="ruff|F821", proposed_rule="rule1"),
            ProposedConstraint(pattern_key="ruff|F401", proposed_rule="rule2"),
        ]
        store_proposals_in_session(session, proposals)
        assert len(session.proposed_constraints) == 2
        assert session.proposed_constraints[0]["pattern_key"] == "ruff|F821"
        assert session.proposed_constraints[1]["pattern_key"] == "ruff|F401"

    def test_does_not_overwrite_existing(self) -> None:
        session = SessionMemory()
        session.proposed_constraints = [{"pattern_key": "ruff|F821", "status": "accepted"}]
        proposals = [
            ProposedConstraint(pattern_key="ruff|F821", proposed_rule="new rule"),
        ]
        store_proposals_in_session(session, proposals)
        assert len(session.proposed_constraints) == 1
        assert session.proposed_constraints[0]["status"] == "accepted"

    def test_appends_only_new_keys(self) -> None:
        session = SessionMemory()
        session.proposed_constraints = [{"pattern_key": "ruff|F821"}]
        proposals = [
            ProposedConstraint(pattern_key="ruff|F821"),
            ProposedConstraint(pattern_key="ruff|F401"),
        ]
        store_proposals_in_session(session, proposals)
        assert len(session.proposed_constraints) == 2

    def test_empty_proposals_is_noop(self) -> None:
        session = SessionMemory()
        store_proposals_in_session(session, [])
        assert len(session.proposed_constraints) == 0


# ── update_constraint_status ───────────────────────────────────────


class TestUpdateConstraintStatus:
    def test_updates_existing_constraint(self) -> None:
        session = SessionMemory()
        session.proposed_constraints = [{"pattern_key": "ruff|F821", "status": "proposed"}]
        result = update_constraint_status(session, "ruff|F821", "accepted")
        assert result is True
        assert session.proposed_constraints[0]["status"] == "accepted"

    def test_returns_false_when_not_found(self) -> None:
        session = SessionMemory()
        session.proposed_constraints = [{"pattern_key": "ruff|F401", "status": "proposed"}]
        result = update_constraint_status(session, "ruff|F821", "rejected")
        assert result is False

    def test_empty_session(self) -> None:
        session = SessionMemory()
        result = update_constraint_status(session, "ruff|F821", "accepted")
        assert result is False

    def test_updates_to_rejected(self) -> None:
        session = SessionMemory()
        session.proposed_constraints = [{"pattern_key": "ruff|F821", "status": "proposed"}]
        result = update_constraint_status(session, "ruff|F821", "rejected")
        assert result is True
        assert session.proposed_constraints[0]["status"] == "rejected"

    def test_updates_first_match_only(self) -> None:
        """If there are duplicates (shouldn't happen), only the first is updated."""
        session = SessionMemory()
        session.proposed_constraints = [
            {"pattern_key": "ruff|F821", "status": "proposed"},
            {"pattern_key": "ruff|F821", "status": "proposed"},
        ]
        update_constraint_status(session, "ruff|F821", "accepted")
        assert session.proposed_constraints[0]["status"] == "accepted"
        assert session.proposed_constraints[1]["status"] == "proposed"


# ── Module-level constant coverage ────────────────────────────────


class TestModuleConstants:
    def test_default_proposal_threshold(self) -> None:
        assert _DEFAULT_PROPOSAL_THRESHOLD == 5

    def test_pattern_constraint_map_has_expected_keys(self) -> None:
        expected_keys = {"F821", "F401", "return-value", "complexity", "B101"}
        assert set(_PATTERN_CONSTRAINT_MAP.keys()) == expected_keys

    def test_behavior_constraint_map_has_expected_keys(self) -> None:
        expected_keys = {
            "approach_cycling",
            "failure_amnesia",
            "premature_action",
            "brute_force_escalation",
            "verification_debt",
            "stale_model",
        }
        assert set(_BEHAVIOR_CONSTRAINT_MAP.keys()) == expected_keys

    def test_stopwords_are_all_lowercase(self) -> None:
        for word in _COHERENCE_STOPWORDS:
            assert word == word.lower(), f"Stopword {word!r} is not lowercase"

    def test_polarity_sets_are_disjoint(self) -> None:
        overlap = _POSITIVE_POLARITY & _NEGATIVE_POLARITY
        assert overlap == set(), f"Polarity sets overlap: {overlap}"

    def test_all_pattern_templates_have_required_keys(self) -> None:
        for key, tmpl in _PATTERN_CONSTRAINT_MAP.items():
            assert "rule_type" in tmpl, f"{key} missing rule_type"
            assert "template" in tmpl, f"{key} missing template"
            assert "rationale_template" in tmpl, f"{key} missing rationale_template"
            assert "base_confidence" in tmpl, f"{key} missing base_confidence"

    def test_all_behavior_templates_have_required_keys(self) -> None:
        for key, tmpl in _BEHAVIOR_CONSTRAINT_MAP.items():
            assert "rule_type" in tmpl, f"{key} missing rule_type"
            assert "template" in tmpl, f"{key} missing template"
            assert "rationale_template" in tmpl, f"{key} missing rationale_template"
            assert "base_confidence" in tmpl, f"{key} missing base_confidence"

    def test_rationale_templates_accept_count_and_window(self) -> None:
        for key, tmpl in {**_PATTERN_CONSTRAINT_MAP, **_BEHAVIOR_CONSTRAINT_MAP}.items():
            formatted = tmpl["rationale_template"].format(count=10, window=5)
            assert "10" in formatted, f"{key} rationale doesn't include count"
            assert "5" in formatted, f"{key} rationale doesn't include window"
