"""Backward-compatibility shim — moved to lintgate.hooks.habit."""

from lintgate.hooks.habit import (  # noqa: F401
    check_habit_api_calibration,
    record_behavior_event,
    record_habit_event_lightweight,
    try_habit_compaction,
)
