"""Tests for controller-level issue classification (M6)."""

from __future__ import annotations

from lintgate.controlplane.issue_classification import (
    ClassifiedIssue,
    SurvivingIssueClass,
    classify_controller_outcomes,
)


class TestClassifyControllerOutcomes:
    def test_complete_returns_empty(self):
        result = classify_controller_outcomes("COMPLETE", {}, [])
        assert result == []

    def test_tool_failure(self):
        outcomes = [{"file": "src/bad.py", "state": "TOOL_FAILURE", "error": "parse error"}]
        result = classify_controller_outcomes("TOOL_FAILURE", {}, outcomes)
        assert len(result) == 1
        assert result[0].classification == SurvivingIssueClass.TOOLING_GAP
        assert "bad.py" in result[0].fingerprint

    def test_needs_oracle(self):
        outcomes = [
            {"file": "src/a.py", "state": "NEEDS_ORACLE", "workflow_id": "wf1"},
            {"file": "src/b.py", "state": "CONVERGED"},
        ]
        result = classify_controller_outcomes("NEEDS_ORACLE", {}, outcomes)
        assert len(result) == 1
        assert result[0].classification == SurvivingIssueClass.SPEC_GAP
        assert result[0].evidence_ref == "wf1"

    def test_needs_decomposition(self):
        outcomes = [{"file": "src/big.py", "state": "NEEDS_DECOMPOSITION", "workflow_id": "wf2"}]
        result = classify_controller_outcomes("NEEDS_DECOMPOSITION", {}, outcomes)
        assert len(result) == 1
        assert result[0].classification == SurvivingIssueClass.SPEC_GAP

    def test_advisory_only(self):
        result = classify_controller_outcomes("ADVISORY_ONLY", {}, [])
        assert len(result) == 1
        assert result[0].classification == SurvivingIssueClass.ADVISORY_ONLY

    def test_blocked_by_verifier(self):
        outcomes = [{"file": "src/x.py", "state": "BLOCKED_TOPOLOGY", "workflow_id": "wf3"}]
        result = classify_controller_outcomes("BLOCKED_BY_VERIFIER", {}, outcomes)
        assert len(result) == 1
        assert result[0].classification == SurvivingIssueClass.TOOLING_GAP

    def test_ready_for_review_no_theory(self):
        outcomes = [{"file": "src/r.py", "state": "READY_TO_APPLY_WITH_REVIEW", "workflow_id": "wf4"}]
        result = classify_controller_outcomes("READY_FOR_REVIEW", {}, outcomes)
        assert len(result) == 1
        assert result[0].classification == SurvivingIssueClass.SPEC_GAP

    def test_ready_for_review_with_theory_match(self):
        outcomes = [{"file": "src/r.py", "state": "READY_TO_APPLY_WITH_REVIEW"}]
        claims = [{"scope": "src/r.py", "claim": "must not have side effects"}]
        result = classify_controller_outcomes("READY_FOR_REVIEW", {}, outcomes, theory_claims=claims)
        assert len(result) == 1
        assert result[0].classification == SurvivingIssueClass.POLICY_VIOLATION
        assert result[0].theory_claim == "must not have side effects"


class TestClassifiedIssue:
    def test_to_dict_minimal(self):
        ci = ClassifiedIssue(
            fingerprint="test:fp",
            classification=SurvivingIssueClass.SPEC_GAP,
        )
        d = ci.to_dict()
        assert d["fingerprint"] == "test:fp"
        assert d["classification"] == "spec_gap"
        assert "evidence_ref" not in d
        assert "theory_claim" not in d

    def test_to_dict_full(self):
        ci = ClassifiedIssue(
            fingerprint="test:fp",
            classification=SurvivingIssueClass.POLICY_VIOLATION,
            evidence_ref="/path/to/evidence.json",
            theory_claim="must be pure",
        )
        d = ci.to_dict()
        assert d["evidence_ref"] == "/path/to/evidence.json"
        assert d["theory_claim"] == "must be pure"


class TestSurvivingIssueClassEnum:
    def test_all_values(self):
        expected = {"policy_violation", "spec_gap", "equivalent_survivor", "tooling_gap", "advisory_only"}
        assert {e.value for e in SurvivingIssueClass} == expected

    def test_str_enum(self):
        assert SurvivingIssueClass.SPEC_GAP.value == "spec_gap"
        assert "SPEC_GAP" in str(SurvivingIssueClass.SPEC_GAP)
