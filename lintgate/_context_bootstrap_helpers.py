"""Backward-compatible shim — delegates to lintgate.context._bootstrap_helpers."""

from .context._bootstrap_helpers import *  # noqa: F401,F403

# Explicit re-exports of underscore-prefixed names (not covered by `*`).
from .context._bootstrap_helpers import (  # noqa: F401,E402
    _NEGATIVE_CUE_RE,
    _NO_THEORY,
    _PERF_ANTI_PATTERN_CUE,
    _build_quick_wins,
    _collect_machine_rule_lines,
    _project_metadata,
    _read_readme_description,
    _recommended_commands,
    _rule_to_line,
    _select_actionable_anti_patterns,
)
