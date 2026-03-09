"""Backward-compatibility shim — canonical location: behavior/_error_utils.py."""

from __future__ import annotations

from .behavior._error_utils import (  # noqa: F401
    _ERROR_EVIDENCE_PREFIXES,
    _ERROR_STOPWORDS,
    _error_like_match,
    _error_tokens,
    _extract_hypothesis_error_candidates,
    _normalize_error_text,
)
