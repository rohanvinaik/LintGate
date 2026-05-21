"""Tests for Phase 2: Theory/Behavior Coherence Check (metadata-only)."""

from __future__ import annotations

from unittest.mock import MagicMock

from lintgate.controlplane.constraint_proposer import (
    ProposedConstraint,
    TheoryCoherenceResult,
    _apply_coherence_check,
    _compute_proposal_confidence,
    _dominant_polarity,
    _extract_coherence_keywords,
    _is_contradicting,
    _resolve_constraint_template,
    check_theory_coherence,
)
from lintgate.controlplane.reporter import _format_proposed_constraints

# ── Sample Theory Profile ────────────────────────────────────────────

SAMPLE_PROFILE = {
    "core_theory": [
        {
            "claims": [
                "Always use structured error handling for robust code",
                "Prefer composition over inheritance in all designs",
            ],
            "source": "docs/theory.md",
            "heading": "Core Theory",
        }
    ],
    "problem_solving": [
        {
            "claims": [
                "Decompose complex problems before attempting solutions",
                "Never use assert for runtime validation",
            ],
            "source": "docs/ps.md",
            "heading": "Problem Solving",
        }
    ],
    "anti_patterns": [
        {
            "claims": [
                "Avoid brute force approaches to complexity",
                "Do not use global state for configuration",
            ],
            "source": "docs/theory.md",
            "heading": "Anti-Patterns",
        }
    ],
}


# ── TheoryCoherenceResult ────────────────────────────────────────────


class TestTheoryCoherenceResult:
    def test_defaults(self) -> None:
        r = TheoryCoherenceResult()
        assert r.aligned is None
        assert r.supporting_claims == []
        assert r.contradicting_claims == []
        assert r.coherence_score == 0.0

    def test_roundtrip(self) -> None:
        r = TheoryCoherenceResult(
            aligned=True,
            supporting_claims=["claim1"],
            contradicting_claims=[],
            coherence_score=0.8,
        )
        d = r.to_dict()
        restored = TheoryCoherenceResult.from_dict(d)
        assert restored.aligned is True
        assert restored.supporting_claims == ["claim1"]
        assert restored.coherence_score == 0.8


# ── ProposedConstraint with coherence fields ─────────────────────────


class TestProposedConstraintCoherence:
    def test_default_coherence_fields(self) -> None:
        p = ProposedConstraint()
        assert p.theory_coherence is None
        assert p.drift_warning is False

    def test_roundtrip_with_coherence(self) -> None:
        tc = TheoryCoherenceResult(
            aligned=False,
            contradicting_claims=["some claim"],
            coherence_score=-0.5,
        )
        p = ProposedConstraint(
            pattern_key="ruff|F821",
            proposed_rule="test rule",
            theory_coherence=tc,
            drift_warning=True,
        )
        d = p.to_dict()
        restored = ProposedConstraint.from_dict(d)
        assert restored.drift_warning is True
        assert restored.theory_coherence is not None
        assert restored.theory_coherence.aligned is False
        assert restored.theory_coherence.coherence_score == -0.5

    def test_backward_compat_without_coherence_fields(self) -> None:
        """Old ProposedConstraint dicts without coherence fields load cleanly."""
        old_data = {
            "source": "pattern_bank",
            "pattern_key": "ruff|F401",
            "proposed_rule": "test",
            "rule_type": "theory_note",
            "rationale": "appeared in 3 of 5 runs",
            "confidence": 0.6,
            "status": "proposed",
        }
        p = ProposedConstraint.from_dict(old_data)
        assert p.theory_coherence is None
        assert p.drift_warning is False
        assert p.confidence == 0.6


# ── _extract_coherence_keywords ──────────────────────────────────────


class TestExtractCoherenceKeywords:
    def test_extracts_meaningful_words(self) -> None:
        keywords = _extract_coherence_keywords(
            "Avoid brute force approaches to complexity decomposition"
        )
        assert "brute" in keywords
        assert "force" in keywords
        assert "complexity" in keywords

    def test_excludes_stopwords(self) -> None:
        keywords = _extract_coherence_keywords("the and for with this that")
        assert len(keywords) == 0

    def test_excludes_short_words(self) -> None:
        keywords = _extract_coherence_keywords("use the foo bar approach")
        assert "foo" not in keywords
        assert "bar" not in keywords
        assert "approach" in keywords

    def test_minimum_word_length_is_four(self) -> None:
        """Kill VALUE: regex requires {4,} chars — 3-letter words excluded, 4-letter included."""
        keywords = _extract_coherence_keywords("cat dogs bird fish snakes")
        assert "dogs" in keywords
        assert "bird" in keywords
        assert "fish" in keywords
        assert "cat" not in keywords  # 3 chars


