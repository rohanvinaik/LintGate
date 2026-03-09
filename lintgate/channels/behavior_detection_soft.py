"""Backward-compatibility shim — canonical location: behavior/detection_soft.py."""

from __future__ import annotations

from .behavior.detection_soft import (  # noqa: F401
    INTEGRATION_PATHS,
    INTEGRATION_VERIFY_BASH_PATTERNS,
    INTEGRATION_VERIFY_TOOLS,
    detect_consecutive_failures,
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
