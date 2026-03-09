"""Soft behavioral signal detectors — re-export facade.

All detectors are split across sub-modules for module size compliance:
- _soft_action: premature_action, tool_repetition, consecutive_failures
- _soft_verification: serial_discovery, verification_debt, integration_verification_debt
- _soft_workflow: stale_model, mass_delegation, redundant_planning

This module re-exports all public names for backward compatibility.
"""

from __future__ import annotations

# Action/execution pattern detectors
from ._soft_action import (
    detect_consecutive_failures,
    detect_premature_action,
    detect_tool_repetition,
)

# Verification debt detectors + constants
from ._soft_verification import (
    INTEGRATION_PATHS,
    INTEGRATION_VERIFY_BASH_PATTERNS,
    INTEGRATION_VERIFY_TOOLS,
    detect_integration_verification_debt,
    detect_serial_discovery,
    detect_verification_debt,
)

# Workflow anti-pattern detectors
from ._soft_workflow import (
    detect_mass_delegation,
    detect_redundant_planning,
    detect_stale_model,
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
