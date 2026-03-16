"""PrescriptiveSpec composer — builds specs from theory + compass.

Includes claim projection (target-scoped filtering) and the
PrescriptiveSpecComposer class.
"""

from __future__ import annotations

import re
from typing import Any

from .predicates import (
    Predicate,
    PredicateOp,
    compile_claim,
)
from .types import (
    ForbiddenBehavior,
    Invariant,
)

_STOPWORDS = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "shall",
    "should",
    "may",
    "might",
    "must",
    "can",
    "could",
    "to",
    "of",
    "in",
    "for",
    "on",
    "with",
    "at",
    "by",
    "from",
    "as",
    "into",
    "through",
    "during",
    "before",
    "after",
    "above",
    "below",
    "between",
    "out",
    "off",
    "over",
    "under",
    "again",
    "further",
    "then",
    "once",
    "and",
    "but",
    "or",
    "nor",
    "not",
    "no",
    "so",
    "if",
    "it",
    "its",
    "this",
    "that",
    "these",
    "those",
    "each",
    "every",
    "all",
    "any",
    "both",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "only",
    "own",
    "same",
    "than",
    "too",
    "very",
}

_CAUSAL_MARKERS = re.compile(
    r"\b(because|therefore|since|thus|consequently|as a result|"
    r"this means|which causes|in order to|so that)\b",
    re.IGNORECASE,
)
_CONTRASTIVE_MARKERS = re.compile(
    r"\b(however|instead|rather than|unlike|but|although|"
    r"despite|nevertheless|on the other hand)\b",
    re.IGNORECASE,
)

# ── Target-scoped claim projection ──────────────────────────────────


def project_claims(
    target_key: str,
    compass: Any,
    theory_profile: dict[str, Any],
    interface_hint: dict[str, Any] | None = None,
    func_spec: Any | None = None,
) -> tuple[list[Invariant], list[ForbiddenBehavior], list[dict]]:
    """Project compass + theory claims onto a specific target.

    Returns (applicable_invariants, applicable_forbidden, projection_log).

    Projection logic:
    1. Claims whose text mentions the target function name → high confidence
    2. Claims whose compiled predicate op matches target context
       (e.g., PURE claim + pure function → relevant) → medium
    3. Generic project-level claims → low confidence (demoted by 0.15)
    4. Claims contradicted by func_spec evidence → rejected with log entry
    """
    func_name = target_key.split("::")[-1] if "::" in target_key else target_key
    projection_log: list[dict] = []
    invariants: list[Invariant] = []
    forbidden: list[ForbiddenBehavior] = []

    # Determine target context for relevance matching
    is_pure = False
    if func_spec is not None:
        is_pure = getattr(getattr(func_spec, "core", None), "is_pure", False)
    if interface_hint and interface_hint.get("problem_class") == "pure":
        is_pure = True

    # ── Compass directives ────────────────────────────────────────
    if hasattr(compass, "directives"):
        for i, directive in enumerate(getattr(compass, "directives", [])):
            text = getattr(directive, "text", "")
            kind = getattr(directive, "kind", "")
            confidence, relevance = _score_claim_relevance(text, func_name, is_pure)

            if kind == "toward":
                invariants.append(
                    Invariant(
                        name=f"toward_{i}",
                        predicate=compile_claim(text),
                        description=text,
                        source=f"compass:toward:{i}",
                        confidence=confidence,
                        kind="alignment",
                    )
                )
            elif kind in ("forbidden", "away"):
                severity = "hard" if kind == "forbidden" else "soft"
                forbidden.append(
                    ForbiddenBehavior(
                        predicate=compile_claim(text),
                        description=text,
                        source=f"compass:{kind}:{i}",
                        severity=severity,
                    )
                )

            projection_log.append(
                {
                    "source": f"compass:{kind}:{i}",
                    "text": text[:80],
                    "relevance": relevance,
                    "confidence": round(confidence, 3),
                    "action": "included",
                }
            )

    # ── Theory claims ─────────────────────────────────────────────
    for facet_name, facet_data in theory_profile.items():
        if not isinstance(facet_data, dict):
            continue
        claims = facet_data.get("claims", [])
        for k, claim in enumerate(claims):
            text = claim.get("text", "") if isinstance(claim, dict) else str(claim)
            base_conf = claim.get("confidence", 0.7) if isinstance(claim, dict) else 0.7

            if base_conf < 0.6:
                projection_log.append(
                    {
                        "source": f"theory:{facet_name}:{k}",
                        "text": text[:80],
                        "relevance": "rejected",
                        "confidence": round(base_conf, 3),
                        "action": "rejected_low_confidence",
                    }
                )
                continue

            # Check for contradiction with func_spec evidence
            pred = compile_claim(text)
            if _claim_contradicted_by_spec(pred, func_spec):
                projection_log.append(
                    {
                        "source": f"theory:{facet_name}:{k}",
                        "text": text[:80],
                        "relevance": "contradicted",
                        "confidence": round(base_conf, 3),
                        "action": "rejected_contradicted",
                    }
                )
                continue

            confidence, relevance = _score_claim_relevance(text, func_name, is_pure)
            # Apply base confidence as floor
            confidence = max(confidence, base_conf)
            # Boost for causal/contrastive markers
            if _CAUSAL_MARKERS.search(text) or _CONTRASTIVE_MARKERS.search(text):
                confidence = min(1.0, confidence + 0.1)

            inv_kind = "safety"
            if facet_name in ("alignment", "core_theory"):
                inv_kind = "alignment"

            invariants.append(
                Invariant(
                    name=f"theory_{facet_name}_{k}",
                    predicate=pred,
                    description=text,
                    source=f"theory:{facet_name}:{k}",
                    confidence=confidence,
                    kind=inv_kind,
                )
            )
            projection_log.append(
                {
                    "source": f"theory:{facet_name}:{k}",
                    "text": text[:80],
                    "relevance": relevance,
                    "confidence": round(confidence, 3),
                    "action": "included",
                }
            )

    return invariants, forbidden, projection_log


def _score_claim_relevance(text: str, func_name: str, is_pure: bool) -> tuple[float, str]:
    """Score how relevant a claim is to a specific function target.

    Returns (confidence, relevance_level).
    """
    text_lower = text.lower()
    func_lower = func_name.lower()

    # Level 1: claim text mentions the function name → high
    if func_lower in text_lower and len(func_name) > 3:
        return 0.9, "high"

    # Level 2: predicate op matches target context → medium
    pred = compile_claim(text)
    if pred.op == PredicateOp.PURE and is_pure:
        return 0.75, "medium"
    if pred.op in (PredicateOp.RETURNS_NON_NULL, PredicateOp.IS_TYPE, PredicateOp.PARAM_COUNT_LTE):
        return 0.75, "medium"

    # Level 3: generic project-level claim → low (demoted)
    return 0.55, "low"


def _claim_contradicted_by_spec(pred: Predicate, func_spec: Any) -> bool:
    """Check if a claim predicate is contradicted by func_spec evidence."""
    if func_spec is None:
        return False

    core = getattr(func_spec, "core", None)
    if core is None:
        return False

    # PURE claim contradicted by non-pure function
    if pred.op == PredicateOp.PURE and not getattr(core, "is_pure", False):
        testability = getattr(func_spec, "testability", None)
        if testability and getattr(testability, "is_stateful", False):
            return True

    return False
