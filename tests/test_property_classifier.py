from lintgate.linters.performance_checks.algebra_types import PropertyKind, PurityResult
from lintgate.linters.performance_checks.properties import classify_properties


def _get_func_node(code):
    import ast
    tree = ast.parse(code)
    return tree.body[0]

def _mock_purity(name, params=1):
    return PurityResult(
        is_pure=True,
        parameter_count=params,
        function_name=name,
        qualified_name=name,
        line=1,
        confidence=1.0,
        side_effects=(),
        return_annotation=None
    )

def test_detect_idempotent():
    code = "def to_int(x: int) -> int: return int(x)"
    node = _get_func_node(code)
    purity = _mock_purity("to_int")
    # Purity result needs the return annotation
    purity = PurityResult(
        function_name=purity.function_name,
        qualified_name=purity.qualified_name,
        line=purity.line,
        is_pure=purity.is_pure,
        confidence=purity.confidence,
        side_effects=purity.side_effects,
        parameter_count=purity.parameter_count,
        return_annotation="int"
    )
    props = classify_properties(node, purity)
    kinds = [p.kind for p in props.properties]
    assert PropertyKind.IDEMPOTENT in kinds

def test_detect_commutative_associative():
    code = "def add(a, b): return a + b"
    node = _get_func_node(code)
    purity = _mock_purity("add", 2)
    props = classify_properties(node, purity)
    kinds = [p.kind for p in props.properties]
    assert PropertyKind.COMMUTATIVE in kinds
    assert PropertyKind.ASSOCIATIVE in kinds

def test_detect_monotonic():
    code = "def increment(x): return x + 1"
    node = _get_func_node(code)
    purity = _mock_purity("increment")
    props = classify_properties(node, purity)
    kinds = [p.kind for p in props.properties]
    assert PropertyKind.MONOTONIC in kinds

def test_detect_bounded():
    code = "def clamp(x): return max(0, min(x, 10))"
    node = _get_func_node(code)
    purity = _mock_purity("clamp")
    props = classify_properties(node, purity)
    kinds = [p.kind for p in props.properties]
    assert PropertyKind.BOUNDED in kinds

def test_detect_complex_associative():
    # Bitwise ops are also associative
    code = "def bitwise_or(a, b): return a | b"
    node = _get_func_node(code)
    purity = _mock_purity("bitwise_or", 2)
    props = classify_properties(node, purity)
    kinds = [p.kind for p in props.properties]
    assert PropertyKind.ASSOCIATIVE in kinds
    assert PropertyKind.COMMUTATIVE in kinds
