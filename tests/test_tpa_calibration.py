"""Tests for TPA calibration — point counting, sigma calibration, confidence."""

from __future__ import annotations

import ast
import textwrap

from lintgate.specification.tpa_calibration import calibrate_sigma, compute_tpa_points


def _parse_func(code: str) -> ast.FunctionDef:
    tree = ast.parse(textwrap.dedent(code))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            return node
    raise ValueError("No FunctionDef found")


class TestTPAPointCounting:
    def test_simple_function(self):
        func = _parse_func("""
        def add(a, b):
            return a + b
        """)
        points = compute_tpa_points(func)
        assert points >= 2  # 2 params

    def test_branching_function(self):
        func = _parse_func("""
        def check(x):
            if x > 0:
                return x
            else:
                return -x
        """)
        points = compute_tpa_points(func)
        assert points >= 3  # 1 param + 1 if + returns

    def test_try_except(self):
        func = _parse_func("""
        def safe_div(a, b):
            try:
                return a / b
            except ZeroDivisionError:
                return 0
        """)
        points = compute_tpa_points(func)
        assert points >= 4  # 2 params + try + except

    def test_loop_function(self):
        func = _parse_func("""
        def total(items):
            result = 0
            for item in items:
                result += item
            return result
        """)
        points = compute_tpa_points(func)
        assert points >= 2  # param + for

    def test_empty_function(self):
        func = _parse_func("""
        def noop():
            pass
        """)
        points = compute_tpa_points(func)
        assert points >= 0


class TestSigmaCalibration:
    def test_agreement_high_confidence(self):
        result = calibrate_sigma(decision_tree_sigma=5, tpa_points=6)
        assert result.tpa_confidence > 0.5

    def test_disagreement_low_confidence(self):
        result = calibrate_sigma(decision_tree_sigma=2, tpa_points=20)
        assert result.tpa_confidence < 0.5

    def test_calibrated_sigma_between(self):
        result = calibrate_sigma(decision_tree_sigma=4, tpa_points=8)
        tpa_sigma = result.tpa_sigma
        # Calibrated should be influenced by both
        assert result.tpa_points == 8
        assert tpa_sigma > 0

    def test_zero_sigma(self):
        result = calibrate_sigma(decision_tree_sigma=0, tpa_points=0)
        assert result.tpa_sigma == 0
        assert result.tpa_confidence >= 0.0
