"""Backward-compatibility shim — moved to lintgate.hooks.controlplane."""

from lintgate.hooks.controlplane import (  # noqa: F401
    _SESSION_TELEMETRY_COUNTER_KEY,
    _SESSION_TELEMETRY_UPDATE_CAP,
    accumulate_session_telemetry,
    apply_behavior_delta,
    can_apply_session_telemetry,
    extract_finding_indexes,
    load_global_priors,
    mark_session_telemetry_applied,
    post_process_session,
    record_snapshot_behavior,
    refresh_runtime_after_run,
    resolve_event_model_key,
    run_constraint_proposer,
    save_run_details,
    select_telemetry_profile,
    session_telemetry_updates_used,
    setup_session_and_gate,
)
