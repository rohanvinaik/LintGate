"""Theory scoring — claim classification and quality scoring.

Extracted from theory_extractor.py. Contains the scoring heuristics used to
determine whether a sentence is a meaningful theory claim and which facet
it belongs to, plus the heading/paragraph signal constants that drive
section classification.
"""

from __future__ import annotations

import re
from typing import Any

# ─── Heading patterns that signal theory-relevant sections ────────────

_THEORY_HEADING_SIGNALS: dict[str, list[str]] = {
    "core_theory": [
        r"theor",
        r"abstract",
        r"introduction",
        r"foundation",
        r"what (?:this|it) is",
        r"overview",
        r"definition",
        r"core (?:insight|concept|idea|principle)",
        r"key insight",
        r"fundamental",
        r"hypothesis",
        r"research question",
        r"motivation",
        r"framing",
        r"scope and stance",
    ],
    "problem_solving": [
        r"approach",
        r"method",
        r"strateg",
        r"heuristic",
        r"how (?:it|we|this) (?:work|solv|think|approach)",
        r"algorithm",
        r"pipeline",
        r"workflow",
        r"process",
        r"walkthrough",
        r"technique",
        r"lesson",
        r"recommendation",
        r"what worked",
        r"practical guidance",
    ],
    "alignment": [
        r"alignment",
        r"proper.*(?:vs|versus).*improper",
        r"(?:wrong|correct|right|good|bad)\s+(?:vs|versus|and|example)",
        r"example.*(?:wrong|correct|proper|improper)",
        r"(?:what|how).*(?:good|right|correct).*look",
        r"(?:why|how).*matters?",
        r"non[- ]?goal",
        r"scope",
        r"what could be better",
        r"what .* gets right",
    ],
    "architecture": [
        r"architect",
        r"design",
        r"system",
        r"structure",
        r"why (?:this|we|not)",
        r"rationale",
        r"trade-?off",
        r"comparison",
        r"alternative",
        r"integration",
        r"specification",
        r"decomposition",
        r"module",
        r"performance",
        r"optimi[sz]",
        r"profil",
        r"benchmark",
        r"scaling",
    ],
    "anti_patterns": [
        r"anti[- ]?pattern",
        r"pitfall",
        r"(?:do )?not",
        r"avoid",
        r"warning",
        r"danger",
        r"mistake",
        r"(?:what|how).*(?:wrong|fail|break|ruin)",
        r"common (?:error|mistake|problem)",
        r"what didn.t work",
        r"caused problem",
        r"risk",
        r"mitigation",
    ],
    "abstractions": [
        r"concept",
        r"vocabular",
        r"terminolog",
        r"glossar",
        r"key (?:term|concept|abstraction|definition)",
        r"primitive",
        r"building block",
        r"data (?:model|structure|type)",
        r"component",
        r"metric",
        r"evaluation",
    ],
}

