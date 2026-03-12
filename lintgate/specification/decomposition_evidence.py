"""Decomposition evidence — multi-lens agreement for extraction recommendations.

Requires cross-lens agreement before recommending extraction. Mutation
survival alone (2+ surviving categories) is necessary but not sufficient.
The recommendation strengthens only when specification, structure, and
coupling signals agree.

Recommendation outcomes:
- KEEP_TESTING: mutation gaps exist but other lenses don't support decomposition
- EXTRACT_BOUNDARY: multiple independent lenses agree on extraction
- INSUFFICIENT_EVIDENCE: not enough data to make a recommendation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DecompositionRecommendation(str, Enum):
    """Cross-lens decomposition recommendation."""

    KEEP_TESTING = "KEEP_TESTING"
    EXTRACT_BOUNDARY = "EXTRACT_BOUNDARY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass
class LensSignal:
    """Evidence from a single analysis lens."""

    lens: str
    confidence: float
    detail: str


@dataclass
class DecompositionVerdict:
    """Multi-lens decomposition verdict for a single function."""

    function_key: str = ""
    recommendation: DecompositionRecommendation = DecompositionRecommendation.INSUFFICIENT_EVIDENCE
    supporting_lenses: list[LensSignal] = field(default_factory=list)
    cross_lens_score: float = 0.0
    responsibility_boundary: str = ""
    expected_benefits: list[str] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "recommendation": self.recommendation.value,
            "cross_lens_score": round(self.cross_lens_score, 2),
            "supporting_lenses": [
                {"lens": s.lens, "confidence": round(s.confidence, 2), "detail": s.detail}
                for s in self.supporting_lenses
            ],
            "rationale": self.rationale,
        }
        if self.responsibility_boundary:
            d["responsibility_boundary"] = self.responsibility_boundary
        if self.expected_benefits:
            d["expected_benefits"] = self.expected_benefits
        return d


def evaluate_decomposition(
    function_key: str,
    surviving_categories: list[str],
    mutation_cache_entry: dict | None = None,
    spec_data: dict | None = None,
    composition_gamma: float | None = None,
    topology_state: str = "",
) -> DecompositionVerdict:
    """Evaluate decomposition evidence from multiple lenses.

    Args:
        function_key: Canonical function key.
        surviving_categories: Mutation categories with survivors.
        mutation_cache_entry: Cached mutation profiling data.
        spec_data: Specification analysis data (from spec_file_analyze).
        composition_gamma: Composition gap gamma value, if available.
        topology_state: Test topology state (NORMAL, MOCK_BOUNDARY_DOMINANT, etc.).
    """
    signals: list[LensSignal] = []

    # Lens 1: Mutation diversity
    mutation_signal = _evaluate_mutation_lens(surviving_categories, mutation_cache_entry)
    if mutation_signal:
        signals.append(mutation_signal)

    # Lens 2: Specification complexity
    spec_signal = _evaluate_specification_lens(spec_data)
    if spec_signal:
        signals.append(spec_signal)

    # Lens 3: Composition gap
    composition_signal = _evaluate_composition_lens(composition_gamma)
    if composition_signal:
        signals.append(composition_signal)

    # Topology adjustment — mock-dominant topology weakens all evidence
    if topology_state == "MOCK_BOUNDARY_DOMINANT":
        return DecompositionVerdict(
            function_key=function_key,
            recommendation=DecompositionRecommendation.KEEP_TESTING,
            supporting_lenses=signals,
            cross_lens_score=0.0,
            rationale=(
                "Test topology is mock-boundary dominant. "
                "Decomposition evidence is unreliable under current test topology. "
                "Improve test coverage before reconsidering extraction."
            ),
        )

    # Multi-lens agreement check
    return _synthesize_verdict(function_key, signals, surviving_categories)


def _evaluate_mutation_lens(
    surviving_categories: list[str],
    cache_entry: dict | None,
) -> LensSignal | None:
    """Mutation lens: diverse surviving categories suggest entangled responsibilities."""
    if len(surviving_categories) < 2:
        return None

    confidence = min(0.5 + 0.1 * len(surviving_categories), 0.8)

    # Boost if survival is genuinely high
    if cache_entry:
        survival_rate = cache_entry.get("survival_rate", 0.0)
        if survival_rate > 0.5:
            confidence = min(confidence + 0.1, 0.9)

    return LensSignal(
        lens="mutation",
        confidence=confidence,
        detail=f"{len(surviving_categories)} surviving categories: {', '.join(surviving_categories)}",
    )


def _evaluate_specification_lens(spec_data: dict | None) -> LensSignal | None:
    """Specification lens: high sigma + regime B suggests structural complexity."""
    if not spec_data:
        return None

    sigma = spec_data.get("sigma", 0)
    regime = spec_data.get("regime", "")
    spec_level = spec_data.get("specification_level", 0.0)

    # Regime B with low spec_level is a signal for decomposition
    if regime == "B" and spec_level < 0.5:
        return LensSignal(
            lens="specification",
            confidence=0.6,
            detail=f"Regime B, spec_level={spec_level:.2f}, sigma={sigma}",
        )

    # High sigma alone is weaker evidence
    if sigma >= 15 and spec_level < 0.3:
        return LensSignal(
            lens="specification",
            confidence=0.5,
            detail=f"High sigma={sigma} with low spec_level={spec_level:.2f}",
        )

    return None


def _evaluate_composition_lens(gamma: float | None) -> LensSignal | None:
    """Composition lens: high gamma indicates interface complexity."""
    if gamma is None:
        return None

    if gamma >= 3.0:
        return LensSignal(
            lens="composition_gap",
            confidence=min(0.5 + gamma * 0.05, 0.85),
            detail=f"Composition gap gamma={gamma:.1f}",
        )

    return None


def _synthesize_verdict(
    function_key: str,
    signals: list[LensSignal],
    surviving_categories: list[str],
) -> DecompositionVerdict:
    """Synthesize a decomposition verdict from collected lens signals."""
    if not signals:
        return DecompositionVerdict(
            function_key=function_key,
            recommendation=DecompositionRecommendation.INSUFFICIENT_EVIDENCE,
            rationale="No lens signals available to support decomposition.",
        )

    # Compute cross-lens score via probability union
    product = 1.0
    for s in signals:
        product *= 1.0 - s.confidence
    cross_lens_score = 1.0 - product

    lens_count = len(signals)

    # Require at least 2 independent lenses for EXTRACT_BOUNDARY
    if lens_count >= 2 and cross_lens_score >= 0.7:
        lens_names = [s.lens for s in signals]
        benefits = _infer_benefits(surviving_categories, signals)
        boundary = _infer_boundary(surviving_categories)
        return DecompositionVerdict(
            function_key=function_key,
            recommendation=DecompositionRecommendation.EXTRACT_BOUNDARY,
            supporting_lenses=signals,
            cross_lens_score=cross_lens_score,
            responsibility_boundary=boundary,
            expected_benefits=benefits,
            rationale=(
                f"{lens_count} independent lenses agree (score={cross_lens_score:.2f}): "
                f"{', '.join(lens_names)}. "
                "Extraction along the responsibility boundary is recommended."
            ),
        )

    # Single lens or weak agreement — keep testing
    if lens_count == 1 or cross_lens_score < 0.5:
        return DecompositionVerdict(
            function_key=function_key,
            recommendation=DecompositionRecommendation.KEEP_TESTING,
            supporting_lenses=signals,
            cross_lens_score=cross_lens_score,
            rationale=(
                f"Only {lens_count} lens(es) support decomposition "
                f"(score={cross_lens_score:.2f}). "
                "Continue testing to strengthen evidence before extracting."
            ),
        )

    # Between: 2 lenses but moderate score
    return DecompositionVerdict(
        function_key=function_key,
        recommendation=DecompositionRecommendation.KEEP_TESTING,
        supporting_lenses=signals,
        cross_lens_score=cross_lens_score,
        rationale=(
            f"{lens_count} lenses with moderate agreement "
            f"(score={cross_lens_score:.2f}). "
            "Evidence is not yet strong enough for extraction."
        ),
    )


def _infer_benefits(
    surviving_categories: list[str],
    signals: list[LensSignal],
) -> list[str]:
    """Infer expected benefits of extraction."""
    benefits: list[str] = []
    lens_names = {s.lens for s in signals}

    if "mutation" in lens_names:
        benefits.append("better_local_testability")
    if "specification" in lens_names:
        benefits.append("reduced_coupling")
    if "composition_gap" in lens_names:
        benefits.append("cleaner_interface")
    if len(surviving_categories) >= 3:
        benefits.append("easier_repair")
    return benefits


def _infer_boundary(surviving_categories: list[str]) -> str:
    """Infer a responsibility boundary description from surviving categories."""
    if "VALUE" in surviving_categories and "STATE" in surviving_categories:
        return "Separate pure computation from stateful operations"
    if "SWAP" in surviving_categories and "BOUNDARY" in surviving_categories:
        return "Separate parameter handling from boundary validation"
    if len(surviving_categories) >= 3:
        return "Function has multiple entangled responsibilities"
    return "Consider extracting along the dominant mutation category boundary"
