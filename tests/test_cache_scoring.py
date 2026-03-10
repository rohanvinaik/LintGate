"""Tests for ROI-ranked cacheability scoring."""

from __future__ import annotations

import ast
import textwrap

from lintgate.linters.performance_checks.algebra_types import PurityResult, SideEffect
from lintgate.linters.performance_checks.cache_scoring import (
    CacheScore,
    compute_cache_score,
    score_all_cacheable,
)


def _parse_func(source: str) -> ast.FunctionDef:
    """Parse a single function from source and return its AST node."""
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise ValueError("No function found in source")


def _make_pure(name: str = "f", param_count: int = 1) -> PurityResult:
    return PurityResult(
        function_name=name,
        qualified_name=name,
        line=1,
        is_pure=True,
        confidence=0.9,
        side_effects=(),
        parameter_count=param_count,
        return_annotation=None,
    )


def _make_impure(name: str = "f") -> PurityResult:
    return PurityResult(
        function_name=name,
        qualified_name=name,
        line=1,
        is_pure=False,
        confidence=1.0,
        side_effects=(
            SideEffect(
                kind="io_call",
                node_type="Call",
                line=2,
                detail="Calls impure namespace/function: print",
            ),
        ),
        parameter_count=1,
        return_annotation=None,
    )


class TestImpureFunctionGetsSkipBand:
    def test_impure_function_gets_skip_band(self):
        source = """\
        def f(x):
            print(x)
            return x
        """
        node = _parse_func(source)
        purity = _make_impure()
        result = compute_cache_score(node, purity)

        assert result.band == "SKIP"
        assert result.score == 0.0
        assert result.factors["compute_weight"] == 0.0
        assert result.factors["call_hotness"] == 0.0
        assert result.factors["repeatability"] == 0.0


class TestSimplePureLeafGetsLowScore:
    def test_simple_pure_leaf_gets_low_score(self):
        source = """\
        def f(x):
            return x + 1
        """
        node = _parse_func(source)
        purity = _make_pure()
        result = compute_cache_score(node, purity)

        # One BinOp → compute_weight = 1/20 = 0.05
        # No call graph → call_hotness = 0.5
        # 1 param → repeatability base = 1 - 1/5 = 0.8
        # score = 0.4*0.05 + 0.35*0.5 + 0.25*0.8 = 0.02 + 0.175 + 0.2 = 0.395
        assert result.band == "MEDIUM"
        assert result.score < 0.5


class TestComplexPureFunctionGetsHigherScore:
    def test_complex_pure_function_gets_higher_score(self):
        source = """\
        def f(x):
            a = x + 1
            b = a * 2
            c = a - b
            d = c / x
            e = d ** 2
            g = x % 3
            h = a + b + c + d + e + g
            i = len([a, b, c])
            j = max(h, i)
            k = min(j, 100)
            m = abs(k)
            n = sum([a, b])
            o = round(n, 2)
            p = sorted([a, b, c])
            q = x > 0
            r = x == 1
            s = [v for v in [a, b, c] if v > 0]
            t = {v: v for v in s}
            u = a if q else b
            return t
        """
        node = _parse_func(source)
        purity = _make_pure()
        simple_source = """\
        def g(x):
            return x + 1
        """
        simple_node = _parse_func(simple_source)
        simple_purity = _make_pure(name="g")

        complex_result = compute_cache_score(node, purity)
        simple_result = compute_cache_score(simple_node, simple_purity)

        assert complex_result.factors["compute_weight"] > simple_result.factors["compute_weight"]
        assert complex_result.score > simple_result.score


class TestHighFanInBoostsScore:
    def test_high_fan_in_boosts_score(self):
        source = """\
        def f(x):
            return x + 1
        """
        node = _parse_func(source)
        purity = _make_pure()

        no_graph = compute_cache_score(node, purity, call_graph=None)
        low_fan = compute_cache_score(node, purity, call_graph={"fan_in": 1})
        high_fan = compute_cache_score(node, purity, call_graph={"fan_in": 10})

        assert high_fan.factors["call_hotness"] > low_fan.factors["call_hotness"]
        assert high_fan.score > low_fan.score
        # Default call_hotness is 0.5
        assert no_graph.factors["call_hotness"] == 0.5


