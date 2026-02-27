import ast

from lintgate.linters.performance_checks.algebra_types import PropertyKind, PurityResult
from lintgate.linters.performance_checks.properties import classify_properties
from lintgate.mutation.state import FunctionMutationState


def test_classify_properties_with_low_survival():
    """Verify that low survival increases confidence."""
    node = ast.parse("def f(): return 1").body[0]
    purity = PurityResult("f", "f", 1, True, 0.7, (), 0, "int")

    # Low survival (0.1)
    state = FunctionMutationState(
        file_path="f.py",
        function_name="f",
        code_hash="abc",
        test_hash="def",
        total=10,
        killed=9,
        survived=1,
    )

    props = classify_properties(node, purity, state)
    pure_prop = [p for p in props.properties if p.kind == PropertyKind.PURE][0]

    assert pure_prop.confidence == 0.9
    assert "[MUTATION VERIFIED" in pure_prop.evidence


def test_classify_properties_with_moderate_survival():
    """Verify that moderate survival penalizes confidence."""
    node = ast.parse("def f(): return 1").body[0]
    purity = PurityResult("f", "f", 1, True, 0.8, (), 0, "int")

    # Moderate survival (0.4)
    state = FunctionMutationState(
        file_path="f.py",
        function_name="f",
        code_hash="abc",
        test_hash="def",
        total=10,
        killed=6,
        survived=4,
    )

    props = classify_properties(node, purity, state)
    pure_prop = [p for p in props.properties if p.kind == PropertyKind.PURE][0]

    assert pure_prop.confidence == 0.4  # 0.8 * 0.5
    assert "[MUTATION PENALIZED" in pure_prop.evidence


def test_classify_properties_with_high_survival():
    """Verify that high survival gates/refutes purity."""
    node = ast.parse("def f(): return 1").body[0]
    purity = PurityResult("f", "f", 1, True, 0.9, (), 0, "int")

    # High survival (0.7)
    state = FunctionMutationState(
        file_path="f.py",
        function_name="f",
        code_hash="abc",
        test_hash="def",
        total=10,
        killed=3,
        survived=7,
    )

    props = classify_properties(node, purity, state)
    pure_prop = [p for p in props.properties if p.kind == PropertyKind.PURE][0]

    assert pure_prop.confidence == 0.1
    assert "[MUTATION GATED" in pure_prop.evidence


def test_classify_properties_without_mutation_state():
    """Verify default behavior when mutation state is missing."""
    node = ast.parse("def f(): return 1").body[0]
    purity = PurityResult("f", "f", 1, True, 0.7, (), 0, "int")

    props = classify_properties(node, purity, None)
    pure_prop = [p for p in props.properties if p.kind == PropertyKind.PURE][0]

    assert pure_prop.confidence == 0.7
    assert "[MUTATION" not in pure_prop.evidence
