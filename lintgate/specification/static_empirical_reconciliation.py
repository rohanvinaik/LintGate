"""Static/empirical reconciliation — overlay empirical mutation data onto static spec estimates.

Compares symbolic sigma/regime/phase predictions from the static analyzer
against actual mutation profiling results when available. Produces a per-function
empirical overlay that makes the relationship explicit rather than silently
collapsing discovery failures into low spec coverage.

The overlay is additive — it annotates the static estimate, it does not replace it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OverlayStatus(str, Enum):
    """Classification of static-vs-empirical agreement."""

    NO_EMPIRICAL_DATA = "NO_EMPIRICAL_DATA"
    AGREES = "AGREES"
    CONTRADICTS = "CONTRADICTS"
    DISCOVERY_FAILURE = "DISCOVERY_FAILURE"
    TOPOLOGY_LIMITED = "TOPOLOGY_LIMITED"


@dataclass
class EmpiricalOverlay:
    """Empirical overlay for a single function's spec estimate."""

    status: OverlayStatus = OverlayStatus.NO_EMPIRICAL_DATA
    mutation_runs_seen: int = 0
    empirical_sigma_upper_bound: int = 0
    empirical_survival_rate: float = 0.0
    empirical_tail: bool = False
    overlay_confidence: float = 0.0
    overlay_rationale: str = ""

    def to_dict(self) -> dict:
        d: dict = {
            "status": self.status.value,
            "overlay_confidence": round(self.overlay_confidence, 2),
            "overlay_rationale": self.overlay_rationale,
        }
        if self.mutation_runs_seen > 0:
            d["mutation_runs_seen"] = self.mutation_runs_seen
            d["empirical_sigma_upper_bound"] = self.empirical_sigma_upper_bound
            d["empirical_survival_rate"] = round(self.empirical_survival_rate, 3)
        if self.empirical_tail:
            d["empirical_tail"] = True
        return d


def build_overlay(
    function_key: str,
    static_sigma: int,
    static_regime: str,
    static_phase: str,
    mutation_cache: dict[str, dict] | None,
) -> EmpiricalOverlay:
    """Build an empirical overlay for a function from cached mutation data.

    Args:
        function_key: Canonical function key (e.g., "module.py::func").
        static_sigma: Static sigma estimate from AST/predictor.
        static_regime: Static regime classification (A/B/unknown).
        static_phase: Static phase classification (bulk/transition/tail/complete).
        mutation_cache: Cached mutation results keyed by function_key, or None.

    Returns:
        EmpiricalOverlay with reconciliation status and rationale.
    """
    if not mutation_cache or function_key not in mutation_cache:
        return EmpiricalOverlay(
            status=OverlayStatus.NO_EMPIRICAL_DATA,
            overlay_rationale="No mutation profiling data available for this function.",
        )

    entry = mutation_cache[function_key]
    return _reconcile(static_sigma, static_regime, static_phase, entry)


def _reconcile(
    static_sigma: int,
    static_regime: str,
    static_phase: str,
    entry: dict,
) -> EmpiricalOverlay:
    """Reconcile a static estimate against one cached mutation entry."""
    discovery_state = entry.get("discovery_state", "")
    topology_state = entry.get("topology_state", "")
    survival_interpretation = entry.get("survival_interpretation", "")

    total_mutants = entry.get("total_mutants", 0)
    survival_rate = entry.get("survival_rate", 0.0)

    # Discovery failure — empirical data exists but is unreliable
    if discovery_state in (
        "NO_TEST_FILES",
        "TEST_FILES_FOUND_NONE_LINKED",
        "DISCOVERY_IMPORT_FAILED",
    ):
        return EmpiricalOverlay(
            status=OverlayStatus.DISCOVERY_FAILURE,
            mutation_runs_seen=1,
            empirical_sigma_upper_bound=total_mutants,
            empirical_survival_rate=survival_rate,
            overlay_confidence=0.2,
            overlay_rationale=(
                f"Discovery failed ({discovery_state}). "
                "Mutation results do not reflect true specification state."
            ),
        )

    # Topology limited — mocks dominate, survival is artifact
    if (
        topology_state == "MOCK_BOUNDARY_DOMINANT"
        or survival_interpretation == "MOCK_BOUNDARY_ARTIFACT"
    ):
        return EmpiricalOverlay(
            status=OverlayStatus.TOPOLOGY_LIMITED,
            mutation_runs_seen=1,
            empirical_sigma_upper_bound=total_mutants,
            empirical_survival_rate=survival_rate,
            overlay_confidence=0.3,
            overlay_rationale=(
                "Test topology is mock-boundary dominant. "
                "Mutation survival rate may not reflect true specification gaps."
            ),
        )

    # We have meaningful empirical data — compare against static
    empirical_tail = _detect_empirical_tail(entry)

    agrees, rationale = _check_agreement(
        static_sigma,
        static_regime,
        static_phase,
        total_mutants,
        survival_rate,
        empirical_tail,
    )

    confidence = _compute_overlay_confidence(
        topology_state,
        survival_interpretation,
        total_mutants,
    )

    status = OverlayStatus.AGREES if agrees else OverlayStatus.CONTRADICTS

    return EmpiricalOverlay(
        status=status,
        mutation_runs_seen=1,
        empirical_sigma_upper_bound=total_mutants,
        empirical_survival_rate=survival_rate,
        empirical_tail=empirical_tail,
        overlay_confidence=confidence,
        overlay_rationale=rationale,
    )


