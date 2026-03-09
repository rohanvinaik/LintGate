"""Backward-compatible shim — delegates to lintgate.context.auditor."""

from .context.auditor import *  # noqa: F401,F403

# Explicit re-exports of underscore-prefixed names (not covered by `*`).
from .context.auditor import (  # noqa: F401,E402
    _REQUIRED_FACETS,
    _build_recommendation,
    _check_contradictions,
    _check_enforceable_rules,
    _check_length,
    _check_path_references,
    _check_rule_coverage,
    _check_staleness,
    _check_theory_facets,
    _check_theory_staleness,
    _extract_path_refs,
    _find_dead_paths,
    _is_regex_enforceable,
)
