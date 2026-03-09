"""Backward-compatibility shim — canonical location: behavior/detection_hard.py."""

from __future__ import annotations

from .behavior.detection_hard import (  # noqa: F401
    _detect_amnesia_from_action_history,
    _detect_amnesia_from_error_memory,
    _detect_amnesia_from_hypotheses,
    detect_approach_cycling,
    detect_brute_force_escalation,
    detect_failure_amnesia,
)