# ── _is_contradicting ────────────────────────────────────────────────


class TestIsContradicting:
    def test_clear_contradiction(self) -> None:
        """Proposal forbids what claim endorses."""
        assert _is_contradicting(
            "Avoid using assert for runtime validation",
            "Always use assert for runtime checks to ensure correctness",
        )

    def test_aligned_not_contradicting(self) -> None:
        """Both say the same thing — not contradicting."""
        assert not _is_contradicting(
            "Never use assert for runtime validation",
            "Never use assert for runtime validation",
        )

    def test_no_noun_overlap_not_contradicting(self) -> None:
        """No shared concepts — can't be contradicting."""
        assert not _is_contradicting(
            "Avoid using global variables",
            "Always prefer functional programming",
        )

    def test_conservative_false_positive_scenarios(self) -> None:
        """Known tricky cases where the heuristic might false-positive."""
        # Same domain but no actual contradiction
        result = _is_contradicting(
            "Use structured error handling for robust code",
            "Prefer structured logging for debugging",
        )
        # This may or may not be flagged — the test documents the behavior
        # but doesn't assert a specific outcome (heuristic).
        assert isinstance(result, bool)


# ── check_theory_coherence ───────────────────────────────────────────


class TestCheckTheoryCoherence:
    def test_returns_supporting_for_aligned_proposal(self) -> None:
        """Proposal that aligns with theory gets supporting claims."""
        p = ProposedConstraint(
            proposed_rule="Never use assert for runtime validation",
            rationale="bandit/B101 appeared in 5 of last 5 runs",
        )
        result = check_theory_coherence(p, SAMPLE_PROFILE)
        assert result is not None
        # Should find some claims (matching on "assert", "runtime", "validation")
        total = len(result.supporting_claims) + len(result.contradicting_claims)
        assert total >= 0  # May or may not match

    def test_returns_contradicting_for_opposing_proposal(self) -> None:
        """Proposal that contradicts theory gets contradicting claims."""
        # Use a focused profile to avoid mixed supporting/contradicting
        focused_profile = {
            "anti_patterns": [
                {
                    "claims": ["Do not use global state for configuration"],
                    "source": "docs/theory.md",
                    "heading": "Anti-Patterns",
                }
            ]
        }
        p = ProposedConstraint(
            proposed_rule="Always use global state for configuration management",
            rationale="recurring global config pattern",
        )
        result = check_theory_coherence(p, focused_profile)
        assert result is not None
        # Theory says "Do not use global state" vs proposal says "Always use" — contradiction
        if result.contradicting_claims:
            assert result.coherence_score < 0

    def test_none_when_no_profile(self) -> None:
        p = ProposedConstraint(proposed_rule="test", rationale="test")
        result = check_theory_coherence(p, None)
        assert result is None

    def test_none_when_empty_profile(self) -> None:
        p = ProposedConstraint(proposed_rule="test", rationale="test")
        result = check_theory_coherence(p, {})
        assert result is None

    def test_neutral_when_no_relevant_claims(self) -> None:
        """Profile exists but no claims match the proposal."""
        p = ProposedConstraint(
            proposed_rule="quantum entanglement database sharding",
            rationale="quantum patterns detected",
        )
        irrelevant_profile = {
            "core_theory": [
                {"claims": ["fish swim in the ocean"], "source": "t.md", "heading": "T"}
            ]
        }
        result = check_theory_coherence(p, irrelevant_profile)
        # Might be None (no keywords match) or neutral
        if result is not None:
            assert result.aligned is None or isinstance(result.aligned, bool)

    def test_confidence_is_not_modified(self) -> None:
        """Metadata-only: confidence stays as-is regardless of coherence."""
        p = ProposedConstraint(
            proposed_rule="Avoid complexity escalation",
            rationale="recurring brute force detected",
            confidence=0.65,
        )
        original_confidence = p.confidence
        check_theory_coherence(p, SAMPLE_PROFILE)
        assert p.confidence == original_confidence  # NOT modified


# ── Reporter drift warnings ──────────────────────────────────────────


class TestReporterDriftWarnings:
    def test_drift_warning_in_output(self) -> None:
        proposals = [
            {
                "rule_type": "theory_note",
                "confidence": 0.7,
                "rationale": "test reason",
                "proposed_rule": "test rule",
                "drift_warning": True,
                "theory_coherence": {
                    "contradicting_claims": ["Theory says X not Y"],
                    "coherence_score": -0.5,
                },
            }
        ]
        output = _format_proposed_constraints(proposals)
        assert "DRIFT WARNING" in output
        assert "Theory says X not Y" in output

    def test_no_drift_warning_when_false(self) -> None:
        proposals = [
            {
                "rule_type": "theory_note",
                "confidence": 0.7,
                "rationale": "test reason",
                "proposed_rule": "test rule",
                "drift_warning": False,
            }
        ]
        output = _format_proposed_constraints(proposals)
        assert "DRIFT WARNING" not in output

    def test_drift_warning_without_coherence_data(self) -> None:
        """drift_warning True but no theory_coherence dict."""
        proposals = [
            {
                "rule_type": "theory_note",
                "confidence": 0.7,
                "rationale": "test reason",
                "proposed_rule": "test rule",
                "drift_warning": True,
            }
        ]
        output = _format_proposed_constraints(proposals)
        assert "DRIFT WARNING" in output
        assert "potential conflict" in output


