"""Tests for dependency_clustering — targeting VALUE/TYPE/SWAP/BOUNDARY mutations."""

from __future__ import annotations

import ast

from lintgate.linters.structure_checks.dependency_clustering import (
    _collect_reads,
    _collect_scope_vars,
    _collect_writes,
    _compute_block_cc,
    _compute_confidence,
    _get_decorator_name,
    _get_param_names,
    _is_bag_of_handlers,
    _is_handler_decorator,
    _StmtInfo,
    _suggest_name,
)


def _parse_stmt(code: str) -> ast.stmt:
    return ast.parse(code).body[0]


def _parse_func(code: str) -> ast.FunctionDef:
    node = _parse_stmt(code)
    assert isinstance(node, ast.FunctionDef)
    return node


def _make_info(writes: frozenset[str] = frozenset(), reads: frozenset[str] = frozenset()) -> _StmtInfo:
    return _StmtInfo(index=0, stmt=_parse_stmt("x = 1"), reads=reads, writes=writes, has_exit=False)


# ── _collect_reads / _collect_writes ──────────────────────────────────


class TestCollectReads:
    def test_simple_load(self):
        node = _parse_stmt("y = x + z")
        assert _collect_reads(node) == {"x", "z"}

    def test_no_reads(self):
        node = _parse_stmt("x = 1")
        assert _collect_reads(node) == set()

    def test_function_call(self):
        node = _parse_stmt("y = foo(bar)")
        reads = _collect_reads(node)
        assert "foo" in reads
        assert "bar" in reads


class TestCollectWrites:
    def test_simple_store(self):
        node = _parse_stmt("x = 1")
        assert _collect_writes(node) == {"x"}

    def test_no_writes(self):
        node = _parse_stmt("print(x)")
        assert "x" not in _collect_writes(node)

    def test_del(self):
        node = _parse_stmt("del x")
        assert "x" in _collect_writes(node)

    def test_multiple_targets(self):
        node = _parse_stmt("a = b = 1")
        writes = _collect_writes(node)
        assert "a" in writes
        assert "b" in writes


# ── _compute_block_cc ─────────────────────────────────────────────────


class TestComputeBlockCC:
    def test_simple_assignment(self):
        stmts = [_parse_stmt("x = 1")]
        assert _compute_block_cc(stmts) == 0

    def test_if_adds_complexity(self):
        stmts = [_parse_stmt("if x: y = 1")]
        assert _compute_block_cc(stmts) > 0

    def test_nested_if(self):
        code = "if x:\n    if y:\n        z = 1"
        stmts = [_parse_stmt(code)]
        assert _compute_block_cc(stmts) >= 2


# ── _get_param_names ──────────────────────────────────────────────────


class TestGetParamNames:
    def test_positional_args(self):
        func = _parse_func("def f(a, b, c): pass")
        assert _get_param_names(func) == {"a", "b", "c"}

    def test_with_vararg(self):
        func = _parse_func("def f(*args): pass")
        assert "args" in _get_param_names(func)

    def test_with_kwarg(self):
        func = _parse_func("def f(**kwargs): pass")
        assert "kwargs" in _get_param_names(func)

    def test_no_params(self):
        func = _parse_func("def f(): pass")
        assert _get_param_names(func) == set()


# ── _suggest_name ─────────────────────────────────────────────────────


class TestSuggestName:
    def test_from_writes(self):
        block = [_make_info(writes=frozenset({"result", "temp"}))]
        name = _suggest_name(block, "process")
        assert name == "_compute_result"

    def test_underscore_writes_ignored(self):
        block = [_make_info(writes=frozenset({"_internal"}))]
        name = _suggest_name(block, "process")
        assert name == "_process_helper"

    def test_no_writes(self):
        block = [_make_info(writes=frozenset())]
        name = _suggest_name(block, "handle")
        assert name == "_handle_helper"


# ── _compute_confidence ───────────────────────────────────────────────


