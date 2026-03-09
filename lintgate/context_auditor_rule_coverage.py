"""Rule coverage and directive enforceability classification.

Extracted from context_auditor_checks.py — connected component #1.

Contains: DirectiveClassification, classify_directive_enforceability,
check_rule_coverage, and all private helpers for syntactic/architectural
signal detection and coverage-token matching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ── Coverage Token Helpers ────────────────────────────────────────────


def _coverage_tokens(text: str) -> set[str]:
    """Tokenize text for fuzzy directive-to-rule coverage matching."""
    tokens: set[str] = set()
    for token in re.findall(r"[a-z0-9_]{3,}", text.lower()):
        for part in re.split(r"[_\W]+", token):
            part = part.strip()
            if len(part) < 3:
                continue
            if part.endswith("s") and len(part) > 4:
                part = part[:-1]
            tokens.add(part)
    return tokens


_COVERAGE_STOPWORDS = {
    "that",
    "which",
    "with",
    "from",
    "this",
    "have",
    "does",
    "will",
    "should",
    "must",
    "into",
    "them",
    "than",
    "been",
    "each",
    "only",
    "also",
    "just",
}


# ── Directive Enforceability Classification ───────────────────────────

_SYNTACTIC_IDENTIFIER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\w+(?:\.\w+)+"),
    re.compile(r"`[^`]+`"),
    re.compile(r"\b[A-Z][A-Z0-9_]{3,}\b"),
    re.compile(r"\b\w+_\w+\(\)"),
    re.compile(r"\b\w+_\w+_\w+\b"),
]

_ARCHITECTURAL_CUE_RE = re.compile(
    r"\b(?:"
    r"approach|abstraction|bypass|coherence|constraint|discipline|"
    r"iterate|understand|verify|repeat|same\s+approach|one-off|"
    r"task-specific|ad[\s-]?hoc|shortcut|"
    r"instead\s+of|prefer\b.*\bover|"
    r"without\s+(?:understanding|checking|verifying|reading)|"
    r"hard\s+to|difficult|ensure|maintain|avoid\s+\w+ing"
    r")\b",
    re.IGNORECASE,
)


def _count_syntactic_ids(text: str) -> int:
    matches: set[str] = set()
    for pat in _SYNTACTIC_IDENTIFIER_PATTERNS:
        for m in pat.findall(text):
            matches.add(m)
    return len(matches)


def _has_syntactic_id(text: str) -> bool:
    return any(pat.search(text) for pat in _SYNTACTIC_IDENTIFIER_PATTERNS)


@dataclass
class DirectiveClassification:
    """3-way classification of a DO NOT directive's enforceability."""

    classification: str  # "enforceable" | "architectural" | "uncertain"
    confidence: float = 1.0
    reason: str = ""


def classify_directive_enforceability(directive: str) -> DirectiveClassification:
    """3-way classifier: enforceable / architectural / uncertain."""
    text = directive.strip()
    has_syntactic = _has_syntactic_id(text)
    has_architectural = bool(_ARCHITECTURAL_CUE_RE.search(text))

    if has_syntactic and not has_architectural:
        return DirectiveClassification(
            classification="enforceable",
            confidence=0.95,
            reason="Contains syntactic identifier with no architectural cues.",
        )

    if has_syntactic and has_architectural:
        syn_count = _count_syntactic_ids(text)
        arch_count = len(_ARCHITECTURAL_CUE_RE.findall(text))
        if syn_count > arch_count:
            return DirectiveClassification(
                classification="enforceable",
                confidence=0.7,
                reason=f"Syntactic signals ({syn_count}) outweigh architectural ({arch_count}).",
            )
        if arch_count > syn_count:
            return DirectiveClassification(
                classification="architectural",
                confidence=0.7,
                reason=f"Architectural cues ({arch_count}) outweigh syntactic ({syn_count}).",
            )
        return DirectiveClassification(
            classification="uncertain",
            confidence=0.4,
            reason=f"Equal syntactic ({syn_count}) and architectural ({arch_count}) signals.",
        )

    if has_architectural and not has_syntactic:
        return DirectiveClassification(
            classification="architectural",
            confidence=0.9,
            reason="Architectural/process cues with no syntactic identifiers.",
        )

    return DirectiveClassification(
        classification="uncertain",
        confidence=0.3,
        reason="No syntactic or architectural signals detected.",
    )


