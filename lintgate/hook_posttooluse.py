"""Backward-compatibility shim — moved to lintgate.hooks.posttooluse.

Entry point (main) and all public/private names are re-exported so that
existing imports, pyproject.toml console_scripts, and test patches continue
to work without changes.
"""

import contextlib

# Re-export the underscore-prefixed backward-compat aliases that tests import
# Re-export top-level imports used by _run_legacy_pipeline and others
from lintgate.hooks.posttooluse import (  # noqa: F401
    _arbitrate_output,
    _build_channels,
    _can_apply_session_telemetry,
    _detect_edit_functions,
    _detect_new_functions,
    _detect_write_functions,
    _exit_clean,
    _extract_func_name,
    _fallback_config,
    _finalize_report,
    _log_controlplane_metric,
    _mark_session_telemetry_applied,
    _normalize_fields,
    _parse_hook_input,
    _record_habit_event_lightweight,
    _refresh_runtime_state_lightweight,
    _resolve_event_model_key,
    _run_controlplane,
    _run_legacy_pipeline,
    _select_telemetry_profile,
    _session_telemetry_updates_used,
    classify_change,
    format_report,
    load_config,
    load_last_run,
    log_metric,
    main,
    run_linters,
    save_run,
    select_tier,
    update_issue_memory,
)

with contextlib.suppress(ImportError):
    from lintgate.hooks.posttooluse import aggregate_results, build_registry  # noqa: F401
