"""Tests for lintgate.linters.performance_checks.perf007_numerical_loop."""

from __future__ import annotations

import ast

from lintgate.linters.performance_checks.perf007_numerical_loop import (
    _has_arithmetic_on_var,
    _is_small_range_bound,
    _references_var,
    check_numerical_loop,
)


def _parse_range_call(code: str) -> ast.Call:
    """Parse a range(...) call from source code."""
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            return node
    raise ValueError("No Call node found")


# ── _references_var ──────────────────────────────────────────────────


class TestReferencesVar:
    def test_direct_name_match(self):
        tree = ast.parse("i")
        name_node = tree.body[0].value  # type: ignore[attr-defined]
        assert _references_var(name_node, "i") is True

    def test_direct_name_no_match(self):
        tree = ast.parse("j")
        name_node = tree.body[0].value  # type: ignore[attr-defined]
        assert _references_var(name_node, "i") is False

    def test_subscript_with_matching_index(self):
        tree = ast.parse("arr[i]")
        sub_node = tree.body[0].value  # type: ignore[attr-defined]
        assert _references_var(sub_node, "i") is True

    def test_subscript_with_non_matching_index(self):
        tree = ast.parse("arr[j]")
        sub_node = tree.body[0].value  # type: ignore[attr-defined]
        assert _references_var(sub_node, "i") is False


# ── _is_small_range_bound ────────────────────────────────────────────


class TestIsSmallRangeBound:
    def test_range_50_is_small(self):
        call = _parse_range_call("range(50)")
        assert _is_small_range_bound(call) is True

    def test_range_100_is_not_small(self):
        call = _parse_range_call("range(100)")
        assert _is_small_range_bound(call) is False

    def test_range_0_1000_is_not_small(self):
        call = _parse_range_call("range(0, 1000)")
        assert _is_small_range_bound(call) is False

    def test_range_0_50_is_small(self):
        call = _parse_range_call("range(0, 50)")
        assert _is_small_range_bound(call) is True

    def test_range_with_variable_bound_is_not_small(self):
        call = _parse_range_call("range(n)")
        assert _is_small_range_bound(call) is False


# ── _has_arithmetic_on_var ───────────────────────────────────────────


class TestHasArithmeticOnVar:
    def test_addition_on_loop_var(self):
        tree = ast.parse("x = i + 1")
        body = tree.body
        assert _has_arithmetic_on_var(body, "i") is True

    def test_no_arithmetic(self):
        tree = ast.parse("print(i)")
        body = tree.body
        assert _has_arithmetic_on_var(body, "i") is False

    def test_arithmetic_on_different_var(self):
        tree = ast.parse("x = j * 2")
        body = tree.body
        assert _has_arithmetic_on_var(body, "i") is False


# ── check_numerical_loop ─────────────────────────────────────────────


class TestCheckNumericalLoop:
    def test_flags_large_arithmetic_loop(self):
        code = """\
for i in range(10000):
    x = i * 2 + 1
"""
        tree = ast.parse(code)
        issues = list(check_numerical_loop(tree, "test.py"))
        assert len(issues) == 1
        assert issues[0].kind == "PERF007"
        assert issues[0].line == 1
        assert issues[0].evidence["loop_var"] == "i"

    def test_skips_small_range(self):
        code = """\
for i in range(10):
    x = i * 2
"""
        tree = ast.parse(code)
        issues = list(check_numerical_loop(tree, "test.py"))
        assert issues == []

    def test_skips_when_numpy_imported(self):
        code = """\
import numpy
for i in range(10000):
    x = i * 2
"""
        tree = ast.parse(code)
        issues = list(check_numerical_loop(tree, "test.py"))
        assert issues == []

    def test_skips_non_arithmetic_loop(self):
        code = """\
for i in range(10000):
    print(i)
"""
        tree = ast.parse(code)
        issues = list(check_numerical_loop(tree, "test.py"))
        assert issues == []