# Paragraph-level signals for theory content
_THEORY_PARAGRAPH_SIGNALS = {
    "core_theory": [
        re.compile(
            r"the (?:key|core|fundamental|central) (?:insight|idea|principle|claim)",
            re.I,
        ),
        re.compile(
            r"this (?:system|project|architecture|approach) (?:is|uses|implements|demonstrates)",
            re.I,
        ),
        re.compile(r"(?:we|the system) (?:define|articulate|propose|claim|argue)", re.I),
        re.compile(r"the theory (?:of|behind|underlying)", re.I),
        re.compile(r"we (?:hypothesize|propose|conjecture) that", re.I),
        re.compile(r"this work (?:addresses|tests|investigates|explores)", re.I),
        re.compile(r"the (?:hypothesis|conjecture|thesis) is", re.I),
    ],
    "problem_solving": [
        re.compile(r"(?:it is|we find that) .*(?:easier|better|faster|more efficient) to", re.I),
        re.compile(r"rather than .*, (?:we|the system|this)", re.I),
        re.compile(r"(?:by|through) (?:encoding|exploiting|leveraging|using)", re.I),
        re.compile(r"transform.* (?:intractable|exponential|brute.?force).* into", re.I),
        re.compile(r"(?:instead of|rather than|not by|not through)", re.I),
        re.compile(r"\*\*lesson[:\*]", re.I),
        re.compile(r"\*\*recommendation", re.I),
        re.compile(r"(?:what worked|what didn.t work)", re.I),
        re.compile(r"the (?:fix|solution|workaround|approach) was", re.I),
    ],
    "alignment": [
        re.compile(r"if you .*, you will (?:ruin|break|destroy|undermine|bypass)", re.I),
        re.compile(r"(?:wrong|incorrect|improper|bad|misaligned).*(?:because|since|as)", re.I),
        re.compile(r"(?:correct|proper|right|good|aligned).*(?:because|since|as)", re.I),
        re.compile(r"the (?:goal|purpose|point) is (?:not )?(?:just )?to", re.I),
        re.compile(
            r"this (?:approach|method|way|solution) (?:supports?|enables?|allows?)",
            re.I,
        ),
        re.compile(r"\*\*non[- ]?goal\*\*", re.I),
        re.compile(r"(?:primary|secondary) objective", re.I),
    ],
    "anti_patterns": [
        re.compile(r"(?:task|problem)[- ]specific (?:function|solution|hack|workaround)", re.I),
        re.compile(r"(?:black[- ]?box|monolith|hard[- ]?cod|ad[- ]?hoc)", re.I),
        re.compile(r"bypass.* (?:learning|composition|architecture|system)", re.I),
        re.compile(r"(?:will|would|can) (?:ruin|break|destroy|undermine)", re.I),
        re.compile(r"(?:trying harder|brute force|premature|overfitting)", re.I),
    ],
    "architecture": [
        re.compile(r"\b(?:O\(n[²2]\)|quadratic|exponential|linear time)\b", re.I),
        re.compile(r"\b(?:vectori[sz]|batch|parallel)\b.*\b(?:instead|rather|prefer)\b", re.I),
        re.compile(
            r"\b(?:performance|latency|throughput|bottleneck)\b.*\b(?:because|since|critical)\b",
            re.I,
        ),
        re.compile(
            r"\b(?:JIT|numba|numpy|vectori[sz]ed)\b.*\b(?:hot|loop|path|critical)\b",
            re.I,
        ),
    ],
}

# Contrastive markers that signal alignment criteria
_CONTRASTIVE_MARKERS = [
    re.compile(r"^#+\s*(?:WRONG|INCORRECT|BAD|IMPROPER|ANTI-PATTERN)", re.I | re.M),
    re.compile(r"^#+\s*(?:CORRECT|RIGHT|GOOD|PROPER|ALIGNED)", re.I | re.M),
    re.compile(r"(?:wrong|incorrect).*(?:vs|versus|→).*(?:correct|right)", re.I),
    re.compile(r"(?:instead of|rather than|not like|don't do)", re.I),
]


# ─── Scoring functions ────────────────────────────────────────────────


def _score_universal(s: str) -> int:
    """Score universal theory claim signals present in any facet."""
    score = 0
    if re.search(r"\b(?:because|since|therefore|thus|hence|so that)\b", s, re.I):
        score += 1
    if re.search(r"\b(?:rather than|instead of|not by|unlike)\b", s, re.I):
        score += 1
    if re.search(r"\b(?:key|core|fundamental|central|critical|essential)\b", s, re.I):
        score += 1
    if re.search(r"\b(?:emerges?|enables?|ensures?|provides?|demonstrates?)\b", s, re.I):
        score += 1
    return score


