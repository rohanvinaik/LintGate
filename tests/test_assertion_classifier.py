"""Tests for assertion_classifier — assertion kind detection."""

from __future__ import annotations

from lintgate.linters.test_effectiveness.assertion_classifier import classify_test_file
from lintgate.linters.test_effectiveness.types import AssertionKind


def test_equality_assertion():
    """Detect assert x == y as equality."""
    source = """
def test_foo():
    result = foo()
    assert result == 42
"""
    result = classify_test_file(source)
    assert "test_foo" in result
    assertions = result["test_foo"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.EQUALITY
    assert assertions[0].strength == 0.9


def test_is_none_assertion():
    """Detect assert x is None."""
    source = """
def test_foo():
    assert result is None
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.IS_NONE
    assert assertions[0].strength == 0.2


def test_is_not_none_assertion():
    """Detect assert x is not None."""
    source = """
def test_foo():
    assert result is not None
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.IS_NOT_NONE
    assert assertions[0].strength == 0.3


def test_bare_assert_is_true():
    """Detect bare assert x as IS_TRUE."""
    source = """
def test_foo():
    assert result
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.IS_TRUE
    assert assertions[0].strength == 0.2


def test_isinstance_check():
    """Detect assert isinstance(x, T)."""
    source = """
def test_foo():
    assert isinstance(result, dict)
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.ISINSTANCE_CHECK
    assert assertions[0].strength == 0.3


def test_comparison_assertion():
    """Detect assert x > y."""
    source = """
def test_foo():
    assert result > 0
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.COMPARISON
    assert assertions[0].strength == 0.85


def test_length_check():
    """Detect assert len(x) == n."""
    source = """
def test_foo():
    assert len(result) == 3
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.LENGTH_CHECK
    assert assertions[0].strength == 0.8


def test_collection_membership():
    """Detect assert x in collection."""
    source = """
def test_foo():
    assert "key" in result
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.COLLECTION_MEMBERSHIP
    assert assertions[0].strength == 0.8


def test_pytest_raises():
    """Detect pytest.raises context manager."""
    source = """
import pytest

def test_foo():
    with pytest.raises(ValueError):
        foo(None)
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.RAISES
    assert assertions[0].strength == 0.7


def test_hypothesis_given_decorator():
    """Detect @given decorator as hypothesis property."""
    source = """
from hypothesis import given
import hypothesis.strategies as st

@given(st.integers())
def test_foo(x):
    assert foo(x) == foo(x)
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    # Should have 1 equality + 1 hypothesis_property
    kinds = {a.kind for a in assertions}
    assert AssertionKind.HYPOTHESIS_PROPERTY in kinds
    assert AssertionKind.EQUALITY in kinds


def test_not_assertion_is_false():
    """Detect assert not x as IS_FALSE."""
    source = """
def test_foo():
    assert not result
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.IS_FALSE


def test_inequality_assertion():
    """Detect assert x != y."""
    source = """
def test_foo():
    assert result != 0
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.INEQUALITY
    assert assertions[0].strength == 0.7


def test_multiple_assertions():
    """Multiple assertions in one test function are all classified."""
    source = """
def test_foo():
    result = foo()
    assert result is not None
    assert result == 42
    assert len(result.items) == 3
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 3
    kinds = [a.kind for a in assertions]
    assert kinds == [AssertionKind.IS_NOT_NONE, AssertionKind.EQUALITY, AssertionKind.LENGTH_CHECK]


def test_class_method_test():
    """Test functions inside test classes are detected."""
    source = """
class TestFoo:
    def test_bar(self):
        assert result == 42
"""
    result = classify_test_file(source)
    assert "TestFoo.test_bar" in result
    assertions = result["TestFoo.test_bar"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.EQUALITY


def test_non_test_functions_skipped():
    """Non-test functions are not analyzed."""
    source = """
def helper():
    assert True

def test_real():
    assert 1 == 1
"""
    result = classify_test_file(source)
    assert "helper" not in result
    assert "test_real" in result


def test_syntax_error_returns_empty():
    """Syntax errors return empty dict."""
    result = classify_test_file("def test_foo( broken syntax")
    assert result == {}


def test_range_check():
    """Detect chained comparison as range check."""
    source = """
def test_foo():
    assert 0 <= result <= 100
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.RANGE_CHECK
    assert assertions[0].strength == 0.9


def test_regex_match():
    """Detect assert re.match(...)."""
    source = """
import re

def test_foo():
    assert re.match(r"^\\d+$", result)
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.REGEX_MATCH
    assert assertions[0].strength == 0.7
