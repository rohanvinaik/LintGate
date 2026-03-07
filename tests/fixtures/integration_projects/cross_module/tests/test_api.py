"""Tests for API module."""

from cross_module.api import process


def test_process_exact():
    assert process(2, 3) == 14
    assert process(0, 0) == 2
