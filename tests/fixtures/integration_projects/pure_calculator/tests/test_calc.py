"""Tests for pure calculator with exact-value assertions."""

from pure_calculator.calc import add, factorial


def test_add_exact():
    assert add(2, 3) == 5
    assert add(0, 0) == 0
    assert add(-1, 1) == 0


def test_factorial_exact():
    assert factorial(0) == 1
    assert factorial(5) == 120
    assert factorial(1) == 1
