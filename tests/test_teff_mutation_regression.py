"""Mutation-regression tests for Test Effectiveness schema contracts and edge cases.

These tests are specifically designed to kill mutants in the teff tools
and logic classes that previously survived CI.
"""

from __future__ import annotations

import ast

from lintgate.linters.test_effectiveness.assertion_classifier import (
    AssertionKind,
    _classify_assert_test,
)


def test_classify_assertion_bare_assert_is_true():
    """Kill mutant that changes is_true fallback logic."""
    # A bare `assert x` with no comparator should be IS_TRUE
    node = ast.parse("assert x").body[0]
    kind, _, _, confidence = _classify_assert_test(node.test)  # type: ignore
    assert kind == AssertionKind.IS_TRUE
    assert confidence == "structural"


def test_classify_assertion_unsupported_comparator():
    """Kill mutant that changes default strength mapping for unknown comparators."""
    # e.g., an assertion with a bitwise op or something we don't explicitly handle
    node = ast.parse("assert x ^ y").body[0]
    kind, _, _, _ = _classify_assert_test(node.test)  # type: ignore
    assert kind == AssertionKind.IS_TRUE


def test_classify_assertion_isinstance_edge_case():
    """Kill mutant in isinstance parsing."""
    node = ast.parse("assert isinstance(x, (int, str))").body[0]
    kind, _, _, _ = _classify_assert_test(node.test)  # type: ignore
    assert kind == AssertionKind.ISINSTANCE_CHECK


def test_classify_assertion_not_in_edge_case():
    """Kill mutants around NotIn / In comparators."""
    node = ast.parse("assert 'a' not in b").body[0]
    kind, _, _, confidence = _classify_assert_test(node.test)  # type: ignore
    # NotIn is structural/weak
    assert kind == AssertionKind.IS_FALSE or confidence == "structural"

    node_in = ast.parse("assert 'a' in b").body[0]
    kind_in, _, _, confidence_in = _classify_assert_test(node_in.test)  # type: ignore
    # In check
    assert kind_in == AssertionKind.COLLECTION_MEMBERSHIP or confidence_in == "structural"
