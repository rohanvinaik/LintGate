"""Backward-compatible shim — delegates to lintgate.context.bootstrap."""

from .context.bootstrap import *  # noqa: F401,F403

# Explicit re-exports of underscore-prefixed names (not covered by `*`).
from .context.bootstrap import (  # noqa: F401,E402
    _NEGATIVE_CUE_RE,
    _NEVER_OVERWRITE,
    _NO_THEORY,
    _PERF_ANTI_PATTERN_CUE,
    _build_quick_wins,
    _collect_dead_path_review_items,
    _collect_directive_review_items,
    _collect_facet_fallback_items,
    _collect_machine_rule_lines,
    _collect_review_items,
    _extract_dead_paths,
    _facet_or_fallback,
    _migrate_to_managed_sections,
    _model_biased_guardrails,
    _normalize_sentence,
    _parse_managed_sections,
    _project_metadata,
    _read_readme_description,
    _recommended_commands,
    _render_agents_md,
    _render_claude_md,
    _render_inquiry_md,
    _render_theory_rules_md,
    _resolve_model_profile,
    _rule_to_line,
    _select_actionable_anti_patterns,
    _summarize_audit,
    _write_drafts,
)
