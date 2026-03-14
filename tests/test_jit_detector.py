"""Tests for the JIT candidate detector."""

import ast

from lintgate.linters.performance_checks.jit_detector import (
    JitCandidate,
    _annotation_text,
    _compute_band,
    _count_allocations,
    _count_arithmetic,
    _count_loops,
    _count_statements,
    _is_numeric_annotation,
    _recommend_backend,
    _score_arithmetic_intensity,
    _score_loop_density,
    _score_low_allocation,
    _score_numeric_signature,
    detect_jit_candidates,
)
from lintgate.linters.performance_checks.purity import analyze_purity


def _candidates_for(code: str, file_path: str = "") -> list[JitCandidate]:
    """Parse *code*, run purity analysis, and return JIT candidates."""
    tree = ast.parse(code)
    purity = analyze_purity(tree)
    return detect_jit_candidates(tree, purity, file_path=file_path)


# ------------------------------------------------------------------
# Core filtering
# ------------------------------------------------------------------


def test_impure_function_excluded():
    code = """
def impure_sum(xs):
    global total
    total = 0
    for x in xs:
        total += x
    return total
"""
    candidates = _candidates_for(code)
    assert candidates == [], "Impure functions must never appear as JIT candidates"


def test_empty_tree_returns_empty():
    tree = ast.parse("")
    purity = analyze_purity(tree)
    candidates = detect_jit_candidates(tree, purity)
    assert candidates == []


# ------------------------------------------------------------------
# Scoring: high-value numeric kernel
# ------------------------------------------------------------------


def test_pure_numeric_loop_scores_high():
    code = """
def dot_product(a: list[float], b: list[float]) -> float:
    total = 0.0
    for i in range(len(a)):
        total += a[i] * b[i]
    return total
"""
    candidates = _candidates_for(code)
    assert len(candidates) == 1
    c = candidates[0]
    assert c.function_name == "dot_product"
    assert c.jit_band in ("HIGH", "MEDIUM")
    assert c.jit_score >= 0.3
    # Numeric annotations should contribute
    assert c.factors["numeric_signature"] > 0.0
    # Has a loop
    assert c.factors["loop_density"] > 0.0
    # Has arithmetic
    assert c.factors["arithmetic_intensity"] > 0.0


# ------------------------------------------------------------------
# Scoring: allocation-heavy function
# ------------------------------------------------------------------


def test_object_heavy_function_scores_low():
    code = """
def build_records(n: int) -> list[float]:
    results = list()
    mapping = dict()
    extras = set()
    backup = dict()
    another = list()
    more = set()
    for i in range(n):
        results.append(float(i))
    return results
"""
    candidates = _candidates_for(code)
    # Either excluded entirely (score < 0.2) or has a low allocation factor
    if candidates:
        c = candidates[0]
        assert c.factors["low_allocation"] < 0.5


# ------------------------------------------------------------------
# Scoring: no annotations
# ------------------------------------------------------------------


def test_no_annotations_reduces_score():
    code = """
def add(a, b):
    return a + b
"""
    candidates = _candidates_for(code)
    # With no annotations and minimal body, score should be very low
    if candidates:
        assert candidates[0].factors["numeric_signature"] == 0.0


# ------------------------------------------------------------------
# Scoring: arithmetic-intensive
# ------------------------------------------------------------------


def test_arithmetic_intensive_function():
    code = """
def polynomial(x: float) -> float:
    return x * x * x + 3 * x * x - 2 * x + 1
"""
    candidates = _candidates_for(code)
    assert len(candidates) >= 1
    c = candidates[0]
    assert c.factors["arithmetic_intensity"] > 0.5


# ------------------------------------------------------------------
# Backend recommendations
# ------------------------------------------------------------------


def test_recommended_backend_numba_for_loops():
    code = """
def sum_range(n: int) -> int:
    total = 0
    for i in range(n):
        total += i
    return total
"""
    candidates = _candidates_for(code)
    assert len(candidates) >= 1
    c = candidates[0]
    assert c.factors["loop_density"] > 0.3
    assert c.recommended_backend == "numba"


