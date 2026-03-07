"""Mutable state module for integration testing."""

_db = {}


def clear():
    """Clear all state."""
    _db.clear()
