"""Tests for assertion_classifier — assertion kind detection."""

from __future__ import annotations

import ast
import os
import tempfile

from lintgate.linters.test_effectiveness.assertion_classifier import (
    _classify_compare,
    _get_name,
    _unparse_expr,
    classify_test_file,
    classify_test_file_from_path,
)
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


# --- Tests for uncovered lines ---


def test_get_name_unsupported_node_returns_empty():
    """_get_name returns '' for nodes that are not Name or Attribute (line 24)."""
    # ast.Constant is neither ast.Name nor ast.Attribute
    node = ast.Constant(value=42)
    assert _get_name(node) == ""


def test_unparse_expr_fallback_on_failure():
    """_unparse_expr falls back to _get_name when ast.unparse raises (lines 31-32)."""
    # Create a malformed node that causes ast.unparse to fail
    node = ast.BoolOp()
    # Remove required fields to make unparse fail
    node.op = ast.And()
    # Don't set 'values' — ast.unparse will raise
    result = _unparse_expr(node)
    # Falls through to _get_name which returns "" for BoolOp
    assert result == ""


def test_classify_compare_empty_ops():
    """_classify_compare with empty ops list returns IS_TRUE (line 57)."""
    node = ast.Compare(
        left=ast.Name(id="x"),
        ops=[],
        comparators=[],
    )
    kind = _classify_compare(node)
    assert kind == AssertionKind.IS_TRUE


def test_classify_compare_is_non_none():
    """assert x is y (where y is not None) returns IS_TRUE (lines 81-82)."""
    source = """
def test_foo():
    assert x is sentinel
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.IS_TRUE


def test_classify_compare_is_not_non_none():
    """assert x is not y (where y is not None) returns IS_TRUE (lines 83-84)."""
    source = """
def test_foo():
    assert x is not sentinel
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.IS_TRUE


def test_classify_compare_unknown_op_fallback():
    """_classify_compare with an unrecognized op falls back to IS_TRUE (line 86)."""
    # Construct a Compare node with a non-standard op type via subclassing
    # MatMult is not a comparison op, but we can use ast.BitOr placeholder
    # Instead, use a direct call with a patched ops list
    node = ast.Compare(
        left=ast.Name(id="x"),
        ops=[ast.MatMult()],  # type: ignore[list-item] # not a valid cmpop
        comparators=[ast.Name(id="y")],
    )
    kind = _classify_compare(node)
    assert kind == AssertionKind.IS_TRUE


def test_assert_not_isinstance():
    """assert not isinstance(x, T) is classified as ISINSTANCE_CHECK (lines 105-107)."""
    source = """
def test_foo():
    assert not isinstance(result, str)
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.ISINSTANCE_CHECK


def test_assert_callable():
    """assert callable(x) is classified as ISINSTANCE_CHECK (lines 117-118)."""
    source = """
def test_foo():
    assert callable(my_func)
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.ISINSTANCE_CHECK
    assert "my_func" in assertions[0].target_expression


def test_assert_any_all():
    """assert any(...) and assert all(...) are COLLECTION_MEMBERSHIP (lines 123-124)."""
    source = """
def test_foo():
    assert any(x > 0 for x in items)
    assert all(x > 0 for x in items)
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 2
    assert assertions[0].kind == AssertionKind.COLLECTION_MEMBERSHIP
    assert assertions[1].kind == AssertionKind.COLLECTION_MEMBERSHIP


def test_assert_true_constant():
    """assert True literal is classified as IS_TRUE (lines 130-131)."""
    source = """
def test_foo():
    assert True
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.IS_TRUE
    assert assertions[0].target_expression == "True"


def test_assert_false_constant():
    """assert False literal is classified as IS_FALSE (lines 132)."""
    source = """
def test_foo():
    assert False
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.IS_FALSE
    assert assertions[0].target_expression == "False"


def test_has_given_decorator_attribute_form():
    """@hypothesis.given (no call parens) detected via ast.Attribute branch (lines 187-189)."""
    source = """
import hypothesis

@hypothesis.given
def test_foo(x):
    assert x == x
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    kinds = {a.kind for a in assertions}
    assert AssertionKind.HYPOTHESIS_PROPERTY in kinds


def test_has_given_decorator_bare_name():
    """Bare @given (not a call) detected via ast.Name branch (lines 190-191)."""
    source = """
from hypothesis import given

@given
def test_foo(x):
    assert x == x
"""
    result = classify_test_file(source)
    assertions = result["test_foo"]
    kinds = {a.kind for a in assertions}
    assert AssertionKind.HYPOTHESIS_PROPERTY in kinds


def test_async_test_function():
    """Async test functions are analyzed via visit_AsyncFunctionDef (line 214)."""
    source = """
async def test_async_check():
    result = await some_coroutine()
    assert result == 42
"""
    result = classify_test_file(source)
    assert "test_async_check" in result
    assertions = result["test_async_check"]
    assert len(assertions) == 1
    assert assertions[0].kind == AssertionKind.EQUALITY


def test_classify_test_file_from_path_nonexistent():
    """classify_test_file_from_path returns {} on OSError (lines 273-274)."""
    result = classify_test_file_from_path("/nonexistent/path/to/test_file.py")
    assert result == {}


def test_classify_test_file_from_path_success():
    """classify_test_file_from_path reads and classifies a real file."""
    source = """
def test_hello():
    assert 1 == 1
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(source)
        f.flush()
        path = f.name
    try:
        result = classify_test_file_from_path(path)
        assert "test_hello" in result
        assert result["test_hello"][0].kind == AssertionKind.EQUALITY
    finally:
        os.unlink(path)
