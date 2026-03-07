"""Risk-based prioritization model for specification analysis.

Computes risk scores and priority bands (P0/P1/P2) for functions
based on purity, fan-in/out, API surface, testability, and regime.
"""

from __future__ import annotations

from .types import RiskProfile


def compute_risk_score(
    is_pure: bool,
    fan_in: int,
    fan_out: int,
    is_public: bool,
    testability_score: float,
    regime: str,
) -> RiskProfile:
    """Compute risk score and priority band for a function.

    Args:
        is_pure: Whether the function is pure.
        fan_in: Number of callers (from call graph).
        fan_out: Number of callees.
        is_public: Whether function is part of API surface.
        testability_score: DFT score (0.0-1.0).
        regime: Specification regime ("A", "B", or "unknown").

    Returns:
        RiskProfile with score, band, and human-readable factors.
    """
    score = 0.0
    factors: list[str] = []

    if not is_pure:
        score += 0.3
        factors.append("impure")

    if fan_in >= 5:
        score += 0.2
        factors.append(f"high fan-in ({fan_in})")

    if fan_out >= 5:
        score += 0.1
        factors.append(f"high fan-out ({fan_out})")

    if is_public:
        score += 0.2
        factors.append("public API")

    if testability_score < 0.5:
        score += 0.1
        factors.append(f"low testability ({testability_score:.2f})")

    if regime == "B":
        score += 0.1
        factors.append("Regime B (exponential)")

    score = min(score, 1.0)

    if score >= 0.7:
        band = "P0"
    elif score >= 0.4:
        band = "P1"
    else:
        band = "P2"

    return RiskProfile(risk_score=score, priority_band=band, risk_factors=factors)