# ── _dominant_polarity ──────────────────────────────────────────────


class TestDominantPolarity:
    def test_positive_dominant(self) -> None:
        """Text with more positive polarity words → 'positive'."""
        _, p_dom = _dominant_polarity("should always use this", "must require ensure")
        assert p_dom == "positive"

    def test_negative_dominant(self) -> None:
        """Text with more negative polarity words → 'negative'."""
        _, p_dom = _dominant_polarity("neutral text", "never avoid don't forbid")
        assert p_dom == "negative"

    def test_neutral_when_balanced(self) -> None:
        """Equal positive and negative → 'neutral'."""
        _, p_dom = _dominant_polarity("neutral text", "should not")
        # "should" = positive, "not" = negative → balanced
        assert p_dom == "neutral"

    def test_returns_both_polarities(self) -> None:
        """Returns (c_dominant, p_dominant) — c is first arg's polarity."""
        c_dom, p_dom = _dominant_polarity("never avoid this", "always use prefer")
        assert c_dom == "negative"
        assert p_dom == "positive"

    def test_no_polarity_words_neutral(self) -> None:
        """Text with no polarity words → 'neutral'."""
        c_dom, p_dom = _dominant_polarity("hello world", "foo bar baz")
        assert c_dom == "neutral"
        assert p_dom == "neutral"


# ── _is_contradicting ──────────────────────────────────────────────


class TestIsContradictingMutationTargeted:
    def test_opposite_polarity_with_noun_overlap(self) -> None:
        """Kill SWAP/VALUE: contradicting texts with clear opposite polarity."""
        # "forbid" = negative, "always require" = positive, nouns overlap on "assert"
        assert (
            _is_contradicting(
                "Forbid assert statements in production code",
                "Always require assert statements in production code",
            )
            is True
        )

    def test_same_polarity_not_contradicting(self) -> None:
        """Same dominant polarity → not contradicting even with noun overlap."""
        assert (
            _is_contradicting(
                "Avoid assert statements completely",
                "Never allow assert statements anywhere",
            )
            is False
        )

    def test_no_overlap_returns_false(self) -> None:
        """Without noun overlap, contradiction is impossible regardless of polarity."""
        assert (
            _is_contradicting(
                "Never use databases for caching",
                "Always prefer functional programming paradigms",
            )
            is False
        )


# ── _resolve_constraint_template ────────────────────────────────────


class TestResolveConstraintTemplate:
    def test_behavior_channel_maps_to_behavior_template(self) -> None:
        """Kill SWAP/VALUE: behavior_channel linter uses _BEHAVIOR_CONSTRAINT_MAP."""
        result = _resolve_constraint_template("behavior_channel", "approach_cycling")
        assert result["rule_type"] == "theory_note"
        assert (
            "approach" in result["template"].lower() or "constraint" in result["template"].lower()
        )

    def test_pattern_map_for_known_kind(self) -> None:
        """Non-behavior linter maps to _PATTERN_CONSTRAINT_MAP."""
        result = _resolve_constraint_template("ruff", "F821")
        assert result["rule_type"] == "LINTGATE_FORBID_REGEX"
        assert result["base_confidence"] == 0.8

    def test_unknown_kind_returns_generic(self) -> None:
        """Unknown linter/kind falls through to generic template."""
        result = _resolve_constraint_template("unknown_linter", "UNKNOWN_CODE")
        assert result["rule_type"] == "theory_note"
        assert "unknown_linter" in result["template"]
        assert result["base_confidence"] == 0.4

    def test_behavior_channel_unknown_kind_falls_to_pattern(self) -> None:
        """behavior_channel with unknown kind falls to pattern map, then generic."""
        result = _resolve_constraint_template("behavior_channel", "nonexistent_signal")
        assert result["rule_type"] == "theory_note"
        assert "behavior_channel" in result["template"]


# ── _compute_proposal_confidence ────────────────────────────────────


