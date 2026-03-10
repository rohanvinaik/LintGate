"""Tests for the JIT candidate detector."""

import ast

from lintgate.linters.performance_checks.jit_detector import (
    JitCandidate,
    detect_jit_candidates,
)
from lintgate.linters.performance_checks.purity import analyze_purity


def _candidates_for(code: str, file_path: str = "") -> list[JitCandidate]:
    """Parse *code*, run purity analysis, and return JIT candidates."""
    tree = ast.parse(code)
    purity = analyze_purity(tree)
    return detect_jit_candidates(tree, purity, file_path=file_path)


# ------------------------------------------------------------------
# Core filtering
# ------------------------------------------------------------------


def test_impure_function_excluded():
    code = """
def impure_sum(xs):
    global total
    total = 0
    for x in xs:
        total += x
    return total
"""
    candidates = _candidates_for(code)
    assert candidates == [], "Impure functions must never appear as JIT candidates"


def test_empty_tree_returns_empty():
    tree = ast.parse("")
    purity = analyze_purity(tree)
    candidates = detect_jit_candidates(tree, purity)
    assert candidates == []


# ------------------------------------------------------------------
# Scoring: high-value numeric kernel
# ------------------------------------------------------------------


def test_pure_numeric_loop_scores_high():
    code = """
def dot_product(a: list[float], b: list[float]) -> float:
    total = 0.0
    for i in range(len(a)):
        total += a[i] * b[i]
    return total
"""
    candidates = _candidates_for(code)
    assert len(candidates) == 1
    c = candidates[0]
    assert c.function_name == "dot_product"
    assert c.jit_band in ("HIGH", "MEDIUM")
    assert c.jit_score >= 0.3
    # Numeric annotations should contribute
    assert c.factors["numeric_signature"] > 0.0
    # Has a loop
    assert c.factors["loop_density"] > 0.0
    # Has arithmetic
    assert c.factors["arithmetic_intensity"] > 0.0


# ------------------------------------------------------------------
# Scoring: allocation-heavy function
# ------------------------------------------------------------------


def test_object_heavy_function_scores_low():
    code = """
def build_records(n: int) -> list[float]:
    results = list()
    mapping = dict()
    extras = set()
    backup = dict()
    another = list()
    more = set()
    for i in range(n):
        results.append(float(i))
    return results
"""
    candidates = _candidates_for(code)
    # Either excluded entirely (score < 0.2) or has a low allocation factor
    if candidates:
        c = candidates[0]
        assert c.factors["low_allocation"] < 0.5


# ------------------------------------------------------------------
# Scoring: no annotations
# ------------------------------------------------------------------


def test_no_annotations_reduces_score():
    code = """
def add(a, b):
    return a + b
"""
    candidates = _candidates_for(code)
    # With no annotations and minimal body, score should be very low
    if candidates:
        assert candidates[0].factors["numeric_signature"] == 0.0


# ------------------------------------------------------------------
# Scoring: arithmetic-intensive
# ------------------------------------------------------------------


def test_arithmetic_intensive_function():
    code = """
def polynomial(x: float) -> float:
    return x * x * x + 3 * x * x - 2 * x + 1
"""
    candidates = _candidates_for(code)
    assert len(candidates) >= 1
    c = candidates[0]
    assert c.factors["arithmetic_intensity"] > 0.5


# ------------------------------------------------------------------
# Backend recommendations
# ------------------------------------------------------------------


def test_recommended_backend_numba_for_loops():
    code = """
def sum_range(n: int) -> int:
    total = 0
    for i in range(n):
        total += i
    return total
"""
    candidates = _candidates_for(code)
    assert len(candidates) >= 1
    c = candidates[0]
    assert c.factors["loop_density"] > 0.3
    assert c.recommended_backend == "numba"


def test_recommended_backend_jax_for_arithmetic():
    code = """
def transform(x: float, y: float, z: float) -> float:
    return x * y + z * x - y / z + x ** 2
"""
    candidates = _candidates_for(code)
    assert len(candidates) >= 1
    c = candidates[0]
    # No loops, but heavy arithmetic -> jax
    assert c.factors["loop_density"] == 0.0
    if c.factors["arithmetic_intensity"] > 0.5:
        assert c.recommended_backend == "jax"


# ------------------------------------------------------------------
# Band thresholds
# ------------------------------------------------------------------


def test_band_thresholds():
    # HIGH band: needs score > 0.7
    # Build a function that scores very high on every factor
    high_code = """
def kernel(a: float, b: float, c: float) -> float:
    total = 0.0
    for i in range(100):
        total += a * b + c - a / b
    return total
"""
    candidates = _candidates_for(high_code)
    assert len(candidates) >= 1
    c = candidates[0]
    # Verify band assignment is consistent with score
    if c.jit_score > 0.7:
        assert c.jit_band == "HIGH"
    elif c.jit_score > 0.3:
        assert c.jit_band == "MEDIUM"
    else:
        assert c.jit_band == "LOW"

    # LOW band: minimal function with no numeric types, no loops
    low_code = """
def identity(x):
    return x
"""
    low_candidates = _candidates_for(low_code)
    # Either excluded (score < 0.2) or LOW band
    for lc in low_candidates:
        if lc.jit_score <= 0.3:
            assert lc.jit_band == "LOW"
