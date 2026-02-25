"""Compliance Tracking — monitor how agents respond to behavioral nudges.

Tracks 'accepted', 'ignored', and 'overridden' nudge outcomes and computes
a rolling compliance rate used for authority escalation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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


class ComplianceManager:
    """Manages compliance state and calculates modulation terms for escalation."""

    def __init__(self, session_memory: dict[str, Any]):
        self.session_memory = session_memory
        if "compliance_stats" not in self.session_memory:
            self.session_memory["compliance_stats"] = {
                "accepted_count": 0,
                "ignored_count": 0,
                "overridden_count": 0,
                "total_nudges": 0,
            }

    def record_outcomes(self, outcomes: dict[str, str]):
        """Record nudge outcomes and update aggregate stats."""
        stats = self.session_memory["compliance_stats"]
        for _signal, outcome in outcomes.items():
            stats["total_nudges"] += 1
            if outcome == "accepted":
                stats["accepted_count"] += 1
            elif outcome == "ignored":
                stats["ignored_count"] += 1
            elif outcome == "overridden":
                stats["overridden_count"] += 1

        # Update rolling compliance rate in compass for use by escalation engine
        if "behavior_compass" in self.session_memory:
            bc = self.session_memory["behavior_compass"]
            total = stats["total_nudges"]
            rate = stats["accepted_count"] / total if total > 0 else 1.0
            bc["compliance_rate"] = round(rate, 2)

    def get_compliance_rate(self) -> float:
        stats = self.session_memory["compliance_stats"]
        total = stats["total_nudges"]
        return stats["accepted_count"] / total if total > 0 else 1.0
