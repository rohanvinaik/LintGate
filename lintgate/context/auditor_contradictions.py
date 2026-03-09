"""Contradiction detection between DO and DO NOT directives.

Extracted from context_auditor_checks.py — connected component #3.

Contains: check_contradictions and private helpers for keyword extraction
and negation-pair detection.
"""

from __future__ import annotations

import re
from typing import Any


def check_contradictions(
    checks: list[dict[str, Any]],
    suggestions: list[str],
    guidance: dict[str, Any],
) -> None:
    directives = guidance.get("directives", {})
    do_directives = {d.lower() for d in directives.get("do", [])}
    do_not_directives = {d.lower() for d in directives.get("do_not", [])}

    do_keywords = _extract_keywords(do_directives)
    do_not_keywords = _extract_keywords(do_not_directives)

    overlap = do_keywords & do_not_keywords
    noise_words = {
        "use",
        "create",
        "make",
        "add",
        "set",
        "get",
        "run",
        "check",
        "test",
        "write",
        "read",
        "file",
        "code",
        "the",
        "and",
        "for",
        "this",
        "that",
        "with",
        "from",
        "have",
        "been",
        "will",
        "when",
        "each",
        "only",
        "should",
        "must",
        "never",
        "always",
        "before",
        "after",
        "into",
        "ensure",
        "avoid",
        "keep",
        "more",
        "less",
        "than",
        "also",
        "project",
        "tool",
        "module",
        "system",
        "data",
        "state",
        "mode",
        "path",
        "config",
        "session",
        "context",
        "theory",
        "snapshot",
    }
    meaningful_overlap = overlap - noise_words

    # Detect explicit "always X" vs "never X" negation pairs
    negation_pairs = _detect_negation_pairs(do_directives, do_not_directives)

    # Lower threshold when negation pairs provide strong contradiction evidence
    threshold = 1 if negation_pairs else 2

    if len(meaningful_overlap) >= threshold or negation_pairs:
        evidence_parts: list[str] = []
        if meaningful_overlap:
            evidence_parts.append(
                f"overlapping concepts: {', '.join(sorted(meaningful_overlap)[:5])}"
            )
        if negation_pairs:
            evidence_parts.append(f"always/never conflicts: {', '.join(negation_pairs[:3])}")
        evidence_str = "; ".join(evidence_parts)
        checks.append(
            {
                "check": "contradictions",
                "status": "warn",
                "detail": f"DO and DO NOT directives conflict — {evidence_str}",
            }
        )
        overlap_str = ", ".join(sorted(meaningful_overlap | set(negation_pairs))[:5])
        suggestions.append(
            f"Review potentially contradictory directives involving: {overlap_str}. "
            f"Ensure DO and DO NOT sections don't give conflicting guidance."
        )
    else:
        checks.append(
            {
                "check": "contradictions",
                "status": "pass",
                "detail": "No obvious contradictions detected between DO and DO NOT directives",
            }
        )


def _extract_keywords(directives: set[str]) -> set[str]:
    """Extract significant keywords from directive text for overlap detection.

    Extracts both unigrams (4+ chars) and bigrams for richer overlap detection.
    """
    keywords: set[str] = set()
    for d in directives:
        words = re.findall(r"\b[a-z]{4,}\b", d)
        keywords.update(words)
        # Add bigrams for multi-word concept matching
        for i in range(len(words) - 1):
            keywords.add(f"{words[i]} {words[i + 1]}")
    return keywords


def _detect_negation_pairs(do_directives: set[str], do_not_directives: set[str]) -> list[str]:
    """Detect "always X" vs "never X" contradictions between directive sets.

    Returns list of contradicting terms (e.g., "caching" if DO says
    "always use caching" and DO NOT says "never use caching").
    """
    always_terms: set[str] = set()
    never_terms: set[str] = set()

    for d in do_directives:
        for m in re.finditer(r"\balways\s+(?:use\s+)?(\w{4,})", d):
            always_terms.add(m.group(1))
    for d in do_not_directives:
        for m in re.finditer(r"\bnever\s+(?:use\s+)?(\w{4,})", d):
            never_terms.add(m.group(1))

    return sorted(always_terms & never_terms)
