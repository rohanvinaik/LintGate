import ast

from lintgate.integrations.hypothesis_bridge import generate_hypothesis_template
from lintgate.linters.performance_checks.algebra_types import (
    AlgebraicProperty,
    BoundSpec,
    FunctionProperties,
    PropertyKind,
    PurityResult,
)


def test_hypothesis_bridge_basic():
    # Pure
    props = FunctionProperties(
        purity=PurityResult(
            is_pure=True,
            parameter_count=2,
            function_name="f",
            qualified_name="f",
            line=1,
            confidence=1.0,
            side_effects=(),
            return_annotation=None,
        ),
        properties=[
            AlgebraicProperty(
                kind=PropertyKind.COMMUTATIVE, confidence=1.0, evidence=""
            ),
            AlgebraicProperty(
                kind=PropertyKind.IDEMPOTENT, confidence=1.0, evidence=""
            ),
            AlgebraicProperty(
                kind=PropertyKind.ASSOCIATIVE, confidence=1.0, evidence=""
            ),
            AlgebraicProperty(
                kind=PropertyKind.BOUNDED,
                confidence=1.0,
                evidence="",
                bound_spec=BoundSpec(lower=0, upper=10, source=ast.Pass()),
            ),
        ],
        optimization_hints=set(),
    )
    result = generate_hypothesis_template("my_func", props)
    assert result is not None
    assert "test_my_func_is_commutative" in result


def test_hypothesis_bridge_impure():
    props = FunctionProperties(
        purity=PurityResult(
            is_pure=False,
            parameter_count=0,
            function_name="f",
            qualified_name="f",
            line=1,
            confidence=1.0,
            side_effects=(),
            return_annotation=None,
        ),
        properties=[],
        optimization_hints=set(),
    )
    result = generate_hypothesis_template("my_func", props)
    assert result is None
