"""Backward-compatible shim — delegates to lintgate.context.auditor_checks."""

from .context.auditor_checks import *  # noqa: F401,F403

# Explicit re-exports of underscore-prefixed names (not covered by `*`).
from .context.auditor_checks import (  # noqa: F401,E402
    _HEADER_RE,
    _count_syntactic_ids,
    _coverage_tokens,
    _detect_generated_patterns,
    _detect_negation_pairs,
    _directive_has_matching_rule,
    _extract_keywords,
    _find_bare_name_in_project,
    _has_syntactic_id,
    _is_regex_enforceable,
    _matches_generated_pattern,
)

# Private constants re-exported from sub-modules via auditor_checks.
from .context.auditor_path_refs import (  # noqa: F401,E402
    _BACKTICK_PATH_RE,
    _HF_MODEL_ID_RE,
    _PATH_EXTENSIONS,
    _SHELL_CMD_PREFIXES,
    _URL_PREFIXES,
)
from .context.auditor_rule_coverage import (  # noqa: F401,E402
    _ARCHITECTURAL_CUE_RE,
    _COVERAGE_STOPWORDS,
    _SYNTACTIC_IDENTIFIER_PATTERNS,
)
