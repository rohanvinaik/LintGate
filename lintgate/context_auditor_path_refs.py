"""Backward-compatible shim — delegates to lintgate.context.auditor_path_refs."""

from .context.auditor_path_refs import *  # noqa: F401,F403

# Explicit re-exports of underscore-prefixed names (not covered by `*`).
from .context.auditor_path_refs import (  # noqa: F401,E402
    _BACKTICK_PATH_RE,
    _HF_MODEL_ID_RE,
    _PATH_EXTENSIONS,
    _SHELL_CMD_PREFIXES,
    _URL_PREFIXES,
    _URL_SCHEME_RE,
    _detect_generated_patterns,
    _find_bare_name_in_project,
    _matches_generated_pattern,
)