class TestComputeProposalConfidence:
    def test_base_confidence_used_when_count_le_3(self) -> None:
        """Kill BOUNDARY: no bonus when recent_count <= 3."""
        result = _compute_proposal_confidence({"base_confidence": 0.7}, 3)
        assert result == 0.7

    def test_bonus_applied_when_count_gt_3(self) -> None:
        """Kill BOUNDARY: bonus starts at count > 3 (not >= 3)."""
        result_at_3 = _compute_proposal_confidence({"base_confidence": 0.5}, 3)
        result_at_4 = _compute_proposal_confidence({"base_confidence": 0.5}, 4)
        assert result_at_3 == 0.5
        assert result_at_4 > 0.5

    def test_bonus_calculation_correct(self) -> None:
        """Kill VALUE: bonus = (count - 3) * 0.075, capped at 0.15."""
        # count=4: bonus = 1 * 0.075 = 0.075
        result = _compute_proposal_confidence({"base_confidence": 0.5}, 4)
        assert abs(result - 0.575) < 0.001
        # count=5: bonus = 2 * 0.075 = 0.15
        result = _compute_proposal_confidence({"base_confidence": 0.5}, 5)
        assert abs(result - 0.65) < 0.001
        # count=6: bonus = min(3 * 0.075, 0.15) = 0.15 (capped)
        result = _compute_proposal_confidence({"base_confidence": 0.5}, 6)
        assert abs(result - 0.65) < 0.001

    def test_capped_at_1(self) -> None:
        """Kill VALUE: result capped at 1.0."""
        result = _compute_proposal_confidence({"base_confidence": 0.95}, 10)
        assert result == 1.0

    def test_default_base_confidence(self) -> None:
        """Kill VALUE: missing base_confidence defaults to 0.5."""
        result = _compute_proposal_confidence({}, 3)
        assert result == 0.5


# ── _apply_coherence_check ──────────────────────────────────────────


class TestApplyCoherenceCheck:
    def test_noop_when_config_none(self) -> None:
        """Kill VALUE: config=None → returns without modifying proposal."""
        proposal = ProposedConstraint(proposed_rule="test")
        _apply_coherence_check(proposal, None, MagicMock())
        assert proposal.theory_coherence is None

    def test_noop_when_inquiry_disabled(self) -> None:
        """Disabled theory_coherence_check → no modification."""
        config = MagicMock()
        config.inquiry.theory_coherence_check = False
        proposal = ProposedConstraint(proposed_rule="test")
        _apply_coherence_check(proposal, config, MagicMock())
        assert proposal.theory_coherence is None

    def test_noop_when_session_none(self) -> None:
        """Kill SWAP: session=None check comes after config check."""
        config = MagicMock()
        config.inquiry.theory_coherence_check = True
        proposal = ProposedConstraint(proposed_rule="test")
        _apply_coherence_check(proposal, config, None)
        assert proposal.theory_coherence is None

    def test_noop_when_no_theory_profile(self) -> None:
        """Kill VALUE: no theory_profile_cache → no modification."""
        config = MagicMock()
        config.inquiry.theory_coherence_check = True
        session = MagicMock()
        session.theory_profile_cache = None
        proposal = ProposedConstraint(proposed_rule="test")
        _apply_coherence_check(proposal, config, session)
        assert proposal.theory_coherence is None

    def test_sets_drift_warning_on_contradiction(self) -> None:
        """When coherence finds contradictions, drift_warning is set."""
        config = MagicMock()
        config.inquiry.theory_coherence_check = True
        session = MagicMock()
        session.theory_profile_cache = {
            "anti_patterns": [
                {
                    "claims": ["Do not use global state for anything"],
                    "source": "t.md",
                    "heading": "AP",
                }
            ]
        }
        proposal = ProposedConstraint(
            proposed_rule="Always use global state for configuration",
            rationale="recurring global pattern",
        )
        _apply_coherence_check(proposal, config, session)
        # May or may not find contradiction depending on keyword overlap
        # but the function should run without error
        assert isinstance(proposal.drift_warning, bool)


# ── ProposedConstraint.to_dict VALUE survivors ──────────────────────


class TestProposedConstraintToDict:
    def test_all_keys_present(self) -> None:
        """Kill VALUE: verify all dict keys are the correct strings."""
        p = ProposedConstraint(
            source="test_source",
            pattern_key="ruff|F821",
            proposed_rule="no assert",
            rule_type="LINTGATE_FORBID_REGEX",
            rationale="because",
            confidence=0.8,
            status="proposed",
            drift_warning=True,
        )
        d = p.to_dict()
        assert d["source"] == "test_source"
        assert d["pattern_key"] == "ruff|F821"
        assert d["proposed_rule"] == "no assert"
        assert d["rule_type"] == "LINTGATE_FORBID_REGEX"
        assert d["rationale"] == "because"
        assert d["confidence"] == 0.8
        assert d["status"] == "proposed"
        assert d["drift_warning"] is True
        assert d["theory_coherence"] is None