def _score_core_theory(s: str) -> int:
    score = 0
    if re.search(r"\b(?:is defined as|we define|the definition)\b", s, re.I):
        score += 2
    if re.search(r"\b(?:theory|principle|axiom|invariant|postulate)\b", s, re.I):
        score += 1
    if re.search(r"\b(?:we hypothesize|hypothesis|conjecture|research question)\b", s, re.I):
        score += 2
    if re.search(r"this (?:work|project|research) (?:address|test|investigat|explor)", s, re.I):
        score += 1
    return score


def _score_problem_solving(s: str) -> int:
    score = 0
    if re.search(
        r"\b(?:easier|better|tractable|efficient|guided)\b.*\b(?:than|over|compared)\b", s, re.I
    ):
        score += 2
    if re.search(r"\btransform.*(?:into|to)\b", s, re.I):
        score += 1
    if re.search(r"\b(?:scan|parse|split|classif|extract|deduplicat|score|report)\w*\b", s, re.I):
        score += 1
    if re.search(r"\b(?:step|phase|pipeline|workflow|process)\b", s, re.I):
        score += 1
    if re.search(r"\b(?:first|then|next|finally)\b", s, re.I):
        score += 1
    if re.search(r"\*\*Lesson", s):
        score += 2
    if re.search(r"the (?:fix|solution|workaround) was", s, re.I):
        score += 1
    return score


def _score_alignment(s: str) -> int:
    score = 0
    if re.search(r"\b(?:ruin|break|destroy|undermine|bypass)\b", s, re.I):
        score += 2
    if re.search(r"\b(?:proper|correct|aligned|right way)\b", s, re.I):
        score += 1
    if re.search(r"\b(?:goal|purpose|point) is\b", s, re.I):
        score += 1
    if re.search(r"\b(?:non[- ]?goal|not a goal|out of scope)\b", s, re.I):
        score += 2
    if re.search(r"\b(?:primary|secondary) objective\b", s, re.I):
        score += 1
    return score


def _score_architecture(s: str) -> int:
    score = 0
    if re.search(r"\bwhy\b.*\b(?:not|over|instead|rather)\b", s, re.I):
        score += 2
    if re.search(r"\b(?:design|chose|decided|tradeoff|trade-off)\b", s, re.I):
        score += 1
    if re.search(r"\*\*Rationale\*\*", s):
        score += 2
    if re.search(r"\b(?:decompos|modular|separation of concerns|drop[- ]?in)\b", s, re.I):
        score += 1
    if re.search(
        r"\b(?:O\(n|quadratic|exponential|vectori[sz]|batch|performance|latency|throughput)\b",
        s,
        re.I,
    ):
        score += 1
    return score


def _score_anti_patterns(s: str) -> int:
    score = 0
    if re.search(r"\b(?:will|would|can|could)\s+(?:ruin|break|destroy|fail)\b", s, re.I):
        score += 2
    if re.search(r"\b(?:black.?box|monolith|hard.?cod|ad.?hoc|hack|workaround)\b", s, re.I):
        score += 1
    if re.search(r"\b(?:trying harder|premature|overfitting|scope creep)\b", s, re.I):
        score += 1
    sentence_lower = s.lower()
    tool_desc_patterns = ["provides", "channel", "linter", "tier", "analysis"]
    if sum(1 for p in tool_desc_patterns if p in sentence_lower) >= 1:
        score -= 2
    if re.search(r"\b(?:provides|returns|supports|contains|includes)\b", sentence_lower):
        score -= 1
    return score


def _score_abstractions(s: str) -> int:
    score = 0
    if re.search(r"\b(?:we (?:call|define|term)|is called|known as|refers to)\b", s, re.I):
        score += 2
    if re.search(r"\*\*\w+(?:\s+\w+){0,3}\*\*", s):
        score += 1
    return score


_FACET_SCORERS: dict[str, Any] = {
    "core_theory": _score_core_theory,
    "problem_solving": _score_problem_solving,
    "alignment": _score_alignment,
    "architecture": _score_architecture,
    "anti_patterns": _score_anti_patterns,
    "abstractions": _score_abstractions,
}