def _detect_empirical_tail(entry: dict) -> bool:
    """Detect whether the function is in an empirical tail phase.

    A tail phase means tests are producing diminishing returns —
    the convergence curve has flattened.
    """
    trajectory = entry.get("trajectory", {})
    if isinstance(trajectory, dict):
        phase = trajectory.get("phase", "")
        if phase == "tail":
            return True
        tail_onset = trajectory.get("tail_onset_step")
        if tail_onset is not None:
            return True
    return False


def _check_agreement(
    static_sigma: int,
    static_regime: str,
    static_phase: str,
    total_mutants: int,
    survival_rate: float,
    empirical_tail: bool,
) -> tuple[bool, str]:
    """Check whether static and empirical views agree.

    Returns (agrees, rationale).
    """
    parts: list[str] = []
    contradictions: list[str] = []

    # Sigma comparison: static sigma vs empirical mutant count
    # They measure different things but should be in the same order of magnitude
    if static_sigma > 0 and total_mutants > 0:
        ratio = total_mutants / static_sigma
        if 0.3 <= ratio <= 3.0:
            parts.append(
                f"Sigma consistent: static={static_sigma}, empirical mutants={total_mutants}."
            )
        else:
            contradictions.append(
                f"Sigma divergence: static={static_sigma} vs "
                f"empirical mutants={total_mutants} (ratio={ratio:.1f}x)."
            )

    # Phase comparison
    empirical_phase = _infer_empirical_phase(survival_rate, empirical_tail)
    if static_phase == empirical_phase:
        parts.append(f"Phase agrees: {static_phase}.")
    elif _phases_compatible(static_phase, empirical_phase):
        parts.append(f"Phase compatible: static={static_phase}, empirical={empirical_phase}.")
    else:
        contradictions.append(
            f"Phase mismatch: static={static_phase}, empirical={empirical_phase}."
        )

    # Regime vs empirical tractability
    if static_regime == "A" and survival_rate > 0.7:
        contradictions.append(
            f"Regime conflict: static says tractable (A) but survival rate is {survival_rate:.0%}."
        )
    elif static_regime == "B" and survival_rate < 0.1:
        parts.append("Regime-B function is well-specified empirically.")

    agrees = len(contradictions) == 0
    if agrees:
        rationale = " ".join(parts) if parts else "Static and empirical estimates are consistent."
    else:
        rationale = " ".join(contradictions + parts)

    return agrees, rationale


def _infer_empirical_phase(survival_rate: float, empirical_tail: bool) -> str:
    """Infer empirical phase from survival rate and tail detection."""
    if survival_rate < 0.01:
        return "complete"
    if empirical_tail:
        return "tail"
    if survival_rate < 0.3:
        return "transition"
    return "bulk"


def _phases_compatible(phase_a: str, phase_b: str) -> bool:
    """Check if two phases are adjacent (compatible but not identical)."""
    order = ["bulk", "transition", "tail", "complete"]
    try:
        idx_a = order.index(phase_a)
        idx_b = order.index(phase_b)
        return abs(idx_a - idx_b) <= 1
    except ValueError:
        return False


def _compute_overlay_confidence(
    topology_state: str,
    survival_interpretation: str,
    total_mutants: int,
) -> float:
    """Compute confidence in the overlay based on empirical data quality."""
    confidence = 0.8

    # More mutants → more confidence in empirical data
    if total_mutants < 5:
        confidence -= 0.2
    elif total_mutants >= 20:
        confidence += 0.1

    # Topology adjustments
    if topology_state == "PATCHED_INTERNAL_CALLS":
        confidence -= 0.15

    # Low-confidence survival interpretation
    if survival_interpretation == "LOW_CONFIDENCE":
        confidence -= 0.2

    return max(0.1, min(1.0, confidence))
