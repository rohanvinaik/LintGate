"""Backward-compatibility shim — canonical location: behavior/_soft_action.py."""

from __future__ import annotations

from .behavior._soft_action import (  # noqa: F401
    detect_consecutive_failures,
    detect_premature_action,
    detect_tool_repetition,
)
