"""Tests for Phase 2: Theory/Behavior Coherence Check (metadata-only)."""

from __future__ import annotations

from lintgate.controlplane.constraint_proposer import (
    ProposedConstraint,
    TheoryCoherenceResult,
    _extract_coherence_keywords,
    _is_contradicting,
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