class TestComputeConfidence:
    def test_base_confidence(self):
        block = [_make_info()]
        conf = _compute_confidence(block, {"x", "y", "z"}, {"out"}, 3)
        assert conf == 0.50

    def test_small_inputs_boost(self):
        block = [_make_info()]
        conf = _compute_confidence(block, {"x"}, {"out"}, 3)
        assert conf == 0.60  # base 0.50 + 0.10 for ≤2 inputs

    def test_void_boost(self):
        block = [_make_info()]
        conf = _compute_confidence(block, {"x", "y", "z"}, set(), 3)
        assert conf == 0.60  # base 0.50 + 0.10 for 0 outputs

    def test_large_block_boost(self):
        block = [_make_info() for _ in range(6)]
        conf = _compute_confidence(block, {"x", "y", "z"}, {"out"}, 3)
        assert conf == 0.55  # base 0.50 + 0.05 for ≥5 stmts

    def test_high_cc_boost(self):
        block = [_make_info()]
        conf = _compute_confidence(block, {"x", "y", "z"}, {"out"}, 10)
        assert conf == 0.60  # base 0.50 + 0.10 for cc≥8

    def test_all_boosts_capped(self):
        block = [_make_info() for _ in range(6)]
        conf = _compute_confidence(block, {"x"}, set(), 10)
        # 0.50 + 0.10 + 0.10 + 0.05 + 0.10 = 0.85 (cap)
        assert conf == 0.85

    def test_swap_sensitivity(self):
        """Swapping inputs/outputs changes confidence."""
        block = [_make_info()]
        conf_a = _compute_confidence(block, {"x"}, {"a", "b", "c"}, 3)
        conf_b = _compute_confidence(block, {"a", "b", "c"}, {"x"}, 3)
        # First has ≤2 inputs boost, second doesn't
        assert conf_a != conf_b


# ── _get_decorator_name ───────────────────────────────────────────────


class TestGetDecoratorName:
    def test_simple_name(self):
        func = _parse_func("@route\ndef f(): pass")
        dec = func.decorator_list[0]
        assert _get_decorator_name(dec) == "route"

    def test_attribute(self):
        func = _parse_func("@app.route\ndef f(): pass")
        dec = func.decorator_list[0]
        assert _get_decorator_name(dec) == "app.route"

    def test_call(self):
        func = _parse_func("@app.route('/')\ndef f(): pass")
        dec = func.decorator_list[0]
        assert _get_decorator_name(dec) == "app.route"

    def test_no_name(self):
        # Complex expression decorator
        func = _parse_func("@(lambda: None)\ndef f(): pass")
        dec = func.decorator_list[0]
        assert _get_decorator_name(dec) is None


# ── _is_handler_decorator ─────────────────────────────────────────────


class TestIsHandlerDecorator:
    def test_app_route_is_handler(self):
        func = _parse_func("@app.route\ndef f(): pass")
        assert _is_handler_decorator(func.decorator_list[0]) is True

    def test_mcp_tool_is_handler(self):
        func = _parse_func("@mcp.tool\ndef f(): pass")
        assert _is_handler_decorator(func.decorator_list[0]) is True

    def test_property_is_not_handler(self):
        func = _parse_func("@property\ndef f(): pass")
        assert _is_handler_decorator(func.decorator_list[0]) is False


# ── _collect_scope_vars ───────────────────────────────────────────────


class TestCollectScopeVars:
    def test_basic(self):
        node = _parse_stmt("y = x + z")
        reads: set[str] = set()
        writes: set[str] = set()
        _collect_scope_vars(node, reads, writes)
        assert "x" in reads
        assert "z" in reads
        assert "y" in writes

    def test_swap_reads_writes_differ(self):
        """Reads and writes sets are distinct — swapping args would break."""
        node = _parse_stmt("y = x")
        reads: set[str] = set()
        writes: set[str] = set()
        _collect_scope_vars(node, reads, writes)
        assert reads != writes


# ── _is_bag_of_handlers ──────────────────────────────────────────────


class TestIsBagOfHandlers:
    def test_all_decorated_funcs(self):
        tree = ast.parse(
            "@route\ndef a(): pass\n@route\ndef b(): pass\n@route\ndef c(): pass\n"
        )
        assert _is_bag_of_handlers(tree.body) is True

    def test_mixed_content(self):
        tree = ast.parse("x = 1\ndef a(): pass\n")
        assert _is_bag_of_handlers(tree.body) is False

    def test_empty_body(self):
        assert _is_bag_of_handlers([]) is False
