"""Tests for source_mapper — test-to-source mapping."""

from __future__ import annotations

import os
import tempfile

from lintgate.linters.test_effectiveness.source_mapper import (
    _strip_test_prefix,
    build_source_function_index,
    map_tests_to_source,
)


def test_strip_test_prefix_simple():
    """test_foo → foo."""
    assert _strip_test_prefix("test_foo") == "foo"


def test_strip_test_prefix_with_suffix():
    """test_foo_returns_expected_output → foo."""
    assert _strip_test_prefix("test_foo_returns_expected_output") == "foo"


def test_strip_test_prefix_class_qualified():
    """TestFoo.test_bar → bar."""
    assert _strip_test_prefix("TestFoo.test_bar") == "bar"


def test_strip_test_prefix_no_prefix():
    """No test_ prefix returns as-is."""
    assert _strip_test_prefix("helper_func") == "helper_func"


def test_strip_test_prefix_raises_suffix():
    """test_foo_raises_error → foo."""
    assert _strip_test_prefix("test_foo_raises_error") == "foo"


def test_build_source_function_index():
    """Indexes public functions from source files."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(
            "def public_func():\n"
            "    pass\n"
            "\n"
            "def _private_func():\n"
            "    pass\n"
            "\n"
            "class MyClass:\n"
            "    def method(self):\n"
            "        pass\n"
        )
        f.flush()
        filepath = f.name

    try:
        index = build_source_function_index([filepath])
        assert "public_func" in index
        assert "_private_func" in index  # indexed but private
        assert "MyClass.method" in index
        assert index["public_func"] == filepath
    finally:
        os.unlink(filepath)


def test_map_tests_to_source_by_name():
    """Maps test_foo to foo via naming convention."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as src:
        src.write("def compute():\n    return 42\n")
        src.flush()
        src_path = src.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", prefix="test_", delete=False) as test:
        test.write(
            "from module import compute\n\ndef test_compute():\n    assert compute() == 42\n"
        )
        test.flush()
        test_path = test.name

    try:
        index = build_source_function_index([src_path])
        mapping = map_tests_to_source(test_path, index)
        assert "compute" in mapping
        assert "test_compute" in mapping["compute"]
    finally:
        os.unlink(src_path)
        os.unlink(test_path)


def test_map_tests_to_source_empty_on_syntax_error():
    """Syntax errors in test file return empty mapping."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("def test_foo( broken syntax")
        f.flush()
        filepath = f.name

    try:
        mapping = map_tests_to_source(filepath, {"foo": "/some/path.py"})
        assert mapping == {}
    finally:
        os.unlink(filepath)


def test_build_source_function_index_syntax_error():
    """Syntax errors in source files are skipped."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("def broken( syntax")
        f.flush()
        filepath = f.name

    try:
        index = build_source_function_index([filepath])
        assert index == {}
    finally:
        os.unlink(filepath)
