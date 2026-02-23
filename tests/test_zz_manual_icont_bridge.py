import ast

from lintgate.integrations.icontract_bridge import generate_icontract_decorators
from lintgate.linters.performance_checks.algebra_types import (
    AlgebraicProperty,
    BoundSpec,
    FunctionProperties,
    PropertyKind,
    PurityResult,
)


def test_icontract_bridge_basic():
    # Pure
    props = FunctionProperties(
        purity=PurityResult(is_pure=True, parameter_count=2, function_name="f", qualified_name="f", line=1, confidence=1.0, side_effects=(), return_annotation=None),
        properties=[
            AlgebraicProperty(kind=PropertyKind.COMMUTATIVE, confidence=1.0, evidence=""),
            AlgebraicProperty(kind=PropertyKind.BOUNDED, confidence=1.0, evidence="", bound_spec=BoundSpec(lower=0, upper=10, source=ast.Pass())),
            AlgebraicProperty(kind=PropertyKind.BOUNDED, confidence=1.0, evidence="", bound_spec=BoundSpec(lower=0, upper=None, source=ast.Pass())),
            AlgebraicProperty(kind=PropertyKind.BOUNDED, confidence=1.0, evidence="", bound_spec=BoundSpec(lower=None, upper=10, source=ast.Pass())),
        ],
        optimization_hints=set()
    )
    result = generate_icontract_decorators(props)
    assert len(result) == 3

def test_icontract_bridge_impure():
    props = FunctionProperties(
        purity=PurityResult(is_pure=False, parameter_count=0, function_name="f", qualified_name="f", line=1, confidence=1.0, side_effects=(), return_annotation=None),
        properties=[],
        optimization_hints=set()
    )
    result = generate_icontract_decorators(props)
    assert result == []
