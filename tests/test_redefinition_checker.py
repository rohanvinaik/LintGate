"""Tests for lintgate.linters.redefinition_checker — duplicate definition detection."""

from __future__ import annotations

import ast
import textwrap

import pytest

from lintgate.linters.redefinition_checker import (
    RedefinitionChecker,
    _check_file,
    _check_scope,
    _has_overload_decorator,
    _has_property_decorator,
    _is_type_checking_block,
)


# ── _has_overload_decorator ───────────────────────────────────────────


def _parse_funcdef(code: str) -> ast.FunctionDef:
    """Parse code and return the first function definition."""
    tree = ast.parse(textwrap.dedent(code))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise ValueError("No function definition found")


class TestHasOverloadDecorator:
    def test_bare_overload(self):
        node = _parse_funcdef(
            """\
            @overload
            def f(x: int) -> int: ...
            """
        )
        assert _has_overload_decorator(node) is True

    def test_typing_overload(self):
        node = _parse_funcdef(
            """\
            @typing.overload
            def f(x: int) -> int: ...
            """
        )
        assert _has_overload_decorator(node) is True

    def test_no_decorator(self):
        node = _parse_funcdef(
            """\
            def f(x: int) -> int:
                return x
            """
        )
        assert _has_overload_decorator(node) is False

    def test_other_decorator(self):
        node = _parse_funcdef(
            """\
            @staticmethod
            def f(): pass
            """
        )
        assert _has_overload_decorator(node) is False


# ── _has_property_decorator ───────────────────────────────────────────


class TestHasPropertyDecorator:
    def test_property_decorator(self):
        node = _parse_funcdef(
            """\
            @property
            def name(self): return self._name
            """
        )
        assert _has_property_decorator(node) is True

    def test_setter_decorator(self):
        node = _parse_funcdef(
            """\
            @name.setter
            def name(self, value): self._name = value
            """
        )
        assert _has_property_decorator(node) is True

    def test_deleter_decorator(self):
        node = _parse_funcdef(
            """\
            @name.deleter
            def name(self): del self._name
            """
        )
        assert _has_property_decorator(node) is True

    def test_cached_property_name(self):
        node = _parse_funcdef(
            """\
            @cached_property
            def value(self): return 42
            """
        )
        assert _has_property_decorator(node) is True

    def test_functools_cached_property(self):
        node = _parse_funcdef(
            """\
            @functools.cached_property
            def value(self): return 42
            """
        )
        assert _has_property_decorator(node) is True

    def test_no_property_decorator(self):
        node = _parse_funcdef(
            """\
            def f(self): return 1
            """
        )
        assert _has_property_decorator(node) is False


# ── _is_type_checking_block ──────────────────────────────────────────


class TestIsTypeCheckingBlock:
    def test_bare_type_checking(self):
        tree = ast.parse("if TYPE_CHECKING:\n    pass\n")
        node = tree.body[0]
        assert _is_type_checking_block(node) is True

    def test_typing_type_checking(self):
        tree = ast.parse("if typing.TYPE_CHECKING:\n    pass\n")
        node = tree.body[0]
        assert _is_type_checking_block(node) is True

    def test_regular_if(self):
        tree = ast.parse("if True:\n    pass\n")
        node = tree.body[0]
        assert _is_type_checking_block(node) is False

    def test_non_if_node(self):
        tree = ast.parse("x = 1\n")
        node = tree.body[0]
        assert _is_type_checking_block(node) is False


# ── _check_scope ─────────────────────────────────────────────────────


class TestCheckScope:
    def test_duplicate_function_detected(self):
        code = textwrap.dedent("""\
            def foo():
                pass
            def foo():
                pass
        """)
        tree = ast.parse(code)
        issues = list(_check_scope(tree.body, "test.py", scope_name="<module>"))
        assert len(issues) == 1
        assert issues[0].kind == "redefinition"
        assert issues[0].severity == "blocking"
        assert issues[0].confidence == 1.0
        assert issues[0].evidence["name"] == "foo"
        assert issues[0].evidence["first_line"] == 1
        assert issues[0].evidence["second_line"] == 3
        assert issues[0].evidence["scope"] == "<module>"

    def test_duplicate_class_detected(self):
        code = textwrap.dedent("""\
            class Foo:
                pass
            class Foo:
                pass
        """)
        tree = ast.parse(code)
        issues = list(_check_scope(tree.body, "test.py", scope_name="<module>"))
        assert len(issues) == 1
        assert issues[0].evidence["first_type"] == "class"
        assert issues[0].evidence["second_type"] == "class"

    def test_no_duplicates_no_issues(self):
        code = textwrap.dedent("""\
            def foo(): pass
            def bar(): pass
        """)
        tree = ast.parse(code)
        issues = list(_check_scope(tree.body, "test.py", scope_name="<module>"))
        assert issues == []

    def test_overload_skipped(self):
        code = textwrap.dedent("""\
            @overload
            def foo(x: int) -> int: ...
            @overload
            def foo(x: str) -> str: ...
            def foo(x):
                return x
        """)
        tree = ast.parse(code)
        issues = list(_check_scope(tree.body, "test.py", scope_name="<module>"))
        assert issues == []

    def test_try_block_skipped(self):
        code = textwrap.dedent("""\
            def foo(): pass
            try:
                def foo(): pass
            except Exception:
                pass
        """)
        tree = ast.parse(code)
        issues = list(_check_scope(tree.body, "test.py", scope_name="<module>"))
        # The try block is skipped entirely, so no redefinition is detected
        assert issues == []

    def test_if_block_skipped(self):
        code = textwrap.dedent("""\
            def foo(): pass
            if some_condition:
                def foo(): pass
        """)
        tree = ast.parse(code)
        issues = list(_check_scope(tree.body, "test.py", scope_name="<module>"))
        assert issues == []


# ── _check_file ──────────────────────────────────────────────────────


class TestCheckFile:
    def test_file_with_redefinition(self, tmp_path):
        src = tmp_path / "dup.py"
        src.write_text(textwrap.dedent("""\
            def foo():
                pass
            def foo():
                pass
        """))
        issues = list(_check_file(str(src)))
        assert len(issues) == 1
        assert issues[0].file == str(src)
        assert issues[0].kind == "redefinition"

    def test_class_method_redefinition(self, tmp_path):
        src = tmp_path / "cls.py"
        src.write_text(textwrap.dedent("""\
            class MyClass:
                def method(self):
                    pass
                def method(self):
                    pass
        """))
        issues = list(_check_file(str(src)))
        assert len(issues) == 1
        assert issues[0].evidence["scope"] == "class MyClass"

    def test_nonexistent_file_yields_nothing(self):
        issues = list(_check_file("/no/such/file.py"))
        assert issues == []

    def test_syntax_error_file_yields_nothing(self, tmp_path):
        src = tmp_path / "broken.py"
        src.write_text("def f(:\n")
        issues = list(_check_file(str(src)))
        assert issues == []

    def test_clean_file_no_issues(self, tmp_path):
        src = tmp_path / "clean.py"
        src.write_text(textwrap.dedent("""\
            def alpha(): pass
            def beta(): pass
            class Gamma: pass
        """))
        issues = list(_check_file(str(src)))
        assert issues == []
