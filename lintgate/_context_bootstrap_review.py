"""Backward-compatible shim — delegates to lintgate.context._bootstrap_review."""

from .context._bootstrap_review import *  # noqa: F401,F403

# Explicit re-exports of underscore-prefixed names (not covered by `*`).
from .context._bootstrap_review import (  # noqa: F401,E402
    _NO_THEORY,
    _collect_dead_path_review_items,
    _collect_directive_review_items,
    _collect_facet_fallback_items,
    _collect_review_items,
    _extract_dead_paths,
)