def _is_regex_enforceable(directive: str) -> bool:
    """Classify whether a DO NOT directive can be enforced via regex."""
    return classify_directive_enforceability(directive).classification == "enforceable"


# ── Rule Coverage Check ───────────────────────────────────────────────


def _directive_has_matching_rule(
    directive_lower: str,
    directive_words: set[str],
    all_matching_rules: list[dict[str, Any]],
) -> bool:
    """Check if a directive has a matching rule via text or keyword overlap."""
    for r in all_matching_rules:
        rule_text = " ".join(
            [
                str(r.get("message", "")),
                str(r.get("source", "")),
                str(r.get("pattern", "")),
            ]
        ).lower()

        if directive_lower in rule_text:
            return True

        if directive_words:
            rule_words = _coverage_tokens(rule_text)
            overlap = directive_words & rule_words
            min_overlap = 1 if len(directive_words) <= 3 else 2
            if len(overlap) >= min_overlap:
                return True
    return False


def check_rule_coverage(
    checks: list[dict[str, Any]],
    suggestions: list[str],
    guidance: dict[str, Any],
    existing_rules: list[dict[str, Any]],
    thresholds: dict[str, Any],
) -> None:
    do_not_directives = guidance.get("directives", {}).get("do_not", [])
    total_do_not = len(do_not_directives)
    min_coverage_pct = thresholds["min_rule_coverage_pct"]

    if total_do_not == 0:
        checks.append(
            {
                "check": "machine_rules",
                "status": "pass",
                "detail": "No DO NOT directives found (nothing to enforce)",
            }
        )
        return

    enforceable: list[str] = []
    architectural: list[str] = []
    for directive in do_not_directives:
        if _is_regex_enforceable(directive):
            enforceable.append(directive)
        else:
            architectural.append(directive)

    forbid_rules = [r for r in existing_rules if r.get("kind") == "forbid_regex"]
    require_rules = [r for r in existing_rules if r.get("kind") == "require_regex"]
    all_matching_rules = forbid_rules + require_rules

    covered = 0
    uncovered_directives: list[str] = []

    for directive in enforceable:
        directive_lower = directive.lower()
        directive_words = _coverage_tokens(directive_lower) - _COVERAGE_STOPWORDS

        has_rule = _directive_has_matching_rule(
            directive_lower,
            directive_words,
            all_matching_rules,
        )
        if has_rule:
            covered += 1
        else:
            uncovered_directives.append(directive)

    total_enforceable = len(enforceable)

    if total_enforceable == 0:
        arch_note = (
            f" ({len(architectural)} architectural directive(s) noted)" if architectural else ""
        )
        checks.append(
            {
                "check": "machine_rules",
                "status": "pass",
                "detail": f"No regex-enforceable DO NOT directives found{arch_note}",
            }
        )
        return

    coverage_pct = (covered / total_enforceable) * 100
    arch_note = f" ({len(architectural)} architectural)" if architectural else ""

    if coverage_pct < min_coverage_pct:
        checks.append(
            {
                "check": "machine_rules",
                "status": "warn",
                "detail": (
                    f"{covered}/{total_enforceable} enforceable DO NOT directives have rules "
                    f"({coverage_pct:.0f}%, threshold: {min_coverage_pct}%){arch_note}"
                ),
            }
        )
        for directive in uncovered_directives[:3]:
            truncated = directive[:100] + "..." if len(directive) > 100 else directive
            suggestions.append(f"Add LINTGATE_FORBID_REGEX for: '{truncated}'")
    else:
        checks.append(
            {
                "check": "machine_rules",
                "status": "pass",
                "detail": (
                    f"{covered}/{total_enforceable} enforceable DO NOT directives have rules "
                    f"({coverage_pct:.0f}%){arch_note}"
                ),
            }
        )
