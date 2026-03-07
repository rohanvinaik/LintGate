"""Tests for test design signal extraction — BVA, EQ, decision rules, cause-effect."""

from __future__ import annotations

import ast
import textwrap

from lintgate.specification.test_design_signals import extract_all


def _parse_func(code: str) -> ast.FunctionDef:
    tree = ast.parse(textwrap.dedent(code))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            return node
    raise ValueError("No FunctionDef found")


class TestBoundaryPoints:
    def test_comparison_with_literal(self):
        func = _parse_func("""
        def check(x):
            if x > 10:
                return True
            return False
        """)
        signals = extract_all(func)
        assert signals.boundary_points >= 1

    def test_multiple_comparisons(self):
        func = _parse_func("""
        def validate(x, y):
            if x > 0 and y < 100:
                return True
            if x == 0:
                return None
            return False
        """)
        signals = extract_all(func)
        assert signals.boundary_points >= 2

    def test_no_comparisons(self):
        func = _parse_func("""
        def identity(x):
            return x
        """)
        signals = extract_all(func)
        assert signals.boundary_points == 0


class TestEquivalencePartitions:
    def test_isinstance_check(self):
        func = _parse_func("""
        def process(x):
            if isinstance(x, int):
                return x + 1
            if isinstance(x, str):
                return len(x)
            return None
        """)
        signals = extract_all(func)
        assert signals.equivalence_partitions >= 2

    def test_none_check(self):
        func = _parse_func("""
        def safe_len(x):
            if x is None:
                return 0
            return len(x)
        """)
        signals = extract_all(func)
        assert signals.equivalence_partitions >= 1

    def test_no_type_checks(self):
        func = _parse_func("""
        def add(a, b):
            return a + b
        """)
        signals = extract_all(func)
        assert signals.equivalence_partitions == 0


class TestDecisionRules:
    def test_nested_if_elif(self):
        func = _parse_func("""
        def classify(x, y):
            if x > 0:
                if y > 0:
                    return "both_positive"
                else:
                    return "x_positive"
            elif x == 0:
                return "zero"
            else:
                return "negative"
        """)
        signals = extract_all(func)
        assert signals.decision_rule_count >= 2

    def test_simple_function(self):
        func = _parse_func("""
        def identity(x):
            return x
        """)
        signals = extract_all(func)
        assert signals.decision_rule_count == 0


class TestPredicateEffectLinks:
    def test_if_with_return_and_raise(self):
        func = _parse_func("""
        def validate(x):
            if x < 0:
                raise ValueError("negative")
            if x > 100:
                return 100
            return x
        """)
        signals = extract_all(func)
        assert signals.predicate_effect_links >= 2

    def test_no_predicates(self):
        func = _parse_func("""
        def constant():
            return 42
        """)
        signals = extract_all(func)
        assert signals.predicate_effect_links == 0


class TestExtractAll:
    def test_returns_test_design_signals(self):
        func = _parse_func("def f(x): return x")
        signals = extract_all(func)
        assert hasattr(signals, "boundary_points")
        assert hasattr(signals, "equivalence_partitions")
        assert hasattr(signals, "decision_rule_count")
        assert hasattr(signals, "predicate_effect_links")

    def test_complex_function(self):
        func = _parse_func("""
        def process(data, mode):
            if data is None:
                raise ValueError("no data")
            if isinstance(data, list):
                if len(data) > 100:
                    data = data[:100]
                if mode == "sum":
                    return sum(data)
                elif mode == "avg":
                    return sum(data) / len(data)
            return data
        """)
        signals = extract_all(func)
        assert signals.boundary_points >= 1
        assert signals.equivalence_partitions >= 1
        assert signals.predicate_effect_links >= 1
