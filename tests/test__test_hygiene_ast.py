"""Tests for lintgate/channels/_test_hygiene_ast.py."""

from __future__ import annotations

import ast
import hashlib
import os
import textwrap

from lintgate.channels._test_hygiene_ast import (
    _extract_class_test_methods,
    _extract_test_functions,
    _function_body_ast_hash,
    _function_body_source,
    _function_context_hash,
    _parse_file,
    _read_source,
)


# ── _parse_file ──────────────────────────────────────────────────────────


def test_parse_file_valid(tmp_path):
    p = tmp_path / "valid.py"
    p.write_text("x = 1\n")
    tree = _parse_file(str(p))
    assert isinstance(tree, ast.Module)
    assert len(tree.body) == 1


def test_parse_file_syntax_error(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text("def (:\n")
    assert _parse_file(str(p)) is None


def test_parse_file_missing():
    assert _parse_file("/nonexistent/path/xyz.py") is None


# ── _read_source ─────────────────────────────────────────────────────────


def test_read_source_valid(tmp_path):
    p = tmp_path / "src.py"
    p.write_text("hello = 'world'\n")
    assert _read_source(str(p)) == "hello = 'world'\n"


def test_read_source_missing():
    assert _read_source("/nonexistent/xyz.py") is None


# ── _extract_class_test_methods ──────────────────────────────────────────


def test_extract_class_test_methods_basic():
    src = textwrap.dedent("""\
        class TestFoo:
            def test_alpha(self):
                pass
            def helper(self):
                pass
            def test_beta(self):
                pass
    """)
    tree = ast.parse(src)
    cls_node = tree.body[0]
    result = _extract_class_test_methods(cls_node)
    assert len(result) == 2
    assert result[0][0] == "test_alpha"
    assert result[0][2] == "TestFoo"
    assert result[1][0] == "test_beta"


def test_extract_class_test_methods_empty():
    src = textwrap.dedent("""\
        class TestEmpty:
            pass
    """)
    tree = ast.parse(src)
    cls_node = tree.body[0]
    assert _extract_class_test_methods(cls_node) == []


# ── _extract_test_functions ──────────────────────────────────────────────


def test_extract_test_functions_mixed():
    src = textwrap.dedent("""\
        def test_standalone():
            pass

        def helper():
            pass

        class TestGroup:
            def test_inner(self):
                pass
    """)
    tree = ast.parse(src)
    result = _extract_test_functions(tree)
    assert len(result) == 2
    names = [r[0] for r in result]
    assert names == ["test_standalone", "test_inner"]
    # standalone has class_name=None, inner has "TestGroup"
    assert result[0][2] is None
    assert result[1][2] == "TestGroup"


def test_extract_test_functions_async():
    src = textwrap.dedent("""\
        async def test_async_fn():
            pass
    """)
    tree = ast.parse(src)
    result = _extract_test_functions(tree)
    assert len(result) == 1
    assert result[0][0] == "test_async_fn"
    assert result[0][2] is None


# ── _function_body_source ────────────────────────────────────────────────


def test_function_body_source_simple():
    src = textwrap.dedent("""\
        def test_x():
            a = 1
            b = 2
    """)
    tree = ast.parse(src)
    func = tree.body[0]
    body = _function_body_source(src, func)
    assert "a = 1" in body
    assert "b = 2" in body


def test_function_body_source_skips_docstring():
    src = textwrap.dedent("""\
        def test_y():
            \"\"\"Docstring.\"\"\"
            return 42
    """)
    tree = ast.parse(src)
    func = tree.body[0]
    body = _function_body_source(src, func)
    assert "return 42" in body
    assert "Docstring" not in body


def test_function_body_source_empty_after_docstring():
    src = textwrap.dedent("""\
        def test_z():
            \"\"\"Only a docstring.\"\"\"
    """)
    tree = ast.parse(src)
    func = tree.body[0]
    body = _function_body_source(src, func)
    assert body == ""


# ── _function_context_hash ───────────────────────────────────────────────


def test_function_context_hash_differs_by_decorator():
    src_a = textwrap.dedent("""\
        def test_a(x):
            pass
    """)
    src_b = textwrap.dedent("""\
        @pytest.mark.parametrize("x", [1, 2])
        def test_a(x):
            pass
    """)
    tree_a = ast.parse(src_a)
    tree_b = ast.parse(src_b)
    hash_a = _function_context_hash(tree_a.body[0])
    hash_b = _function_context_hash(tree_b.body[0])
    assert hash_a != hash_b


def test_function_context_hash_excludes_self():
    src = textwrap.dedent("""\
        def test_m(self, fixture_a):
            pass
    """)
    tree = ast.parse(src)
    h = _function_context_hash(tree.body[0])
    # Should only include "fixture_a", not "self"
    parts = ["fixture_a"]
    expected = hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
    assert h == expected


def test_function_context_hash_deterministic():
    src = textwrap.dedent("""\
        def test_d():
            pass
    """)
    tree = ast.parse(src)
    h1 = _function_context_hash(tree.body[0])
    h2 = _function_context_hash(tree.body[0])
    assert h1 == h2
    assert len(h1) == 16


# ── _function_body_ast_hash ─────────────────────────────────────────────


def test_function_body_ast_hash_identical_bodies():
    src_a = textwrap.dedent("""\
        def test_a():
            x = 1
    """)
    src_b = textwrap.dedent("""\
        def test_b():
            x = 1
    """)
    tree_a = ast.parse(src_a)
    tree_b = ast.parse(src_b)
    assert _function_body_ast_hash(tree_a.body[0]) == _function_body_ast_hash(tree_b.body[0])


def test_function_body_ast_hash_different_bodies():
    src_a = textwrap.dedent("""\
        def test_a():
            x = 1
    """)
    src_b = textwrap.dedent("""\
        def test_b():
            x = 2
    """)
    tree_a = ast.parse(src_a)
    tree_b = ast.parse(src_b)
    assert _function_body_ast_hash(tree_a.body[0]) != _function_body_ast_hash(tree_b.body[0])


def test_function_body_ast_hash_empty_body():
    src = textwrap.dedent("""\
        def test_e():
            \"\"\"Only docstring.\"\"\"
    """)
    tree = ast.parse(src)
    h = _function_body_ast_hash(tree.body[0])
    expected = hashlib.sha256(b"empty").hexdigest()[:16]
    assert h == expected
