"""Backward-compatibility shim — moved to lintgate.hooks.posttooluse.

Entry point (main) and all public/private names are re-exported so that
existing imports, pyproject.toml console_scripts, and test patches continue
to work without changes.
"""

import contextlib

from lintgate.hooks.arbitration import arbitrate_output as _arbitrate_output  # noqa: F401
from lintgate.hooks.controlplane import (
    can_apply_session_telemetry as _can_apply_session_telemetry,  # noqa: F401, E501
)
from lintgate.hooks.controlplane import (
    mark_session_telemetry_applied as _mark_session_telemetry_applied,  # noqa: F401, E501
)
from lintgate.hooks.controlplane import (
    resolve_event_model_key as _resolve_event_model_key,  # noqa: F401
)
from lintgate.hooks.controlplane import (
    select_telemetry_profile as _select_telemetry_profile,  # noqa: F401
)
from lintgate.hooks.controlplane import (
    session_telemetry_updates_used as _session_telemetry_updates_used,  # noqa: F401, E501
)
from lintgate.hooks.habit import (
    record_habit_event_lightweight as _record_habit_event_lightweight,  # noqa: F401
)
from lintgate.hooks.posttooluse import (  # noqa: F401
    _build_channels,
    _detect_edit_functions,
    _detect_new_functions,
    _detect_write_functions,
    _exit_clean,
    _extract_func_name,
    _fallback_config,
    _finalize_report,
    _log_controlplane_metric,
    _normalize_fields,
    _parse_hook_input,
    _run_controlplane,
    _run_legacy_pipeline,
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
from lintgate.hooks.runtime_state import (
    refresh_runtime_state_lightweight as _refresh_runtime_state_lightweight,  # noqa: F401, E501
)

with contextlib.suppress(ImportError):
    from lintgate.hooks.posttooluse import aggregate_results, build_registry  # noqa: F401