def _score_claim(sentence: str, facet: str) -> int:
    """Score how likely a sentence is to be a meaningful theory claim.

    Returns 0 for non-claims, 1+ for likely claims. Higher = stronger signal.
    """
    score = _score_universal(sentence)
    facet_scorer = _FACET_SCORERS.get(facet)
    if facet_scorer is not None:
        score += facet_scorer(sentence)
    return score


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, handling common markdown artifacts."""
    # Replace newlines that aren't paragraph breaks with spaces
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    # Split on paragraph breaks
    paragraphs = re.split(r"\n\s*\n", text)

    sentences: list[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para or para.startswith("|") or para.startswith("- ["):
            continue
        # Split paragraph into sentences
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", para)
        sentences.extend(parts)

    return sentences


def _pick_best_summary_claim(
    claims: list[str],
    exclude: set[str] | None = None,
) -> str:
    """Pick the most informative claim to use as a facet summary.

    Prefers claims that:
    - Have causal/contrastive language (because, rather than, enables)
    - Are a reasonable length (40-200 chars) — not too terse, not too verbose
    - Don't contain CODE placeholders or path fragments
    - Haven't already been used as a summary for another facet
    """
    exclude = exclude or set()

    def quality_score(claim: str) -> float:
        s = 0.0
        # Penalize very short or very long claims
        length = len(claim)
        if length < 40:
            s -= 2.0  # Too terse to be informative
        elif length < 80:
            s += 0.5
        elif length <= 200:
            s += 1.0  # Sweet spot
        else:
            s -= 0.5  # Getting verbose

        # Penalize noise markers
        if "CODE" in claim:
            s -= 3.0
        if claim.count("/") > 2:
            s -= 2.0
        # Penalize purely descriptive content (no theory markers)
        if not re.search(
            r"\b(?:because|since|therefore|rather|instead|enables?|designed|approach|key|core|fundamental|must|should|critical|hypothesis)\b",
            claim,
            re.I,
        ):
            s -= 1.0

        # Reward theory-quality language
        if re.search(r"\b(?:because|since|therefore|thus)\b", claim, re.I):
            s += 2.0
        if re.search(r"\b(?:rather than|instead of|not by|unlike)\b", claim, re.I):
            s += 2.0
        if re.search(
            r"\b(?:enables?|ensures?|provides?|designed|architecture|approach)\b",
            claim,
            re.I,
        ):
            s += 1.0
        if re.search(r"\b(?:key|core|fundamental|central|critical)\b", claim, re.I):
            s += 1.0
        if re.search(r"\b(?:hypothesis|hypothesize|conjecture|propose|we argue)\b", claim, re.I):
            s += 1.5

        return s

    # Filter out already-used summaries
    available = [c for c in claims if c not in exclude]
    if not available:
        available = claims  # Fall back to all if all were excluded

    scored = [(quality_score(c), c) for c in available]
    scored.sort(key=lambda x: -x[0])
    return scored[0][1] if scored else "(no theory content found)"


def _classify_section(section: Any) -> list[str]:
    """Determine which theory facets a section belongs to."""
    facets: list[str] = []
    heading_lower = section.heading.lower()

    # Check heading signals
    for facet, patterns in _THEORY_HEADING_SIGNALS.items():
        for pattern in patterns:
            if re.search(pattern, heading_lower):
                facets.append(facet)
                break

    # Check paragraph-level signals in body
    for facet, compiled_patterns in _THEORY_PARAGRAPH_SIGNALS.items():
        if facet in facets:
            continue  # Already classified by heading
        for pat in compiled_patterns:
            if pat.search(section.body):
                facets.append(facet)
                break

    # Check contrastive markers for alignment
    if "alignment" not in facets:
        for marker in _CONTRASTIVE_MARKERS:
            if marker.search(section.body):
                facets.append("alignment")
                break

    return facets
