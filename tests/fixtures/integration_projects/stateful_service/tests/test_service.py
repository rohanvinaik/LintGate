"""Tests for stateful service with structural assertions."""

from stateful_service.service import get_record, save_record


def test_save_and_get():
    result = save_record("key1", "value1")
    assert result is not None
    assert get_record("key1") is not None