def test_recommended_backend_jax_for_arithmetic():
    code = """
def transform(x: float, y: float, z: float) -> float:
    return x * y + z * x - y / z + x ** 2
"""
    candidates = _candidates_for(code)
    assert len(candidates) >= 1
    c = candidates[0]
    # No loops, but heavy arithmetic -> jax
    assert c.factors["loop_density"] == 0.0
    if c.factors["arithmetic_intensity"] > 0.5:
        assert c.recommended_backend == "jax"


# ------------------------------------------------------------------
# Band thresholds
# ------------------------------------------------------------------


def test_band_thresholds():
    # HIGH band: needs score > 0.7
    # Build a function that scores very high on every factor
    high_code = """
def kernel(a: float, b: float, c: float) -> float:
    total = 0.0
    for i in range(100):
        total += a * b + c - a / b
    return total
"""
    candidates = _candidates_for(high_code)
    assert len(candidates) >= 1
    c = candidates[0]
    # Verify band assignment is consistent with score
    if c.jit_score > 0.7:
        assert c.jit_band == "HIGH"
    elif c.jit_score > 0.3:
        assert c.jit_band == "MEDIUM"
    else:
        assert c.jit_band == "LOW"

    # LOW band: minimal function with no numeric types, no loops
    low_code = """
def identity(x):
    return x
"""
    low_candidates = _candidates_for(low_code)
    # Either excluded (score < 0.2) or LOW band
    for lc in low_candidates:
        if lc.jit_score <= 0.3:
            assert lc.jit_band == "LOW"


# ==================================================================
# Direct unit tests for internal helpers
# ==================================================================


