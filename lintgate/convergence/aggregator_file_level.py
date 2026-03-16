"""File-level lens adapters and weighted aggregation for convergence."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from .aggregator import _probability_union

if TYPE_CHECKING:
    from lintgate.types import LintIssue
from .evidence import Actionability, ConvergenceResult, LensEvidence, LensKind

# ── File-level lens adapters ─────────────────────────────────────────
#
# Each file-level adapter converts native data into list[LensEvidence]
# with filepath targets instead of function targets.


def adapt_cohesion_file(data: dict) -> list[LensEvidence]:
    """Adapt file-level cohesion data.

    Input: {filepath: {score, component_count}}
    score<0.5 or components>1 → support split.
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
                detail=f"File cohesion={score:.2f}, components={components}",
                raw=info,
            )
        )
    return results


def adapt_fan_in_file(data: dict, threshold: int = 5) -> list[LensEvidence]:
    """Adapt file-level fan-in data.

    Input: {module: count}
    High fan-in (>=threshold) → oppose split (too many dependents).
    <=1 → support split (few dependents, safe to restructure).
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
                    detail=f"Fan-in={count} (high, split risky)",
                    raw={"fan_in": count},
                )
            )
        elif count <= 1:
            results.append(
                LensEvidence(
                    lens=LensKind.FAN_IN,
                    target=module,
                    confidence=0.4,
                    signal="support",
                    detail=f"Fan-in={count} (low, safe to restructure)",
                    raw={"fan_in": count},
                )
            )
    return results


def adapt_cochange_file(data: dict, filepath: str, threshold: float = 0.4) -> list[LensEvidence]:
    """Adapt file-level co-change data filtered to a specific filepath.

    Input: {pairs: [{file_a, file_b, coupling_strength}]} filtered to filepath.
    High coupling (>=threshold) → oppose split.
    """
    results: list[LensEvidence] = []
    for pair in (data or {}).get("pairs", []):
        file_a = pair.get("file_a", "")
        file_b = pair.get("file_b", "")
        if filepath not in (file_a, file_b):
            continue
        strength = pair.get("coupling_strength", 0.0)
        if strength >= threshold:
            results.append(
                LensEvidence(
                    lens=LensKind.COCHANGE,
                    target=filepath,
                    confidence=min(strength, 1.0),
                    signal="oppose",
                    detail=f"Coupled with {file_b if file_a == filepath else file_a} ({strength:.2f})",
                    raw=pair,
                )
            )
    return results


def adapt_import_weight_file(data: dict) -> list[LensEvidence]:
    """Adapt file-level import tracing data.

    Input: {module: {has_module_level_io, depth, non_stdlib_deps}}
    IO → oppose split; shallow deps → support split.
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
                    detail=f"Module-level IO (depth={depth})",
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


# ── File-level weighted aggregation ─────────────────────────────────

_FILE_LENS_WEIGHTS = {
    LensKind.COHESION: 2.0,
    LensKind.FAN_IN: 1.5,
    LensKind.COCHANGE: 1.0,
    LensKind.IMPORT_TRACING: 1.0,
}


def _apply_weight(confidence: float, weight: float) -> float:
    """Apply lens weight to confidence, capped at 1.0."""
    return min(confidence * weight, 1.0)


def classify_file_actionability(
    net: float, count: int, cohesion_data: dict | None = None
) -> Actionability:
    """Classify file-level actionability.

    - cohesion<0.3 + 3+ components + net>=0.5 + 2+ lenses → SPLIT
    - net>=0.75 + 3+ lenses → SPLIT
    - else INVESTIGATE
    """
    if cohesion_data:
        score = cohesion_data.get("score", 1.0)
        components = cohesion_data.get("component_count", 1)
        if score < 0.3 and components >= 3 and net >= 0.5 and count >= 2:
            return Actionability.SPLIT
    if net >= 0.75 and count >= 3:
        return Actionability.SPLIT
    return Actionability.INVESTIGATE


def aggregate_file(
    evidence: list[LensEvidence],
    cohesion_map: dict | None = None,
    split_proposals_map: dict | None = None,
) -> list[ConvergenceResult]:
    """Aggregate file-level evidence with weighted probability union.

    Sets target_type="file", attaches split proposals from split_proposals_map.
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

        # Apply weights before probability union
        weighted_support = [
            _apply_weight(e.confidence, _FILE_LENS_WEIGHTS.get(e.lens, 1.0)) for e in support
        ]
        weighted_oppose = [
            _apply_weight(e.confidence, _FILE_LENS_WEIGHTS.get(e.lens, 1.0)) for e in oppose
        ]

        support_prob = _probability_union(weighted_support)
        oppose_prob = _probability_union(weighted_oppose)
        net = max(support_prob - oppose_prob, 0.0)

        supporting_lenses = sorted({e.lens for e in support}, key=lambda lk: lk.value)
        opposing_lenses = sorted({e.lens for e in oppose}, key=lambda lk: lk.value)

        cohesion_data = (cohesion_map or {}).get(target)
        actionability = classify_file_actionability(net, len(supporting_lenses), cohesion_data)

        proposals = (split_proposals_map or {}).get(target, [])

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
                target_type="file",
                split_proposals=proposals,
            )
        )

    return sorted(results, key=lambda r: r.net_confidence, reverse=True)


# ── Function-level adapters ─────────────────────────────────────────


def adapt_contract_coverage(
    published_targets: dict,
    consumed_targets: dict,
) -> list[LensEvidence]:
    """Adapt contract coverage data.

    When a target function appears in a channel's published metrics but NOT
    in a consuming channel's expected format, this is convergence evidence
    supporting decomposition — the function sits at a contract boundary
    where coverage gaps indicate structural coupling.

    Input:
        published_targets: {func_key: {channel, metric_key}} — functions in published metrics
        consumed_targets: {func_key: {channel, metric_key}} — functions in consumed metrics
    """
    results: list[LensEvidence] = []
    published_only = set(published_targets.keys()) - set(consumed_targets.keys())

    for func_key in published_only:
        info = published_targets[func_key]
        results.append(
            LensEvidence(
                lens=LensKind.CONTRACT_COVERAGE,
                target=func_key,
                confidence=0.5,
                signal="support",
                detail=(
                    f"Published by {info.get('channel', '?')} "
                    f"but not consumed by any downstream channel"
                ),
                raw=info,
            )
        )
    return results


def adapt_cross_channel(findings: list[LintIssue] | list | None) -> list[LensEvidence]:
    """Adapt cross-channel coherence findings (COH001-003).

    Input: list[LintIssue] — each finding → support decomposition.
    """
    results: list[LensEvidence] = []
    for f in findings or []:
        target = getattr(f, "file", None) or ""
        kind = getattr(f, "kind", "")
        message = getattr(f, "message", "")
        results.append(
            LensEvidence(
                lens=LensKind.CROSS_CHANNEL,
                target=target,
                confidence=0.5,
                signal="support",
                detail=f"{kind}: {message}",
                raw={"kind": kind, "message": message},
            )
        )
    return results
