"""Backward-compatible shim — delegates to lintgate.context.auditor_rule_coverage."""

from .context.auditor_rule_coverage import *  # noqa: F401,F403

# Explicit re-exports of underscore-prefixed names (not covered by `*`).
from .context.auditor_rule_coverage import (  # noqa: F401,E402
    _ARCHITECTURAL_CUE_RE,
    _COVERAGE_STOPWORDS,
    _SYNTACTIC_IDENTIFIER_PATTERNS,
    _count_syntactic_ids,
    _coverage_tokens,
    _directive_has_matching_rule,
    _has_syntactic_id,
    _is_regex_enforceable,
)
