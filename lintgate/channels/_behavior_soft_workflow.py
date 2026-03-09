"""Backward-compatibility shim — canonical location: behavior/_soft_workflow.py."""

from __future__ import annotations

from .behavior._soft_workflow import (  # noqa: F401
    detect_mass_delegation,
    detect_redundant_planning,
    detect_stale_model,
)
