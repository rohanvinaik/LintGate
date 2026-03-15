"""Tests for lintgate.channels._contract_drift_types.

Covers _extract_function_return_arities, _arity_from_annotation,
_extract_function_params, _find_function_line, and _filepath_to_module.
"""

from __future__ import annotations

import ast
import textwrap
from typing import cast

from lintgate.channels._contract_drift_types import (
    AffectedTestSite,
    ContractDriftResult,
    SignatureChange,
    _arity_from_annotation,
    _extract_function_params,
    _extract_function_return_arities,
    _filepath_to_module,
    _find_function_line,
)

# ── _extract_function_return_arities ────────────────────────────────


class TestExtractFunctionReturnArities:
    def test_tuple_return(self):
        src = textwrap.dedent("""\
            def func():
                return a, b, c
        """)
        tree = ast.parse(src)
        arities = _extract_function_return_arities(tree)
        assert arities == {"func": 3}

    def test_scalar_return_excluded(self):
        src = textwrap.dedent("""\
            def func():
                return 42
        """)
        tree = ast.parse(src)
        arities = _extract_function_return_arities(tree)
        assert arities == {}

    def test_no_return_excluded(self):
        src = textwrap.dedent("""\
            def func():
                pass
        """)
        tree = ast.parse(src)
        arities = _extract_function_return_arities(tree)
        assert arities == {}

    def test_annotated_tuple_return(self):
        src = textwrap.dedent("""\
            def func() -> tuple[int, str]:
                return 1, "x"
        """)
        tree = ast.parse(src)
        arities = _extract_function_return_arities(tree)
        assert arities == {"func": 2}

    def test_multiple_functions(self):
        src = textwrap.dedent("""\
            def a():
                return 1, 2
            def b():
                return 1, 2, 3
            def c():
                return 42
        """)
        tree = ast.parse(src)
        arities = _extract_function_return_arities(tree)
        assert arities == {"a": 2, "b": 3}

    def test_async_function(self):
        src = textwrap.dedent("""\
            async def afunc() -> tuple[int, str, float]:
                return 1, "x", 0.5
        """)
        tree = ast.parse(src)
        arities = _extract_function_return_arities(tree)
        assert arities == {"afunc": 3}


# ── _arity_from_annotation ─────────────────────────────────────────


class TestArityFromAnnotation:
    def test_tuple_annotation(self):
        src = "def f() -> tuple[int, str, float]: pass"
        tree = ast.parse(src)
        func_node = cast("ast.FunctionDef", tree.body[0])
        assert _arity_from_annotation(func_node) == 3

    def test_no_annotation(self):
        src = "def f(): pass"
        tree = ast.parse(src)
        func_node = cast("ast.FunctionDef", tree.body[0])
        assert _arity_from_annotation(func_node) is None

    def test_non_tuple_annotation(self):
        src = "def f() -> int: pass"
        tree = ast.parse(src)
        func_node = cast("ast.FunctionDef", tree.body[0])
        assert _arity_from_annotation(func_node) is None

    def test_non_subscript_annotation(self):
        src = "def f() -> list: pass"
        tree = ast.parse(src)
        func_node = cast("ast.FunctionDef", tree.body[0])
        assert _arity_from_annotation(func_node) is None


# ── _extract_function_params ────────────────────────────────────────


class TestExtractFunctionParams:
    def test_simple_params(self):
        src = textwrap.dedent("""\
            def func(x, y, z):
                pass
        """)
        tree = ast.parse(src)
        params = _extract_function_params(tree)
        assert params == {"func": {"x", "y", "z"}}

    def test_self_cls_excluded(self):
        src = textwrap.dedent("""\
            class C:
                def method(self, x):
                    pass
                @classmethod
                def clsmethod(cls, y):
                    pass
        """)
        tree = ast.parse(src)
        params = _extract_function_params(tree)
        assert params["method"] == {"x"}
        assert params["clsmethod"] == {"y"}

    def test_vararg_kwarg(self):
        src = textwrap.dedent("""\
            def func(*args, **kwargs):
                pass
        """)
        tree = ast.parse(src)
        params = _extract_function_params(tree)
        assert params == {"func": {"*args", "**kwargs"}}

    def test_kwonly_params(self):
        src = textwrap.dedent("""\
            def func(x, *, key):
                pass
        """)
        tree = ast.parse(src)
        params = _extract_function_params(tree)
        assert params == {"func": {"x", "key"}}

    def test_no_params(self):
        src = textwrap.dedent("""\
            def func():
                pass
        """)
        tree = ast.parse(src)
        params = _extract_function_params(tree)
        assert params == {"func": set()}


# ── _find_function_line ─────────────────────────────────────────────


class TestFindFunctionLine:
    def test_finds_correct_line(self):
        src = textwrap.dedent("""\
            x = 1

            def target():
                pass
        """)
        tree = ast.parse(src)
        assert _find_function_line(tree, "target") == 3

    def test_returns_zero_for_missing(self):
        src = textwrap.dedent("""\
            def other():
                pass
        """)
        tree = ast.parse(src)
        assert _find_function_line(tree, "nonexistent") == 0

    def test_async_function(self):
        src = textwrap.dedent("""\
            async def async_target():
                pass
        """)
        tree = ast.parse(src)
        assert _find_function_line(tree, "async_target") == 1

    def test_multiple_functions_correct_line(self):
        src = textwrap.dedent("""\
            def first():
                pass

            def second():
                pass

            def third():
                pass
        """)
        tree = ast.parse(src)
        assert _find_function_line(tree, "first") == 1
        assert _find_function_line(tree, "second") == 4
        assert _find_function_line(tree, "third") == 7


# ── _filepath_to_module ─────────────────────────────────────────────


class TestFilepathToModule:
    def test_simple_path(self):
        result = _filepath_to_module("lintgate/channels/drift.py")
        # Should strip .py and convert to dotted path
        assert "." in result
        assert "py" not in result.split(".")[-1] or result.endswith(".py") is False

    def test_strips_py_extension(self):
        result = _filepath_to_module("some/module.py")
        assert not result.endswith(".py")

    def test_no_extension(self):
        result = _filepath_to_module("some/module")
        # Should still produce a dotted path
        assert "some" in result or "module" in result

    def test_fallback_last_three_parts(self):
        # Path where no part looks like a valid on-disk package
        result = _filepath_to_module("x/y/z/w.py")
        # Should produce dotted representation from parts
        parts = result.split(".")
        assert len(parts) >= 2


# ── dataclass sanity ────────────────────────────────────────────────


class TestDataclasses:
    def test_signature_change_defaults(self):
        sc = SignatureChange(module="m", function="f", file="f.py")
        assert sc.line == 0
        assert sc.change_type == ""
        assert sc.old_value is None
        assert sc.new_value is None

    def test_affected_test_site_defaults(self):
        site = AffectedTestSite(test_file="t.py", line=5)
        assert site.unpacking_arity is None
        assert site.call_expression == ""

    def test_contract_drift_result_defaults(self):
        sc = SignatureChange(module="m", function="f", file="f.py")
        result = ContractDriftResult(change=sc)
        assert result.affected_sites == []
        assert result.advisory == ""
