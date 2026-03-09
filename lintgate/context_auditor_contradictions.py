"""Backward-compatible shim — delegates to lintgate.context.auditor_contradictions."""

from .context.auditor_contradictions import *  # noqa: F401,F403

# Explicit re-exports of underscore-prefixed names (not covered by `*`).
from .context.auditor_contradictions import (  # noqa: F401,E402
    _detect_negation_pairs,
    _extract_keywords,
)
