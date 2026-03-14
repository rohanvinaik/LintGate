"""Tests for lintgate/integrations/icontract_bridge.py — icontract decorator generation."""

from __future__ import annotations

from lintgate.integrations.icontract_bridge import generate_icontract_decorators
from lintgate.linters.performance_checks.algebra_types import (
    AlgebraicProperty,
    BoundSpec,
    FunctionProperties,
    PropertyKind,
    PurityResult,
)


def _pure_result(name: str = "clamp") -> PurityResult:
    return PurityResult(
        function_name=name,
        qualified_name=f"module.{name}",
        line=1,
        is_pure=True,
        confidence=1.0,
        side_effects=(),
        parameter_count=2,
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


# ── generate_icontract_decorators ─────────────────────────────────


def test_bounded_both_bounds() -> None:
    purity = _pure_result()
    prop = AlgebraicProperty(
        kind=PropertyKind.BOUNDED,
        confidence=1.0,
        evidence="clamp",
        bound_spec=BoundSpec(lower=0.0, upper=1.0, source="clamp"),
    )
    fp = FunctionProperties(purity=purity, properties=(prop,), optimization_hints=())
    result = generate_icontract_decorators(fp)
    assert len(result) == 1
    assert "0.0 <= result <= 1.0" in result[0]


def test_bounded_lower_only() -> None:
    purity = _pure_result()
    prop = AlgebraicProperty(
        kind=PropertyKind.BOUNDED,
        confidence=1.0,
        evidence="positive",
        bound_spec=BoundSpec(lower=0.0, upper=None, source="positive"),
    )
    fp = FunctionProperties(purity=purity, properties=(prop,), optimization_hints=())
    result = generate_icontract_decorators(fp)
    assert len(result) == 1
    assert "result >= 0.0" in result[0]


def test_bounded_upper_only() -> None:
    purity = _pure_result()
    prop = AlgebraicProperty(
        kind=PropertyKind.BOUNDED,
        confidence=1.0,
        evidence="max",
        bound_spec=BoundSpec(lower=None, upper=100.0, source="max"),
    )
    fp = FunctionProperties(purity=purity, properties=(prop,), optimization_hints=())
    result = generate_icontract_decorators(fp)
    assert len(result) == 1
    assert "result <= 100.0" in result[0]


def test_impure_returns_empty() -> None:
    purity = _impure_result()
    fp = FunctionProperties(purity=purity, properties=(), optimization_hints=())
    result = generate_icontract_decorators(fp)
    assert result == []


def test_non_bounded_properties_return_empty() -> None:
    purity = _pure_result()
    prop = AlgebraicProperty(kind=PropertyKind.COMMUTATIVE, confidence=1.0, evidence="test")
    fp = FunctionProperties(purity=purity, properties=(prop,), optimization_hints=())
    result = generate_icontract_decorators(fp)
    assert result == []


def test_tuple_input_format() -> None:
    """Should also accept (PurityResult, list[AlgebraicProperty]) tuple."""
    purity = _pure_result()
    prop = AlgebraicProperty(
        kind=PropertyKind.BOUNDED,
        confidence=1.0,
        evidence="clamp",
        bound_spec=BoundSpec(lower=0.0, upper=1.0, source="clamp"),
    )
    result = generate_icontract_decorators((purity, [prop]))
    assert len(result) == 1
    assert "0.0 <= result <= 1.0" in result[0]


def test_bounded_no_bound_spec_skipped() -> None:
    purity = _pure_result()
    prop = AlgebraicProperty(
        kind=PropertyKind.BOUNDED,
        confidence=1.0,
        evidence="none",
        bound_spec=None,
    )
    fp = FunctionProperties(purity=purity, properties=(prop,), optimization_hints=())
    result = generate_icontract_decorators(fp)
    assert result == []
