"""Tests for the constraint proposer — pattern bank → theory feedback loop."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lintgate.controlplane.constraint_proposer import (
    ProposedConstraint,
    TheoryCoherenceResult,
    check_theory_coherence,
    propose_constraints_from_patterns,
    store_proposals_in_session,
    update_constraint_status,
)
from lintgate.controlplane.session_memory import SessionMemory
from lintgate.controlplane.types import ControlPlaneConfig, InquiryConfig

# ── Helpers ──────────────────────────────────────────────────────────


def _make_pattern_report(
    alerts: list[dict] | None = None,
) -> dict:
    """Build a pattern report like update_pattern_bank() returns."""
    return {
        "alerted_patterns": alerts or [],
        "top_categories": [],
        "total_pattern_keys_tracked": len(alerts) if alerts else 0,
    }


def _recurring_alert(
    linter: str = "ruff",
    kind: str = "F821",
    recent_run_count: int = 5,
    count_this_run: int = 2,
) -> dict:
    """Build a recurring-across-runs pattern alert."""
    return {
        "linter": linter,
        "kind": kind,
        "count_this_run": count_this_run,
        "files_this_run": 1,
        "recent_run_count": recent_run_count,
        "total_count": 15,
        "alert_reason": "recurring_across_runs",
    }


# ── TheoryCoherenceResult ────────────────────────────────────────────


class TestTheoryCoherenceResult:
    def test_default_values(self) -> None:
        tc = TheoryCoherenceResult()
        assert tc.aligned is None
        assert tc.supporting_claims == []
        assert tc.contradicting_claims == []
        assert tc.coherence_score == 0.0

    def test_to_dict_contains_all_fields(self) -> None:
        tc = TheoryCoherenceResult(
            aligned=True,
            supporting_claims=["claim_a", "claim_b"],
            contradicting_claims=["claim_c"],
            coherence_score=0.333,
        )
        d = tc.to_dict()
        assert d["aligned"] is True
        assert d["supporting_claims"] == ["claim_a", "claim_b"]
        assert d["contradicting_claims"] == ["claim_c"]
        assert d["coherence_score"] == 0.333

    def test_roundtrip(self) -> None:
        original = TheoryCoherenceResult(
            aligned=True,
            supporting_claims=["a"],
            contradicting_claims=[],
            coherence_score=1.0,
        )
        restored = TheoryCoherenceResult.from_dict(original.to_dict())
        assert restored.aligned == original.aligned
        assert restored.coherence_score == original.coherence_score
        assert restored.supporting_claims == original.supporting_claims
        assert restored.contradicting_claims == original.contradicting_claims

    def test_from_dict_empty_dict_uses_defaults(self) -> None:
        tc = TheoryCoherenceResult.from_dict({})
        assert tc.aligned is None
        assert tc.supporting_claims == []
        assert tc.contradicting_claims == []
        assert tc.coherence_score == 0.0

    def test_from_dict_partial_keys(self) -> None:
        tc = TheoryCoherenceResult.from_dict({"aligned": False, "coherence_score": -0.5})
        assert tc.aligned is False
        assert tc.coherence_score == -0.5
        assert tc.supporting_claims == []

    def test_roundtrip_none_aligned(self) -> None:
        original = TheoryCoherenceResult(aligned=None, coherence_score=0.0)
        restored = TheoryCoherenceResult.from_dict(original.to_dict())
        assert restored.aligned is None

    def test_roundtrip_false_aligned(self) -> None:
        original = TheoryCoherenceResult(
            aligned=False,
            contradicting_claims=["bad"],
            coherence_score=-1.0,
        )
        restored = TheoryCoherenceResult.from_dict(original.to_dict())
        assert restored.aligned is False
        assert restored.coherence_score == -1.0


# ── ProposedConstraint ───────────────────────────────────────────────


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
            pattern_key="ruff|F821",
            proposed_rule="some rule",
            confidence=0.8,
        )
        d = pc.to_dict()
        assert d["theory_coherence"] is None
        assert d["drift_warning"] is False
        assert d["pattern_key"] == "ruff|F821"

    def test_to_dict_with_theory_coherence(self) -> None:
        tc = TheoryCoherenceResult(aligned=True, coherence_score=0.5)
        pc = ProposedConstraint(theory_coherence=tc, drift_warning=True)
        d = pc.to_dict()
        assert d["theory_coherence"]["aligned"] is True
        assert d["drift_warning"] is True

    def test_roundtrip(self) -> None:
        pc = ProposedConstraint(
            source="pattern_bank",
            pattern_key="ruff|F821",
            proposed_rule="# LINTGATE_FORBID_REGEX: ...",
            rule_type="LINTGATE_FORBID_REGEX",
            rationale="ruff/F821 in 5 of last 5 runs",
            confidence=0.85,
            status="proposed",
        )
        data = pc.to_dict()
        restored = ProposedConstraint.from_dict(data)
        assert restored.pattern_key == "ruff|F821"
        assert restored.confidence == 0.85
        assert restored.status == "proposed"
        assert restored.rule_type == "LINTGATE_FORBID_REGEX"
        assert restored.source == "pattern_bank"

    def test_roundtrip_with_theory_coherence(self) -> None:
        tc = TheoryCoherenceResult(
            aligned=False,
            contradicting_claims=["claim_x"],
            coherence_score=-0.5,
        )
        pc = ProposedConstraint(
            pattern_key="mypy|return-value",
            theory_coherence=tc,
            drift_warning=True,
        )
        restored = ProposedConstraint.from_dict(pc.to_dict())
        assert restored.theory_coherence is not None
        assert restored.theory_coherence.aligned is False
        assert restored.theory_coherence.contradicting_claims == ["claim_x"]
        assert restored.drift_warning is True

    def test_from_dict_empty_dict_uses_defaults(self) -> None:
        pc = ProposedConstraint.from_dict({})
        assert pc.source == "pattern_bank"
        assert pc.pattern_key == ""
        assert pc.confidence == 0.0
        assert pc.status == "proposed"
        assert pc.theory_coherence is None

    def test_from_dict_theory_coherence_not_dict_becomes_none(self) -> None:
        """If theory_coherence is not a dict, it should be treated as None."""
        pc = ProposedConstraint.from_dict({"theory_coherence": "not_a_dict"})
        assert pc.theory_coherence is None

    def test_from_dict_theory_coherence_none_stays_none(self) -> None:
        pc = ProposedConstraint.from_dict({"theory_coherence": None})
        assert pc.theory_coherence is None


# ── Proposal Generation ─────────────────────────────────────────────


class TestProposalGeneration:
    def test_recurring_f821_proposes_forbid(self) -> None:
        """Recurring F821 should generate a LINTGATE_FORBID_REGEX proposal."""
        report = _make_pattern_report([_recurring_alert(kind="F821")])
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 1
        assert proposals[0].pattern_key == "ruff|F821"
        assert proposals[0].rule_type == "LINTGATE_FORBID_REGEX"
        assert "FORBID" in proposals[0].proposed_rule
        assert proposals[0].confidence > 0.0
        assert proposals[0].status == "proposed"

    def test_recurring_f401_proposes_theory_note(self) -> None:
        """Recurring F401 should generate a theory note (advisory, not forbid)."""
        report = _make_pattern_report([_recurring_alert(kind="F401")])
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 1
        assert proposals[0].rule_type == "theory_note"
        assert "imports" in proposals[0].proposed_rule.lower()

    def test_recurring_b101_proposes_forbid(self) -> None:
        """Recurring B101 (bandit assert) should generate a FORBID proposal."""
        report = _make_pattern_report([_recurring_alert(linter="bandit", kind="B101")])
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 1
        assert proposals[0].rule_type == "LINTGATE_FORBID_REGEX"
        assert "assert" in proposals[0].proposed_rule.lower()
        assert proposals[0].pattern_key == "bandit|B101"

    def test_recurring_return_value_proposes_require(self) -> None:
        """Recurring return-value should generate a LINTGATE_REQUIRE_REGEX proposal."""
        report = _make_pattern_report([_recurring_alert(linter="mypy", kind="return-value")])
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 1
        assert proposals[0].rule_type == "LINTGATE_REQUIRE_REGEX"
        assert "return type" in proposals[0].proposed_rule.lower()

    def test_recurring_complexity_proposes_theory_note(self) -> None:
        """Recurring complexity should generate a theory note."""
        report = _make_pattern_report([_recurring_alert(linter="radon", kind="complexity")])
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 1
        assert proposals[0].rule_type == "theory_note"
        assert "complexity" in proposals[0].proposed_rule.lower()

    def test_unknown_kind_gets_generic_template(self) -> None:
        """Unknown pattern kinds get a generic theory note."""
        report = _make_pattern_report([_recurring_alert(kind="Z999", linter="unknown")])
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 1
        assert proposals[0].rule_type == "theory_note"
        assert "unknown/Z999" in proposals[0].proposed_rule

    def test_below_threshold_no_proposal(self) -> None:
        """Patterns below the threshold should not generate proposals."""
        report = _make_pattern_report([_recurring_alert(recent_run_count=2)])
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 0

    def test_at_threshold_generates_proposal(self) -> None:
        """Patterns exactly at the threshold should generate proposals."""
        report = _make_pattern_report([_recurring_alert(recent_run_count=5)])
        proposals = propose_constraints_from_patterns(report, threshold=5)

        assert len(proposals) == 1

    def test_single_run_volume_ignored(self) -> None:
        """Single-run volume alerts should not trigger proposals."""
        alert = _recurring_alert()
        alert["alert_reason"] = "single_run_volume"
        report = _make_pattern_report([alert])
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 0

    def test_empty_alerts(self) -> None:
        report = _make_pattern_report([])
        proposals = propose_constraints_from_patterns(report)
        assert proposals == []

    def test_empty_pattern_report(self) -> None:
        """A report with no alerted_patterns key should return empty."""
        proposals = propose_constraints_from_patterns({})
        assert proposals == []

    def test_behavioral_pattern_alert(self) -> None:
        """Behavioral alerts (behavior_channel) should use the behavior map."""
        report = _make_pattern_report(
            [_recurring_alert(linter="behavior_channel", kind="approach_cycling")]
        )
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 1
        assert proposals[0].rule_type == "theory_note"
        assert "constraint" in proposals[0].proposed_rule.lower()

    def test_behavioral_unknown_kind_falls_through_to_generic(self) -> None:
        """Unknown behavior_channel kinds should fall through to generic template."""
        report = _make_pattern_report(
            [_recurring_alert(linter="behavior_channel", kind="unknown_signal")]
        )
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 1
        assert proposals[0].rule_type == "theory_note"
        assert "behavior_channel/unknown_signal" in proposals[0].proposed_rule

    def test_multiple_alerts_generates_multiple_proposals(self) -> None:
        """Multiple different alerts should generate multiple proposals."""
        alerts = [
            _recurring_alert(kind="F821"),
            _recurring_alert(kind="F401"),
            _recurring_alert(linter="radon", kind="complexity"),
        ]
        report = _make_pattern_report(alerts)
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 3
        keys = {p.pattern_key for p in proposals}
        assert keys == {"ruff|F821", "ruff|F401", "radon|complexity"}

    def test_duplicate_alerts_in_single_batch_deduped(self) -> None:
        """Two alerts with the same linter|kind in one batch should produce one proposal."""
        alerts = [
            _recurring_alert(kind="F821"),
            _recurring_alert(kind="F821", recent_run_count=7),
        ]
        report = _make_pattern_report(alerts)
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 1

    def test_rationale_includes_count_and_window(self) -> None:
        """Rationale should include the recurrence count and window."""
        report = _make_pattern_report([_recurring_alert(kind="F821", recent_run_count=7)])
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert "7" in proposals[0].rationale
        assert "5" in proposals[0].rationale

    def test_source_is_pattern_bank(self) -> None:
        """All proposals from this function should have source=pattern_bank."""
        report = _make_pattern_report([_recurring_alert(kind="F821")])
        proposals = propose_constraints_from_patterns(report, threshold=3)
        assert proposals[0].source == "pattern_bank"


# ── Deduplication ────────────────────────────────────────────────────


class TestDeduplication:
    def test_no_duplicate_proposals_in_session(self) -> None:
        """Already-proposed patterns in session should not be re-proposed."""
        session = SessionMemory(project_root="/test")
        session.proposed_constraints.append(
            {
                "pattern_key": "ruff|F821",
                "status": "proposed",
            }
        )

        report = _make_pattern_report([_recurring_alert(kind="F821")])
        proposals = propose_constraints_from_patterns(report, session=session, threshold=3)

        assert len(proposals) == 0

    def test_different_pattern_not_deduped(self) -> None:
        """Different patterns should still generate proposals."""
        session = SessionMemory(project_root="/test")
        session.proposed_constraints.append(
            {
                "pattern_key": "ruff|F821",
                "status": "proposed",
            }
        )

        report = _make_pattern_report([_recurring_alert(kind="F401")])
        proposals = propose_constraints_from_patterns(report, session=session, threshold=3)

        assert len(proposals) == 1
        assert proposals[0].pattern_key == "ruff|F401"

    def test_existing_rule_dedup(self) -> None:
        """Rules that already exist in CLAUDE.md should not be re-proposed."""
        report = _make_pattern_report([_recurring_alert(kind="F821")])

        existing_rules = [
            "# LINTGATE_FORBID_REGEX: undefined name pattern — avoid referencing undefined symbols",
        ]
        proposals = propose_constraints_from_patterns(
            report,
            existing_rules=existing_rules,
            threshold=3,
        )

        assert len(proposals) == 0

    def test_rejected_constraint_still_deduped(self) -> None:
        """A previously rejected constraint should still be deduped from proposals."""
        session = SessionMemory(project_root="/test")
        session.proposed_constraints.append(
            {
                "pattern_key": "ruff|F821",
                "status": "rejected",
            }
        )

        report = _make_pattern_report([_recurring_alert(kind="F821")])
        proposals = propose_constraints_from_patterns(report, session=session, threshold=3)

        assert len(proposals) == 0

    def test_no_session_no_dedup_error(self) -> None:
        """When session is None, dedup should be skipped without error."""
        report = _make_pattern_report([_recurring_alert(kind="F821")])
        proposals = propose_constraints_from_patterns(report, session=None, threshold=3)

        assert len(proposals) == 1


# ── Confidence ───────────────────────────────────────────────────────


class TestConfidence:
    def test_confidence_scales_with_recurrence(self) -> None:
        """More runs with the pattern leads to higher confidence."""
        report_3 = _make_pattern_report([_recurring_alert(kind="F821", recent_run_count=3)])
        report_5 = _make_pattern_report([_recurring_alert(kind="F821", recent_run_count=5)])

        proposals_3 = propose_constraints_from_patterns(report_3, threshold=3)
        proposals_5 = propose_constraints_from_patterns(report_5, threshold=3)

        assert len(proposals_3) == 1
        assert len(proposals_5) == 1
        assert proposals_5[0].confidence >= proposals_3[0].confidence

    def test_confidence_at_threshold_no_bonus(self) -> None:
        """At exactly count=3, recurrence bonus should be 0."""
        report = _make_pattern_report([_recurring_alert(kind="F821", recent_run_count=3)])
        proposals = propose_constraints_from_patterns(report, threshold=3)

        # F821 base_confidence is 0.8; no bonus at count=3
        assert proposals[0].confidence == 0.8

    def test_confidence_capped_at_one(self) -> None:
        """Confidence should never exceed 1.0."""
        report = _make_pattern_report([_recurring_alert(kind="F821", recent_run_count=100)])
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert proposals[0].confidence <= 1.0

    def test_multiple_proposals_sorted_by_confidence(self) -> None:
        """Multiple proposals should be sorted by confidence descending."""
        alerts = [
            _recurring_alert(kind="F821", recent_run_count=5),  # high base_confidence 0.8
            _recurring_alert(
                kind="complexity", linter="radon", recent_run_count=5
            ),  # low base_confidence 0.5
        ]
        report = _make_pattern_report(alerts)
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 2
        assert proposals[0].confidence >= proposals[1].confidence

    def test_low_base_confidence_pattern(self) -> None:
        """Generic unknown patterns have base_confidence=0.4."""
        report = _make_pattern_report(
            [_recurring_alert(kind="XYZ", linter="custom", recent_run_count=3)]
        )
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert proposals[0].confidence == 0.4

    def test_confidence_bonus_increments_with_count(self) -> None:
        """Verify bonus grows with count before the 0.15 cap kicks in."""
        # Use complexity (base=0.5) so the 1.0 cap doesn't interfere
        results = []
        for count in [4, 5]:
            report = _make_pattern_report(
                [_recurring_alert(kind="complexity", linter="radon", recent_run_count=count)]
            )
            proposals = propose_constraints_from_patterns(report, threshold=3)
            results.append(proposals[0].confidence)

        # count=4: bonus=0.075 → 0.575; count=5: bonus=0.15 → 0.65
        assert results[0] < results[1]


# ── Session Integration ──────────────────────────────────────────────


class TestSessionIntegration:
    def test_store_proposals(self) -> None:
        session = SessionMemory(project_root="/test")
        proposals = [
            ProposedConstraint(pattern_key="ruff|F821", proposed_rule="rule1"),
            ProposedConstraint(pattern_key="ruff|F401", proposed_rule="rule2"),
        ]
        store_proposals_in_session(session, proposals)

        assert len(session.proposed_constraints) == 2
        assert session.proposed_constraints[0]["pattern_key"] == "ruff|F821"
        assert session.proposed_constraints[1]["pattern_key"] == "ruff|F401"

    def test_store_empty_proposals(self) -> None:
        """Storing empty proposals should not modify session."""
        session = SessionMemory(project_root="/test")
        store_proposals_in_session(session, [])
        assert len(session.proposed_constraints) == 0

    def test_store_idempotent(self) -> None:
        """Storing the same proposal twice should not duplicate."""
        session = SessionMemory(project_root="/test")
        proposals = [ProposedConstraint(pattern_key="ruff|F821", proposed_rule="rule1")]

        store_proposals_in_session(session, proposals)
        store_proposals_in_session(session, proposals)

        assert len(session.proposed_constraints) == 1

    def test_store_mixed_new_and_existing(self) -> None:
        """Storing a mix of new and existing proposals should only add new ones."""
        session = SessionMemory(project_root="/test")
        session.proposed_constraints.append({"pattern_key": "ruff|F821"})

        proposals = [
            ProposedConstraint(pattern_key="ruff|F821", proposed_rule="rule1"),
            ProposedConstraint(pattern_key="ruff|F401", proposed_rule="rule2"),
        ]
        store_proposals_in_session(session, proposals)

        assert len(session.proposed_constraints) == 2
        keys = {c["pattern_key"] for c in session.proposed_constraints}
        assert keys == {"ruff|F821", "ruff|F401"}

    def test_update_status_accepted(self) -> None:
        session = SessionMemory(project_root="/test")
        session.proposed_constraints.append(
            {
                "pattern_key": "ruff|F821",
                "status": "proposed",
            }
        )

        result = update_constraint_status(session, "ruff|F821", "accepted")
        assert result is True
        assert session.proposed_constraints[0]["status"] == "accepted"

    def test_update_status_rejected(self) -> None:
        session = SessionMemory(project_root="/test")
        session.proposed_constraints.append(
            {
                "pattern_key": "ruff|F401",
                "status": "proposed",
            }
        )

        result = update_constraint_status(session, "ruff|F401", "rejected")
        assert result is True
        assert session.proposed_constraints[0]["status"] == "rejected"

    def test_update_status_not_found(self) -> None:
        session = SessionMemory(project_root="/test")
        result = update_constraint_status(session, "ruff|NOPE", "accepted")
        assert result is False

    def test_update_status_empty_constraints(self) -> None:
        """Updating with empty proposed_constraints should return False."""
        session = SessionMemory(project_root="/test")
        result = update_constraint_status(session, "any|key", "accepted")
        assert result is False

    def test_update_status_finds_correct_among_multiple(self) -> None:
        """With multiple constraints, the correct one should be updated."""
        session = SessionMemory(project_root="/test")
        session.proposed_constraints.extend(
            [
                {"pattern_key": "ruff|F821", "status": "proposed"},
                {"pattern_key": "ruff|F401", "status": "proposed"},
                {"pattern_key": "mypy|return-value", "status": "proposed"},
            ]
        )

        result = update_constraint_status(session, "ruff|F401", "accepted")
        assert result is True
        assert session.proposed_constraints[0]["status"] == "proposed"
        assert session.proposed_constraints[1]["status"] == "accepted"
        assert session.proposed_constraints[2]["status"] == "proposed"


# ── Theory Coherence ─────────────────────────────────────────────────


class TestCheckTheoryCoherence:
    def test_none_profile_returns_none(self) -> None:
        """check_theory_coherence with None profile should return None."""
        proposal = ProposedConstraint(proposed_rule="avoid complexity")
        result = check_theory_coherence(proposal, None)
        assert result is None

    def test_empty_profile_returns_none(self) -> None:
        """check_theory_coherence with empty dict profile should return None."""
        proposal = ProposedConstraint(proposed_rule="avoid complexity")
        result = check_theory_coherence(proposal, {})
        assert result is None

    def test_proposal_with_no_keywords_returns_none(self) -> None:
        """If no meaningful keywords can be extracted, return None."""
        proposal = ProposedConstraint(proposed_rule="", rationale="")
        result = check_theory_coherence(proposal, {"facets": {}})
        assert result is None

    @patch("lintgate.theory_extractor.get_theory_context_from_profile")
    def test_no_claims_returns_aligned_none(self, mock_get: MagicMock) -> None:
        """If theory profile returns no claims, aligned should be None."""
        mock_get.return_value = {"claims": []}
        proposal = ProposedConstraint(
            proposed_rule="avoid complexity patterns",
            rationale="appeared often",
        )
        result = check_theory_coherence(proposal, {"facets": {}})
        assert result is not None
        assert result.aligned is None
        assert result.coherence_score == 0.0

    @patch("lintgate.theory_extractor.get_theory_context_from_profile")
    def test_all_supporting_claims(self, mock_get: MagicMock) -> None:
        """All claims supporting should yield positive score and aligned=True."""
        mock_get.return_value = {
            "claims": [
                {"claim": "prefer smaller composable functions always"},
            ]
        }
        proposal = ProposedConstraint(
            proposed_rule="prefer smaller functions always",
            rationale="complexity appeared often",
        )
        result = check_theory_coherence(proposal, {"facets": {}})
        assert result is not None
        assert result.aligned is True
        assert result.coherence_score > 0

    @patch("lintgate.theory_extractor.get_theory_context_from_profile")
    def test_all_contradicting_claims(self, mock_get: MagicMock) -> None:
        """All claims contradicting should yield negative score and aligned=False."""
        mock_get.return_value = {
            "claims": [
                {"claim": "prefer complexity patterns always"},
            ]
        }
        proposal = ProposedConstraint(
            proposed_rule="avoid complexity patterns",
            rationale="issues appeared often",
        )
        result = check_theory_coherence(proposal, {"facets": {}})
        assert result is not None
        assert result.aligned is False
        assert result.coherence_score < 0

    @patch("lintgate.theory_extractor.get_theory_context_from_profile")
    def test_mixed_claims_score(self, mock_get: MagicMock) -> None:
        """Mix of supporting and contradicting claims should produce intermediate score."""
        mock_get.return_value = {
            "claims": [
                {"claim": "prefer smaller functions always"},
                {"claim": "prefer complexity patterns always"},
            ]
        }
        proposal = ProposedConstraint(
            proposed_rule="avoid complexity patterns",
            rationale="issues appeared often",
        )
        result = check_theory_coherence(proposal, {"facets": {}})
        assert result is not None
        # Score should be between -1 and 1
        assert -1.0 <= result.coherence_score <= 1.0

    @patch("lintgate.theory_extractor.get_theory_context_from_profile")
    def test_claims_without_overlap_are_supporting(self, mock_get: MagicMock) -> None:
        """Claims without noun overlap are treated as non-contradicting (supporting)."""
        mock_get.return_value = {
            "claims": [
                {"claim": "database connections should pool"},
            ]
        }
        proposal = ProposedConstraint(
            proposed_rule="avoid complexity patterns",
            rationale="issues appeared often",
        )
        result = check_theory_coherence(proposal, {"facets": {}})
        assert result is not None
        # No overlap → not contradicting → supporting
        assert result.coherence_score >= 0


# ── Apply Coherence Check ────────────────────────────────────────────


class TestApplyCoherenceCheck:
    def test_config_none_skips(self) -> None:
        """When config is None, coherence check should be skipped."""
        proposal = ProposedConstraint(proposed_rule="something")
        session = SessionMemory()
        session.theory_profile_cache = {"facets": {}}

        # Should not raise, and proposal should be unchanged
        from lintgate.controlplane.constraint_proposer import _apply_coherence_check

        _apply_coherence_check(proposal, None, session)
        assert proposal.theory_coherence is None

    def test_config_disabled_skips(self) -> None:
        """When theory_coherence_check is False, coherence check should be skipped."""
        from lintgate.controlplane.constraint_proposer import _apply_coherence_check

        config = ControlPlaneConfig(inquiry=InquiryConfig(theory_coherence_check=False))
        proposal = ProposedConstraint(proposed_rule="something")
        session = SessionMemory()
        session.theory_profile_cache = {"facets": {}}

        _apply_coherence_check(proposal, config, session)
        assert proposal.theory_coherence is None

    def test_session_none_skips(self) -> None:
        """When session is None, coherence check should be skipped."""
        from lintgate.controlplane.constraint_proposer import _apply_coherence_check

        config = ControlPlaneConfig(inquiry=InquiryConfig(theory_coherence_check=True))
        proposal = ProposedConstraint(proposed_rule="something")

        _apply_coherence_check(proposal, config, None)
        assert proposal.theory_coherence is None

    def test_no_theory_profile_cache_skips(self) -> None:
        """When session has no theory_profile_cache, coherence check should be skipped."""
        from lintgate.controlplane.constraint_proposer import _apply_coherence_check

        config = ControlPlaneConfig(inquiry=InquiryConfig(theory_coherence_check=True))
        proposal = ProposedConstraint(proposed_rule="something")
        session = SessionMemory()
        session.theory_profile_cache = None

        _apply_coherence_check(proposal, config, session)
        assert proposal.theory_coherence is None

    @patch("lintgate.controlplane.constraint_proposer.check_theory_coherence")
    def test_applies_coherence_and_sets_drift_warning(self, mock_check: MagicMock) -> None:
        from lintgate.controlplane.constraint_proposer import _apply_coherence_check

        mock_check.return_value = TheoryCoherenceResult(
            aligned=False,
            contradicting_claims=["contradicts something"],
            coherence_score=-0.5,
        )
        config = ControlPlaneConfig(inquiry=InquiryConfig(theory_coherence_check=True))
        session = SessionMemory()
        session.theory_profile_cache = {"facets": {"core_theory": {}}}
        proposal = ProposedConstraint(proposed_rule="avoid something")
        _apply_coherence_check(proposal, config, session)
        assert proposal.theory_coherence is not None
        assert proposal.drift_warning is True

    @patch("lintgate.controlplane.constraint_proposer.check_theory_coherence")
    def test_no_contradictions_no_drift_warning(self, mock_check: MagicMock) -> None:
        from lintgate.controlplane.constraint_proposer import _apply_coherence_check

        mock_check.return_value = TheoryCoherenceResult(
            aligned=True,
            supporting_claims=["supports something"],
            contradicting_claims=[],
            coherence_score=1.0,
        )
        config = ControlPlaneConfig(inquiry=InquiryConfig(theory_coherence_check=True))
        session = SessionMemory()
        session.theory_profile_cache = {"facets": {}}
        proposal = ProposedConstraint(proposed_rule="prefer something")
        _apply_coherence_check(proposal, config, session)
        assert proposal.theory_coherence is not None
        assert proposal.drift_warning is False

    @patch("lintgate.controlplane.constraint_proposer.check_theory_coherence")
    def test_coherence_returns_none_leaves_proposal_unchanged(self, mock_check: MagicMock) -> None:
        from lintgate.controlplane.constraint_proposer import _apply_coherence_check

        mock_check.return_value = None
        config = ControlPlaneConfig(inquiry=InquiryConfig(theory_coherence_check=True))
        session = SessionMemory()
        session.theory_profile_cache = {"facets": {}}
        proposal = ProposedConstraint(proposed_rule="test something")
        _apply_coherence_check(proposal, config, session)
        assert proposal.theory_coherence is None
        assert proposal.drift_warning is False


# ── Keyword Extraction ───────────────────────────────────────────────


class TestExtractCoherenceKeywords:
    def test_extracts_4plus_char_words(self) -> None:
        from lintgate.controlplane.constraint_proposer import _extract_coherence_keywords

        result = _extract_coherence_keywords("The quick brown fox jumps")
        assert "quick" in result
        assert "brown" in result
        assert "jumps" in result
        assert "fox" not in result  # only 3 chars
        assert "The" not in result  # only 3 chars

    def test_excludes_stopwords(self) -> None:
        from lintgate.controlplane.constraint_proposer import _extract_coherence_keywords

        result = _extract_coherence_keywords("this should have been detected")
        assert "this" not in result
        assert "should" not in result
        assert "have" not in result
        assert "been" not in result
        assert "detected" not in result  # 'detected' is in stopwords

    def test_empty_string(self) -> None:
        from lintgate.controlplane.constraint_proposer import _extract_coherence_keywords

        result = _extract_coherence_keywords("")
        assert result == []

    def test_only_short_words(self) -> None:
        from lintgate.controlplane.constraint_proposer import _extract_coherence_keywords

        result = _extract_coherence_keywords("the fox ran up")
        assert result == []

    def test_mixed_case(self) -> None:
        from lintgate.controlplane.constraint_proposer import _extract_coherence_keywords

        result = _extract_coherence_keywords("Complexity PATTERNS detected")
        assert "complexity" in result
        assert "patterns" in result
        # 'detected' is in stopwords
        assert "detected" not in result

    def test_special_characters_ignored(self) -> None:
        from lintgate.controlplane.constraint_proposer import _extract_coherence_keywords

        result = _extract_coherence_keywords("avoid (complexity) patterns: [always]")
        assert "complexity" in result
        assert "patterns" in result
        assert "always" in result


# ── Is Contradicting ─────────────────────────────────────────────────


class TestIsContradicting:
    def test_opposite_polarity_with_overlap_returns_true(self) -> None:
        from lintgate.controlplane.constraint_proposer import _is_contradicting

        assert (
            _is_contradicting(
                "avoid complexity patterns",
                "prefer complexity patterns always",
            )
            is True
        )

    def test_same_polarity_with_overlap_returns_false(self) -> None:
        from lintgate.controlplane.constraint_proposer import _is_contradicting

        assert (
            _is_contradicting(
                "prefer complexity patterns always",
                "use complexity patterns always",
            )
            is False
        )

    def test_no_overlap_returns_false(self) -> None:
        from lintgate.controlplane.constraint_proposer import _is_contradicting

        assert (
            _is_contradicting(
                "avoid complexity patterns",
                "prefer database connections always",
            )
            is False
        )

    def test_empty_strings_returns_false(self) -> None:
        from lintgate.controlplane.constraint_proposer import _is_contradicting

        assert _is_contradicting("", "") is False

    def test_neutral_polarity_returns_false(self) -> None:
        """If one text has neutral polarity (no polarity words), return False."""
        from lintgate.controlplane.constraint_proposer import _is_contradicting

        assert (
            _is_contradicting(
                "complexity patterns observed",
                "avoid complexity patterns",
            )
            is False
        )

    def test_both_negative_returns_false(self) -> None:
        from lintgate.controlplane.constraint_proposer import _is_contradicting

        assert (
            _is_contradicting(
                "never allow complexity patterns",
                "avoid complexity patterns",
            )
            is False
        )

    def test_both_positive_returns_false(self) -> None:
        from lintgate.controlplane.constraint_proposer import _is_contradicting

        assert (
            _is_contradicting(
                "always prefer complexity patterns",
                "use complexity patterns",
            )
            is False
        )


# ── Resolve Constraint Template ──────────────────────────────────────


class TestResolveConstraintTemplate:
    def test_behavior_channel_maps_to_behavior_map(self) -> None:
        from lintgate.controlplane.constraint_proposer import (
            _BEHAVIOR_CONSTRAINT_MAP,
            _resolve_constraint_template,
        )

        result = _resolve_constraint_template("behavior_channel", "approach_cycling")
        assert result == _BEHAVIOR_CONSTRAINT_MAP["approach_cycling"]

    def test_behavior_channel_unknown_kind_falls_through(self) -> None:
        """Unknown behavior_channel kinds should fall through to generic template."""
        from lintgate.controlplane.constraint_proposer import _resolve_constraint_template

        result = _resolve_constraint_template("behavior_channel", "unknown_kind")
        assert result["rule_type"] == "theory_note"
        assert "behavior_channel/unknown_kind" in result["template"]

    def test_pattern_map_lookup(self) -> None:
        from lintgate.controlplane.constraint_proposer import (
            _PATTERN_CONSTRAINT_MAP,
            _resolve_constraint_template,
        )

        result = _resolve_constraint_template("ruff", "F821")
        assert result == _PATTERN_CONSTRAINT_MAP["F821"]

    def test_generic_fallback(self) -> None:
        from lintgate.controlplane.constraint_proposer import _resolve_constraint_template

        result = _resolve_constraint_template("custom_linter", "CUSTOM001")
        assert result["rule_type"] == "theory_note"
        assert "custom_linter/CUSTOM001" in result["template"]
        assert result["base_confidence"] == 0.4

    def test_all_behavior_keys_resolve(self) -> None:
        """All keys in _BEHAVIOR_CONSTRAINT_MAP should resolve correctly."""
        from lintgate.controlplane.constraint_proposer import (
            _BEHAVIOR_CONSTRAINT_MAP,
            _resolve_constraint_template,
        )

        for kind in _BEHAVIOR_CONSTRAINT_MAP:
            result = _resolve_constraint_template("behavior_channel", kind)
            assert result == _BEHAVIOR_CONSTRAINT_MAP[kind]

    def test_all_pattern_keys_resolve(self) -> None:
        """All keys in _PATTERN_CONSTRAINT_MAP should resolve correctly."""
        from lintgate.controlplane.constraint_proposer import (
            _PATTERN_CONSTRAINT_MAP,
            _resolve_constraint_template,
        )

        for kind in _PATTERN_CONSTRAINT_MAP:
            result = _resolve_constraint_template("some_linter", kind)
            assert result == _PATTERN_CONSTRAINT_MAP[kind]


# ── Compute Proposal Confidence ──────────────────────────────────────


class TestComputeProposalConfidence:
    def test_no_bonus_at_or_below_3(self) -> None:
        from lintgate.controlplane.constraint_proposer import _compute_proposal_confidence

        template = {"base_confidence": 0.5}
        assert _compute_proposal_confidence(template, 3) == 0.5
        assert _compute_proposal_confidence(template, 2) == 0.5
        assert _compute_proposal_confidence(template, 1) == 0.5

    def test_above_threshold_adds_bonus(self) -> None:
        from lintgate.controlplane.constraint_proposer import _compute_proposal_confidence

        template = {"base_confidence": 0.8}
        result = _compute_proposal_confidence(template, 4)
        assert result > 0.8

    def test_bonus_capped_at_0_15(self) -> None:
        """Recurrence bonus should not exceed 0.15."""
        from lintgate.controlplane.constraint_proposer import _compute_proposal_confidence

        template = {"base_confidence": 0.5}
        result = _compute_proposal_confidence(template, 100)
        # base + max_bonus = 0.5 + 0.15 = 0.65
        assert result == 0.65

    def test_total_capped_at_1_0(self) -> None:
        from lintgate.controlplane.constraint_proposer import _compute_proposal_confidence

        template = {"base_confidence": 0.95}
        result = _compute_proposal_confidence(template, 100)
        assert result == 1.0

    def test_missing_base_confidence_defaults_to_0_5(self) -> None:
        from lintgate.controlplane.constraint_proposer import _compute_proposal_confidence

        template: dict = {}
        result = _compute_proposal_confidence(template, 3)
        assert result == 0.5

    def test_returns_float(self) -> None:
        from lintgate.controlplane.constraint_proposer import _compute_proposal_confidence

        template = {"base_confidence": 0.7}
        result = _compute_proposal_confidence(template, 5)
        assert isinstance(result, float)


# ── Module Constants ─────────────────────────────────────────────────


class TestModuleConstants:
    def test_polarity_sets_are_disjoint(self) -> None:
        from lintgate.controlplane.constraint_proposer import (
            _NEGATIVE_POLARITY,
            _POSITIVE_POLARITY,
        )

        overlap = _POSITIVE_POLARITY & _NEGATIVE_POLARITY
        assert overlap == set()

    def test_pattern_constraint_map_has_required_keys(self) -> None:
        """Every template in _PATTERN_CONSTRAINT_MAP should have required fields."""
        from lintgate.controlplane.constraint_proposer import _PATTERN_CONSTRAINT_MAP

        for kind, template in _PATTERN_CONSTRAINT_MAP.items():
            assert "rule_type" in template, f"Missing rule_type in {kind}"
            assert "template" in template, f"Missing template in {kind}"
            assert "rationale_template" in template, f"Missing rationale_template in {kind}"
            assert "base_confidence" in template, f"Missing base_confidence in {kind}"

    def test_behavior_constraint_map_has_required_keys(self) -> None:
        """Every template in _BEHAVIOR_CONSTRAINT_MAP should have required fields."""
        from lintgate.controlplane.constraint_proposer import _BEHAVIOR_CONSTRAINT_MAP

        for kind, template in _BEHAVIOR_CONSTRAINT_MAP.items():
            assert "rule_type" in template, f"Missing rule_type in {kind}"
            assert "template" in template, f"Missing template in {kind}"
            assert "rationale_template" in template, f"Missing rationale_template in {kind}"
            assert "base_confidence" in template, f"Missing base_confidence in {kind}"

    def test_all_rationale_templates_have_placeholders(self) -> None:
        """All rationale_template strings should have {count} and {window} placeholders."""
        from lintgate.controlplane.constraint_proposer import (
            _BEHAVIOR_CONSTRAINT_MAP,
            _PATTERN_CONSTRAINT_MAP,
        )

        for kind, template in {**_PATTERN_CONSTRAINT_MAP, **_BEHAVIOR_CONSTRAINT_MAP}.items():
            rt = template["rationale_template"]
            # Verify the template can be formatted without error
            formatted = rt.format(count=5, window=5)
            assert "5" in formatted, f"Rationale template for {kind} missing placeholders"


# ── Integration: Coherence with Proposal Pipeline ────────────────────


class TestCoherenceIntegration:
    @patch("lintgate.controlplane.constraint_proposer.check_theory_coherence")
    def test_proposal_with_coherence_enabled(self, mock_check: MagicMock) -> None:
        """When coherence check is enabled, proposals should have theory_coherence."""
        mock_check.return_value = TheoryCoherenceResult(
            aligned=True,
            supporting_claims=["claim1"],
            coherence_score=1.0,
        )
        config = ControlPlaneConfig(inquiry=InquiryConfig(theory_coherence_check=True))
        session = SessionMemory(project_root="/test")
        session.theory_profile_cache = {"facets": {"core_theory": {}}}

        report = _make_pattern_report([_recurring_alert(kind="F821")])
        proposals = propose_constraints_from_patterns(
            report, session=session, threshold=3, config=config
        )

        assert len(proposals) == 1
        assert proposals[0].theory_coherence is not None
        assert proposals[0].theory_coherence.aligned is True

    def test_proposal_without_coherence_config(self) -> None:
        """Without coherence config, proposals should have no theory_coherence."""
        report = _make_pattern_report([_recurring_alert(kind="F821")])
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 1
        assert proposals[0].theory_coherence is None
        assert proposals[0].drift_warning is False


# ── Build Generic Template ───────────────────────────────────────────


class TestBuildGenericTemplate:
    def test_generic_template_structure(self) -> None:
        from lintgate.controlplane.constraint_proposer import _build_generic_template

        result = _build_generic_template("custom", "CODE001")
        assert result["rule_type"] == "theory_note"
        assert "custom/CODE001" in result["template"]
        assert result["base_confidence"] == 0.4
        # Verify rationale template has placeholders
        formatted = result["rationale_template"].format(count=3, window=5)
        assert "custom/CODE001" in formatted
        assert "3" in formatted
        assert "5" in formatted

    def test_generic_template_different_linters(self) -> None:
        from lintgate.controlplane.constraint_proposer import _build_generic_template

        result_a = _build_generic_template("linter_a", "X1")
        result_b = _build_generic_template("linter_b", "X2")
        assert "linter_a/X1" in result_a["template"]
        assert "linter_b/X2" in result_b["template"]
        assert result_a["template"] != result_b["template"]
