"""Backward-compatibility shim — moved to lintgate.hooks.runtime_state."""

from lintgate.hooks.runtime_state import (  # noqa: F401
    RuntimeStateWriteMetric,
    derive_focus_intent,
    log_runtime_state_write_metric,
    mesh_finding_counts,
    mesh_symbol_blocker_count,
    refresh_runtime_state_lightweight,
    refresh_runtime_state_with_session,
    runtime_targets,
    write_dynamic_runtime_files,
)
