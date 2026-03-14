"""Tests for lintgate/integrations/hypothesis_bridge.py — Hypothesis test generation."""

from __future__ import annotations

from lintgate.integrations.hypothesis_bridge import (
    _render_property_test,
    generate_hypothesis_template,
)
from lintgate.linters.performance_checks.algebra_types import (
    AlgebraicProperty,
    BoundSpec,
    FunctionProperties,
    PropertyKind,
    PurityResult,
)


def _pure_result(name: str = "add", param_count: int = 2) -> PurityResult:
    return PurityResult(
        function_name=name,
        qualified_name=f"module.{name}",
        line=1,
        is_pure=True,
        confidence=1.0,
        side_effects=(),
        parameter_count=param_count,
        return_annotation=None,
    )


def _impure_result(name: str = "write_file") -> PurityResult:
    return PurityResult(
        function_name=name,
        qualified_name=f"module.{name}",
        line=1,
        is_pure=False,
        confidence=0.5,
        side_effects=(),
        parameter_count=1,
        return_annotation=None,
    )


# ── _render_property_test ─────────────────────────────────────────


def test_render_commutative() -> None:
    prop = AlgebraicProperty(kind=PropertyKind.COMMUTATIVE, confidence=1.0, evidence="test")
    result = _render_property_test("add", prop, "x=st.integers(), y=st.integers()", "x, y")
    assert result is not None
    assert "commutative" in result
    assert "add(x, y) == add(y, x)" in result


def test_render_associative() -> None:
    prop = AlgebraicProperty(kind=PropertyKind.ASSOCIATIVE, confidence=1.0, evidence="test")
    result = _render_property_test("add", prop, "x=st.integers()", "x")
    assert result is not None
    assert "associative" in result


def test_render_idempotent() -> None:
    prop = AlgebraicProperty(kind=PropertyKind.IDEMPOTENT, confidence=1.0, evidence="test")
    result = _render_property_test("norm", prop, "x=st.integers()", "x")
    assert result is not None
    assert "idempotent" in result
    assert "norm(norm(x)) == norm(x)" in result


def test_render_bounded_with_both_bounds() -> None:
    prop = AlgebraicProperty(
        kind=PropertyKind.BOUNDED,
        confidence=1.0,
        evidence="clamp",
        bound_spec=BoundSpec(lower=0.0, upper=1.0, source="clamp"),
    )
    result = _render_property_test("clamp", prop, "x=st.integers()", "x")
    assert result is not None
    assert "result >= 0.0" in result
    assert "result <= 1.0" in result


def test_render_bounded_no_bound_spec_returns_none() -> None:
    prop = AlgebraicProperty(
        kind=PropertyKind.BOUNDED,
        confidence=1.0,
        evidence="clamp",
        bound_spec=None,
    )
    result = _render_property_test("clamp", prop, "x=st.integers()", "x")
    assert result is None


def test_render_pure_returns_none_for_unknown_kind() -> None:
    prop = AlgebraicProperty(kind=PropertyKind.PURE, confidence=1.0, evidence="pure")
    result = _render_property_test("func", prop, "x=st.integers()", "x")
    assert result is None


# ── generate_hypothesis_template ──────────────────────────────────


def test_generate_template_pure_with_properties() -> None:
    purity = _pure_result()
    props = [AlgebraicProperty(kind=PropertyKind.COMMUTATIVE, confidence=1.0, evidence="test")]
    fp = FunctionProperties(purity=purity, properties=tuple(props), optimization_hints=())
    result = generate_hypothesis_template("add", fp)
    assert result is not None
    assert "commutative" in result
    assert "from hypothesis" in result


def test_generate_template_impure_returns_none() -> None:
    purity = _impure_result()
    fp = FunctionProperties(purity=purity, properties=(), optimization_hints=())
    result = generate_hypothesis_template("write_file", fp)
    assert result is None


def test_generate_template_no_properties_makes_crash_test() -> None:
    purity = _pure_result("identity", 1)
    fp = FunctionProperties(purity=purity, properties=(), optimization_hints=())
    result = generate_hypothesis_template("identity", fp)
    assert result is not None
    assert "does_not_crash" in result


def test_generate_template_tuple_input() -> None:
    """Should also accept (PurityResult, list[AlgebraicProperty]) tuple."""
    purity = _pure_result()
    props = [AlgebraicProperty(kind=PropertyKind.COMMUTATIVE, confidence=1.0, evidence="test")]
    result = generate_hypothesis_template("add", (purity, props))
    assert result is not None
    assert "commutative" in result
