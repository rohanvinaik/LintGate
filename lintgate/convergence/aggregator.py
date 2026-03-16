"""Convergence aggregator: multi-lens evidence fusion via probability union.

Three lenses at 0.6 confidence each yield 1 - 0.4^3 = 0.936 — much higher
than any single lens. This module groups evidence by target, computes
support/oppose probabilities, and classifies actionability.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from .evidence import Actionability, ConvergenceResult, LensEvidence, LensKind

if TYPE_CHECKING:
    from lintgate.types import Prescription


# ── Core engine (pure functions) ─────────────────────────────────────


def _probability_union(confidences: list[float]) -> float:
    """Compute 1 - prod(1 - c) for independent evidence sources."""
    if not confidences:
        return 0.0
    product = 1.0
    for c in confidences:
        product *= 1.0 - c
    return 1.0 - product


def classify_actionability(net: float, count: int) -> Actionability:
    """Classify actionability from net confidence and supporting lens count."""
    if net >= 0.75 and count >= 3:
        return Actionability.EXTRACT
    if net >= 0.5 and count >= 2:
        return Actionability.SPLIT
    return Actionability.INVESTIGATE


def aggregate(evidence: list[LensEvidence]) -> list[ConvergenceResult]:
    """Group evidence by target, compute probabilities, and classify.

    Returns results sorted by net_confidence descending.
    """
    if not evidence:
        return []

    by_target: dict[str, list[LensEvidence]] = defaultdict(list)
    for e in evidence:
        by_target[e.target].append(e)

    results: list[ConvergenceResult] = []
    for target, items in by_target.items():
        support = [e for e in items if e.signal == "support"]
        oppose = [e for e in items if e.signal == "oppose"]

        support_prob = _probability_union([e.confidence for e in support])
        oppose_prob = _probability_union([e.confidence for e in oppose])
        net = max(support_prob - oppose_prob, 0.0)

        supporting_lenses = sorted({e.lens for e in support}, key=lambda lk: lk.value)
        opposing_lenses = sorted({e.lens for e in oppose}, key=lambda lk: lk.value)

        actionability = classify_actionability(net, len(supporting_lenses))

        results.append(
            ConvergenceResult(
                target=target,
                support_prob=support_prob,
                oppose_prob=oppose_prob,
                net_confidence=net,
                supporting_lenses=supporting_lenses,
                opposing_lenses=opposing_lenses,
                actionability=actionability,
                evidence=items,
            )
        )

    return sorted(results, key=lambda r: r.net_confidence, reverse=True)


# ── Lens adapters ────────────────────────────────────────────────────
#
# Each adapter converts native data from one analysis lens into
# a list[LensEvidence].  Convention: return [] for empty/missing input.


def adapt_purity(data: dict | list) -> list[LensEvidence]:
    """Adapt purity analysis output.

    Accepts two formats:
    - list[dict]: [{name, file, hints}] — emitted by performance channel as pure_function_list
    - dict: {func: {file, confidence, hints}} — legacy purity_profile format
      (deprecated, will be removed in v1.1)

    Pure functions → support extraction safety.
    """
    results: list[LensEvidence] = []
    if isinstance(data, list):
        # pure_function_list format: [{name, file, hints}]
        for item in data:
            func = item.get("name", "")
            if not func:
                continue
            conf = item.get("confidence", 0.8)
            results.append(
                LensEvidence(
                    lens=LensKind.PURITY,
                    target=func,
                    confidence=conf,
                    signal="support",
                    detail=f"Pure function, hints={item.get('hints', [])}",
                    raw=item,
                )
            )
    else:
        # Legacy purity_profile format: {func: {file, confidence, hints}}
        for func, info in (data or {}).items():
            conf = info.get("confidence", 0.5)
            results.append(
                LensEvidence(
                    lens=LensKind.PURITY,
                    target=func,
                    confidence=conf,
                    signal="support",
                    detail=f"Pure function, hints={info.get('hints', [])}",
                    raw=info,
                )
            )
    return results


def adapt_mutation(data: dict) -> list[LensEvidence]:
    """Adapt mutation survival data.

    Input: {target: {survival_rate, survived_categories}}
    Survival >= 0.3 → support decomposition.
    """
    results: list[LensEvidence] = []
    for target, info in (data or {}).items():
        rate = info.get("survival_rate", 0.0)
        if rate < 0.3:
            continue
        cats = info.get("survived_categories", [])
        results.append(
            LensEvidence(
                lens=LensKind.MUTATION,
                target=target,
                confidence=min(rate, 1.0),
                signal="support",
                detail=f"Survival {rate:.0%} across {len(cats)} categories",
                raw=info,
            )
        )
    return results


def adapt_specification(data: dict) -> list[LensEvidence]:
    """Adapt specification complexity data.

    Input: {func_key: {spec_level, regime, sigma, is_pure, risk_score, priority_band}}
    Under-specified Regime B → support decomposition.
    Well-specified → oppose decomposition.
    """
    results: list[LensEvidence] = []
    for func_key, info in (data or {}).items():
        spec_level = info.get("spec_level", 0.0)
        regime = info.get("regime", "unknown")

        if regime == "B" and spec_level < 0.5:
            conf = min(1.0 - spec_level, 1.0)
            results.append(
                LensEvidence(
                    lens=LensKind.SPECIFICATION,
                    target=func_key,
                    confidence=conf,
                    signal="support",
                    detail=f"Regime B, spec_level={spec_level:.2f} (under-specified)",
                    raw=info,
                )
            )
        elif spec_level >= 0.7:
            results.append(
                LensEvidence(
                    lens=LensKind.SPECIFICATION,
                    target=func_key,
                    confidence=min(spec_level, 1.0),
                    signal="oppose",
                    detail=f"Well-specified (spec_level={spec_level:.2f})",
                    raw=info,
                )
            )
    return results


def adapt_composition_gap(data: dict) -> list[LensEvidence]:
    """Adapt composition gap data.

    Input: {edge_key: {gamma, integration_surface, spec_independent}}
    High gamma → support decomposition at boundary.
    """
    results: list[LensEvidence] = []
    for edge_key, info in (data or {}).items():
        gamma = info.get("gamma", 0.0)
        if gamma <= 0:
            continue
        conf = min(gamma / 5.0, 1.0)
        results.append(
            LensEvidence(
                lens=LensKind.COMPOSITION_GAP,
                target=edge_key,
                confidence=conf,
                signal="support",
                detail=f"Composition gap gamma={gamma:.2f}",
                raw=info,
            )
        )
    return results


def adapt_cohesion(data: dict) -> list[LensEvidence]:
    """Adapt cohesion analysis output.

    Input: {filepath: {score, component_count}}
    Score < 0.5 or components > 1 → support decomposition.
    """
    results: list[LensEvidence] = []
    for filepath, info in (data or {}).items():
        score = info.get("score", 1.0)
        components = info.get("component_count", 1)
        if score >= 0.5 and components <= 1:
            continue
        conf = max(1.0 - score, 0.1)
        results.append(
            LensEvidence(
                lens=LensKind.COHESION,
                target=filepath,
                confidence=conf,
                signal="support",
                detail=f"Cohesion={score:.2f}, components={components}",
                raw=info,
            )
        )
    return results


def adapt_fan_in(data: dict, threshold: int = 5) -> list[LensEvidence]:
    """Adapt module fan-in data.

    Input: {module: fan_in_count}
    High fan-in → oppose extraction (too many dependents).
    Zero fan-in → support extraction (orphan).
    """
    results: list[LensEvidence] = []
    for module, count in (data or {}).items():
        if count >= threshold:
            conf = min(count / (threshold * 2), 1.0)
            results.append(
                LensEvidence(
                    lens=LensKind.FAN_IN,
                    target=module,
                    confidence=conf,
                    signal="oppose",
                    detail=f"Fan-in={count} (high, extraction risky)",
                    raw={"fan_in": count},
                )
            )
        elif count == 0:
            results.append(
                LensEvidence(
                    lens=LensKind.FAN_IN,
                    target=module,
                    confidence=0.4,
                    signal="support",
                    detail="Fan-in=0 (orphan, safe to extract)",
                    raw={"fan_in": count},
                )
            )
    return results


def adapt_cochange(data: dict, threshold: float = 0.4) -> list[LensEvidence]:
    """Adapt co-change coupling data.

    Input: {pairs: [{file_a, file_b, coupling_strength}]}
    High coupling → oppose extraction (tightly coupled).
    Low coupling → support extraction.
    """
    results: list[LensEvidence] = []
    for pair in (data or {}).get("pairs", []):
        strength = pair.get("coupling_strength", 0.0)
        file_a = pair.get("file_a", "")
        file_b = pair.get("file_b", "")
        target = f"{file_a}<->{file_b}"
        if strength >= threshold:
            results.append(
                LensEvidence(
                    lens=LensKind.COCHANGE,
                    target=target,
                    confidence=min(strength, 1.0),
                    signal="oppose",
                    detail=f"Coupling={strength:.2f} (high, splitting risky)",
                    raw=pair,
                )
            )
        else:
            results.append(
                LensEvidence(
                    lens=LensKind.COCHANGE,
                    target=target,
                    confidence=max(1.0 - strength, 0.1),
                    signal="support",
                    detail=f"Coupling={strength:.2f} (low, splitting safe)",
                    raw=pair,
                )
            )
    return results


def adapt_dep_clustering(
    prescriptions: list[Prescription] | list | None,
) -> list[LensEvidence]:
    """Adapt dependency clustering prescriptions.

    Input: list[Prescription] — each extractable block → support.
    """
    results: list[LensEvidence] = []
    for p in prescriptions or []:
        target = getattr(p, "target", str(p)) if not isinstance(p, dict) else p.get("target", "")
        conf = (
            getattr(p, "confidence", 0.5) if not isinstance(p, dict) else p.get("confidence", 0.5)
        )
        results.append(
            LensEvidence(
                lens=LensKind.DEP_CLUSTERING,
                target=target,
                confidence=conf,
                signal="support",
                detail=f"Extractable block: {getattr(p, 'action', '') if not isinstance(p, dict) else p.get('action', '')}",
                raw={"prescription": str(p)},
            )
        )
    return results


def adapt_assertion_quality(data: dict) -> list[LensEvidence]:
    """Adapt assertion quality / test effectiveness data.

    Input: {func: {effectiveness_score, weakness_taxonomy}}
    Weak or untested → support decomposition (needs better tests → simpler units).
    """
    results: list[LensEvidence] = []
    for func, info in (data or {}).items():
        score = info.get("effectiveness_score", 1.0)
        taxonomy = info.get("weakness_taxonomy", [])
        if score >= 0.8 and not taxonomy:
            continue
        conf = max(1.0 - score, 0.1)
        results.append(
            LensEvidence(
                lens=LensKind.ASSERTION_QUALITY,
                target=func,
                confidence=conf,
                signal="support",
                detail=f"Effectiveness={score:.2f}, weaknesses={taxonomy}",
                raw=info,
            )
        )
    return results


def adapt_algebraic(data: dict) -> list[LensEvidence]:
    """Adapt algebraic property analysis.

    Input: {func: {properties, extraction_safety, hints}}
    Safe + properties → support; unsafe → oppose.
    """
    results: list[LensEvidence] = []
    for func, info in (data or {}).items():
        safety = info.get("extraction_safety", "unknown")
        properties = info.get("properties", [])
        hints = info.get("hints", [])
        if safety == "safe" and properties:
            results.append(
                LensEvidence(
                    lens=LensKind.ALGEBRAIC,
                    target=func,
                    confidence=0.6,
                    signal="support",
                    detail=f"Safe extraction, properties={properties}, hints={hints}",
                    raw=info,
                )
            )
        elif safety == "unsafe":
            results.append(
                LensEvidence(
                    lens=LensKind.ALGEBRAIC,
                    target=func,
                    confidence=0.5,
                    signal="oppose",
                    detail=f"Unsafe extraction: {info.get('reason', 'unknown')}",
                    raw=info,
                )
            )
    return results


def adapt_import_tracing(data: dict) -> list[LensEvidence]:
    """Adapt import tracing analysis.

    Input: {module: {has_module_level_io, depth, non_stdlib_deps}}
    IO at module level → oppose; shallow deps → support.
    """
    results: list[LensEvidence] = []
    for module, info in (data or {}).items():
        has_io = info.get("has_module_level_io", False)
        depth = info.get("depth", 0)
        non_stdlib = info.get("non_stdlib_deps", 0)
        if has_io:
            results.append(
                LensEvidence(
                    lens=LensKind.IMPORT_TRACING,
                    target=module,
                    confidence=0.6,
                    signal="oppose",
                    detail=f"Module-level IO detected (depth={depth})",
                    raw=info,
                )
            )
        elif depth <= 2 and non_stdlib <= 3:
            results.append(
                LensEvidence(
                    lens=LensKind.IMPORT_TRACING,
                    target=module,
                    confidence=0.4,
                    signal="support",
                    detail=f"Shallow deps (depth={depth}, non_stdlib={non_stdlib})",
                    raw=info,
                )
            )
    return results


def adapt_call_graph(data: dict, threshold: int = 8) -> list[LensEvidence]:
    """Adapt call graph analysis.

    Input: {func: {fan_in, fan_out}}
    High fan-out → support decomposition (doing too much).
    """
    results: list[LensEvidence] = []
    for func, info in (data or {}).items():
        fan_out = info.get("fan_out", 0)
        if fan_out >= threshold:
            conf = min(fan_out / (threshold * 2), 1.0)
            results.append(
                LensEvidence(
                    lens=LensKind.CALL_GRAPH,
                    target=func,
                    confidence=conf,
                    signal="support",
                    detail=f"Fan-out={fan_out} (high, doing too much)",
                    raw=info,
                )
            )
    return results
