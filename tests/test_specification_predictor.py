"""Tests for the specification predictor — 6-path decision tree, DFT, statefulness."""

from __future__ import annotations

import ast
import textwrap

from lintgate.specification.predictor import (
    PredictorInput,
    compute_dft_score,
    count_ast_categories,
    count_branches,
    detect_statefulness,
    predict,
)


def _parse_func(code: str) -> ast.FunctionDef:
    """Parse a code snippet and return the first FunctionDef node."""
    tree = ast.parse(textwrap.dedent(code))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            return node
    raise ValueError("No FunctionDef found in code")


# ── Decision tree paths ───────────────────────────────────────────────


class TestDecisionTree:
    """Test all 6 decision tree paths."""

    def test_path1_pure_high_semantic_healthy(self):
        """Pure + semantic_ratio >= 0.5 + no weakness → well-specified."""
        func = _parse_func("""
        def add(a, b):
            return a + b
        """)
        signals = PredictorInput(
            is_pure=True,
            purity_confidence=0.9,
            semantic_ratio=0.6,
            weakness_taxonomy="",
            assertion_count=5,
        )
        result = predict(func, signals)
        assert result.spec_level > 0.0
        assert result.regime == "A"

    def test_path2_pure_low_semantic(self):
        """Pure + semantic_ratio < 0.5 → under-specified."""
        func = _parse_func("""
        def compute(x, y, z):
            if x > 0:
                return y * z
            return y + z
        """)
        signals = PredictorInput(
            is_pure=True,
            purity_confidence=0.8,
            semantic_ratio=0.2,
            weakness_taxonomy="",
            assertion_count=1,
        )
        result = predict(func, signals)
        assert result.spec_level < 0.5
        assert result.regime == "A"

    def test_path3_pure_with_weakness(self):
        """Pure + weakness → maximum wasted opportunity."""
        func = _parse_func("""
        def transform(data):
            return [x * 2 for x in data]
        """)
        signals = PredictorInput(
            is_pure=True,
            purity_confidence=0.9,
            semantic_ratio=0.6,
            weakness_taxonomy="STRUCTURAL_ONLY",
            assertion_count=0,
        )
        result = predict(func, signals)
        assert result.spec_level < 0.5

    def test_path4_impure_simple(self):
        """Impure + ast_category_count <= 8 → tractable."""
        func = _parse_func("""
        def save(data, path):
            with open(path, 'w') as f:
                f.write(str(data))
        """)
        signals = PredictorInput(
            is_pure=False,
            purity_confidence=0.9,
            semantic_ratio=0.3,
            weakness_taxonomy="",
            assertion_count=2,
        )
        result = predict(func, signals)
        assert result.regime in ("A", "B")

    def test_path5_impure_complex_progressing(self):
        """Impure + high categories + semantic >= 0.5 → hard but progressing."""
        func = _parse_func("""
        def process(data, config, logger, db, cache, validator, formatter, transformer, mapper):
            if data:
                for item in data:
                    if config.get('validate'):
                        try:
                            result = validator(item)
                            if result:
                                logger.info(result)
                                db.save(result)
                            else:
                                cache.invalidate(item)
                        except Exception as e:
                            logger.error(e)
                            raise
            return data
        """)
        signals = PredictorInput(
            is_pure=False,
            purity_confidence=0.9,
            semantic_ratio=0.6,
            weakness_taxonomy="",
            assertion_count=3,
        )
        result = predict(func, signals)
        assert result.sigma > 0

    def test_path6_impure_complex_weak(self):
        """Impure + high complexity + weak tests → Regime B candidate."""
        func = _parse_func("""
        def orchestrate(a, b, c, d, e, f, g, h, i):
            if a > 0:
                for x in b:
                    if c.get(x):
                        try:
                            d.process(x)
                            if e:
                                for y in f:
                                    if g(y):
                                        h.update(y)
                                    else:
                                        i.rollback(y)
                        except Exception:
                            pass
            return None
        """)
        signals = PredictorInput(
            is_pure=False,
            purity_confidence=0.9,
            semantic_ratio=0.1,
            weakness_taxonomy="GENUINELY_WEAK",
            assertion_count=0,
        )
        result = predict(func, signals)
        assert result.regime == "B"


# ── AST helpers ───────────────────────────────────────────────────────


class TestASTHelpers:
    def test_count_branches(self):
        func = _parse_func("""
        def f(x):
            if x > 0:
                for i in range(x):
                    while i > 0:
                        i -= 1
            return x
        """)
        assert count_branches(func) >= 3

    def test_count_ast_categories(self):
        func = _parse_func("""
        def f(x, y):
            if x:
                return x + y
            return y
        """)
        categories = count_ast_categories(func)
        assert categories >= 3

    def test_count_branches_simple(self):
        func = _parse_func("""
        def f():
            return 1
        """)
        assert count_branches(func) == 0


# ── DFT scoring ───────────────────────────────────────────────────────


class TestDFTScoring:
    def test_pure_function_high_score(self):
        func = _parse_func("""
        def add(a, b):
            return a + b
        """)
        profile = compute_dft_score(func)
        assert profile.testability_score >= 0.7
        assert not profile.is_stateful

    def test_stateful_function_low_score(self):
        func = _parse_func("""
        def update(self, value):
            self.data = value
            self.count += 1
        """)
        profile = compute_dft_score(func)
        assert profile.testability_score < 1.0
        assert profile.is_stateful


# ── Statefulness detection ────────────────────────────────────────────


class TestStatefulness:
    def test_detects_self_assignment(self):
        func = _parse_func("""
        def set_value(self, v):
            self.value = v
        """)
        assert detect_statefulness(func) is True

    def test_pure_not_stateful(self):
        func = _parse_func("""
        def add(a, b):
            return a + b
        """)
        assert detect_statefulness(func) is False

    def test_detects_global(self):
        func = _parse_func("""
        def increment():
            global counter
            counter += 1
        """)
        assert detect_statefulness(func) is True


# ── PredictionResult invariants ───────────────────────────────────────


class TestPredictionResult:
    def test_sigma_positive(self):
        func = _parse_func("""
        def f(x):
            if x > 0:
                return x
            return -x
        """)
        signals = PredictorInput(is_pure=True, purity_confidence=0.9)
        result = predict(func, signals)
        assert result.sigma >= 1

    def test_spec_level_bounded(self):
        func = _parse_func("def f(): return 1")
        signals = PredictorInput(is_pure=True, purity_confidence=0.9, assertion_count=100)
        result = predict(func, signals)
        assert 0.0 <= result.spec_level <= 1.0

    def test_phase_valid(self):
        func = _parse_func("def f(x): return x")
        signals = PredictorInput(is_pure=True, purity_confidence=0.9)
        result = predict(func, signals)
        assert result.phase in ("bulk", "transition", "tail", "complete")
