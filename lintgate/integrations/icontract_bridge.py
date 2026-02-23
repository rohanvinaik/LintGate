"""Bridge between algebraic properties and icontract decorators."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintgate.linters.performance_checks.algebra_types import (
    AlgebraicProperty,
    FunctionProperties,
    PropertyKind,
    PurityResult,
)


def generate_icontract_decorators(
    properties: FunctionProperties | tuple[PurityResult, list[AlgebraicProperty]],
) -> list[str]:
    """Generate icontract decorators for the given properties."""
    if isinstance(properties, tuple):
        purity, props_list = properties
    else:
        purity = properties.purity
        props_list = list(properties.properties)

    if not purity.is_pure:
        return []

    decorators = []

    for prop in props_list:
        if prop.kind == PropertyKind.BOUNDED and prop.bound_spec:
            if prop.bound_spec.lower is not None and prop.bound_spec.upper is not None:
                decorators.append(
                    f"@icontract.ensure(lambda result: {prop.bound_spec.lower} <= result <= {prop.bound_spec.upper})"
                )
            elif prop.bound_spec.lower is not None:
                decorators.append(
                    f"@icontract.ensure(lambda result: result >= {prop.bound_spec.lower})"
                )
            elif prop.bound_spec.upper is not None:
                decorators.append(
                    f"@icontract.ensure(lambda result: result <= {prop.bound_spec.upper})"
                )

    # A potential extension is to add @icontract.snapshot to track inputs for monotonically increasing checks, etc.

    return decorators