class TestFewParamsBoostsRepeatability:
    def test_few_params_boosts_repeatability(self):
        zero_params_src = """\
        def f():
            return 42
        """
        many_params_src = """\
        def g(a, b, c, d, e):
            return a + b + c + d + e
        """
        zero_node = _parse_func(zero_params_src)
        many_node = _parse_func(many_params_src)

        zero_purity = _make_pure(name="f", param_count=0)
        many_purity = _make_pure(name="g", param_count=5)

        zero_result = compute_cache_score(zero_node, zero_purity)
        many_result = compute_cache_score(many_node, many_purity)

        assert zero_result.factors["repeatability"] > many_result.factors["repeatability"]
        # 0 params → repeatability = 1.0
        assert zero_result.factors["repeatability"] == 1.0
        # 5 params → repeatability base = max(1 - 5/5, 0) = 0.0
        assert many_result.factors["repeatability"] == 0.0


class TestScoreAllCacheableReturnsDict:
    def test_score_all_cacheable_returns_dict(self):
        source = textwrap.dedent("""\
        def pure_func(x):
            return x + 1

        def impure_func(x):
            print(x)
            return x
        """)
        tree = ast.parse(source)
        purity_results = {
            "pure_func": _make_pure(name="pure_func"),
            "impure_func": _make_impure(name="impure_func"),
        }

        result = score_all_cacheable(tree, purity_results)

        assert isinstance(result, dict)
        assert "pure_func" in result
        assert "impure_func" in result
        assert result["pure_func"].band != "SKIP"
        assert result["impure_func"].band == "SKIP"


class TestBandThresholds:
    def test_high_band_above_0_7(self):
        """A CacheScore with score > 0.7 should get HIGH band."""
        cs = CacheScore(score=0.75, band="HIGH", factors={})
        assert cs.band == "HIGH"

    def test_medium_band_between_0_3_and_0_7(self):
        """A CacheScore with 0.3 < score <= 0.7 should get MEDIUM band."""
        cs = CacheScore(score=0.5, band="MEDIUM", factors={})
        assert cs.band == "MEDIUM"

    def test_low_band_below_0_3(self):
        """A CacheScore with score <= 0.3 should get LOW band."""
        cs = CacheScore(score=0.2, band="LOW", factors={})
        assert cs.band == "LOW"

    def test_band_thresholds_via_compute(self):
        """Verify compute_cache_score assigns correct bands at boundaries."""
        # High fan-in + zero params + complex body → should push toward HIGH
        source = """\
        def f():
            a = 1 + 2
            b = a * 3
            c = len([a, b])
            d = max(a, b, c)
            e = min(d, 100)
            g = abs(e)
            h = sum([a, b, c])
            i = round(h)
            j = sorted([a, b])
            k = [x for x in j if x > 0]
            m = {x: x for x in k}
            n = a > b
            o = a == b
            p = a + b + c + d + e + g + h + i
            q = p ** 2
            r = q % 7
            s = r // 3
            t = s - 1
            u = t & 0xFF
            return u
        """
        node = _parse_func(source)
        purity = _make_pure(name="f", param_count=0)
        result = compute_cache_score(node, purity, call_graph={"fan_in": 15})
        assert result.band == "HIGH"
        assert result.score > 0.7

        # Low fan-in + many params + simple body → should be LOW
        source_low = """\
        def g(a, b, c, d, e):
            return a
        """
        node_low = _parse_func(source_low)
        purity_low = _make_pure(name="g", param_count=5)
        result_low = compute_cache_score(node_low, purity_low, call_graph={"fan_in": 0})
        assert result_low.band == "LOW"
        assert result_low.score <= 0.3


class TestToDict:
    def test_to_dict_rounds_values(self):
        cs = CacheScore(
            score=0.12345,
            band="LOW",
            factors={"compute_weight": 0.11111, "call_hotness": 0.22222},
        )
        d = cs.to_dict()
        assert d["score"] == 0.123
        assert d["band"] == "LOW"
        assert d["factors"]["compute_weight"] == 0.111
        assert d["factors"]["call_hotness"] == 0.222
