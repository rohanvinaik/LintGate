"""Tests for source_mapper — test-to-source mapping."""

from __future__ import annotations

import ast
import os
import tempfile

from lintgate.linters.test_effectiveness.source_mapper import (
    _get_name,
    _ImportCollector,
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


# --- _get_name tests (lines 21-24) ---


def test_get_name_attribute_node():
    """_get_name extracts dotted name from ast.Attribute (e.g., obj.attr)."""
    tree = ast.parse("obj.attr", mode="eval")
    # tree.body is an ast.Attribute node
    result = _get_name(tree.body)
    assert result == "obj.attr"


def test_get_name_nested_attribute():
    """_get_name handles nested attributes (a.b.c)."""
    tree = ast.parse("a.b.c", mode="eval")
    result = _get_name(tree.body)
    assert result == "a.b.c"


def test_get_name_unsupported_node_returns_empty():
    """_get_name returns empty string for unsupported node types."""
    tree = ast.parse("42", mode="eval")
    # tree.body is ast.Constant, not Name or Attribute
    result = _get_name(tree.body)
    assert result == ""


def test_get_name_attribute_on_unsupported_base():
    """_get_name handles Attribute where value is unsupported (e.g., call().attr)."""
    tree = ast.parse("foo().attr", mode="eval")
    # tree.body is ast.Attribute with value=ast.Call (unsupported base)
    result = _get_name(tree.body)
    # prefix is "" from the Call node, so result is just "attr"
    assert result == "attr"


# --- _ImportCollector.visit_Import tests (lines 35-38) ---


def test_import_collector_visit_import():
    """_ImportCollector handles plain 'import foo' statements."""
    tree = ast.parse("import os\nimport sys")
    collector = _ImportCollector()
    collector.visit(tree)
    assert "os" in collector.imported_modules
    assert "sys" in collector.imported_modules
    assert collector.imported_names["os"] == "os"
    assert collector.imported_names["sys"] == "sys"


def test_import_collector_visit_import_with_alias():
    """_ImportCollector handles 'import foo as bar' statements."""
    tree = ast.parse("import numpy as np")
    collector = _ImportCollector()
    collector.visit(tree)
    assert "numpy" in collector.imported_modules
    assert collector.imported_names["np"] == "numpy"
    assert "numpy" not in collector.imported_names


# --- map_tests_to_source: class_stack / test_qualname (lines 178, 183, 187) ---


def test_map_tests_class_qualified_test_name():
    """Tests inside a class get class-qualified names (TestFoo.test_bar)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as src:
        src.write("def bar():\n    return 1\n")
        src.flush()
        src_path = src.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as test:
        test.write(
            "from module import bar\n\n"
            "class TestSomething:\n"
            "    def test_bar(self):\n"
            "        assert bar() == 1\n"
        )
        test.flush()
        test_path = test.name

    try:
        index = build_source_function_index([src_path])
        mapping = map_tests_to_source(test_path, index)
        assert "bar" in mapping
        # The test should be recorded with class-qualified name
        test_names = mapping["bar"]
        assert any("test_bar" in name for name in test_names)
    finally:
        os.unlink(src_path)
        os.unlink(test_path)


def test_map_tests_skips_non_test_functions():
    """Non-test functions (no test_ prefix) inside test file are skipped."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as test:
        test.write(
            "def helper_setup():\n"
            "    pass\n\n"
            "def test_something():\n"
            "    helper_setup()\n"
            "    assert True\n"
        )
        test.flush()
        test_path = test.name

    try:
        mapping = map_tests_to_source(test_path, {"something": "/src/mod.py"})
        # helper_setup should not appear as a test function
        for tests in mapping.values():
            assert "helper_setup" not in tests
    finally:
        os.unlink(test_path)


# --- map_tests_to_source: call_name direct match (lines 198-199) ---


def test_map_tests_call_name_direct_match_in_source_index():
    """Call names that match source index directly (not via import) are recorded."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as test:
        # The test calls a function that is in the source index but NOT imported
        # by local name -- it uses the dotted call name directly
        test.write("import mymodule\n\ndef test_alpha():\n    mymodule.do_work()\n")
        test.flush()
        test_path = test.name

    try:
        # "mymodule.do_work" is in the source index directly
        index = {"mymodule.do_work": "/src/mymodule.py", "do_work": "/src/mymodule.py"}
        mapping = map_tests_to_source(test_path, index)
        # "mymodule.do_work" should be matched via the elif branch (line 198-199):
        # call_name "mymodule.do_work" is in source_function_index
        assert "mymodule.do_work" in mapping
        assert "test_alpha" in mapping["mymodule.do_work"]
    finally:
        os.unlink(test_path)


# --- map_tests_to_source: TestFoo -> Foo class mapping (lines 207-211) ---


def test_map_tests_class_prefix_strip_testfoo_to_foo():
    """TestFoo.test_bar maps to Foo.bar via class prefix stripping."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as src:
        src.write("class Foo:\n    def bar(self):\n        return 1\n")
        src.flush()
        src_path = src.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as test:
        test.write("class TestFoo:\n    def test_bar(self):\n        assert True\n")
        test.flush()
        test_path = test.name

    try:
        index = build_source_function_index([src_path])
        # Verify Foo.bar is in the index
        assert "Foo.bar" in index
        mapping = map_tests_to_source(test_path, index)
        # TestFoo.test_bar -> strip "Test" prefix -> Foo, strip test_ -> bar
        # -> Foo.bar should be matched (lines 207-211)
        assert "Foo.bar" in mapping
        assert "TestFoo.test_bar" in mapping["Foo.bar"]
    finally:
        os.unlink(src_path)
        os.unlink(test_path)


def test_map_tests_class_prefix_non_test_class_no_strip():
    """Classes not starting with 'Test' do not trigger Foo.bar stripping."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as test:
        test.write("class SuiteFoo:\n    def test_bar(self):\n        assert True\n")
        test.flush()
        test_path = test.name

    try:
        index = {"Foo.bar": "/src/foo.py", "bar": "/src/foo.py"}
        mapping = map_tests_to_source(test_path, index)
        # SuiteFoo does not start with "Test", so Foo.bar should NOT be matched
        # via the class prefix stripping logic
        # bar may match via naming convention though
        if "Foo.bar" in mapping:
            # Should not be matched via class stripping path
            raise AssertionError("Foo.bar should not match from SuiteFoo class")
    finally:
        os.unlink(test_path)
