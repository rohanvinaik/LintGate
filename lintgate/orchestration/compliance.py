"""Compliance Tracking — monitor how agents respond to behavioral nudges.

Tracks 'accepted', 'ignored', and 'overridden' nudge outcomes and computes
a rolling compliance rate used for authority escalation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ComplianceOutcome(str, Enum):
    """Outcomes of a behavioral nudge."""

    FOLLOWED = "followed"  # Agent took the recommended action
    IGNORED = "ignored"  # Agent ignored the nudge and continued as before
    OVERRIDDEN = "overridden"  # Agent explicitly rejected or bypassed the nudge
    UNCERTAIN = "uncertain"  # Insufficient evidence to classify


@dataclass
class ComplianceStats:
    """Aggregate compliance statistics."""

    accepted_count: int = 0
    ignored_count: int = 0
    overridden_count: int = 0
    total_nudges: int = 0

    @property
    def compliance_rate(self) -> float:
        if self.total_nudges == 0:
            return 1.0
        return self.accepted_count / self.total_nudges


class ComplianceClassifier:
    """Classifies agent actions relative to behavioral nudges."""

    def classify(
        self,
        event: dict[str, Any],
        last_disposition: str | None,
        last_nudge: dict[str, Any] | None,
    ) -> ComplianceOutcome:
        """Determine if the current event complies with the last nudge."""
        if not last_disposition and not last_nudge:
            return ComplianceOutcome.UNCERTAIN

        tool = event.get("tool", "")

        # Rule 1: Edit without lint nudge
        if last_disposition and "edit without lint" in last_disposition.lower():
            if tool == "Bash":
                cmd = str(event.get("input", {}).get("command", "")).lower()
                if any(x in cmd for x in ["ruff", "mypy", "lint", "pytest"]):
                    return ComplianceOutcome.FOLLOWED
            if tool == "Edit":
                return ComplianceOutcome.IGNORED

        # Rule 2: Bash without prediction nudge
        if last_disposition and "bash without prediction" in last_disposition.lower():
            if tool == "Bash":
                # If they are running bash again without using constraint_check
                return ComplianceOutcome.IGNORED
            if tool == "constraint_check":
                return ComplianceOutcome.FOLLOWED

        return ComplianceOutcome.UNCERTAIN


class ComplianceManager:
    """Manages compliance state and calculates modulation terms for escalation."""

    def __init__(self, session_memory: dict[str, Any]):
        self.session_memory = session_memory
        self.classifier = ComplianceClassifier()
        if "compliance_stats" not in self.session_memory:
            self.session_memory["compliance_stats"] = {
                "accepted_count": 0,
                "ignored_count": 0,
                "overridden_count": 0,
                "total_nudges": 0,
            }

    def evaluate_and_record(
        self,
        event: dict[str, Any],
        last_disposition: str | None = None,
        last_nudge: dict[str, Any] | None = None,
    ) -> ComplianceOutcome:
        """Classify current action vs last nudge and record result."""
        outcome = self.classifier.classify(event, last_disposition, last_nudge)

        if outcome != ComplianceOutcome.UNCERTAIN:
            self.record_outcomes({"current_nudge": outcome.value})

        return outcome

    def record_outcomes(self, outcomes: dict[str, str]):
        """Record nudge outcomes and update aggregate stats."""
        stats = self.session_memory["compliance_stats"]
        for _signal, outcome in outcomes.items():
            stats["total_nudges"] += 1
            if outcome == "followed" or outcome == "accepted":
                stats["accepted_count"] += 1
            elif outcome == "ignored":
                stats["ignored_count"] += 1
            elif outcome == "overridden":
                stats["overridden_count"] += 1

        # Update rolling compliance rate in compass for use by escalation engine
        if "behavior_compass" in self.session_memory:
            bc = self.session_memory["behavior_compass"]
            total = stats["total_nudges"]
            # Compass usually expects a dict, but some implementations use a key on the object
            # We'll set it in both common places if possible, but here we update session state
            rate = stats["accepted_count"] / total if total > 0 else 1.0
            bc["compliance_rate"] = round(rate, 2)

    def get_compliance_rate(self) -> float:
        stats = self.session_memory["compliance_stats"]
        total = stats["total_nudges"]
        return stats["accepted_count"] / total if total > 0 else 1.0