def _parse_func(code: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Parse a single function definition from *code*."""
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise ValueError("No function found in code")


def _parse_body(code: str) -> list[ast.stmt]:
    """Parse *code* and return the body statements of the first function."""
    return _parse_func(code).body


# ------------------------------------------------------------------
# _annotation_text
# ------------------------------------------------------------------


class TestAnnotationText:
    def test_none_returns_empty(self):
        assert _annotation_text(None) == ""

    def test_simple_name(self):
        tree = ast.parse("x: float = 0")
        ann_node = tree.body[0].annotation  # type: ignore[attr-defined]
        assert _annotation_text(ann_node) == "float"

    def test_complex_annotation(self):
        tree = ast.parse("x: list[int] = []")
        ann_node = tree.body[0].annotation  # type: ignore[attr-defined]
        result = _annotation_text(ann_node)
        assert result == "list[int]"

    def test_nested_annotation(self):
        tree = ast.parse("x: dict[str, list[float]] = {}")
        ann_node = tree.body[0].annotation  # type: ignore[attr-defined]
        result = _annotation_text(ann_node)
        assert "dict" in result
        assert "float" in result


# ------------------------------------------------------------------
# _is_numeric_annotation
# ------------------------------------------------------------------


class TestIsNumericAnnotation:
    def test_empty_string_is_false(self):
        assert _is_numeric_annotation("") is False

    def test_int_is_numeric(self):
        assert _is_numeric_annotation("int") is True

    def test_float_is_numeric(self):
        assert _is_numeric_annotation("float") is True

    def test_complex_is_numeric(self):
        assert _is_numeric_annotation("complex") is True

    def test_ndarray_is_numeric(self):
        assert _is_numeric_annotation("np.ndarray") is True
        assert _is_numeric_annotation("ndarray") is True
        assert _is_numeric_annotation("numpy.ndarray") is True

    def test_np_dtypes(self):
        assert _is_numeric_annotation("np.float64") is True
        assert _is_numeric_annotation("np.int32") is True

    def test_str_is_not_numeric(self):
        assert _is_numeric_annotation("str") is False

    def test_bool_is_not_numeric(self):
        assert _is_numeric_annotation("bool") is False

    def test_list_float_container(self):
        assert _is_numeric_annotation("list[float]") is True

    def test_list_str_container_not_numeric(self):
        assert _is_numeric_annotation("list[str]") is False

    def test_tuple_int_container(self):
        assert _is_numeric_annotation("tuple[int, int]") is True

    def test_sequence_complex(self):
        assert _is_numeric_annotation("Sequence[complex]") is True

    def test_iterable_float(self):
        assert _is_numeric_annotation("Iterable[float]") is True

    def test_case_insensitive(self):
        # The function lowercases, so "Int" should match "int"
        assert _is_numeric_annotation("Int") is True
        assert _is_numeric_annotation("FLOAT") is True

    def test_spaces_ignored(self):
        assert _is_numeric_annotation("list[ float ]") is True


# ------------------------------------------------------------------
# _score_numeric_signature
# ------------------------------------------------------------------


class TestScoreNumericSignature:
    def test_all_numeric_returns_1(self):
        func = _parse_func("def f(a: float, b: float) -> float: return a + b")
        assert _score_numeric_signature(func) == 1.0

    def test_no_annotations_returns_0(self):
        func = _parse_func("def f(a, b): return a + b")
        assert _score_numeric_signature(func) == 0.0

    def test_mixed_annotations(self):
        func = _parse_func("def f(a: float, b: str) -> float: return 0.0")
        score = _score_numeric_signature(func)
        # 2 numeric (a:float, return:float) out of 3 annotations (a, b, return)
        assert score == 2.0 / 3.0

    def test_self_excluded(self):
        func = _parse_func(
            "def f(self, a: float, b: float) -> float: return a + b"
        )
        # self is filtered out, so 3 numeric out of 3 = 1.0
        assert _score_numeric_signature(func) == 1.0

    def test_cls_excluded(self):
        func = _parse_func(
            "def f(cls, a: int) -> int: return a"
        )
        assert _score_numeric_signature(func) == 1.0

    def test_only_return_annotation(self):
        func = _parse_func("def f(a, b) -> float: return 0.0")
        # 1 annotated (return:float), but annotations list = [a_ann='', b_ann='', ret='float']
        # annotated = ['float'], numeric = 1, len(annotations) = 3
        score = _score_numeric_signature(func)
        assert score == 1.0 / 3.0


# ------------------------------------------------------------------
# _count_loops
# ------------------------------------------------------------------


class TestCountLoops:
    def test_no_loops(self):
        body = _parse_body("def f(): return 1")
        assert _count_loops(body) == 0

    def test_single_for(self):
        body = _parse_body("def f():\n for i in range(10): pass")
        assert _count_loops(body) == 1

    def test_single_while(self):
        body = _parse_body("def f():\n while True: break")
        assert _count_loops(body) == 1

    def test_nested_loops(self):
        body = _parse_body(
            "def f():\n for i in range(10):\n  for j in range(10): pass"
        )
        assert _count_loops(body) == 2

    def test_mixed_for_while(self):
        body = _parse_body(
            "def f():\n for i in range(10): pass\n while True: break"
        )
        assert _count_loops(body) == 2


# ------------------------------------------------------------------
# _count_statements
# ------------------------------------------------------------------


class TestCountStatements:
    def test_empty_body(self):
        body = _parse_body("def f(): pass")
        # The pass statement itself counts
        assert _count_statements(body) >= 1

    def test_single_return(self):
        body = _parse_body("def f(): return 1")
        assert _count_statements(body) == 1

    def test_multiple_statements(self):
        body = _parse_body("def f():\n x = 1\n y = 2\n return x + y")
        assert _count_statements(body) == 3

    def test_nested_if_adds_statements(self):
        body = _parse_body(
            "def f():\n if True:\n  x = 1\n else:\n  x = 2\n return x"
        )
        # if + x=1 + x=2 + return = 4
        assert _count_statements(body) == 4


# ------------------------------------------------------------------
# _score_loop_density
# ------------------------------------------------------------------


class TestScoreLoopDensity:
    def test_no_loops_returns_0(self):
        func = _parse_func("def f(): return 1")
        assert _score_loop_density(func) == 0.0

    def test_one_loop_one_stmt(self):
        func = _parse_func("def f():\n for i in range(10): pass")
        score = _score_loop_density(func)
        # 1 loop, body stmts: for + pass = 2, density = 1/2*3 = 1.5 -> min(1.5, 1.0) = 1.0
        assert score > 0.0
        assert score <= 1.0

    def test_capped_at_1(self):
        # Many loops relative to statements should cap at 1.0
        func = _parse_func(
            "def f():\n for i in range(10):\n  for j in range(10): pass"
        )
        score = _score_loop_density(func)
        assert score <= 1.0

    def test_low_density_with_many_statements(self):
        func = _parse_func(
            "def f():\n a=1\n b=2\n c=3\n d=4\n e=5\n f=6\n"
            " g=7\n h=8\n i=9\n for x in range(1): pass"
        )
        score = _score_loop_density(func)
        # 1 loop in ~12 statements -> low density
        assert score < 0.5


# ------------------------------------------------------------------
# _count_arithmetic
# ------------------------------------------------------------------


class TestCountArithmetic:
    def test_no_ops(self):
        body = _parse_body("def f(): return 1")
        arith, total = _count_arithmetic(body)
        assert arith == 0
        assert total == 0

    def test_add_sub(self):
        body = _parse_body("def f(a, b): return a + b - 1")
        arith, total = _count_arithmetic(body)
        assert arith == 2  # + and -
        assert total == 2

    def test_mult_div_pow(self):
        body = _parse_body("def f(a, b): return a * b / 2 ** 3")
        arith, total = _count_arithmetic(body)
        assert arith == 3  # *, /, **
        assert total == 3

    def test_boolean_ops_not_arithmetic(self):
        body = _parse_body("def f(a, b): return a and b")
        arith, total = _count_arithmetic(body)
        assert arith == 0
        assert total == 1  # BoolOp counted in total

    def test_comparison_counted_in_total(self):
        body = _parse_body("def f(a, b): return a > b")
        arith, total = _count_arithmetic(body)
        assert arith == 0
        assert total == 1  # Compare counted in total

    def test_unary_minus(self):
        body = _parse_body("def f(a): return -a")
        arith, total = _count_arithmetic(body)
        assert arith == 1  # USub
        assert total == 1

    def test_unary_plus(self):
        body = _parse_body("def f(a): return +a")
        arith, total = _count_arithmetic(body)
        assert arith == 1  # UAdd
        assert total == 1

    def test_floor_div_and_mod(self):
        body = _parse_body("def f(a, b): return a // b % 2")
        arith, total = _count_arithmetic(body)
        assert arith == 2  # FloorDiv, Mod
        assert total == 2


# ------------------------------------------------------------------
# _score_arithmetic_intensity
# ------------------------------------------------------------------


class TestScoreArithmeticIntensity:
    def test_no_ops_returns_0(self):
        func = _parse_func("def f(): return 1")
        assert _score_arithmetic_intensity(func) == 0.0

    def test_all_arithmetic(self):
        func = _parse_func("def f(a, b): return a + b * 2")
        score = _score_arithmetic_intensity(func)
        # 2 arith ops, 2 total -> 1.0
        assert score == 1.0

    def test_mixed_arith_and_compare(self):
        func = _parse_func("def f(a, b): return a + b if a > b else a - b")
        score = _score_arithmetic_intensity(func)
        # arith: +, -, total: +, -, > (Compare) = 2/3
        assert 0.0 < score < 1.0

    def test_capped_at_1(self):
        func = _parse_func("def f(a): return a + a + a + a")
        score = _score_arithmetic_intensity(func)
        assert score <= 1.0


# ------------------------------------------------------------------
# _count_allocations
# ------------------------------------------------------------------


class TestCountAllocations:
    def test_no_allocations(self):
        body = _parse_body("def f(a, b): return a + b")
        assert _count_allocations(body) == 0

    def test_dict_constructor(self):
        body = _parse_body("def f(): return dict()")
        assert _count_allocations(body) == 1

    def test_list_constructor(self):
        body = _parse_body("def f(): return list()")
        assert _count_allocations(body) == 1

    def test_set_constructor(self):
        body = _parse_body("def f(): return set()")
        assert _count_allocations(body) == 1

    def test_multiple_constructors(self):
        body = _parse_body("def f():\n a = dict()\n b = list()\n c = set()\n return a")
        assert _count_allocations(body) == 3

    def test_fstring_counts_as_allocation(self):
        body = _parse_body("def f(x): return f'value={x}'")
        assert _count_allocations(body) == 1

    def test_class_instantiation_counts(self):
        body = _parse_body("def f(): return MyClass()")
        # MyClass starts with uppercase and is not in _NUMERIC_TYPE_NAMES
        assert _count_allocations(body) == 1

    def test_numeric_type_not_counted(self):
        # Calling int() or float() should NOT count as allocation
        # because these names are lowercase and not in _ALLOC_CONSTRUCTORS
        body = _parse_body("def f(x): return int(x)")
        assert _count_allocations(body) == 0

    def test_frozenset_constructor(self):
        body = _parse_body("def f(): return frozenset()")
        assert _count_allocations(body) == 1

    def test_bytearray_constructor(self):
        body = _parse_body("def f(): return bytearray()")
        assert _count_allocations(body) == 1


# ------------------------------------------------------------------
# _score_low_allocation
# ------------------------------------------------------------------


class TestScoreLowAllocation:
    def test_no_allocations_returns_1(self):
        func = _parse_func("def f(a, b): return a + b")
        assert _score_low_allocation(func) == 1.0

    def test_one_allocation(self):
        func = _parse_func("def f(): return dict()")
        assert _score_low_allocation(func) == 0.8  # 1.0 - 1 * 0.2

    def test_two_allocations(self):
        func = _parse_func("def f():\n a = dict()\n b = list()\n return a")
        assert _score_low_allocation(func) == 0.6  # 1.0 - 2 * 0.2

    def test_five_allocations_returns_0(self):
        func = _parse_func(
            "def f():\n a=dict()\n b=list()\n c=set()\n"
            " d=frozenset()\n e=bytearray()\n return a"
        )
        assert _score_low_allocation(func) == 0.0  # 1.0 - 5 * 0.2

    def test_six_allocations_clamped_at_0(self):
        func = _parse_func(
            "def f():\n a=dict()\n b=list()\n c=set()\n"
            " d=frozenset()\n e=bytearray()\n g=dict()\n return a"
        )
        assert _score_low_allocation(func) == 0.0  # max(1.0 - 6*0.2, 0.0) = 0.0


# ------------------------------------------------------------------
# _compute_band
# ------------------------------------------------------------------


class TestComputeBand:
    def test_high_band(self):
        assert _compute_band(0.71) == "HIGH"
        assert _compute_band(0.9) == "HIGH"
        assert _compute_band(1.0) == "HIGH"

    def test_medium_band(self):
        assert _compute_band(0.31) == "MEDIUM"
        assert _compute_band(0.5) == "MEDIUM"
        assert _compute_band(0.7) == "MEDIUM"

    def test_low_band(self):
        assert _compute_band(0.0) == "LOW"
        assert _compute_band(0.3) == "LOW"
        assert _compute_band(0.29) == "LOW"

    def test_boundary_0_7_is_medium(self):
        # score > 0.7 is HIGH, so exactly 0.7 is MEDIUM
        assert _compute_band(0.7) == "MEDIUM"

    def test_boundary_0_3_is_low(self):
        # score > 0.3 is MEDIUM, so exactly 0.3 is LOW
        assert _compute_band(0.3) == "LOW"


# ------------------------------------------------------------------
# _recommend_backend
# ------------------------------------------------------------------


class TestRecommendBackend:
    def test_high_loop_density_returns_numba(self):
        assert _recommend_backend(0.31, 0.0) == "numba"
        assert _recommend_backend(0.5, 0.9) == "numba"
        assert _recommend_backend(1.0, 1.0) == "numba"

    def test_low_loop_high_arith_returns_jax(self):
        assert _recommend_backend(0.0, 0.51) == "jax"
        assert _recommend_backend(0.3, 0.9) == "jax"
        assert _recommend_backend(0.1, 0.6) == "jax"

    def test_low_both_returns_cython(self):
        assert _recommend_backend(0.0, 0.0) == "cython"
        assert _recommend_backend(0.3, 0.5) == "cython"
        assert _recommend_backend(0.1, 0.1) == "cython"

    def test_boundary_loop_0_3_not_numba(self):
        # loop_density > 0.3 triggers numba, so exactly 0.3 does NOT
        assert _recommend_backend(0.3, 0.0) != "numba"

    def test_boundary_arith_0_5_not_jax(self):
        # arith_intensity > 0.5 triggers jax, so exactly 0.5 does NOT
        assert _recommend_backend(0.0, 0.5) != "jax"

    def test_numba_takes_priority_over_jax(self):
        # When both conditions are met, loop_density is checked first
        assert _recommend_backend(0.5, 0.8) == "numba"
