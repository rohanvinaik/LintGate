"""Direct unit tests for purity.py internal functions.

These tests target the internal helpers that the mutation engine found have
100% survival rate — _get_parameter_count, _compute_pure_confidence,
_check_called_impurity, _propagate_impurity, and _PureFunctionVisitor
state tracking. All tests exercise exact values to kill VALUE, SWAP,
STATE, and TYPE mutations.
"""

from __future__ import annotations

import ast

from lintgate.linters.performance_checks.purity import (
    _check_called_impurity,
    _compute_pure_confidence,
    _get_parameter_count,
    _propagate_impurity,
    _PureFunctionVisitor,
    analyze_purity,
)

# ── helpers ──────────────────────────────────────────────────────────


def _parse_func(code: str, name: str | None = None) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Parse code and return the first (or named) FunctionDef."""
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            name is None or node.name == name
        ):
            return node
    raise ValueError(f"No function {name!r} found")


def _make_visitor(
    code: str, func_name: str | None = None, is_method: bool = False
) -> _PureFunctionVisitor:
    """Create a visitor for a parsed function and run it."""
    node = _parse_func(code, func_name)
    visitor = _PureFunctionVisitor(node, is_method=is_method)
    visitor.visit(node)
    return visitor


# ── _get_parameter_count ─────────────────────────────────────────────


class TestGetParameterCount:
    """VALUE mutations: exact parameter counts for every argument variant."""

    def test_no_args(self):
        node = _parse_func("def f(): pass")
        assert _get_parameter_count(node) == 0

    def test_one_arg(self):
        node = _parse_func("def f(x): pass")
        assert _get_parameter_count(node) == 1

    def test_two_args(self):
        node = _parse_func("def f(a, b): pass")
        assert _get_parameter_count(node) == 2

    def test_kwonly_args(self):
        node = _parse_func("def f(*, key, value): pass")
        assert _get_parameter_count(node) == 2

    def test_mixed_args_and_kwonly(self):
        node = _parse_func("def f(a, b, *, key=None): pass")
        assert _get_parameter_count(node) == 3

    def test_vararg_only(self):
        node = _parse_func("def f(*args): pass")
        assert _get_parameter_count(node) == 1

    def test_kwarg_only(self):
        node = _parse_func("def f(**kwargs): pass")
        assert _get_parameter_count(node) == 1

    def test_vararg_and_kwarg(self):
        node = _parse_func("def f(*args, **kwargs): pass")
        assert _get_parameter_count(node) == 2

    def test_full_signature(self):
        # a, b = 2 regular + *args = 1 + key = 1 kwonly + **kwargs = 1 => 5
        node = _parse_func("def f(a, b, *args, key=None, **kwargs): pass")
        assert _get_parameter_count(node) == 5

    def test_positional_only_args(self):
        node = _parse_func("def f(a, b, /, c): pass")
        assert _get_parameter_count(node) == 3

    def test_positional_only_with_vararg_kwarg(self):
        # a, b (posonly) + c (regular) + *args + key (kwonly) + **kw => 6
        node = _parse_func("def f(a, b, /, c, *args, key=1, **kw): pass")
        assert _get_parameter_count(node) == 6

    def test_self_counted_for_method(self):
        node = _parse_func("class C:\n    def m(self, x): pass", name="m")
        assert _get_parameter_count(node) == 2

    def test_async_function(self):
        node = _parse_func("async def f(a, b, c): pass")
        assert _get_parameter_count(node) == 3


# ── _compute_pure_confidence ─────────────────────────────────────────


class TestComputePureConfidence:
    """VALUE mutations: exact confidence values for every code path."""

    def test_leaf_function_no_calls_returns_0_95(self):
        visitor = _make_visitor("def f(x): return x + 1")
        assert visitor.called_functions == set()
        assert _compute_pure_confidence(visitor, {}) == 0.95

    def test_known_builtin_calls_returns_0_90(self):
        visitor = _make_visitor("def f(x): return sorted(set(x))")
        assert "sorted" in visitor.called_functions
        assert "set" in visitor.called_functions
        assert _compute_pure_confidence(visitor, {}) == 0.90

    def test_same_module_resolved_returns_0_90(self):
        code = "def helper(x): return x\ndef f(x): return helper(x)"
        tree = ast.parse(code)
        # Build function dict as analyze_purity would
        functions = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                v = _PureFunctionVisitor(node)
                v.visit(node)
                functions[node.name] = (node, v)
        caller_visitor = functions["f"][1]
        assert _compute_pure_confidence(caller_visitor, functions) == 0.90

    def test_one_unresolved_lowercase_returns_0_80(self):
        visitor = _make_visitor("def f(x): return helper(x)")
        assert "helper" in visitor.called_functions
        assert _compute_pure_confidence(visitor, {}) == 0.80

    def test_two_unresolved_lowercase_returns_0_80(self):
        visitor = _make_visitor("def f(x): return step_a(step_b(x))")
        assert _compute_pure_confidence(visitor, {}) == 0.80

    def test_three_unresolved_lowercase_returns_0_65(self):
        visitor = _make_visitor(
            "def f(x):\n    a = step_one(x)\n    b = step_two(a)\n    return step_three(b)"
        )
        assert _compute_pure_confidence(visitor, {}) == 0.65

    def test_four_unresolved_returns_0_65(self):
        visitor = _make_visitor(
            "def f(x):\n"
            "    a = step_one(x)\n"
            "    b = step_two(a)\n"
            "    c = step_three(b)\n"
            "    return step_four(c)"
        )
        assert _compute_pure_confidence(visitor, {}) == 0.65

    def test_builtin_plus_unresolved_returns_0_80(self):
        visitor = _make_visitor("def f(x): return len(helper(x))")
        assert _compute_pure_confidence(visitor, {}) == 0.80


# ── _check_called_impurity ───────────────────────────────────────────


class TestCheckCalledImpurity:
    """SWAP and VALUE mutations on the impurity resolution logic."""

    def _build_functions(self, code: str):
        """Build the functions dict as analyze_purity does."""
        tree = ast.parse(code)
        functions = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                v = _PureFunctionVisitor(node)
                v.visit(node)
                functions[node.name] = (node, v)
        return functions

    def test_known_pure_builtin_returns_none(self):
        code = "def f(): return len([])"
        functions = self._build_functions(code)
        node = functions["f"][0]
        result = _check_called_impurity("len", functions, node)
        assert result is None

    def test_same_module_pure_returns_none(self):
        code = "def helper(x): return x\ndef f(x): return helper(x)"
        functions = self._build_functions(code)
        node = functions["f"][0]
        result = _check_called_impurity("helper", functions, node)
        assert result is None

    def test_same_module_impure_returns_side_effect(self):
        code = "def impure():\n    global g\n    g = 1\ndef f(): return impure()"
        functions = self._build_functions(code)
        node = functions["f"][0]
        result = _check_called_impurity("impure", functions, node)
        assert result is not None
        assert result.kind == "impure_call"
        assert "impure" in result.detail

    def test_unknown_uppercase_returns_side_effect(self):
        code = "def f(): return UnknownClass()"
        functions = self._build_functions(code)
        node = functions["f"][0]
        result = _check_called_impurity("UnknownClass", functions, node)
        assert result is not None
        assert result.kind == "impure_call"
        assert "UnknownClass" in result.detail

    def test_unknown_lowercase_returns_none(self):
        code = "def f(): return unknown_helper()"
        functions = self._build_functions(code)
        node = functions["f"][0]
        result = _check_called_impurity("unknown_helper", functions, node)
        assert result is None

    def test_not_in_functions_and_not_builtin_lowercase_returns_none(self):
        """Lowercase unresolved external is assumed pure by convention."""
        node = _parse_func("def f(): pass")
        result = _check_called_impurity("some_func", {}, node)
        assert result is None

    def test_not_in_functions_and_not_builtin_uppercase_returns_effect(self):
        """Uppercase unresolved external is assumed impure (class constructor)."""
        node = _parse_func("def f(): pass")
        result = _check_called_impurity("SomeClass", {}, node)
        assert result is not None
        assert result.kind == "impure_call"


# ── _propagate_impurity ──────────────────────────────────────────────


class TestPropagateImpurity:
    """VALUE mutations on transitive impurity chains."""

    def _build_functions(self, code: str):
        tree = ast.parse(code)
        functions = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                v = _PureFunctionVisitor(node)
                v.visit(node)
                functions[node.name] = (node, v)
        return functions

    def test_direct_impurity_not_changed(self):
        code = "def f():\n    global g\n    g = 1"
        functions = self._build_functions(code)
        _propagate_impurity(functions)
        assert len(functions["f"][1].side_effects) == 1

    def test_transitive_one_hop(self):
        code = "def impure():\n    global g\n    g = 1\n\ndef caller(): return impure()"
        functions = self._build_functions(code)
        assert functions["caller"][1].side_effects == []
        _propagate_impurity(functions)
        assert len(functions["caller"][1].side_effects) == 1
        assert functions["caller"][1].side_effects[0].kind == "impure_call"

    def test_transitive_two_hops(self):
        code = (
            "def impure():\n    global g\n    g = 1\n\n"
            "def mid(): return impure()\n\n"
            "def top(): return mid()"
        )
        functions = self._build_functions(code)
        _propagate_impurity(functions)
        assert len(functions["top"][1].side_effects) == 1
        assert functions["top"][1].side_effects[0].kind == "impure_call"
        assert "mid" in functions["top"][1].side_effects[0].detail

    def test_pure_chain_unchanged(self):
        code = "def a(x): return x + 1\ndef b(x): return a(x)\ndef c(x): return b(x)"
        functions = self._build_functions(code)
        _propagate_impurity(functions)
        for name in ("a", "b", "c"):
            assert functions[name][1].side_effects == []

    def test_mixed_chain(self):
        code = (
            "def pure(x): return x + 1\n"
            "def impure():\n    global g\n    g = 1\n\n"
            "def calls_pure(x): return pure(x)\n"
            "def calls_impure(): return impure()"
        )
        functions = self._build_functions(code)
        _propagate_impurity(functions)
        assert functions["calls_pure"][1].side_effects == []
        assert len(functions["calls_impure"][1].side_effects) == 1


# ── _PureFunctionVisitor state tracking ──────────────────────────────


class TestVisitorStateTracking:
    """STATE and TYPE mutations on visitor internal state."""

    def test_local_names_include_args(self):
        visitor = _make_visitor("def f(a, b, c): pass")
        assert {"a", "b", "c"} <= visitor.local_names

    def test_local_names_include_kwonly(self):
        visitor = _make_visitor("def f(*, key, val): pass")
        assert {"key", "val"} <= visitor.local_names

    def test_local_names_include_vararg(self):
        visitor = _make_visitor("def f(*args): pass")
        assert "args" in visitor.local_names

    def test_local_names_include_kwarg(self):
        visitor = _make_visitor("def f(**kwargs): pass")
        assert "kwargs" in visitor.local_names

    def test_local_names_include_posonly(self):
        visitor = _make_visitor("def f(a, b, /): pass")
        assert {"a", "b"} <= visitor.local_names

    def test_assign_adds_to_local_names(self):
        visitor = _make_visitor("def f():\n    x = 1\n    y = 2")
        assert "x" in visitor.local_names
        assert "y" in visitor.local_names

    def test_ann_assign_adds_to_local_names(self):
        visitor = _make_visitor("def f():\n    x: int = 1")
        assert "x" in visitor.local_names

    def test_called_functions_tracked(self):
        visitor = _make_visitor("def f():\n    foo()\n    bar()")
        assert visitor.called_functions == {"foo", "bar"}

    def test_called_functions_includes_module_qualified(self):
        visitor = _make_visitor("def f():\n    os.path.join('a', 'b')")
        assert "os.path.join" in visitor.called_functions

    def test_method_self_in_local_names(self):
        visitor = _make_visitor(
            "class C:\n    def m(self, x): pass",
            func_name="m",
            is_method=True,
        )
        assert "self" in visitor.local_names

    def test_no_side_effects_for_pure_body(self):
        visitor = _make_visitor("def f(x): return x + 1")
        assert visitor.side_effects == []

    def test_global_produces_side_effect(self):
        visitor = _make_visitor("def f():\n    global g\n    g = 1")
        assert len(visitor.side_effects) >= 1
        assert any(se.kind == "global_write" for se in visitor.side_effects)

    def test_nonlocal_produces_side_effect(self):
        # nonlocal inside a nested function
        code = "def outer():\n    x = 0\n    def inner():\n        nonlocal x\n        x = 1"
        visitor = _make_visitor(code, func_name="inner")
        assert any(se.kind == "nonlocal_write" for se in visitor.side_effects)

    def test_yield_produces_side_effect(self):
        visitor = _make_visitor("def f():\n    yield 1")
        assert any(se.kind == "generator" for se in visitor.side_effects)

    def test_yield_from_produces_side_effect(self):
        visitor = _make_visitor("def f(items):\n    yield from items")
        assert any(
            se.kind == "generator" and se.node_type == "YieldFrom" for se in visitor.side_effects
        )


# ── Visitor: mutable default detection ───────────────────────────────


class TestMutableDefaultDetection:
    """TYPE mutations: each mutable default type is detected."""

    def test_list_default(self):
        visitor = _make_visitor("def f(x=[]): pass")
        effects = [se for se in visitor.side_effects if se.kind == "mutable_default"]
        assert len(effects) == 1

    def test_dict_default(self):
        visitor = _make_visitor("def f(x={}): pass")
        effects = [se for se in visitor.side_effects if se.kind == "mutable_default"]
        assert len(effects) == 1

    def test_set_default(self):
        visitor = _make_visitor("def f(x={1, 2}): pass")
        effects = [se for se in visitor.side_effects if se.kind == "mutable_default"]
        assert len(effects) == 1

    def test_call_default(self):
        visitor = _make_visitor("def f(x=list()): pass")
        effects = [se for se in visitor.side_effects if se.kind == "mutable_default"]
        assert len(effects) == 1

    def test_immutable_default_no_effect(self):
        visitor = _make_visitor("def f(x=42, y='hello', z=None): pass")
        effects = [se for se in visitor.side_effects if se.kind == "mutable_default"]
        assert len(effects) == 0

    def test_kwonly_mutable_default(self):
        visitor = _make_visitor("def f(*, cache={}): pass")
        effects = [se for se in visitor.side_effects if se.kind == "mutable_default"]
        assert len(effects) == 1

    def test_multiple_mutable_defaults(self):
        visitor = _make_visitor("def f(a=[], b={}): pass")
        effects = [se for se in visitor.side_effects if se.kind == "mutable_default"]
        assert len(effects) == 2


# ── Visitor: Call-based impurity ─────────────────────────────────────


class TestCallImpurityDetection:
    """SWAP and VALUE mutations on call-based impurity detection."""

    def test_mutating_method_on_external(self):
        code = "ext = []\ndef f(): ext.append(1)"
        visitor = _make_visitor(code, func_name="f")
        effects = [se for se in visitor.side_effects if se.kind == "mutation"]
        assert len(effects) == 1
        assert ".append()" in effects[0].detail

    def test_mutating_method_on_local_is_safe(self):
        visitor = _make_visitor("def f():\n    x = []\n    x.append(1)")
        mutation_effects = [se for se in visitor.side_effects if se.kind == "mutation"]
        assert mutation_effects == []

    def test_always_impure_method_execute(self):
        visitor = _make_visitor("def f(cur):\n    cur.execute('SELECT 1')")
        io_effects = [se for se in visitor.side_effects if se.kind == "io_call"]
        assert any("execute" in se.detail for se in io_effects)

    def test_path_write_method(self):
        visitor = _make_visitor("def f(p):\n    p.write_text('data')")
        io_effects = [se for se in visitor.side_effects if se.kind == "io_call"]
        assert any("write_text" in se.detail for se in io_effects)

    def test_serializer_write_call(self):
        visitor = _make_visitor("def f(data, fh):\n    json.dump(data, fh)")
        io_effects = [se for se in visitor.side_effects if se.kind == "io_call"]
        assert any("json.dump" in se.detail for se in io_effects)

    def test_ml_impure_method(self):
        visitor = _make_visitor("def f(loss):\n    loss.backward()")
        io_effects = [se for se in visitor.side_effects if se.kind == "io_call"]
        assert any("backward" in se.detail for se in io_effects)

    def test_ml_impure_namespace(self):
        visitor = _make_visitor("def f(path):\n    torch.load(path)")
        io_effects = [se for se in visitor.side_effects if se.kind == "io_call"]
        assert any("torch.load" in se.detail for se in io_effects)


# ── Visitor: Assign/AugAssign/Delete ─────────────────────────────────


class TestAssignMutationDetection:
    """SWAP and TYPE mutations on assignment-based side effects."""

    def test_attribute_write_on_external(self):
        code = "cfg = type('', (), {})()\ndef f(): cfg.debug = True"
        visitor = _make_visitor(code, func_name="f")
        effects = [se for se in visitor.side_effects if se.kind == "attribute_mutation"]
        assert len(effects) == 1
        assert "cfg.debug" in effects[0].detail

    def test_attribute_write_on_self_outside_init(self):
        code = "class C:\n    def set_x(self, v):\n        self.x = v"
        visitor = _make_visitor(code, func_name="set_x", is_method=True)
        effects = [se for se in visitor.side_effects if se.kind == "attribute_mutation"]
        assert len(effects) == 1
        assert "self.x" in effects[0].detail

    def test_attribute_write_on_self_inside_init_is_safe(self):
        code = "class C:\n    def __init__(self, v):\n        self.v = v"
        visitor = _make_visitor(code, func_name="__init__", is_method=True)
        effects = [se for se in visitor.side_effects if se.kind == "attribute_mutation"]
        assert effects == []

    def test_subscript_write_on_external(self):
        code = "registry = {}\ndef f(k, v): registry[k] = v"
        visitor = _make_visitor(code, func_name="f")
        effects = [se for se in visitor.side_effects if se.kind == "mutation"]
        assert len(effects) == 1
        assert "registry" in effects[0].detail

    def test_augassign_on_external_attribute(self):
        code = "counter = type('', (), {'n': 0})()\ndef f(): counter.n += 1"
        visitor = _make_visitor(code, func_name="f")
        effects = [se for se in visitor.side_effects if se.kind == "attribute_mutation"]
        assert len(effects) == 1
        assert "counter.n" in effects[0].detail

    def test_augassign_subscript_on_external(self):
        code = "counts = {}\ndef f(k): counts[k] += 1"
        visitor = _make_visitor(code, func_name="f")
        effects = [se for se in visitor.side_effects if se.kind == "mutation"]
        assert len(effects) == 1

    def test_delete_external_subscript(self):
        code = "cache = {}\ndef f(k): del cache[k]"
        visitor = _make_visitor(code, func_name="f")
        effects = [se for se in visitor.side_effects if se.kind == "mutation"]
        assert len(effects) == 1
        assert "cache" in effects[0].detail

    def test_delete_external_name(self):
        code = "x = 1\ndef f(): del x"
        visitor = _make_visitor(code, func_name="f")
        effects = [se for se in visitor.side_effects if se.kind == "mutation"]
        assert len(effects) == 1

    def test_local_assign_no_side_effect(self):
        visitor = _make_visitor("def f():\n    x = 1\n    x += 2")
        assert visitor.side_effects == []

    def test_local_subscript_no_side_effect(self):
        visitor = _make_visitor("def f():\n    d = {}\n    d['k'] = 1")
        mutation_effects = [se for se in visitor.side_effects if se.kind == "mutation"]
        assert mutation_effects == []


# ── Integration: end-to-end confidence + parameter count ─────────────


class TestEndToEndExactValues:
    """Verify analyze_purity produces exact values for edge cases."""

    def test_positional_only_args_counted(self):
        code = "def f(a, b, /, c): return a + b + c"
        r = analyze_purity(ast.parse(code))["f"]
        assert r.parameter_count == 3
        assert r.is_pure is True

    def test_complex_signature_parameter_count(self):
        code = "def f(a, b, /, c, *args, d=1, e=2, **kw): return a"
        r = analyze_purity(ast.parse(code))["f"]
        # a, b (posonly: 2) + c (regular: 1) + *args (1) + d, e (kwonly: 2) + **kw (1) = 7
        assert r.parameter_count == 7

    def test_two_unresolved_calls_confidence_0_80(self):
        code = "def f(x): return step_a(step_b(x))"
        r = analyze_purity(ast.parse(code))["f"]
        assert r.confidence == 0.80

    def test_nonlocal_side_effect_kind(self):
        code = (
            "def outer():\n"
            "    x = 0\n"
            "    def inner():\n"
            "        nonlocal x\n"
            "        x = 1\n"
            "    return inner"
        )
        results = analyze_purity(ast.parse(code))
        # inner should have nonlocal_write
        if "inner" in results:
            r = results["inner"]
            assert r.is_pure is False
            assert any(se.kind == "nonlocal_write" for se in r.side_effects)

    def test_cls_attribute_mutation_outside_init(self):
        code = "class C:\n    def modify(cls):\n        cls.x = 1"
        results = analyze_purity(ast.parse(code))
        r = results["C.modify"]
        assert r.is_pure is False
        attr_effects = [se for se in r.side_effects if se.kind == "attribute_mutation"]
        assert len(attr_effects) == 1
