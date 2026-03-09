"""Backward-compatibility shim — canonical location: behavior/_soft_verification.py."""

from __future__ import annotations

from .behavior._soft_verification import (  # noqa: F401
    INTEGRATION_PATHS,
    INTEGRATION_VERIFY_BASH_PATTERNS,
    INTEGRATION_VERIFY_TOOLS,
    detect_integration_verification_debt,
    detect_serial_discovery,
    detect_verification_debt,
)
