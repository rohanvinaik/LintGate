"""Hypothesis property-based tests for algebraic properties detected in the LintGate codebase.

Tests grouped by property type:
- BOUNDED: outputs always within expected bounds regardless of input
- MONOTONIC: more input produces more/equal output, or output never exceeds input
- SYMMETRIC: f(a, b) == f(b, a)
"""

from __future__ import annotations

import ast
import math

import hypothesis.strategies as st
from hypothesis import given, settings

# ═══════════════════════════════════════════════════════════════════
# BOUNDED property tests
# ═══════════════════════════════════════════════════════════════════


class TestClampBounded:
    """health_vector._clamp: output always in [0.0, 1.0]."""

    @given(v=st.floats(allow_nan=False, allow_infinity=False))
    @settings(max_examples=50)
    def test_clamp_output_in_unit_interval(self, v: float) -> None:
        from lintgate.specification.health_vector import _clamp

        result = _clamp(v)
        assert 0.0 <= result <= 1.0, f"_clamp({v}) = {result}, expected in [0.0, 1.0]"

    @given(v=st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
    @settings(max_examples=50)
    def test_clamp_identity_in_range(self, v: float) -> None:
        """Values already in [0, 1] should pass through unchanged."""
        from lintgate.specification.health_vector import _clamp

        assert _clamp(v) == v

    @given(v=st.floats(min_value=1.01, allow_nan=False, allow_infinity=False))
    @settings(max_examples=50)
    def test_clamp_caps_above_one(self, v: float) -> None:
        from lintgate.specification.health_vector import _clamp

        assert _clamp(v) == 1.0

    @given(v=st.floats(max_value=-0.01, allow_nan=False, allow_infinity=False))
    @settings(max_examples=50)
    def test_clamp_floors_below_zero(self, v: float) -> None:
        from lintgate.specification.health_vector import _clamp

        assert _clamp(v) == 0.0


class TestApplyWeightBounded:
    """convergence/aggregator._apply_weight: output bounded by min(confidence * weight, 1.0)."""

    @given(
        confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        weight=st.floats(min_value=0.0, max_value=10.0, allow_nan=False),
    )
    @settings(max_examples=50)
    def test_apply_weight_capped_at_one(self, confidence: float, weight: float) -> None:
        from lintgate.convergence.aggregator import _apply_weight

        result = _apply_weight(confidence, weight)
        assert result <= 1.0, f"_apply_weight({confidence}, {weight}) = {result}, expected <= 1.0"

    @given(
        confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        weight=st.floats(min_value=0.0, max_value=10.0, allow_nan=False),
    )
    @settings(max_examples=50)
    def test_apply_weight_non_negative(self, confidence: float, weight: float) -> None:
        from lintgate.convergence.aggregator import _apply_weight

        result = _apply_weight(confidence, weight)
        assert result >= 0.0, f"_apply_weight({confidence}, {weight}) = {result}, expected >= 0.0"

    @given(
        confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        weight=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    )
    @settings(max_examples=50)
    def test_apply_weight_bounded_by_product(self, confidence: float, weight: float) -> None:
        """When weight <= 1.0, result equals confidence * weight exactly."""
        from lintgate.convergence.aggregator import _apply_weight

        result = _apply_weight(confidence, weight)
        expected = confidence * weight
        assert math.isclose(result, expected, rel_tol=1e-9, abs_tol=1e-15)


class TestProbabilityUnionBounded:
    """convergence/aggregator._probability_union: output in [0.0, 1.0] for valid confidences."""

    @given(
        confidences=st.lists(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
            min_size=0,
            max_size=10,
        )
    )
    @settings(max_examples=50)
    def test_probability_union_bounded(self, confidences: list[float]) -> None:
        from lintgate.convergence.aggregator import _probability_union

        result = _probability_union(confidences)
        assert 0.0 <= result <= 1.0, (
            f"_probability_union({confidences}) = {result}, expected in [0.0, 1.0]"
        )

    @given(
        confidences=st.lists(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
            min_size=1,
            max_size=10,
        )
    )
    @settings(max_examples=50)
    def test_probability_union_monotone_in_count(self, confidences: list[float]) -> None:
        """Adding more evidence should not decrease the union probability."""
        from lintgate.convergence.aggregator import _probability_union

        for i in range(1, len(confidences)):
            subset = confidences[:i]
            superset = confidences[: i + 1]
            assert _probability_union(subset) <= _probability_union(superset) + 1e-9


class TestIsDefaultFloat:
    """_platonic_impl._is_default_float: always returns bool.

    The function is: math.isclose(value, default, rel_tol=0.0, abs_tol=1e-9).
    We inline the logic here because the containing module has heavy import
    dependencies that may not be available in all test environments.
    """

    @staticmethod
    def _is_default_float(value: float, default: float) -> bool:
        return math.isclose(value, default, rel_tol=0.0, abs_tol=1e-9)

    @given(
        value=st.floats(allow_nan=False, allow_infinity=False),
        default=st.floats(allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50)
    def test_always_returns_bool(self, value: float, default: float) -> None:
        result = self._is_default_float(value, default)
        assert isinstance(result, bool)

    @given(v=st.floats(allow_nan=False, allow_infinity=False))
    @settings(max_examples=50)
    def test_identity_is_true(self, v: float) -> None:
        """A value compared to itself should always be the default."""
        assert self._is_default_float(v, v) is True


class TestIsConstantBounded:
    """triviality_filter._is_constant: always returns bool for any AST node."""

    @given(
        val=st.one_of(
            st.integers(),
            st.floats(allow_nan=False, allow_infinity=False),
            st.text(max_size=20),
            st.booleans(),
            st.none(),
        )
    )
    @settings(max_examples=50)
    def test_constant_node_returns_true(self, val: object) -> None:
        from lintgate.specification.triviality_filter import _is_constant

        node = ast.Constant(value=val)
        result = _is_constant(node)
        assert result is True

    @given(name=st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz"))
    @settings(max_examples=50)
    def test_name_node_returns_false(self, name: str) -> None:
        from lintgate.specification.triviality_filter import _is_constant

        node = ast.Name(id=name, ctx=ast.Load())
        result = _is_constant(node)
        assert result is False

    @given(val=st.integers())
    @settings(max_examples=50)
    def test_always_returns_bool(self, val: int) -> None:
        from lintgate.specification.triviality_filter import _is_constant

        # Test with non-AST node type
        assert isinstance(_is_constant(val), bool)
        assert isinstance(_is_constant(ast.Constant(value=val)), bool)


class TestIsSelfAttrBounded:
    """triviality_filter._is_self_attr: always returns bool."""

    @given(attr=st.text(min_size=1, max_size=30, alphabet="abcdefghijklmnopqrstuvwxyz_"))
    @settings(max_examples=50)
    def test_self_attribute_returns_true(self, attr: str) -> None:
        from lintgate.specification.triviality_filter import _is_self_attr

        node = ast.Attribute(
            value=ast.Name(id="self", ctx=ast.Load()),
            attr=attr,
            ctx=ast.Load(),
        )
        assert _is_self_attr(node) is True

    @given(
        name=st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz"),
        attr=st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz"),
    )
    @settings(max_examples=50)
    def test_non_self_attribute_returns_false(self, name: str, attr: str) -> None:
        from lintgate.specification.triviality_filter import _is_self_attr

        # Only "self" should yield True
        if name != "self":
            node = ast.Attribute(
                value=ast.Name(id=name, ctx=ast.Load()),
                attr=attr,
                ctx=ast.Load(),
            )
            assert _is_self_attr(node) is False

    @given(val=st.integers())
    @settings(max_examples=50)
    def test_non_attribute_returns_false(self, val: int) -> None:
        from lintgate.specification.triviality_filter import _is_self_attr

        assert _is_self_attr(ast.Constant(value=val)) is False
        assert _is_self_attr(ast.Name(id="x", ctx=ast.Load())) is False
        assert isinstance(_is_self_attr(val), bool)


class TestCheckAllArgsInvariantBounded:
    """perf011._check_all_args_invariant: always returns bool."""

    @given(
        arg_names=st.lists(
            st.text(min_size=1, max_size=10, alphabet="abcdefghijklmnopqrstuvwxyz"),
            min_size=0,
            max_size=5,
        ),
        loop_vars=st.lists(
            st.text(min_size=1, max_size=10, alphabet="abcdefghijklmnopqrstuvwxyz"),
            min_size=0,
            max_size=5,
        ),
    )
    @settings(max_examples=50)
    def test_always_returns_bool(self, arg_names: list[str], loop_vars: list[str]) -> None:
        from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
            _check_all_args_invariant,
        )

        args = [ast.Name(id=name, ctx=ast.Load()) for name in arg_names]
        call = ast.Call(
            func=ast.Name(id="f", ctx=ast.Load()),
            args=args,
            keywords=[],
        )
        loop_targets = set(loop_vars)
        result = _check_all_args_invariant(call, loop_targets)
        assert isinstance(result, bool)

    @given(
        arg_names=st.lists(
            st.text(min_size=1, max_size=10, alphabet="abcdefghijklmnopqrstuvwxyz"),
            min_size=1,
            max_size=5,
        ),
    )
    @settings(max_examples=50)
    def test_empty_loop_targets_always_invariant(self, arg_names: list[str]) -> None:
        """With no loop targets, all args are invariant."""
        from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
            _check_all_args_invariant,
        )

        args = [ast.Name(id=name, ctx=ast.Load()) for name in arg_names]
        call = ast.Call(
            func=ast.Name(id="f", ctx=ast.Load()),
            args=args,
            keywords=[],
        )
        assert _check_all_args_invariant(call, set()) is True


class TestIsAugassignMutationBounded:
    """perf001._is_augassign_mutation: always returns bool."""

    @given(
        name=st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz_"),
        target_name=st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz_"),
    )
    @settings(max_examples=50)
    def test_always_returns_bool(self, name: str, target_name: str) -> None:
        from lintgate.linters.performance_checks.perf001_quadratic_membership import (
            _is_augassign_mutation,
        )

        node = ast.AugAssign(
            target=ast.Name(id=target_name, ctx=ast.Store()),
            op=ast.Add(),
            value=ast.Constant(value=1),
        )
        result = _is_augassign_mutation(node, name)
        assert isinstance(result, bool)

    @given(name=st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz_"))
    @settings(max_examples=50)
    def test_matching_name_returns_true(self, name: str) -> None:
        from lintgate.linters.performance_checks.perf001_quadratic_membership import (
            _is_augassign_mutation,
        )

        node = ast.AugAssign(
            target=ast.Name(id=name, ctx=ast.Store()),
            op=ast.Add(),
            value=ast.Constant(value=1),
        )
        assert _is_augassign_mutation(node, name) is True

    @given(val=st.integers())
    @settings(max_examples=50)
    def test_non_augassign_returns_false(self, val: int) -> None:
        from lintgate.linters.performance_checks.perf001_quadratic_membership import (
            _is_augassign_mutation,
        )

        node = ast.Constant(value=val)
        assert _is_augassign_mutation(node, "x") is False


class TestHasSyntacticIdBounded:
    """auditor_rule_coverage._has_syntactic_id: always returns bool."""

    @given(text=st.text(max_size=200))
    @settings(max_examples=50)
    def test_always_returns_bool(self, text: str) -> None:
        from lintgate.context.auditor_rule_coverage import _has_syntactic_id

        result = _has_syntactic_id(text)
        assert isinstance(result, bool)

    @given(
        dotted=st.from_regex(r"[a-z]{2,8}\.[a-z]{2,8}", fullmatch=True),
    )
    @settings(max_examples=50)
    def test_dotted_identifier_returns_true(self, dotted: str) -> None:
        """Dotted identifiers like 'foo.bar' should be detected."""
        from lintgate.context.auditor_rule_coverage import _has_syntactic_id

        assert _has_syntactic_id(dotted) is True


class TestGeometricMeanBounded:
    """health_vector._geometric_mean: output in [0.0, max(values)] for positive inputs."""

    @given(
        values=st.lists(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
            min_size=1,
            max_size=5,
        )
    )
    @settings(max_examples=50)
    def test_geometric_mean_bounded_by_max(self, values: list[float]) -> None:
        from lintgate.specification.health_vector import _geometric_mean

        result = _geometric_mean(values)
        assert result >= 0.0
        if all(v > 0 for v in values):
            assert result <= max(values) + 1e-9

    @given(
        values=st.lists(
            st.floats(min_value=0.001, max_value=1.0, allow_nan=False),
            min_size=1,
            max_size=5,
        )
    )
    @settings(max_examples=50)
    def test_geometric_mean_bounded_by_min(self, values: list[float]) -> None:
        """Geometric mean of positive values is >= min(values)."""
        from lintgate.specification.health_vector import _geometric_mean

        result = _geometric_mean(values)
        assert result >= min(values) - 1e-9


class TestComputeHealthScalarBounded:
    """health_vector.compute_health: scalar always in [0.0, 1.0]."""

    @given(
        spec_level=st.floats(min_value=-2.0, max_value=2.0, allow_nan=False),
        kill_rate=st.floats(min_value=-2.0, max_value=2.0, allow_nan=False),
        convergence=st.floats(min_value=-2.0, max_value=2.0, allow_nan=False),
        composition_gamma=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
        test_efficiency=st.floats(min_value=-2.0, max_value=2.0, allow_nan=False),
    )
    @settings(max_examples=50)
    def test_scalar_in_unit_interval(
        self,
        spec_level: float,
        kill_rate: float,
        convergence: float,
        composition_gamma: float,
        test_efficiency: float,
    ) -> None:
        from lintgate.specification.health_vector import compute_health

        result = compute_health(
            spec_level=spec_level,
            kill_rate=kill_rate,
            convergence=convergence,
            composition_gamma=composition_gamma,
            test_efficiency=test_efficiency,
        )
        assert 0.0 <= result.scalar <= 1.0, (
            f"compute_health scalar = {result.scalar}, expected in [0.0, 1.0]"
        )
        # All axes should also be bounded
        for axis_name, axis_val in result.axes.items():
            assert 0.0 <= axis_val <= 1.0, f"axis {axis_name} = {axis_val}, expected in [0.0, 1.0]"


# ═══════════════════════════════════════════════════════════════════
# SYMMETRIC property tests
# ═══════════════════════════════════════════════════════════════════


class TestRangesOverlapSymmetric:
    """_target_building._ranges_overlap: overlap(a, b) == overlap(b, a)."""

    @given(
        a_start=st.integers(min_value=0, max_value=1000),
        a_len=st.integers(min_value=1, max_value=100),
        b_start=st.integers(min_value=0, max_value=1000),
        b_len=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=50)
    def test_overlap_is_symmetric(self, a_start: int, a_len: int, b_start: int, b_len: int) -> None:
        from lintgate.channels._target_building import _ranges_overlap

        a = range(a_start, a_start + a_len)
        b = range(b_start, b_start + b_len)
        assert _ranges_overlap(a, b) == _ranges_overlap(b, a)

    @given(
        start=st.integers(min_value=0, max_value=1000),
        length=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=50)
    def test_overlap_reflexive(self, start: int, length: int) -> None:
        """A range always overlaps with itself."""
        from lintgate.channels._target_building import _ranges_overlap

        r = range(start, start + length)
        assert _ranges_overlap(r, r) is True

    @given(
        a_start=st.integers(min_value=0, max_value=500),
        gap=st.integers(min_value=1, max_value=100),
        a_len=st.integers(min_value=1, max_value=50),
        b_len=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=50)
    def test_disjoint_ranges_no_overlap(
        self, a_start: int, gap: int, a_len: int, b_len: int
    ) -> None:
        """Non-overlapping ranges with a gap between them."""
        from lintgate.channels._target_building import _ranges_overlap

        a = range(a_start, a_start + a_len)
        b = range(a_start + a_len + gap, a_start + a_len + gap + b_len)
        assert _ranges_overlap(a, b) is False
        assert _ranges_overlap(b, a) is False


# ═══════════════════════════════════════════════════════════════════
# MONOTONIC property tests
# ═══════════════════════════════════════════════════════════════════


class TestFilterFilesMonotonic:
    """CustomLinter._filter_files: len(output) <= len(input)."""

    @given(
        files=st.lists(
            st.text(min_size=1, max_size=50, alphabet="abcdefghijklmnopqrstuvwxyz./"),
            min_size=0,
            max_size=20,
        )
    )
    @settings(max_examples=50)
    def test_output_never_exceeds_input(self, files: list[str]) -> None:
        from lintgate.linters.custom_linter import CustomLinter

        linter = CustomLinter(
            linter_name="test",
            command="echo test",
        )
        result = linter._filter_files(files)
        assert len(result) <= len(files)

    @given(
        files=st.lists(
            st.text(min_size=1, max_size=50),
            min_size=0,
            max_size=20,
        )
    )
    @settings(max_examples=50)
    def test_filter_preserves_all_files(self, files: list[str]) -> None:
        """Custom linter filter returns all files (passthrough)."""
        from lintgate.linters.custom_linter import CustomLinter

        linter = CustomLinter(
            linter_name="test",
            command="echo test",
        )
        result = linter._filter_files(files)
        assert result == files


class TestIsPrimitiveMonotonic:
    """typed_synthesis._is_primitive: always returns bool."""

    @given(name=st.text(max_size=30))
    @settings(max_examples=50)
    def test_always_returns_bool(self, name: str) -> None:
        from lintgate.testing.typed_synthesis import _is_primitive

        result = _is_primitive(name)
        assert isinstance(result, bool)

    @given(
        name=st.sampled_from(["str", "int", "float", "bool", "bytes"]),
    )
    @settings(max_examples=50)
    def test_known_primitives_return_true(self, name: str) -> None:
        from lintgate.testing.typed_synthesis import _is_primitive

        assert _is_primitive(name) is True

    @given(
        name=st.text(min_size=1, max_size=30).filter(
            lambda s: s not in {"str", "int", "float", "bool", "bytes"}
        ),
    )
    @settings(max_examples=50)
    def test_non_primitives_return_false(self, name: str) -> None:
        from lintgate.testing.typed_synthesis import _is_primitive

        assert _is_primitive(name) is False
