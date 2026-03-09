"""Signal Attribution — decompose behavioral signals into constituent sources.

Provides deterministic attribution for WHY a behavioral signal was triggered,
breaking it down into pattern, theory, outcome, and coherence sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SignalSourceDecomposition:
    """Decomposition of a behavioral signal into its constituent sources."""

    signal_name: str
    pattern_score: float = 0.0  # From hard-coded pattern rules (e.g. repeated sigs)
    theory_score: float = 0.0  # From project theory profile matching
    outcome_score: float = 0.0  # From actual result failures/successes
    coherence_score: float = 0.0  # From cross-channel correlation
    sources: list[str] = field(default_factory=list)

    @property
    def total_confidence(self) -> float:
        """Compute aggregate confidence from sources."""
        # Baseline weights
        weights = {"pattern": 0.4, "theory": 0.2, "outcome": 0.3, "coherence": 0.1}
        return min(
            1.0,
            (
                self.pattern_score * weights["pattern"]
                + self.theory_score * weights["theory"]
                + self.outcome_score * weights["outcome"]
                + self.coherence_score * weights["coherence"]
            )
            / 0.5,
        )  # Scale to 0-1, as weights sum to 1.0 but typically only some are high

    def to_summary(self) -> str:
        """Human-readable attribution summary."""
        parts = []
        if self.pattern_score > 0.5:
            parts.append("pattern match")
        if self.theory_score > 0.5:
            parts.append("theory alignment")
        if self.outcome_score > 0.5:
            parts.append("outcome evidence")
        if self.coherence_score > 0.5:
            parts.append("cross-channel coherence")

        return f"Triggered by: {', '.join(parts)}" if parts else "Triggered by mixed signals"
