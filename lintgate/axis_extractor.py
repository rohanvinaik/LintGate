"""Axis extractor — thin wrapper mapping 7 theory facets to 4 compass axes.

Bridges the theory_extractor (which produces 7-facet profiles) and the
compass system (which operates on 4 axes). The three public functions
provide progressive levels of detail:

- extract_compass: full CompassState (for persistence / deep queries)
- build_compass_pack: compact runtime payload (~500-1500 tokens)
- query_compass: on-demand claim retrieval filtered by axis / keywords
"""

from __future__ import annotations

import re
from typing import Any

from .compass import (
    AXIS_NAMES,
    CompassAxis,
    CompassClaim,
    CompassState,
    compute_gap_report,
)
from .compass_io import migrate_from_theory_profile
from .theory_extractor import extract_theory

# ── Axis-specific heading signals ────────────────────────────────────
#
# These complement the facet-level heading signals in theory_extractor.
# They are used post-migration to boost depth scoring for claims whose
# headings strongly indicate axis relevance.

AXIS_HEADING_SIGNALS: dict[str, list[str]] = {
    "problem": [
        r"problem",
        r"challenge",
        r"requirement",
        r"user need",
        r"pain point",
        r"use case",
        r"motivation",
        r"goal",
        r"objective",
        r"constraint",
    ],
    "solution": [
        r"approach",
        r"design",
        r"architect",
        r"trade-?off",
        r"decision",
        r"strateg",
        r"method",
        r"algorithm",
        r"pipeline",
        r"rationale",
    ],
    "implementation": [
        r"pattern",
        r"convention",
        r"naming",
        r"style",
        r"coding",
        r"format",
        r"structure",
        r"template",
        r"standard",
        r"rule",
    ],
    "world": [
        r"ecosystem",
        r"dependency",
        r"dependencies",
        r"infrastructure",
        r"platform",
        r"deployment",
        r"environment",
        r"external",
        r"integration",
        r"toolchain",
    ],
}

# Pre-compile heading signal patterns for each axis.
_COMPILED_HEADING_SIGNALS: dict[str, list[re.Pattern[str]]] = {
    axis: [re.compile(pat, re.IGNORECASE) for pat in patterns]
    for axis, patterns in AXIS_HEADING_SIGNALS.items()
}


# ── Heading signal scoring ───────────────────────────────────────────


def _heading_matches_axis(heading: str, axis_name: str) -> bool:
    """Check if a claim heading matches axis-specific signals."""
    if not heading:
        return False
    for pat in _COMPILED_HEADING_SIGNALS.get(axis_name, []):
        if pat.search(heading):
            return True
    return False


