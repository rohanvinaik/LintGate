"""Tests for the constraint proposer — pattern bank → theory feedback loop."""

from __future__ import annotations

from lintgate.controlplane.constraint_proposer import (
    ProposedConstraint,
    propose_constraints_from_patterns,
    store_proposals_in_session,
    update_constraint_status,
)
from lintgate.controlplane.session_memory import SessionMemory

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


# ── Proposal Generation ─────────────────────────────────────────────


class TestProposalGeneration:
    def test_recurring_f821_proposes_forbid(self):
        """Recurring F821 should generate a LINTGATE_FORBID_REGEX proposal."""
        report = _make_pattern_report([_recurring_alert(kind="F821")])
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 1
        assert proposals[0].pattern_key == "ruff|F821"
        assert proposals[0].rule_type == "LINTGATE_FORBID_REGEX"
        assert "FORBID" in proposals[0].proposed_rule
        assert proposals[0].confidence > 0.0
        assert proposals[0].status == "proposed"

    def test_recurring_f401_proposes_theory_note(self):
        """Recurring F401 should generate a theory note (advisory, not forbid)."""
        report = _make_pattern_report([_recurring_alert(kind="F401")])
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 1
        assert proposals[0].rule_type == "theory_note"
        assert "imports" in proposals[0].proposed_rule.lower()

    def test_unknown_kind_gets_generic_template(self):
        """Unknown pattern kinds get a generic theory note."""
        report = _make_pattern_report([_recurring_alert(kind="Z999", linter="unknown")])
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 1
        assert proposals[0].rule_type == "theory_note"
        assert "unknown/Z999" in proposals[0].proposed_rule

    def test_below_threshold_no_proposal(self):
        """Patterns below the threshold should not generate proposals."""
        report = _make_pattern_report([_recurring_alert(recent_run_count=2)])
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 0

    def test_single_run_volume_ignored(self):
        """Single-run volume alerts should not trigger proposals."""
        alert = _recurring_alert()
        alert["alert_reason"] = "single_run_volume"
        report = _make_pattern_report([alert])
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 0

    def test_empty_alerts(self):
        report = _make_pattern_report([])
        proposals = propose_constraints_from_patterns(report)
        assert proposals == []


class TestDeduplication:
    def test_no_duplicate_proposals_in_session(self):
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

    def test_different_pattern_not_deduped(self):
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

    def test_existing_rule_dedup(self):
        """Rules that already exist in CLAUDE.md should not be re-proposed."""
        report = _make_pattern_report([_recurring_alert(kind="F821")])

        # The exact template text for F821
        existing_rules = [
            "# LINTGATE_FORBID_REGEX: undefined name pattern — avoid referencing undefined symbols",
        ]
        proposals = propose_constraints_from_patterns(
            report,
            existing_rules=existing_rules,
            threshold=3,
        )

        assert len(proposals) == 0


class TestConfidence:
    def test_confidence_scales_with_recurrence(self):
        """More runs with the pattern → higher confidence."""
        report_3 = _make_pattern_report([_recurring_alert(kind="F821", recent_run_count=3)])
        report_5 = _make_pattern_report([_recurring_alert(kind="F821", recent_run_count=5)])

        proposals_3 = propose_constraints_from_patterns(report_3, threshold=3)
        proposals_5 = propose_constraints_from_patterns(report_5, threshold=3)

        assert len(proposals_3) == 1
        assert len(proposals_5) == 1
        assert proposals_5[0].confidence >= proposals_3[0].confidence

    def test_confidence_capped_at_one(self):
        """Confidence should never exceed 1.0."""
        report = _make_pattern_report([_recurring_alert(kind="F821", recent_run_count=100)])
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert proposals[0].confidence <= 1.0

    def test_multiple_proposals_sorted_by_confidence(self):
        """Multiple proposals should be sorted by confidence descending."""
        alerts = [
            _recurring_alert(kind="F821", recent_run_count=5),  # high base_confidence
            _recurring_alert(
                kind="complexity", linter="radon", recent_run_count=5
            ),  # low base_confidence
        ]
        report = _make_pattern_report(alerts)
        proposals = propose_constraints_from_patterns(report, threshold=3)

        assert len(proposals) == 2
        assert proposals[0].confidence >= proposals[1].confidence


# ── Session Integration ──────────────────────────────────────────────


class TestSessionIntegration:
    def test_store_proposals(self):
        session = SessionMemory(project_root="/test")
        proposals = [
            ProposedConstraint(pattern_key="ruff|F821", proposed_rule="rule1"),
            ProposedConstraint(pattern_key="ruff|F401", proposed_rule="rule2"),
        ]
        store_proposals_in_session(session, proposals)

        assert len(session.proposed_constraints) == 2
        assert session.proposed_constraints[0]["pattern_key"] == "ruff|F821"

    def test_store_idempotent(self):
        """Storing the same proposal twice should not duplicate."""
        session = SessionMemory(project_root="/test")
        proposals = [ProposedConstraint(pattern_key="ruff|F821", proposed_rule="rule1")]

        store_proposals_in_session(session, proposals)
        store_proposals_in_session(session, proposals)

        assert len(session.proposed_constraints) == 1

    def test_update_status_accepted(self):
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

    def test_update_status_not_found(self):
        session = SessionMemory(project_root="/test")
        result = update_constraint_status(session, "ruff|NOPE", "accepted")
        assert result is False


# ── Serialization ────────────────────────────────────────────────────


class TestSerialization:
    def test_roundtrip(self):
        pc = ProposedConstraint(
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
