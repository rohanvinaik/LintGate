"""Backward-compatibility shim — canonical location: behavior/scoring.py."""

from __future__ import annotations

from .behavior.scoring import (  # noqa: F401
    _BIAS_CAP,
    _ERROR_EVIDENCE_PREFIXES,
    _ERROR_STOPWORDS,
    _THEORY_CODA_MAX_CHARS,
    SIGNAL_THEORY_MAP,
    IntentBiasScorer,
    SignalCoordinator,
    _error_like_match,
    _error_tokens,
    _extract_hypothesis_error_candidates,
    _ground_finding_in_theory,
    _normalize_error_text,
)
