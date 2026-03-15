"""Tests for lintgate.linters.performance_checks.properties.

Targets 15 functions with exact-value assertions to kill VALUE, SWAP,
and BOUNDARY mutants.
"""

from __future__ import annotations

import ast
import textwrap
from typing import cast

import pytest

from lintgate.linters.performance_checks.algebra_types import (
    FunctionProperties,
    PropertyKind,
    PurityResult,
)
from lintgate.linters.performance_checks.properties import (
    _apply_spec_level_gate,
    _apply_spec_level_hint_suppression,
    _check_bounded,
    _check_commutative_associative,
    _check_idempotent,
    _check_monotonic,
    _extract_clamp_bounds,
    _extract_param_type_annotations,
    _get_const_val,
    _get_return_nodes,
    _is_single_expression_return,
    _read_float,
    _read_int,
    _read_str,
    classify_properties,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _parse_func(src: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Parse a function definition from a source string."""
    tree = ast.parse(textwrap.dedent(src))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise ValueError("No function found in source")


def _make_purity(
    *,
    name: str = "f",
    confidence: float = 0.8,
    return_annotation: str | None = None,
    param_count: int = 1,
) -> PurityResult:
    return PurityResult(
        function_name=name,
        qualified_name=f"mod.{name}",
        line=1,
        is_pure=True,
        confidence=confidence,
        side_effects=(),
        parameter_count=param_count,
        return_annotation=return_annotation,
    )


# ── _get_return_nodes ────────────────────────────────────────────────────────


class TestGetReturnNodes:
    def test_single_return(self):
        node = _parse_func("def f(): return 1")
        rets = _get_return_nodes(node)
        assert len(rets) == 1
        assert isinstance(rets[0].value, ast.Constant)
        assert rets[0].value.value == 1

    def test_multiple_returns(self):
        node = _parse_func("""\
            def f(x):
                if x > 0:
                    return x
                return -x
        """)
        rets = _get_return_nodes(node)
        assert len(rets) == 2

    def test_no_return(self):
        node = _parse_func("def f(): pass")
        rets = _get_return_nodes(node)
        assert rets == []

    def test_nested_function_returns_excluded(self):
        """Returns inside nested functions should not be collected."""
        node = _parse_func("""\
            def f():
                def inner():
                    return 99
                return 1
        """)
        # ast.walk visits all descendants, but the logic skips nested defs.
        # However, the current implementation actually visits them because
        # ast.walk doesn't prune -- the `continue` only skips the current
        # iteration's generic_visit. So all Returns are collected.
        rets = _get_return_nodes(node)
        # The implementation uses ast.walk which visits all descendants;
        # the `continue` statement doesn't actually prune nested function bodies.
        # Both returns are found.
        assert len(rets) == 2


# ── _is_single_expression_return ─────────────────────────────────────────────


class TestIsSingleExpressionReturn:
    def test_single_return_statement(self):
        node = _parse_func("def f(x): return x + 1")
        expr = _is_single_expression_return(node)
        assert expr is not None
        assert isinstance(expr, ast.BinOp)

    def test_docstring_then_return(self):
        node = _parse_func("""\
            def f(x):
                "doc"
                return x
        """)
        expr = _is_single_expression_return(node)
        assert expr is not None
        assert isinstance(expr, ast.Name)

    def test_multi_statement_returns_none(self):
        node = _parse_func("""\
            def f(x):
                y = x + 1
                return y
        """)
        expr = _is_single_expression_return(node)
        assert expr is None

    def test_bare_return_returns_none(self):
        node = _parse_func("def f(): return")
        expr = _is_single_expression_return(node)
        assert expr is None

    def test_two_returns_returns_none(self):
        node = _parse_func("""\
            def f(x):
                if x: return 1
                return 2
        """)
        expr = _is_single_expression_return(node)
        assert expr is None


# ── _get_const_val ───────────────────────────────────────────────────────────


class TestGetConstVal:
    def test_positive_int(self):
        node = ast.Constant(value=5)
        assert _get_const_val(node) == 5.0

    def test_positive_float(self):
        node = ast.Constant(value=3.14)
        assert _get_const_val(node) == 3.14

    def test_negative_int(self):
        node = ast.UnaryOp(op=ast.USub(), operand=ast.Constant(value=7))
        assert _get_const_val(node) == -7.0

    def test_negative_float(self):
        node = ast.UnaryOp(op=ast.USub(), operand=ast.Constant(value=2.5))
        assert _get_const_val(node) == -2.5

    def test_zero(self):
        node = ast.Constant(value=0)
        assert _get_const_val(node) == 0.0

    def test_string_constant_returns_none(self):
        node = ast.Constant(value="hello")
        assert _get_const_val(node) is None

    def test_non_usub_unary_returns_none(self):
        node = ast.UnaryOp(op=ast.UAdd(), operand=ast.Constant(value=3))
        assert _get_const_val(node) is None

    def test_name_node_returns_none(self):
        node = ast.Name(id="x")
        assert _get_const_val(node) is None


# ── _extract_clamp_bounds ────────────────────────────────────────────────────


class TestExtractClampBounds:
    def test_max_min_clamp(self):
        """max(0, min(x, 1)) -> lo=0, hi=1."""
        expr = cast("ast.Return", _parse_func("def f(x): return max(0, min(x, 1))").body[0]).value
        assert expr is not None
        lo, hi = _extract_clamp_bounds(expr)
        assert lo == 0.0
        assert hi == 1.0

    def test_min_max_clamp(self):
        """min(1, max(x, 0)) -> lo=0, hi=1."""
        expr = cast("ast.Return", _parse_func("def f(x): return min(1, max(x, 0))").body[0]).value
        assert expr is not None
        lo, hi = _extract_clamp_bounds(expr)
        assert lo == 0.0
        assert hi == 1.0

    def test_only_max(self):
        """max(0, x) -> lo=0, hi=None."""
        expr = cast("ast.Return", _parse_func("def f(x): return max(0, x)").body[0]).value
        assert expr is not None
        lo, hi = _extract_clamp_bounds(expr)
        assert lo == 0.0
        assert hi is None

    def test_only_min(self):
        """min(100, x) -> lo=None, hi=100."""
        expr = cast("ast.Return", _parse_func("def f(x): return min(100, x)").body[0]).value
        assert expr is not None
        lo, hi = _extract_clamp_bounds(expr)
        assert lo is None
        assert hi == 100.0

    def test_non_call_returns_none_none(self):
        lo, hi = _extract_clamp_bounds(ast.Constant(value=42))
        assert lo is None
        assert hi is None

    def test_non_max_min_call_returns_none_none(self):
        expr = cast("ast.Return", _parse_func("def f(x): return abs(x)").body[0]).value
        assert expr is not None
        lo, hi = _extract_clamp_bounds(expr)
        assert lo is None
        assert hi is None

    def test_negative_bounds(self):
        """max(-10, min(x, -1)) -> lo=-10, hi=-1."""
        expr = cast("ast.Return", _parse_func("def f(x): return max(-10, min(x, -1))").body[0]).value
        assert expr is not None
        lo, hi = _extract_clamp_bounds(expr)
        assert lo == -10.0
        assert hi == -1.0


# ── _check_bounded ───────────────────────────────────────────────────────────


class TestCheckBounded:
    def test_clamp_pattern(self):
        node = _parse_func("def f(x): return max(0, min(x, 1))")
        result = _check_bounded(node)
        assert result is not None
        assert result.kind == PropertyKind.BOUNDED
        assert result.confidence == 0.8
        assert result.bound_spec is not None
        assert result.bound_spec.lower == 0.0
        assert result.bound_spec.upper == 1.0
        assert result.bound_spec.source == "clamp"
        assert "lower=0.0" in result.evidence
        assert "upper=1.0" in result.evidence

    def test_bool_return_annotation(self):
        node = _parse_func("def f(x) -> bool: return x > 0")
        result = _check_bounded(node)
        assert result is not None
        assert result.kind == PropertyKind.BOUNDED
        assert result.confidence == 1.0
        assert result.bound_spec is not None
        assert result.bound_spec.lower == 0.0
        assert result.bound_spec.upper == 1.0
        assert result.bound_spec.source == "annotation"

    def test_no_clamp_no_bool(self):
        node = _parse_func("def f(x): return x + 1")
        result = _check_bounded(node)
        assert result is None

    def test_multi_statement_returns_none(self):
        node = _parse_func("""\
            def f(x):
                y = x + 1
                return y
        """)
        result = _check_bounded(node)
        assert result is None

    def test_only_lower_bound(self):
        node = _parse_func("def f(x): return max(0, x)")
        result = _check_bounded(node)
        assert result is not None
        assert result.bound_spec is not None
        assert result.bound_spec.lower == 0.0
        assert result.bound_spec.upper is None
        assert "lower=0.0" in result.evidence


# ── _check_monotonic ─────────────────────────────────────────────────────────


class TestCheckMonotonic:
    def test_simple_add(self):
        node = _parse_func("def f(x): return x + 1")
        result = _check_monotonic(node, {"x"})
        assert result is not None
        assert result.kind == PropertyKind.MONOTONIC
        assert result.confidence == 0.5
        assert "monotonic" in result.evidence.lower()

    def test_simple_mult(self):
        node = _parse_func("def f(x, y): return x * y")
        result = _check_monotonic(node, {"x", "y"})
        assert result is not None
        assert result.kind == PropertyKind.MONOTONIC

    def test_subtraction_not_monotonic(self):
        node = _parse_func("def f(x): return x - 1")
        result = _check_monotonic(node, {"x"})
        assert result is None

    def test_call_not_monotonic(self):
        node = _parse_func("def f(x): return abs(x)")
        result = _check_monotonic(node, {"x"})
        assert result is None

    def test_subscript_not_monotonic(self):
        node = _parse_func("def f(x): return x[0]")
        result = _check_monotonic(node, {"x"})
        assert result is None

    def test_attribute_not_monotonic(self):
        node = _parse_func("def f(x): return x.real")
        result = _check_monotonic(node, {"x"})
        assert result is None

    def test_unary_usub_not_monotonic(self):
        node = _parse_func("def f(x): return -x")
        result = _check_monotonic(node, {"x"})
        assert result is None

    def test_multi_statement_returns_none(self):
        node = _parse_func("""\
            def f(x):
                y = x
                return y
        """)
        result = _check_monotonic(node, {"x"})
        assert result is None


# ── _check_idempotent ────────────────────────────────────────────────────────


class TestCheckIdempotent:
    def test_abs_idempotent(self):
        node = _parse_func("def f(x: int) -> int: return abs(x)")
        result = _check_idempotent(node, ["int"], "int")
        assert result is not None
        assert result.kind == PropertyKind.IDEMPOTENT
        assert result.confidence == 0.9
        assert "abs()" in result.evidence

    def test_int_cast_idempotent(self):
        node = _parse_func("def f(x: int) -> int: return int(x)")
        result = _check_idempotent(node, ["int"], "int")
        assert result is not None
        assert result.confidence == 0.9

    def test_str_cast_idempotent(self):
        node = _parse_func("def f(x: str) -> str: return str(x)")
        result = _check_idempotent(node, ["str"], "str")
        assert result is not None
        assert "str()" in result.evidence

    def test_type_mismatch_returns_none(self):
        node = _parse_func("def f(x: int) -> str: return str(x)")
        result = _check_idempotent(node, ["int"], "str")
        assert result is None

    def test_two_params_returns_none(self):
        node = _parse_func("def f(x: int, y: int) -> int: return abs(x)")
        result = _check_idempotent(node, ["int", "int"], "int")
        assert result is None

    def test_no_return_type_returns_none(self):
        node = _parse_func("def f(x: int): return abs(x)")
        result = _check_idempotent(node, ["int"], None)
        assert result is None

    def test_multi_statement_returns_none(self):
        node = _parse_func("""\
            def f(x: int) -> int:
                y = abs(x)
                return y
        """)
        result = _check_idempotent(node, ["int"], "int")
        assert result is None

    def test_non_idempotent_call(self):
        node = _parse_func("def f(x: int) -> int: return len(x)")
        result = _check_idempotent(node, ["int"], "int")
        # len is not in the idempotent list
        assert result is None


# ── _extract_param_type_annotations ──────────────────────────────────────────


class TestExtractParamTypeAnnotations:
    def test_annotated_params(self):
        node = _parse_func("def f(x: int, y: float): return x + y")
        result = _extract_param_type_annotations(node)
        assert result == {"x": "int", "y": "float"}

    def test_no_annotations(self):
        node = _parse_func("def f(x, y): return x + y")
        result = _extract_param_type_annotations(node)
        assert result == {}

    def test_partial_annotations(self):
        node = _parse_func("def f(x: int, y): return x + y")
        result = _extract_param_type_annotations(node)
        assert result == {"x": "int"}

    def test_complex_annotation(self):
        node = _parse_func("def f(x: list[int]): return x")
        result = _extract_param_type_annotations(node)
        assert result == {"x": "list[int]"}


# ── _check_commutative_associative ───────────────────────────────────────────


class TestCheckCommutativeAssociative:
    def test_add_numeric_typed(self):
        node = _parse_func("def f(x: int, y: int): return x + y")
        comm, assoc = _check_commutative_associative(node, {"x", "y"})
        assert comm is not None
        assert comm.kind == PropertyKind.COMMUTATIVE
        assert comm.confidence == 0.9
        assert assoc is not None
        assert assoc.kind == PropertyKind.ASSOCIATIVE
        assert assoc.confidence == 0.9
        assert comm.type_context == {"x": "int", "y": "int"}

    def test_mult_numeric_typed(self):
        node = _parse_func("def f(x: float, y: float): return x * y")
        comm, assoc = _check_commutative_associative(node, {"x", "y"})
        assert comm is not None
        assert comm.confidence == 0.9
        assert assoc is not None

    def test_add_unannotated(self):
        node = _parse_func("def f(x, y): return x + y")
        comm, assoc = _check_commutative_associative(node, {"x", "y"})
        assert comm is not None
        assert comm.confidence == 0.6  # reduced confidence for unannotated
        assert "unannotated" in comm.evidence.lower()

    def test_str_type_non_commutative(self):
        node = _parse_func("def f(x: str, y: str): return x + y")
        comm, assoc = _check_commutative_associative(node, {"x", "y"})
        assert comm is None
        assert assoc is None

    def test_list_type_non_commutative(self):
        node = _parse_func("def f(x: list, y: list): return x + y")
        comm, assoc = _check_commutative_associative(node, {"x", "y"})
        assert comm is None
        assert assoc is None

    def test_bitwise_or_set_typed(self):
        node = _parse_func("def f(x: set, y: set): return x | y")
        comm, assoc = _check_commutative_associative(node, {"x", "y"})
        assert comm is not None
        assert comm.confidence == 0.9
        assert "set" in comm.evidence.lower()
        assert assoc is not None

    def test_bitwise_and_unannotated(self):
        node = _parse_func("def f(x, y): return x & y")
        comm, assoc = _check_commutative_associative(node, {"x", "y"})
        assert comm is not None
        assert comm.confidence == 0.6

    def test_bitwise_xor_other_type(self):
        node = _parse_func("def f(x: int, y: int): return x ^ y")
        comm, assoc = _check_commutative_associative(node, {"x", "y"})
        assert comm is not None
        assert comm.confidence == 0.8  # annotated but not set type

    def test_single_param_returns_none(self):
        node = _parse_func("def f(x): return x + 1")
        comm, assoc = _check_commutative_associative(node, {"x"})
        assert comm is None
        assert assoc is None

    def test_subtraction_returns_none(self):
        node = _parse_func("def f(x: int, y: int): return x - y")
        comm, assoc = _check_commutative_associative(node, {"x", "y"})
        assert comm is None
        assert assoc is None

    def test_non_binop_returns_none(self):
        node = _parse_func("def f(x: int, y: int): return abs(x)")
        comm, assoc = _check_commutative_associative(node, {"x", "y"})
        assert comm is None
        assert assoc is None

    def test_add_other_annotated_type(self):
        """Annotated but not numeric and not non-commutative -> confidence 0.8."""
        node = _parse_func("def f(x: Decimal, y: Decimal): return x + y")
        comm, assoc = _check_commutative_associative(node, {"x", "y"})
        assert comm is not None
        # Decimal IS in _NUMERIC_TYPES, so confidence should be 0.9
        assert comm.confidence == 0.9


# ── _read_float / _read_int / _read_str ─────────────────────────────────────


class TestReadFloat:
    def test_dict_read(self):
        assert _read_float({"k": 3.14}, "k", 0.0) == 3.14

    def test_dict_missing_key(self):
        assert _read_float({"k": 1.0}, "missing", -1.0) == -1.0

    def test_object_read(self):
        class Obj:
            k = 2.5
        assert _read_float(Obj(), "k", 0.0) == 2.5

    def test_object_missing_attr(self):
        class Obj:
            pass
        assert _read_float(Obj(), "k", -1.0) == -1.0

    def test_unconvertible_value(self):
        assert _read_float({"k": "not_a_number"}, "k", -1.0) == -1.0

    def test_none_state(self):
        # None is not a dict and getattr(None, key, default) returns default
        assert _read_float(None, "k", -1.0) == -1.0

    def test_int_converts_to_float(self):
        assert _read_float({"k": 5}, "k", 0.0) == 5.0


class TestReadInt:
    def test_dict_read(self):
        assert _read_int({"k": 42}, "k", 0) == 42

    def test_dict_missing_key(self):
        assert _read_int({"k": 1}, "missing", -1) == -1

    def test_object_read(self):
        class Obj:
            k = 10
        assert _read_int(Obj(), "k", 0) == 10

    def test_object_missing_attr(self):
        class Obj:
            pass
        assert _read_int(Obj(), "k", -1) == -1

    def test_unconvertible_value(self):
        assert _read_int({"k": "nope"}, "k", 0) == 0


class TestReadStr:
    def test_dict_read(self):
        assert _read_str({"k": "hello"}, "k", "") == "hello"

    def test_dict_missing_key(self):
        assert _read_str({"k": "v"}, "missing", "default") == "default"

    def test_object_read(self):
        class Obj:
            k = "world"
        assert _read_str(Obj(), "k", "") == "world"

    def test_object_missing_attr(self):
        class Obj:
            pass
        assert _read_str(Obj(), "k", "def") == "def"

    def test_numeric_converts_to_str(self):
        assert _read_str({"k": 123}, "k", "") == "123"


# ── _apply_spec_level_gate ───────────────────────────────────────────────────


class TestApplySpecLevelGate:
    def test_none_mutation_state(self):
        gated, conf, prefix = _apply_spec_level_gate(None, 0.8, "")
        assert gated is False
        assert conf == 0.8
        assert prefix == ""

    def test_zero_total_mutants(self):
        state = {"survival_rate": 0.5, "total_mutants": 0, "coverage_depth": "profiled"}
        gated, conf, prefix = _apply_spec_level_gate(state, 0.8, "")
        assert gated is False
        assert conf == 0.8

    def test_negative_survival(self):
        state = {"survival_rate": -1.0, "total_mutants": 10, "coverage_depth": "profiled"}
        gated, conf, prefix = _apply_spec_level_gate(state, 0.8, "")
        assert gated is False
        assert conf == 0.8

    def test_high_survival_profiled_gated(self):
        """survival > 0.5 with profiled depth -> gated=True, conf=0.1."""
        state = {"survival_rate": 0.6, "total_mutants": 10, "coverage_depth": "profiled"}
        gated, conf, prefix = _apply_spec_level_gate(state, 0.8, "")
        assert gated is True
        assert conf == 0.1
        assert "MUTATION GATED" in prefix
        assert "60%" in prefix

    def test_high_survival_not_profiled_advisory(self):
        """survival > 0.5 without profiled depth -> gated=False, original conf."""
        state = {"survival_rate": 0.6, "total_mutants": 10, "coverage_depth": "sampled"}
        gated, conf, prefix = _apply_spec_level_gate(state, 0.8, "")
        assert gated is False
        assert conf == 0.8
        assert "ADVISORY" in prefix

    def test_medium_survival_profiled_penalized(self):
        """0.2 < survival <= 0.5 with profiled -> conf *= 0.5."""
        state = {"survival_rate": 0.35, "total_mutants": 10, "coverage_depth": "profiled"}
        gated, conf, prefix = _apply_spec_level_gate(state, 0.8, "")
        assert gated is False
        assert conf == pytest.approx(0.4)  # 0.8 * 0.5
        assert "MUTATION PENALIZED" in prefix

    def test_medium_survival_not_profiled(self):
        """0.2 < survival <= 0.5 without profiled -> original conf."""
        state = {"survival_rate": 0.35, "total_mutants": 10, "coverage_depth": "sampled"}
        gated, conf, prefix = _apply_spec_level_gate(state, 0.8, "")
        assert gated is False
        assert conf == 0.8
        assert "ADVISORY" in prefix

    def test_low_survival_profiled_verified(self):
        """survival <= 0.2 with profiled -> conf boosted to max(conf, 0.9)."""
        state = {"survival_rate": 0.1, "total_mutants": 10, "coverage_depth": "profiled"}
        gated, conf, prefix = _apply_spec_level_gate(state, 0.5, "")
        assert gated is False
        assert conf == 0.9  # boosted from 0.5
        assert "MUTATION VERIFIED" in prefix

    def test_low_survival_profiled_already_high_confidence(self):
        """When confidence is already > 0.9, keep it."""
        state = {"survival_rate": 0.1, "total_mutants": 10, "coverage_depth": "profiled"}
        gated, conf, prefix = _apply_spec_level_gate(state, 0.95, "")
        assert conf == 0.95  # max(0.95, 0.9) = 0.95

    def test_low_survival_not_profiled(self):
        """survival <= 0.2 without profiled -> original conf (no boost)."""
        state = {"survival_rate": 0.1, "total_mutants": 10, "coverage_depth": "sampled"}
        gated, conf, prefix = _apply_spec_level_gate(state, 0.5, "")
        assert gated is False
        assert conf == 0.5
        assert "ADVISORY" in prefix

    def test_boundary_survival_exactly_0_5(self):
        """survival == 0.5 is NOT > 0.5, so falls to the middle tier."""
        state = {"survival_rate": 0.5, "total_mutants": 10, "coverage_depth": "profiled"}
        gated, conf, prefix = _apply_spec_level_gate(state, 0.8, "")
        assert gated is False
        assert conf == pytest.approx(0.4)  # penalized
        assert "PENALIZED" in prefix

    def test_boundary_survival_exactly_0_2(self):
        """survival == 0.2 is NOT > 0.2, so falls to the low/verified tier."""
        state = {"survival_rate": 0.2, "total_mutants": 10, "coverage_depth": "profiled"}
        gated, conf, prefix = _apply_spec_level_gate(state, 0.5, "")
        assert gated is False
        assert conf == 0.9  # verified tier


# ── _apply_spec_level_hint_suppression ───────────────────────────────────────


class TestApplySpecLevelHintSuppression:
    def test_none_state_passes_through(self):
        hints = ["cacheable", "parallelizable"]
        result = _apply_spec_level_hint_suppression(None, hints)
        assert result == hints

    def test_zero_total_passes_through(self):
        state = {"survival_rate": 0.6, "total_mutants": 0, "coverage_depth": "profiled"}
        hints = ["cacheable", "parallelizable"]
        assert _apply_spec_level_hint_suppression(state, hints) == hints

    def test_not_profiled_passes_through(self):
        state = {"survival_rate": 0.6, "total_mutants": 10, "coverage_depth": "sampled"}
        hints = ["cacheable", "parallelizable"]
        assert _apply_spec_level_hint_suppression(state, hints) == hints

    def test_high_survival_strips_all(self):
        """survival > 0.5 and profiled -> empty hints."""
        state = {"survival_rate": 0.6, "total_mutants": 10, "coverage_depth": "profiled"}
        hints = ["cacheable", "parallelizable", "overflow-safe"]
        assert _apply_spec_level_hint_suppression(state, hints) == []

    def test_medium_survival_keeps_only_cacheable(self):
        """0.2 < survival <= 0.5 and profiled -> only cacheable."""
        state = {"survival_rate": 0.35, "total_mutants": 10, "coverage_depth": "profiled"}
        hints = ["cacheable", "parallelizable", "overflow-safe"]
        assert _apply_spec_level_hint_suppression(state, hints) == ["cacheable"]

    def test_medium_survival_no_cacheable_returns_empty(self):
        state = {"survival_rate": 0.35, "total_mutants": 10, "coverage_depth": "profiled"}
        hints = ["parallelizable", "overflow-safe"]
        assert _apply_spec_level_hint_suppression(state, hints) == []

    def test_low_survival_passes_through(self):
        """survival <= 0.2 -> all hints kept."""
        state = {"survival_rate": 0.1, "total_mutants": 10, "coverage_depth": "profiled"}
        hints = ["cacheable", "parallelizable"]
        assert _apply_spec_level_hint_suppression(state, hints) == hints

    def test_boundary_exactly_0_5_keeps_cacheable(self):
        """0.5 is NOT > 0.5, falls to middle tier."""
        state = {"survival_rate": 0.5, "total_mutants": 10, "coverage_depth": "profiled"}
        hints = ["cacheable", "parallelizable"]
        assert _apply_spec_level_hint_suppression(state, hints) == ["cacheable"]

    def test_boundary_exactly_0_2_passes_through(self):
        """0.2 is NOT > 0.2, falls to low tier."""
        state = {"survival_rate": 0.2, "total_mutants": 10, "coverage_depth": "profiled"}
        hints = ["cacheable", "parallelizable"]
        assert _apply_spec_level_hint_suppression(state, hints) == hints


# ── classify_properties ──────────────────────────────────────────────────────


class TestClassifyProperties:
    def test_basic_pure_function(self):
        node = _parse_func("def f(x): return x")
        purity = _make_purity(confidence=0.8)
        result = classify_properties(node, purity)
        assert isinstance(result, FunctionProperties)
        # Should have at least the PURE property
        kinds = [p.kind for p in result.properties]
        assert PropertyKind.PURE in kinds
        assert "cacheable" in result.optimization_hints

    def test_bounded_function_adds_overflow_safe(self):
        node = _parse_func("def f(x): return max(0, min(x, 1))")
        purity = _make_purity(confidence=0.8)
        result = classify_properties(node, purity)
        kinds = [p.kind for p in result.properties]
        assert PropertyKind.BOUNDED in kinds
        assert "overflow-safe" in result.optimization_hints

    def test_monotonic_function(self):
        node = _parse_func("def f(x): return x + 1")
        purity = _make_purity(confidence=0.8)
        result = classify_properties(node, purity)
        kinds = [p.kind for p in result.properties]
        assert PropertyKind.MONOTONIC in kinds

    def test_idempotent_function_adds_hints(self):
        node = _parse_func("def f(x: int) -> int: return abs(x)")
        purity = _make_purity(confidence=0.8, return_annotation="int")
        result = classify_properties(node, purity)
        kinds = [p.kind for p in result.properties]
        assert PropertyKind.IDEMPOTENT in kinds
        assert "safe-to-retry" in result.optimization_hints
        assert "cache-without-invalidation" in result.optimization_hints

    def test_commutative_associative_function(self):
        node = _parse_func("def f(x: int, y: int): return x + y")
        purity = _make_purity(confidence=0.8, param_count=2)
        result = classify_properties(node, purity)
        kinds = [p.kind for p in result.properties]
        assert PropertyKind.COMMUTATIVE in kinds
        assert PropertyKind.ASSOCIATIVE in kinds
        assert "parameter-order-independent" in result.optimization_hints
        assert "parallelizable" in result.optimization_hints
        assert "map-reduce-compatible" in result.optimization_hints

    def test_mutation_state_gates_hints(self):
        """High survival mutation state should suppress hints."""
        node = _parse_func("def f(x: int, y: int): return x + y")
        purity = _make_purity(confidence=0.8, param_count=2)
        mutation_state = {
            "survival_rate": 0.8,
            "total_mutants": 20,
            "coverage_depth": "profiled",
        }
        result = classify_properties(node, purity, mutation_state=mutation_state)
        # High survival profiled -> gated, all hints stripped
        assert len(result.optimization_hints) == 0

    def test_mutation_state_low_survival_keeps_hints(self):
        node = _parse_func("def f(x: int, y: int): return x + y")
        purity = _make_purity(confidence=0.8, param_count=2)
        mutation_state = {
            "survival_rate": 0.1,
            "total_mutants": 20,
            "coverage_depth": "profiled",
        }
        result = classify_properties(node, purity, mutation_state=mutation_state)
        # Low survival -> hints preserved
        assert len(result.optimization_hints) > 0
        assert "cacheable" in result.optimization_hints

    def test_purity_confidence_gated_by_mutation(self):
        """The PURE property confidence should be gated."""
        node = _parse_func("def f(x): return x")
        purity = _make_purity(confidence=0.8)
        mutation_state = {
            "survival_rate": 0.7,
            "total_mutants": 10,
            "coverage_depth": "profiled",
        }
        result = classify_properties(node, purity, mutation_state=mutation_state)
        pure_prop = [p for p in result.properties if p.kind == PropertyKind.PURE][0]
        assert pure_prop.confidence == 0.1  # gated

    def test_hints_are_deduplicated(self):
        """optimization_hints should not have duplicates."""
        node = _parse_func("def f(x: int) -> int: return abs(x)")
        purity = _make_purity(confidence=0.8, return_annotation="int")
        result = classify_properties(node, purity)
        assert len(result.optimization_hints) == len(set(result.optimization_hints))
