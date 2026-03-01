"""Bridge between algebraic properties and Hypothesis test generation."""

from __future__ import annotations

from lintgate.linters.performance_checks.algebra_types import (
    AlgebraicProperty,
    FunctionProperties,
    PropertyKind,
    PurityResult,
)


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

    tests = []

    args_list = (
        ["x", "y", "z"][: purity.parameter_count]
        if purity.parameter_count > 0
        else ["x"]
    )
    args_str = ", ".join(f"{arg}=st.integers()" for arg in args_list)
    args_call = ", ".join(args_list)

    for prop in props_list:
        if prop.kind == PropertyKind.COMMUTATIVE:
            tests.append(f"""@given(x=st.integers(), y=st.integers())
def test_{func_name}_is_commutative(x, y):
    assert {func_name}(x, y) == {func_name}(y, x)
""")
        elif prop.kind == PropertyKind.ASSOCIATIVE:
            tests.append(f"""@given(x=st.integers(), y=st.integers(), z=st.integers())
def test_{func_name}_is_associative(x, y, z):
    assert {func_name}(x, {func_name}(y, z)) == {func_name}({func_name}(x, y), z)
""")
        elif prop.kind == PropertyKind.IDEMPOTENT:
            tests.append(f"""@given(x=st.integers())
def test_{func_name}_is_idempotent(x):
    assert {func_name}({func_name}(x)) == {func_name}(x)
""")
        elif prop.kind == PropertyKind.BOUNDED and prop.bound_spec:
            bounds = []
            if prop.bound_spec.lower is not None:
                bounds.append(f"result >= {prop.bound_spec.lower}")
            if prop.bound_spec.upper is not None:
                bounds.append(f"result <= {prop.bound_spec.upper}")

            check = " and ".join(bounds)
            if not check:
                continue

            tests.append(f"""@given({args_str})
def test_{func_name}_is_bounded({args_call}):
    result = {func_name}({args_call})
    assert {check}
""")

    if not tests:
        # Just a basic fuzz test if no interesting properties
        tests.append(f"""@given({args_str})
def test_{func_name}_does_not_crash({args_call}):
    {func_name}({args_call})
""")

    lines.extend(tests)
    return "\n".join(lines)
