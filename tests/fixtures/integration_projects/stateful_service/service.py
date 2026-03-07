"""Stateful service with side effects for integration testing."""

from stateful_service.store import _db


def save_record(key, value):
    """Save a record to the store (side effect: mutates global state)."""
    _db[key] = value
    return True


def get_record(key):
    """Get a record from the store (side effect: reads global state)."""
    return _db.get(key)
