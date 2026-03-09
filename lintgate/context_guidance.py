"""Backward-compatible shim — delegates to lintgate.context.guidance."""

from .context.guidance import *  # noqa: F401,F403

# Explicit re-exports of underscore-prefixed names (not covered by `*`).
from .context.guidance import (  # noqa: F401,E402
    _BACKTICK_RE,
    _BULLET_PREFIX_RE,
    _CONTEXT_DIRS,
    _CONTEXT_FILENAMES,
    _FORBID_PREFIX,
    _REQUIRE_PREFIX,
    _RULE_PREFIX,
    _classify_directive,
    _clean_line,
    _dedupe_text,
    _extract_path_hints,
    _flatten,
    _infer_rules_from_directives,
    _is_skippable_line,
    _parse_context_file,
    _parse_rule_line,
    _path_hint_matches,
    _resolve_files,
    _safe_relpath,
)
