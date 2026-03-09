"""Error matching helpers for the behavior channel.

Provides fuzzy error-text comparison and hypothesis evidence extraction
utilities used by hard detection rules.

Extracted from behavior_scoring.py for module size compliance.
"""

from __future__ import annotations

import re

# ── Constants ────────────────────────────────────────────────────────────

_ERROR_EVIDENCE_PREFIXES = ("exit!=0 with:", "confirmed by:", "re-observed:")
_ERROR_STOPWORDS = {
    "error",
    "failed",
    "failure",
    "exit",
    "code",
    "status",
    "with",
    "from",
    "during",
    "while",
    "the",
    "and",
    "for",
    "this",
}


# ── Helpers ──────────────────────────────────────────────────────────────


def _normalize_error_text(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _error_tokens(text: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]+", text.lower())
        if len(t) >= 3 and t not in _ERROR_STOPWORDS
    }


def _error_like_match(candidate: str, latest: str) -> bool:
    """Robust similarity check between stored evidence and latest error."""
    cand_norm = _normalize_error_text(candidate)
    latest_norm = _normalize_error_text(latest)
    if not cand_norm or not latest_norm:
        return False

    if cand_norm == latest_norm and len(cand_norm) >= 7:
        return True

    shorter, longer = (
        (cand_norm, latest_norm) if len(cand_norm) <= len(latest_norm) else (latest_norm, cand_norm)
    )
    if len(shorter) >= 12 and shorter in longer:
        return True

    overlap = _error_tokens(cand_norm) & _error_tokens(latest_norm)
    return len(overlap) >= 2


def _extract_hypothesis_error_candidates(evidence_for: list[str]) -> list[str]:
    """Extract likely error-signature strings from hypothesis evidence entries."""
    candidates: list[str] = []
    for ev in evidence_for:
        txt = ev.strip()
        lowered = txt.lower()
        for prefix in _ERROR_EVIDENCE_PREFIXES:
            if lowered.startswith(prefix):
                extracted = txt[len(prefix) :].strip()
                if extracted:
                    candidates.append(extracted)
                break
    return candidates