def _apply_heading_depth_boost(state: CompassState) -> None:
    """Boost axis depth when heading signals reinforce claim relevance.

    For each axis, count claims whose headings match the axis-specific
    heading signals. If enough heading-matched claims exist, bump depth
    up by one level (capped at 3).
    """
    for axis_name in AXIS_NAMES:
        axis = state.axes.get(axis_name)
        if not axis or not axis.claims:
            continue

        matched = sum(
            1 for c in axis.claims if _heading_matches_axis(c.heading, axis_name)
        )

        # Boost threshold: at least 2 heading-matched claims, or 30% of
        # total claims (whichever is larger), triggers a +1 depth bump.
        threshold = max(2, len(axis.claims) * 3 // 10)
        if matched >= threshold and axis.depth < 3:
            axis.depth = min(axis.depth + 1, 3)


# ── Public API ───────────────────────────────────────────────────────


def extract_compass(project_root: str) -> CompassState:
    """Extract a 4-axis compass state from a project's markdown docs.

    Pipeline:
    1. Call extract_theory() to get the 7-facet theory profile.
    2. Map via migrate_from_theory_profile() to produce a CompassState.
    3. Apply axis-specific heading signals for refined depth scoring.
    4. Recompute the gap report with updated depths.

    Args:
        project_root: Absolute path to the project root.

    Returns:
        A fully populated CompassState with axes, directives,
        gap report, and forged timestamp.
    """
    full_result = extract_theory(project_root)
    theory_profile = full_result.get("theory_profile", {})

    state = migrate_from_theory_profile(theory_profile, full_result)

    # Refine depth scoring with axis-level heading signals
    _apply_heading_depth_boost(state)

    # Recompute gap report after depth adjustments
    state.gap_report = compute_gap_report(state)

    return state


def build_compass_pack(project_root: str) -> dict[str, Any]:
    """Build a compact runtime payload for agent context injection.

    Designed to fit in ~500-1500 tokens. Contains per-axis depth and
    summary, directives, gap analysis, and a token estimate.

    Args:
        project_root: Absolute path to the project root.

    Returns:
        Dict with keys: axes, directives, gap_report,
        digest_token_estimate.
    """
    state = extract_compass(project_root)

    axes_summary: dict[str, dict[str, Any]] = {}
    for name in AXIS_NAMES:
        axis = state.axes.get(name)
        if axis:
            axes_summary[name] = {
                "depth": axis.depth,
                "summary": axis.summary,
                "claim_count": len(axis.claims),
            }
        else:
            axes_summary[name] = {
                "depth": 0,
                "summary": "",
                "claim_count": 0,
            }

    directives_list = [d.to_dict() for d in state.directives]

    pack: dict[str, Any] = {
        "axes": axes_summary,
        "directives": directives_list,
        "gap_report": state.gap_report.to_dict(),
        "digest_token_estimate": _estimate_pack_tokens(axes_summary, directives_list),
    }

    return pack


def query_compass(
    state: CompassState,
    axis: str | None = None,
    keywords: list[str] | None = None,
    max_claims: int = 5,
) -> dict[str, Any]:
    """On-demand claim retrieval from a populated CompassState.

    Filters claims by axis name and/or keyword matches in claim text.
    Returns the most relevant claims with their axis context.

    Args:
        state: A populated CompassState (from extract_compass).
        axis: Optional axis name to filter by (problem, solution,
            implementation, world).
        keywords: Optional keywords to match against claim text.
        max_claims: Maximum number of claims to return (default 5).

    Returns:
        Dict with matched_claims list, total_matched count,
        returned_count, truncated flag, and the query parameters.

    Raises:
        ValueError: If max_claims is not positive.
    """
    if max_claims <= 0:
        raise ValueError("max_claims must be > 0")

    axes_to_search = _resolve_axes_to_search(state, axis)

    results = _collect_matching_claims(axes_to_search, keywords)

    # Sort by relevance (descending), then truncate
    results.sort(key=lambda r: -r["relevance_score"])
    total_matched = len(results)
    results = results[:max_claims]

    return {
        "matched_claims": results,
        "total_matched": total_matched,
        "returned_count": len(results),
        "truncated": total_matched > max_claims,
        "query": {"axis": axis, "keywords": keywords},
    }


# ── Internal helpers ─────────────────────────────────────────────────


def _resolve_axes_to_search(
    state: CompassState,
    axis: str | None,
) -> list[CompassAxis]:
    """Resolve which axes to search based on the axis filter."""
    if axis and axis in state.axes:
        return [state.axes[axis]]
    return [a for a in state.axes.values() if a.claims]


def _collect_matching_claims(
    axes: list[CompassAxis],
    keywords: list[str] | None,
) -> list[dict[str, Any]]:
    """Collect and score claims from the given axes."""
    results: list[dict[str, Any]] = []
    for ax in axes:
        for claim in ax.claims:
            score = _score_claim_relevance(claim, keywords)
            if score > 0:
                results.append({
                    "axis": ax.name,
                    "text": claim.text,
                    "source": claim.source,
                    "heading": claim.heading,
                    "confidence": claim.confidence,
                    "origin_facet": claim.origin_facet,
                    "relevance_score": score,
                })
    return results


def _score_claim_relevance(
    claim: CompassClaim,
    keywords: list[str] | None,
) -> int:
    """Score a claim's relevance to the given keywords.

    Returns 0 if no match, 1+ for matching claims.
    With no keywords, all claims score 1 (unfiltered).
    """
    if not keywords:
        return 1

    text_lower = claim.text.lower()
    score = 0
    for kw in keywords:
        if kw.lower() in text_lower:
            score += 1
    return score


def _estimate_pack_tokens(
    axes_summary: dict[str, dict[str, Any]],
    directives: list[dict[str, Any]],
) -> int:
    """Rough token estimate for the compass pack payload.

    Uses ~1.3 tokens per word for English prose / structured data.
    """
    parts: list[str] = []
    for name, info in axes_summary.items():
        parts.append(f"{name}: depth={info['depth']} claims={info['claim_count']}")
        if info.get("summary"):
            parts.append(str(info["summary"]))
    for d in directives:
        parts.append(f"{d.get('kind', '')}: {d.get('text', '')}")

    text = " ".join(parts)
    word_count = len(text.split())
    return int(word_count * 1.3)
