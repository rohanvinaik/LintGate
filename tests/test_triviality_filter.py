"""Tests for AST triviality pre-filter."""

from __future__ import annotations

import ast
import textwrap

from lintgate.specification.triviality_filter import (
    TrivialityClass,
    _dict_values_are_self_attrs,
    _effective_body,
    _is_constant,
    _is_dict_serializer,
    _is_forwarder,
    _is_identity_return,
    _is_literal_return,
    _is_property_getter,
    _is_self_attr,
    _is_simple_accessor,
    _param_names,
    classify_triviality,
    is_trivial,
)


def _parse_func(source: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Parse a single function from source and return the node."""
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise ValueError("No function found in source")


# ── Trivial accessor ──────────────────────────────────────────────


class TestTrivialAccessor:
    def test_return_self_attr(self):
        node = _parse_func("""
            def get_name(self):
                return self.name
        """)
        assert classify_triviality(node) == TrivialityClass.TRIVIAL_ACCESSOR

    def test_return_self_chained_attr(self):
        node = _parse_func("""
            def get_config_value(self):
                return self.config.value
        """)
        assert classify_triviality(node) == TrivialityClass.TRIVIAL_ACCESSOR

    def test_return_self_attr_with_docstring(self):
        node = _parse_func('''
            def get_name(self):
                """Return the name."""
                return self.name
        ''')
        assert classify_triviality(node) == TrivialityClass.TRIVIAL_ACCESSOR

    def test_not_accessor_if_computation(self):
        node = _parse_func("""
            def get_name(self):
                return self.first + " " + self.last
        """)
        assert classify_triviality(node) == TrivialityClass.NONTRIVIAL


# ── Trivial property ──────────────────────────────────────────────


class TestTrivialProperty:
    def test_property_getter(self):
        node = _parse_func("""
            @property
            def name(self):
                return self._name
        """)
        assert classify_triviality(node) == TrivialityClass.TRIVIAL_PROPERTY

    def test_not_property_without_decorator(self):
        node = _parse_func("""
            def name(self):
                return self._name
        """)
        # Without @property it's a simple accessor, not a property
        assert classify_triviality(node) == TrivialityClass.TRIVIAL_ACCESSOR


# ── Trivial identity ──────────────────────────────────────────────


class TestTrivialIdentity:
    def test_return_arg(self):
        node = _parse_func("""
            def identity(x):
                return x
        """)
        assert classify_triviality(node) == TrivialityClass.TRIVIAL_IDENTITY

    def test_empty_body_pass_only(self):
        node = _parse_func("""
            def noop():
                pass
        """)
        assert classify_triviality(node) == TrivialityClass.TRIVIAL_IDENTITY

    def test_docstring_only(self):
        node = _parse_func('''
            def noop():
                """Does nothing."""
                pass
        ''')
        assert classify_triviality(node) == TrivialityClass.TRIVIAL_IDENTITY

    def test_not_identity_if_different_var(self):
        node = _parse_func("""
            def f(x):
                return y
        """)
        assert classify_triviality(node) == TrivialityClass.NONTRIVIAL


# ── Trivial forwarder ─────────────────────────────────────────────


class TestTrivialForwarder:
    def test_pure_delegation(self):
        node = _parse_func("""
            def process(self, data):
                return self.handler.process(data)
        """)
        assert classify_triviality(node) == TrivialityClass.TRIVIAL_FORWARDER

    def test_delegation_with_kwargs(self):
        node = _parse_func("""
            def run(self, x, y):
                return other_func(x, y=y)
        """)
        assert classify_triviality(node) == TrivialityClass.TRIVIAL_FORWARDER

    def test_delegation_with_star_args(self):
        node = _parse_func("""
            def forward(self, *args, **kwargs):
                return self.inner(*args, **kwargs)
        """)
        assert classify_triviality(node) == TrivialityClass.TRIVIAL_FORWARDER

    def test_not_forwarder_if_computed_arg(self):
        node = _parse_func("""
            def process(self, data):
                return self.handler.process(data + 1)
        """)
        assert classify_triviality(node) == TrivialityClass.NONTRIVIAL

    def test_not_forwarder_if_extra_statements(self):
        node = _parse_func("""
            def process(self, data):
                cleaned = data.strip()
                return self.handler.process(cleaned)
        """)
        assert classify_triviality(node) == TrivialityClass.NONTRIVIAL


# ── Trivial serializer ────────────────────────────────────────────


class TestTrivialSerializer:
    def test_return_dict_of_self_attrs(self):
        node = _parse_func("""
            def to_dict(self):
                return {"name": self.name, "age": self.age}
        """)
        assert (
            classify_triviality(node, function_name="to_dict") == TrivialityClass.TRIVIAL_SERIALIZER
        )

    def test_return_dict_literal_without_name(self):
        """Dict of self.attrs is a serializer even without a known name."""
        node = _parse_func("""
            def export(self):
                return {"name": self.name, "age": self.age}
        """)
        assert classify_triviality(node) == TrivialityClass.TRIVIAL_SERIALIZER

    def test_dict_with_constant_values(self):
        node = _parse_func("""
            def to_dict(self):
                return {"type": "user", "name": self.name}
        """)
        assert (
            classify_triviality(node, function_name="to_dict") == TrivialityClass.TRIVIAL_SERIALIZER
        )

    def test_return_tuple_of_self_attrs(self):
        node = _parse_func("""
            def to_tuple(self):
                return (self.x, self.y, self.z)
        """)
        assert classify_triviality(node) == TrivialityClass.TRIVIAL_SERIALIZER

    def test_multi_stmt_dict_builder(self):
        node = _parse_func("""
            def to_dict(self):
                d = {"name": self.name}
                if self.age:
                    d["age"] = self.age
                return d
        """)
        assert (
            classify_triviality(node, function_name="to_dict") == TrivialityClass.TRIVIAL_SERIALIZER
        )

    def test_not_serializer_with_computation(self):
        node = _parse_func("""
            def to_dict(self):
                return {"name": self.name.upper(), "hash": hash(self)}
        """)
        # self.name.upper() is not a self.attr — it's a method call
        assert classify_triviality(node, function_name="to_dict") == TrivialityClass.NONTRIVIAL

    def test_qualified_name_to_dict(self):
        node = _parse_func("""
            def to_dict(self):
                return {"x": self.x}
        """)
        assert (
            classify_triviality(node, function_name="MyClass.to_dict")
            == TrivialityClass.TRIVIAL_SERIALIZER
        )


# ── Nontrivial functions ──────────────────────────────────────────


class TestNontrivial:
    def test_function_with_logic(self):
        node = _parse_func("""
            def compute(self, x):
                if x > 0:
                    return x * 2
                return -x
        """)
        assert classify_triviality(node) == TrivialityClass.NONTRIVIAL

    def test_function_with_loop(self):
        node = _parse_func("""
            def process(self, items):
                result = []
                for item in items:
                    result.append(item.value)
                return result
        """)
        assert classify_triviality(node) == TrivialityClass.NONTRIVIAL

    def test_function_with_exception_handling(self):
        node = _parse_func("""
            def safe_get(self, key):
                try:
                    return self.data[key]
                except KeyError:
                    return None
        """)
        assert classify_triviality(node) == TrivialityClass.NONTRIVIAL


# ── is_trivial convenience ────────────────────────────────────────


class TestIsTrivial:
    def test_trivial_returns_true(self):
        node = _parse_func("""
            def get_x(self):
                return self.x
        """)
        assert is_trivial(node) is True

    def test_nontrivial_returns_false(self):
        node = _parse_func("""
            def compute(x, y):
                return x ** 2 + y ** 2
        """)
        assert is_trivial(node) is False


# ── Helper: _effective_body ──────────────────────────────────────


class TestEffectiveBody:
    """Direct tests for _effective_body — strips docstrings and pass."""

    def test_empty_pass_only(self):
        node = _parse_func("""
            def f():
                pass
        """)
        assert _effective_body(node) == []

    def test_docstring_only(self):
        node = _parse_func('''
            def f():
                """A docstring."""
        ''')
        assert _effective_body(node) == []

    def test_docstring_and_pass(self):
        node = _parse_func('''
            def f():
                """A docstring."""
                pass
        ''')
        assert _effective_body(node) == []

    def test_preserves_real_statements(self):
        node = _parse_func("""
            def f():
                x = 1
                return x
        """)
        body = _effective_body(node)
        assert len(body) == 2
        assert isinstance(body[0], ast.Assign)
        assert isinstance(body[1], ast.Return)

    def test_strips_leading_docstring_keeps_rest(self):
        node = _parse_func('''
            def f():
                """Doc."""
                x = 1
                return x
        ''')
        body = _effective_body(node)
        assert len(body) == 2
        assert isinstance(body[0], ast.Assign)

    def test_non_string_constant_expr_not_stripped(self):
        """An Expr(Constant(42)) at position 0 is NOT a docstring."""
        node = _parse_func("""
            def f():
                42
                return 1
        """)
        body = _effective_body(node)
        # 42 is a constant but not a string — should be kept
        assert len(body) == 2
        assert isinstance(body[0], ast.Expr)

    def test_pass_stripped_anywhere_in_body(self):
        node = _parse_func("""
            def f():
                x = 1
                pass
                return x
        """)
        body = _effective_body(node)
        assert len(body) == 2
        assert all(not isinstance(s, ast.Pass) for s in body)

    def test_multiple_pass_statements(self):
        node = _parse_func("""
            def f():
                pass
                pass
                pass
        """)
        assert _effective_body(node) == []

    def test_string_expr_not_at_position_zero_kept(self):
        """A string expression NOT at index 0 is not a docstring."""
        node = _parse_func("""
            def f():
                x = 1
                "not a docstring"
                return x
        """)
        body = _effective_body(node)
        assert len(body) == 3  # assign, string expr, return


# ── Helper: _is_self_attr ────────────────────────────────────────


class TestIsSelfAttr:
    """Direct tests for _is_self_attr — checks node is self.something."""

    def test_self_dot_x(self):
        tree = ast.parse("self.x")
        node = tree.body[0].value  # type: ignore[attr-defined]
        assert _is_self_attr(node) is True

    def test_cls_dot_x_is_not_self(self):
        tree = ast.parse("cls.x")
        node = tree.body[0].value  # type: ignore[attr-defined]
        assert _is_self_attr(node) is False

    def test_other_dot_x_is_not_self(self):
        tree = ast.parse("obj.x")
        node = tree.body[0].value  # type: ignore[attr-defined]
        assert _is_self_attr(node) is False

    def test_bare_name_not_self_attr(self):
        tree = ast.parse("x")
        node = tree.body[0].value  # type: ignore[attr-defined]
        assert _is_self_attr(node) is False

    def test_none_input(self):
        assert _is_self_attr(None) is False

    def test_constant_input(self):
        tree = ast.parse("42")
        node = tree.body[0].value  # type: ignore[attr-defined]
        assert _is_self_attr(node) is False

    def test_chained_self_attr_is_false(self):
        """self.a.b — the top-level node's .value is self.a, not self."""
        tree = ast.parse("self.a.b")
        node = tree.body[0].value  # type: ignore[attr-defined]
        # Top-level is Attribute(value=Attribute(value=Name('self')))
        # .value is self.a (an Attribute), not Name('self')
        assert _is_self_attr(node) is False


# ── Helper: _is_constant ─────────────────────────────────────────


class TestIsConstant:
    """Direct tests for _is_constant — checks node is ast.Constant."""

    def test_integer(self):
        tree = ast.parse("42")
        node = tree.body[0].value  # type: ignore[attr-defined]
        assert _is_constant(node) is True

    def test_string(self):
        tree = ast.parse("'hello'")
        node = tree.body[0].value  # type: ignore[attr-defined]
        assert _is_constant(node) is True

    def test_none_literal(self):
        tree = ast.parse("None")
        node = tree.body[0].value  # type: ignore[attr-defined]
        assert _is_constant(node) is True

    def test_float(self):
        tree = ast.parse("3.14")
        node = tree.body[0].value  # type: ignore[attr-defined]
        assert _is_constant(node) is True

    def test_bool(self):
        tree = ast.parse("True")
        node = tree.body[0].value  # type: ignore[attr-defined]
        assert _is_constant(node) is True

    def test_name_is_not_constant(self):
        tree = ast.parse("x")
        node = tree.body[0].value  # type: ignore[attr-defined]
        assert _is_constant(node) is False

    def test_call_is_not_constant(self):
        tree = ast.parse("int(5)")
        node = tree.body[0].value  # type: ignore[attr-defined]
        assert _is_constant(node) is False

    def test_none_input(self):
        assert _is_constant(None) is False


# ── Helper: _param_names ─────────────────────────────────────────


class TestParamNames:
    """Direct tests for _param_names — gets parameter names excluding self/cls."""

    def test_no_params(self):
        node = _parse_func("""
            def f():
                pass
        """)
        assert _param_names(node) == set()

    def test_self_excluded(self):
        node = _parse_func("""
            def f(self):
                pass
        """)
        assert _param_names(node) == set()

    def test_cls_excluded(self):
        node = _parse_func("""
            def f(cls):
                pass
        """)
        assert _param_names(node) == set()

    def test_regular_params(self):
        node = _parse_func("""
            def f(self, x, y, z):
                pass
        """)
        assert _param_names(node) == {"x", "y", "z"}

    def test_vararg(self):
        node = _parse_func("""
            def f(*args):
                pass
        """)
        assert _param_names(node) == {"args"}

    def test_kwarg(self):
        node = _parse_func("""
            def f(**kwargs):
                pass
        """)
        assert _param_names(node) == {"kwargs"}

    def test_vararg_and_kwarg(self):
        node = _parse_func("""
            def f(self, *args, **kwargs):
                pass
        """)
        assert _param_names(node) == {"args", "kwargs"}

    def test_kwonly_args(self):
        node = _parse_func("""
            def f(self, *, key, value):
                pass
        """)
        assert _param_names(node) == {"key", "value"}

    def test_posonly_args(self):
        node = _parse_func("""
            def f(x, y, /, z):
                pass
        """)
        assert _param_names(node) == {"x", "y", "z"}

    def test_all_param_types_combined(self):
        node = _parse_func("""
            def f(self, a, /, b, *args, c, **kwargs):
                pass
        """)
        assert _param_names(node) == {"a", "b", "args", "c", "kwargs"}


# ── Helper: _is_property_getter ──────────────────────────────────


class TestIsPropertyGetter:
    """Direct tests for _is_property_getter."""

    def test_property_returning_self_attr(self):
        node = _parse_func("""
            @property
            def name(self):
                return self._name
        """)
        stmt = _effective_body(node)[0]
        assert _is_property_getter(node, stmt) is True

    def test_no_property_decorator(self):
        node = _parse_func("""
            def name(self):
                return self._name
        """)
        stmt = _effective_body(node)[0]
        assert _is_property_getter(node, stmt) is False

    def test_property_returning_non_self_attr(self):
        """@property returning a local var — not a property getter."""
        node = _parse_func("""
            @property
            def name(self):
                return x
        """)
        stmt = _effective_body(node)[0]
        assert _is_property_getter(node, stmt) is False

    def test_property_with_none_return(self):
        """@property with bare `return` (no value)."""
        node = _parse_func("""
            @property
            def name(self):
                return
        """)
        stmt = _effective_body(node)[0]
        assert _is_property_getter(node, stmt) is False

    def test_non_return_statement(self):
        node = _parse_func("""
            @property
            def name(self):
                x = 1
        """)
        stmt = _effective_body(node)[0]
        assert _is_property_getter(node, stmt) is False

    def test_attribute_style_property_decorator(self):
        """Handles decorators like abc.property via Attribute node."""
        source = textwrap.dedent("""
            class C:
                @abc.property
                def name(self):
                    return self._name
        """)
        tree = ast.parse(source)
        func_node: ast.FunctionDef | None = None
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name == "name":
                func_node = n
                break
        assert func_node is not None
        stmt = _effective_body(func_node)[0]
        assert _is_property_getter(func_node, stmt) is True


# ── Helper: _is_simple_accessor ──────────────────────────────────


class TestIsSimpleAccessor:
    """Direct tests for _is_simple_accessor."""

    def test_return_self_attr(self):
        node = _parse_func("""
            def get(self):
                return self.x
        """)
        stmt = _effective_body(node)[0]
        assert _is_simple_accessor(stmt, node) is True

    def test_return_self_chained(self):
        """self.config.value — chained attribute access."""
        node = _parse_func("""
            def get(self):
                return self.config.value
        """)
        stmt = _effective_body(node)[0]
        assert _is_simple_accessor(stmt, node) is True

    def test_return_local_var(self):
        node = _parse_func("""
            def get(self):
                return x
        """)
        stmt = _effective_body(node)[0]
        assert _is_simple_accessor(stmt, node) is False

    def test_return_none_value(self):
        """Bare `return` with no value."""
        node = _parse_func("""
            def get(self):
                return
        """)
        stmt = _effective_body(node)[0]
        assert _is_simple_accessor(stmt, node) is False

    def test_non_return_statement(self):
        node = _parse_func("""
            def get(self):
                x = 1
        """)
        stmt = _effective_body(node)[0]
        assert _is_simple_accessor(stmt, node) is False

    def test_return_call_not_accessor(self):
        node = _parse_func("""
            def get(self):
                return self.compute()
        """)
        stmt = _effective_body(node)[0]
        assert _is_simple_accessor(stmt, node) is False

    def test_triple_chain_not_accessor(self):
        """self.a.b.c — only depth-1 and depth-2 are accepted."""
        node = _parse_func("""
            def get(self):
                return self.a.b.c
        """)
        stmt = _effective_body(node)[0]
        # self.a.b.c: top is Attribute(value=Attribute(value=Attribute(value=Name('self'))))
        # val.value is self.a.b which is NOT _is_self_attr (its .value is self.a, not Name('self'))
        # and val itself is not _is_self_attr either
        assert _is_simple_accessor(stmt, node) is False


# ── Helper: _is_identity_return ──────────────────────────────────


class TestIsIdentityReturn:
    """Direct tests for _is_identity_return."""

    def test_return_param(self):
        node = _parse_func("""
            def f(x):
                return x
        """)
        stmt = _effective_body(node)[0]
        assert _is_identity_return(stmt, node) is True

    def test_return_non_param(self):
        node = _parse_func("""
            def f(x):
                return y
        """)
        stmt = _effective_body(node)[0]
        assert _is_identity_return(stmt, node) is False

    def test_return_self_attr_not_identity(self):
        """Returning self.x is not identity — it must be a bare Name."""
        node = _parse_func("""
            def f(self):
                return self.x
        """)
        stmt = _effective_body(node)[0]
        assert _is_identity_return(stmt, node) is False

    def test_bare_return_not_identity(self):
        node = _parse_func("""
            def f(x):
                return
        """)
        stmt = _effective_body(node)[0]
        assert _is_identity_return(stmt, node) is False

    def test_non_return_statement(self):
        node = _parse_func("""
            def f(x):
                x = 1
        """)
        stmt = _effective_body(node)[0]
        assert _is_identity_return(stmt, node) is False

    def test_return_kwonly_param(self):
        node = _parse_func("""
            def f(*, key):
                return key
        """)
        stmt = _effective_body(node)[0]
        assert _is_identity_return(stmt, node) is True

    def test_return_constant_not_identity(self):
        node = _parse_func("""
            def f(x):
                return 42
        """)
        stmt = _effective_body(node)[0]
        assert _is_identity_return(stmt, node) is False


# ── Helper: _is_forwarder ───────────────────────────────────────


class TestIsForwarderHelper:
    """Direct tests for _is_forwarder — pure delegation check."""

    def test_simple_forwarding(self):
        node = _parse_func("""
            def f(x, y):
                return g(x, y)
        """)
        stmt = _effective_body(node)[0]
        assert _is_forwarder(stmt, node) is True

    def test_forwarding_with_self_attr_arg(self):
        node = _parse_func("""
            def f(self, x):
                return g(self.config, x)
        """)
        stmt = _effective_body(node)[0]
        assert _is_forwarder(stmt, node) is True

    def test_computed_arg_rejects(self):
        node = _parse_func("""
            def f(x):
                return g(x + 1)
        """)
        stmt = _effective_body(node)[0]
        assert _is_forwarder(stmt, node) is False

    def test_starred_param_forwarding(self):
        node = _parse_func("""
            def f(*args):
                return g(*args)
        """)
        stmt = _effective_body(node)[0]
        assert _is_forwarder(stmt, node) is True

    def test_kwargs_unpacking_param(self):
        node = _parse_func("""
            def f(**kwargs):
                return g(**kwargs)
        """)
        stmt = _effective_body(node)[0]
        assert _is_forwarder(stmt, node) is True

    def test_kwargs_unpacking_non_param_rejects(self):
        """**some_dict where some_dict is not a parameter."""
        node = _parse_func("""
            def f(x):
                return g(**other)
        """)
        stmt = _effective_body(node)[0]
        assert _is_forwarder(stmt, node) is False

    def test_keyword_with_param_value(self):
        node = _parse_func("""
            def f(x, y):
                return g(a=x, b=y)
        """)
        stmt = _effective_body(node)[0]
        assert _is_forwarder(stmt, node) is True

    def test_keyword_with_non_param_value(self):
        node = _parse_func("""
            def f(x):
                return g(a=unknown)
        """)
        stmt = _effective_body(node)[0]
        assert _is_forwarder(stmt, node) is False

    def test_keyword_with_self_attr_value(self):
        node = _parse_func("""
            def f(self):
                return g(config=self.config)
        """)
        stmt = _effective_body(node)[0]
        assert _is_forwarder(stmt, node) is True

    def test_keyword_with_computed_value_rejects(self):
        node = _parse_func("""
            def f(x):
                return g(a=x + 1)
        """)
        stmt = _effective_body(node)[0]
        assert _is_forwarder(stmt, node) is False

    def test_not_a_call_not_forwarder(self):
        node = _parse_func("""
            def f(x):
                return x
        """)
        stmt = _effective_body(node)[0]
        assert _is_forwarder(stmt, node) is False

    def test_bare_return_not_forwarder(self):
        node = _parse_func("""
            def f(x):
                return
        """)
        stmt = _effective_body(node)[0]
        assert _is_forwarder(stmt, node) is False

    def test_non_return_not_forwarder(self):
        node = _parse_func("""
            def f(x):
                g(x)
        """)
        stmt = _effective_body(node)[0]
        assert _is_forwarder(stmt, node) is False

    def test_self_forwarding(self):
        """Passing `self` as an argument is allowed."""
        node = _parse_func("""
            def f(self, x):
                return g(self, x)
        """)
        stmt = _effective_body(node)[0]
        assert _is_forwarder(stmt, node) is True

    def test_cls_forwarding(self):
        """Passing `cls` as an argument is allowed."""
        node = _parse_func("""
            def f(cls, x):
                return g(cls, x)
        """)
        stmt = _effective_body(node)[0]
        assert _is_forwarder(stmt, node) is True

    def test_keyword_with_self_value(self):
        """Using `self` as a keyword value is allowed."""
        node = _parse_func("""
            def f(self, x):
                return g(owner=self, data=x)
        """)
        stmt = _effective_body(node)[0]
        assert _is_forwarder(stmt, node) is True

    def test_no_args_call_is_forwarder(self):
        """A zero-argument delegation is still a forwarder."""
        node = _parse_func("""
            def f():
                return g()
        """)
        stmt = _effective_body(node)[0]
        assert _is_forwarder(stmt, node) is True


# ── Helper: _is_dict_serializer ──────────────────────────────────


class TestIsDictSerializer:
    """Direct tests for _is_dict_serializer."""

    def test_single_return_dict_literal(self):
        node = _parse_func("""
            def to_dict(self):
                return {"x": self.x, "y": self.y}
        """)
        body = _effective_body(node)
        assert _is_dict_serializer(body, node) is True

    def test_assign_then_return(self):
        node = _parse_func("""
            def to_dict(self):
                d = {"x": self.x}
                return d
        """)
        body = _effective_body(node)
        assert _is_dict_serializer(body, node) is True

    def test_assign_dict_with_non_self_value_rejects(self):
        node = _parse_func("""
            def to_dict(self):
                d = {"x": compute()}
                return d
        """)
        body = _effective_body(node)
        assert _is_dict_serializer(body, node) is False

    def test_assign_self_attr_allowed(self):
        """Assigning self.x directly (not in a dict literal) is allowed."""
        node = _parse_func("""
            def to_dict(self):
                val = self.name
                return val
        """)
        body = _effective_body(node)
        assert _is_dict_serializer(body, node) is True

    def test_assign_non_self_attr_rejects(self):
        node = _parse_func("""
            def to_dict(self):
                val = compute()
                return val
        """)
        body = _effective_body(node)
        assert _is_dict_serializer(body, node) is False

    def test_conditional_additions_allowed(self):
        node = _parse_func("""
            def to_dict(self):
                d = {"name": self.name}
                if self.age:
                    pass
                return d
        """)
        body = _effective_body(node)
        assert _is_dict_serializer(body, node) is True

    def test_expr_statements_allowed(self):
        """d.update(...) style calls are tolerated."""
        node = _parse_func("""
            def to_dict(self):
                d = {"name": self.name}
                d.update(other)
                return d
        """)
        body = _effective_body(node)
        assert _is_dict_serializer(body, node) is True

    def test_for_loop_rejects(self):
        """A for loop is not a serializer pattern."""
        node = _parse_func("""
            def to_dict(self):
                for item in self.items:
                    pass
                return {}
        """)
        body = _effective_body(node)
        assert _is_dict_serializer(body, node) is False

    def test_body_not_ending_with_return_rejects(self):
        node = _parse_func("""
            def to_dict(self):
                d = {"name": self.name}
                print(d)
        """)
        body = _effective_body(node)
        assert _is_dict_serializer(body, node) is False


# ── Helper: _is_literal_return ───────────────────────────────────


class TestIsLiteralReturn:
    """Direct tests for _is_literal_return."""

    def test_dict_of_self_attrs(self):
        node = _parse_func("""
            def f(self):
                return {"a": self.a, "b": self.b}
        """)
        stmt = _effective_body(node)[0]
        assert _is_literal_return(stmt) is True

    def test_dict_with_constants(self):
        node = _parse_func("""
            def f(self):
                return {"type": "user", "name": self.name}
        """)
        stmt = _effective_body(node)[0]
        assert _is_literal_return(stmt) is True

    def test_dict_with_call_value_rejects(self):
        node = _parse_func("""
            def f(self):
                return {"val": compute()}
        """)
        stmt = _effective_body(node)[0]
        assert _is_literal_return(stmt) is False

    def test_tuple_of_self_attrs(self):
        node = _parse_func("""
            def f(self):
                return (self.x, self.y)
        """)
        stmt = _effective_body(node)[0]
        assert _is_literal_return(stmt) is True

    def test_tuple_with_constants(self):
        node = _parse_func("""
            def f(self):
                return (self.x, 42)
        """)
        stmt = _effective_body(node)[0]
        assert _is_literal_return(stmt) is True

    def test_tuple_with_call_rejects(self):
        node = _parse_func("""
            def f(self):
                return (self.x, compute())
        """)
        stmt = _effective_body(node)[0]
        assert _is_literal_return(stmt) is False

    def test_non_return_statement(self):
        node = _parse_func("""
            def f(self):
                x = 1
        """)
        stmt = _effective_body(node)[0]
        assert _is_literal_return(stmt) is False

    def test_bare_return(self):
        node = _parse_func("""
            def f(self):
                return
        """)
        stmt = _effective_body(node)[0]
        assert _is_literal_return(stmt) is False

    def test_return_list_not_literal(self):
        """Lists are not matched — only dicts and tuples."""
        node = _parse_func("""
            def f(self):
                return [self.x, self.y]
        """)
        stmt = _effective_body(node)[0]
        assert _is_literal_return(stmt) is False

    def test_return_name_not_literal(self):
        node = _parse_func("""
            def f(self):
                return x
        """)
        stmt = _effective_body(node)[0]
        assert _is_literal_return(stmt) is False


# ── Helper: _dict_values_are_self_attrs ──────────────────────────


class TestDictValuesAreSelfAttrs:
    """Direct tests for _dict_values_are_self_attrs."""

    def test_all_self_attrs(self):
        tree = ast.parse('{"a": self.a, "b": self.b}')
        node = tree.body[0].value  # type: ignore[attr-defined]
        assert _dict_values_are_self_attrs(node) is True

    def test_mixed_self_attrs_and_constants(self):
        tree = ast.parse('{"type": "user", "name": self.name}')
        node = tree.body[0].value  # type: ignore[attr-defined]
        assert _dict_values_are_self_attrs(node) is True

    def test_all_constants(self):
        tree = ast.parse('{"a": 1, "b": "x"}')
        node = tree.body[0].value  # type: ignore[attr-defined]
        assert _dict_values_are_self_attrs(node) is True

    def test_call_value_rejects(self):
        tree = ast.parse('{"a": compute()}')
        node = tree.body[0].value  # type: ignore[attr-defined]
        assert _dict_values_are_self_attrs(node) is False

    def test_name_value_rejects(self):
        tree = ast.parse('{"a": x}')
        node = tree.body[0].value  # type: ignore[attr-defined]
        assert _dict_values_are_self_attrs(node) is False

    def test_empty_dict(self):
        tree = ast.parse("{}")
        node = tree.body[0].value  # type: ignore[attr-defined]
        assert _dict_values_are_self_attrs(node) is True

    def test_dict_unpacking_value_is_checked(self):
        """In {**other}, key is None but value is Name('other') — not self attr."""
        tree = ast.parse("{**other}")
        node = tree.body[0].value  # type: ignore[attr-defined]
        # The key is None (unpacking marker), but the value is Name('other')
        # which is neither self.attr nor constant, so it rejects.
        assert _dict_values_are_self_attrs(node) is False

    def test_other_attr_not_self(self):
        tree = ast.parse('{"a": obj.x}')
        node = tree.body[0].value  # type: ignore[attr-defined]
        assert _dict_values_are_self_attrs(node) is False
