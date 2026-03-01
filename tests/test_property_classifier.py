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
        return_annotation=None,
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
        return_annotation="int",
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


def test_detect_bounded_nested_order():
    # Nested in different order
    code = "def clamp(x): return min(100, max(x, -100))"
    node = _get_func_node(code)
    purity = _mock_purity("clamp")
    props = classify_properties(node, purity)
    prop = [p for p in props.properties if p.kind == PropertyKind.BOUNDED][0]
    assert prop.bound_spec.lower == -100.0
    assert prop.bound_spec.upper == 100.0


def test_detect_complex_associative():
    # Bitwise ops are also associative
    code = "def bitwise_or(a, b): return a | b"
    node = _get_func_node(code)
    purity = _mock_purity("bitwise_or", 2)
    props = classify_properties(node, purity)
    kinds = [p.kind for p in props.properties]
    assert PropertyKind.ASSOCIATIVE in kinds
    assert PropertyKind.COMMUTATIVE in kinds


# ── A1: Type-aware commutativity/associativity ────────────────────────


def test_str_concat_not_commutative():
    """String concatenation is NOT commutative — should produce no comm/assoc."""
    code = "def concat(a: str, b: str) -> str: return a + b"
    node = _get_func_node(code)
    purity = _mock_purity("concat", 2)
    props = classify_properties(node, purity)
    kinds = [p.kind for p in props.properties]
    assert PropertyKind.COMMUTATIVE not in kinds
    assert PropertyKind.ASSOCIATIVE not in kinds


def test_list_concat_not_commutative():
    """List concatenation is NOT commutative."""
    code = "def merge(a: list, b: list) -> list: return a + b"
    node = _get_func_node(code)
    purity = _mock_purity("merge", 2)
    props = classify_properties(node, purity)
    kinds = [p.kind for p in props.properties]
    assert PropertyKind.COMMUTATIVE not in kinds
    assert PropertyKind.ASSOCIATIVE not in kinds


def test_int_add_commutative_high_confidence():
    """Annotated int addition → commutative with confidence >= 0.9."""
    code = "def add(a: int, b: int) -> int: return a + b"
    node = _get_func_node(code)
    purity = _mock_purity("add", 2)
    props = classify_properties(node, purity)
    kinds = [p.kind for p in props.properties]
    assert PropertyKind.COMMUTATIVE in kinds
    assert PropertyKind.ASSOCIATIVE in kinds
    comm_prop = [p for p in props.properties if p.kind == PropertyKind.COMMUTATIVE][0]
    assert comm_prop.confidence >= 0.9
    assert comm_prop.type_context is not None
    assert comm_prop.type_context["a"] == "int"


def test_set_union_commutative():
    """Set union is commutative + associative."""
    code = "def union(a: set, b: set) -> set: return a | b"
    node = _get_func_node(code)
    purity = _mock_purity("union", 2)
    props = classify_properties(node, purity)
    kinds = [p.kind for p in props.properties]
    assert PropertyKind.COMMUTATIVE in kinds
    assert PropertyKind.ASSOCIATIVE in kinds
    comm_prop = [p for p in props.properties if p.kind == PropertyKind.COMMUTATIVE][0]
    assert comm_prop.confidence >= 0.9


def test_unannotated_add_reduced_confidence():
    """Unannotated add → commutative with reduced confidence (0.6)."""
    code = "def unknown(a, b): return a + b"
    node = _get_func_node(code)
    purity = _mock_purity("unknown", 2)
    props = classify_properties(node, purity)
    kinds = [p.kind for p in props.properties]
    assert PropertyKind.COMMUTATIVE in kinds
    comm_prop = [p for p in props.properties if p.kind == PropertyKind.COMMUTATIVE][0]
    assert comm_prop.confidence == 0.6
    assert comm_prop.type_context is None


def test_type_context_populated():
    """AlgebraicProperty.type_context populated when annotations available."""
    code = "def mul(x: float, y: float) -> float: return x * y"
    node = _get_func_node(code)
    purity = _mock_purity("mul", 2)
    props = classify_properties(node, purity)
    comm_prop = [p for p in props.properties if p.kind == PropertyKind.COMMUTATIVE][0]
    assert comm_prop.type_context == {"x": "float", "y": "float"}
