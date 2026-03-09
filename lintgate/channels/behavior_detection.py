"""Backward-compatibility shim — canonical location: behavior/detection.py."""

from __future__ import annotations

from .behavior.detection import (  # noqa: F401
    INTEGRATION_PATHS,
    INTEGRATION_VERIFY_BASH_PATTERNS,
    INTEGRATION_VERIFY_TOOLS,
    _detect_amnesia_from_action_history,
    _detect_amnesia_from_error_memory,
    _detect_amnesia_from_hypotheses,
    detect_approach_cycling,
    detect_brute_force_escalation,
    detect_consecutive_failures,
    detect_failure_amnesia,
    detect_integration_verification_debt,
    detect_mass_delegation,
    detect_premature_action,
    detect_redundant_planning,
    detect_serial_discovery,
    detect_stale_model,
    detect_tool_repetition,
    detect_verification_debt,
)

__all__ = [
    # Hard signals
    "detect_approach_cycling",
    "detect_failure_amnesia",
    "detect_brute_force_escalation",
    # Hard signal helpers
    "_detect_amnesia_from_action_history",
    "_detect_amnesia_from_error_memory",
    "_detect_amnesia_from_hypotheses",
    # Soft signals
    "detect_premature_action",
    "detect_serial_discovery",
    "detect_tool_repetition",
    "detect_verification_debt",
    "detect_stale_model",
    "detect_mass_delegation",
    "detect_redundant_planning",
    "detect_integration_verification_debt",
    # Trigger-only
    "detect_consecutive_failures",
    # Constants
    "INTEGRATION_PATHS",
    "INTEGRATION_VERIFY_TOOLS",
    "INTEGRATION_VERIFY_BASH_PATTERNS",
]
