"""Controller-level issue classification for surviving findings.

Classifies the aggregate outcome of a controlplane_execute run,
not individual findings. Per-finding classification requires stronger
evidence linking from channel outputs (follow-up work).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class SurvivingIssueClass(str, Enum):
    """Classification for issues that survive the full orchestration pipeline."""

    POLICY_VIOLATION = "policy_violation"
    SPEC_GAP = "spec_gap"
    EQUIVALENT_SURVIVOR = "equivalent_survivor"
    TOOLING_GAP = "tooling_gap"
    ADVISORY_ONLY = "advisory_only"


@dataclass
class ClassifiedIssue:
    """A finding with its survival classification."""

    fingerprint: str
    classification: SurvivingIssueClass
    evidence_ref: str = ""
    theory_claim: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "fingerprint": self.fingerprint,
            "classification": self.classification.value,
        }
        if self.evidence_ref:
            d["evidence_ref"] = self.evidence_ref
        if self.theory_claim:
            d["theory_claim"] = self.theory_claim
        return d


def classify_controller_outcomes(
    terminal_state: str,
    action_counts: dict[str, int],
    file_outcomes: list[dict[str, Any]],
    theory_claims: list[dict[str, Any]] | None = None,
) -> list[ClassifiedIssue]:
    """Classify the aggregate outcome of a controlplane_execute run.

    This is controller-level classification — it classifies WHY the
    orchestration stopped where it did, not individual code findings.
    """
    classified: list[ClassifiedIssue] = []
    theory_claims = theory_claims or []

    if terminal_state == "COMPLETE":
        return classified

    if terminal_state == "TOOL_FAILURE":
        failures = [f for f in file_outcomes if f.get("state") in ("FAILED", "TOOL_FAILURE")]
        for f in failures:
            classified.append(ClassifiedIssue(
                fingerprint=f"tool_failure:{f.get('file', 'unknown')}",
                classification=SurvivingIssueClass.TOOLING_GAP,
                evidence_ref=f.get("error", ""),
            ))
        return classified

    if terminal_state == "NEEDS_ORACLE":
        oracle_files = [f for f in file_outcomes if f.get("state") == "NEEDS_ORACLE"]
        for f in oracle_files:
            classified.append(ClassifiedIssue(
                fingerprint=f"oracle_needed:{f.get('file', 'unknown')}",
                classification=SurvivingIssueClass.SPEC_GAP,
                evidence_ref=f.get("workflow_id", ""),
            ))
        return classified

    if terminal_state == "NEEDS_DECOMPOSITION":
        decompose_files = [f for f in file_outcomes if f.get("state") == "NEEDS_DECOMPOSITION"]
        for f in decompose_files:
            classified.append(ClassifiedIssue(
                fingerprint=f"decompose_needed:{f.get('file', 'unknown')}",
                classification=SurvivingIssueClass.SPEC_GAP,
                evidence_ref=f.get("workflow_id", ""),
            ))
        return classified

    if terminal_state == "ADVISORY_ONLY":
        classified.append(ClassifiedIssue(
            fingerprint="advisory_aggregate",
            classification=SurvivingIssueClass.ADVISORY_ONLY,
        ))
        return classified

    if terminal_state in ("BLOCKED_BY_VERIFIER", "BLOCKED_BY_ENVIRONMENT"):
        blocked_files = [
            f for f in file_outcomes
            if f.get("state") in ("BLOCKED_DISCOVERY", "BLOCKED_TOPOLOGY")
        ]
        for f in blocked_files:
            classified.append(ClassifiedIssue(
                fingerprint=f"blocked:{f.get('file', 'unknown')}",
                classification=SurvivingIssueClass.TOOLING_GAP,
                evidence_ref=f.get("workflow_id", ""),
            ))
        return classified

    if terminal_state == "READY_FOR_REVIEW":
        review_files = [
            f for f in file_outcomes
            if f.get("state") in ("READY_TO_APPLY_WITH_REVIEW", "READY_FOR_REVIEW")
        ]
        for f in review_files:
            # Check theory claims for policy violations
            claim_match = _match_theory_claim(f.get("file", ""), theory_claims)
            if claim_match:
                classified.append(ClassifiedIssue(
                    fingerprint=f"review_needed:{f.get('file', 'unknown')}",
                    classification=SurvivingIssueClass.POLICY_VIOLATION,
                    theory_claim=claim_match,
                ))
            else:
                classified.append(ClassifiedIssue(
                    fingerprint=f"review_needed:{f.get('file', 'unknown')}",
                    classification=SurvivingIssueClass.SPEC_GAP,
                    evidence_ref=f.get("workflow_id", ""),
                ))
        return classified

    return classified


def _match_theory_claim(file: str, claims: list[dict[str, Any]]) -> str:
    """Find a theory claim that matches the given file."""
    for claim in claims:
        scope = claim.get("scope", "")
        if scope and file and scope in file:
            return claim.get("claim", "")
    return ""
