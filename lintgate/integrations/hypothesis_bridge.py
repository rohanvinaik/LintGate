"""Bridge between algebraic properties and Hypothesis test generation."""

from __future__ import annotations

from lintgate.linters.performance_checks.algebra_types import (
    AlgebraicProperty,
    FunctionProperties,
    PropertyKind,
    PurityResult,
)


def _render_property_test(
    func_name: str,
    prop: AlgebraicProperty,
    args_str: str,
    args_call: str,
) -> str | None:
    """Render a single property test template. Returns None if not applicable."""
    if prop.kind == PropertyKind.COMMUTATIVE:
        return (
            f"@given(x=st.integers(), y=st.integers())\n"
            f"def test_{func_name}_is_commutative(x, y):\n"
            f"    assert {func_name}(x, y) == {func_name}(y, x)\n"
        )
    if prop.kind == PropertyKind.ASSOCIATIVE:
        return (
            f"@given(x=st.integers(), y=st.integers(), z=st.integers())\n"
            f"def test_{func_name}_is_associative(x, y, z):\n"
            f"    assert {func_name}(x, {func_name}(y, z)) == {func_name}({func_name}(x, y), z)\n"
        )
    if prop.kind == PropertyKind.IDEMPOTENT:
        return (
            f"@given(x=st.integers())\n"
            f"def test_{func_name}_is_idempotent(x):\n"
            f"    assert {func_name}({func_name}(x)) == {func_name}(x)\n"
        )
    if prop.kind == PropertyKind.BOUNDED and prop.bound_spec:
        bounds = []
        if prop.bound_spec.lower is not None:
            bounds.append(f"result >= {prop.bound_spec.lower}")
        if prop.bound_spec.upper is not None:
            bounds.append(f"result <= {prop.bound_spec.upper}")
        check = " and ".join(bounds)
        if not check:
            return None
        return (
            f"@given({args_str})\n"
            f"def test_{func_name}_is_bounded({args_call}):\n"
            f"    result = {func_name}({args_call})\n"
            f"    assert {check}\n"
        )
    return None


def generate_hypothesis_template(
    func_name: str,
    properties: FunctionProperties | tuple[PurityResult, list[AlgebraicProperty]],
) -> str | None:
    """Generate a Hypothesis property test template based on detected traits."""
    if isinstance(properties, tuple):
        purity: PurityResult = properties[0]
        props_list: list[AlgebraicProperty] = properties[1]
    else:
        purity = properties.purity
        props_list = list(properties.properties)

    if not purity.is_pure:
        return None

    lines = [
        "from hypothesis import given, strategies as st",
        f"from ... import {func_name}  # TODO: fix import path",
        "",
    ]

    args_list = ["x", "y", "z"][: purity.parameter_count] if purity.parameter_count > 0 else ["x"]
    args_str = ", ".join(f"{arg}=st.integers()" for arg in args_list)
    args_call = ", ".join(args_list)

    tests = [
        t
        for prop in props_list
        if (t := _render_property_test(func_name, prop, args_str, args_call)) is not None
    ]

    if not tests:
        tests.append(
            f"@given({args_str})\n"
            f"def test_{func_name}_does_not_crash({args_call}):\n"
            f"    {func_name}({args_call})\n"
        )

    lines.extend(tests)
    return "\n".join(lines)
