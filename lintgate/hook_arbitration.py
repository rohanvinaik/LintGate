"""Backward-compatibility shim — moved to lintgate.hooks.arbitration."""

from lintgate.hooks.arbitration import (  # noqa: F401
    arbitrate_output,
    build_pulse_delta,
    extract_habit_signals,
    inject_dispositions,
    resolve_verbosity,
    should_emit,
    should_force_emit,
)
