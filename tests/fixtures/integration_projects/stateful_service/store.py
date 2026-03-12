"""Mutable state module for integration testing."""

from typing import Any

_db: dict[str, Any] = {}


def clear():
    """Clear all state."""
    _db.clear()
