"""Authority Level Escalation Engine — modulate how behavioral findings are presented.

Findings start as 'advisory' or 'nudge' and escalate based on recurrence,
significance, model risk, and compliance history.
"""

from __future__ import annotations

from enum import Enum


class AuthorityLevel(str, Enum):
    """Authority levels for behavioral findings.

    Determines how the finding is framed and delivered.
    """

    ADVISORY = "advisory"  # FYI, low confidence or non-critical
    NUDGE = "nudge"  # Reminder, moderate confidence or recurring
    BLOCKING = "blocking"  # Must fix, high confidence or critical
    INTERVENTION = "intervention"  # Immediate stop, extremely critical

    def __lt__(self, other: AuthorityLevel) -> bool:
        hierarchy = [self.ADVISORY, self.NUDGE, self.BLOCKING, self.INTERVENTION]
        return hierarchy.index(self) < hierarchy.index(other)


class AuthorityEscalationEngine:
    """Calculates authority levels based on finding profile and history."""

    def __init__(
        self,
        base_thresholds: dict[str, float] | None = None,
        significance_weight: float = 0.5,
        recurrence_weight: float = 0.3,
        risk_weight: float = 0.2,
    ):
        self.thresholds = base_thresholds or {
            "nudge": 0.4,
            "blocking": 0.7,
            "intervention": 0.9,
        }
        self.significance_weight = significance_weight
        self.recurrence_weight = recurrence_weight
        self.risk_weight = risk_weight

    def calculate_authority(
        self,
        significance: float,
        recurrence_count: int = 0,
        model_risk: str = "moderate",
        compliance_rate: float = 1.0,
    ) -> AuthorityLevel:
        """Calculate the authority level for a finding.

        Args:
            significance: Base finding significance [0-1].
            recurrence_count: Times this finding has occurred in this session.
            model_risk: Model risk level (none, cosmetic, moderate, structural, architectural).
            compliance_rate: Cumulative agent compliance rate [0-1].

        Returns:
            Calculated AuthorityLevel.
        """
        # 1. Normalize recurrence (log scale, cap at 5)
        import math

        rec_score = min(1.0, math.log(recurrence_count + 1, 6)) if recurrence_count > 0 else 0.0

        # 2. Map model risk
        risk_map = {
            "none": 0.0,
            "cosmetic": 0.2,
            "moderate": 0.4,
            "structural": 0.7,
            "architectural": 1.0,
        }
        risk_score = risk_map.get(model_risk.lower(), 0.4)

        # 3. Aggregate base score
        score = (
            significance * self.significance_weight
            + rec_score * self.recurrence_weight
            + risk_score * self.risk_weight
        )

        # 4. Modulate by compliance (low compliance accelerates escalation)
        if compliance_rate < 0.5:
            score *= 1.5 - compliance_rate

        # 5. Map to level
        if score >= self.thresholds["intervention"]:
            return AuthorityLevel.INTERVENTION
        if score >= self.thresholds["blocking"]:
            return AuthorityLevel.BLOCKING
        if score >= self.thresholds["nudge"]:
            return AuthorityLevel.NUDGE

        return AuthorityLevel.ADVISORY

    def get_escalation_reason(
        self,
        level: AuthorityLevel,
        significance: float,
        recurrence: int,
        compliance: float,
    ) -> str:
        """Provide human-readable reasoning for the authority level."""
        if level == AuthorityLevel.INTERVENTION:
            return "Critical recursive failure or extreme risk detected."
        if level == AuthorityLevel.BLOCKING:
            if recurrence >= 3:
                return f"Escalated due to high recurrence ({recurrence}x) and failed resolution."
            return "Escalated due to high significance and system risk."
        if level == AuthorityLevel.NUDGE:
            if compliance < 0.6:
                return "Elevated to nudge due to low compliance rate."
            return "Standard behavioral reminder."
        return "Advisory behavioral context."
