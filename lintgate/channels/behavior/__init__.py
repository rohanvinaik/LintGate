"""Behavior subpackage — behavioral drift detection for the supervision mesh.

Re-exports all public names from the behavior system modules so that
callers can import from the old paths (via shims) or from the new
canonical locations:

    from lintgate.channels.behavior.channel import BehaviorChannel
    from lintgate.channels.behavior.detection import detect_approach_cycling
    from lintgate.channels.behavior.scoring import SignalCoordinator
"""

from __future__ import annotations

# Channel
from .channel import BehaviorChannel

# Detection facade (re-exports hard + soft)
from .detection import (
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

# Scoring + theory grounding
from .scoring import (
    _THEORY_CODA_MAX_CHARS,
    SIGNAL_THEORY_MAP,
    IntentBiasScorer,
    SignalCoordinator,
    _ground_finding_in_theory,
)

__all__ = [
    "BehaviorChannel",
    # Detection
    "detect_approach_cycling",
    "detect_failure_amnesia",
    "detect_brute_force_escalation",
    "detect_premature_action",
    "detect_serial_discovery",
    "detect_tool_repetition",
    "detect_verification_debt",
    "detect_stale_model",
    "detect_mass_delegation",
    "detect_redundant_planning",
    "detect_integration_verification_debt",
    "detect_consecutive_failures",
    "_detect_amnesia_from_action_history",
    "_detect_amnesia_from_error_memory",
    "_detect_amnesia_from_hypotheses",
    # Constants
    "INTEGRATION_PATHS",
    "INTEGRATION_VERIFY_TOOLS",
    "INTEGRATION_VERIFY_BASH_PATTERNS",
    # Scoring
    "IntentBiasScorer",
    "SignalCoordinator",
    "SIGNAL_THEORY_MAP",
    "_THEORY_CODA_MAX_CHARS",
    "_ground_finding_in_theory",
]
