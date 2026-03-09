"""Tests for source_mapper — test-to-source mapping."""

from __future__ import annotations

import ast
import os
import tempfile

from lintgate.linters.test_effectiveness.source_mapper import (
    _coerce_candidate_paths,
    _filter_candidates_by_module_hint,
    _get_name,
    _ImportCollector,
    _module_hint_from_import,
    _path_to_module,
    _strip_test_prefix,
    _symbol_name_from_import,
    _TestFunctionCollector,
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


def test_map_tests_to_source_class_scope_no_leakage_unique_keys():
    """Class-qualified and top-level tests retain correct lexical scope."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "src.py")
        test_path = os.path.join(tmpdir, "test_src.py")

        with open(src_path, "w", encoding="utf-8") as f:
            f.write("def foo():\n    return 1\n")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write(
                "from src import foo\n\n"
                "class TestA:\n"
                "    def test_one(self):\n"
                "        foo()\n\n"
                "def test_top(self=None):\n"
                "    foo()\n\n"
                "class TestB:\n"
                "    def test_two(self):\n"
                "        foo()\n"
            )

        index = build_source_function_index([src_path])
        mapping = map_tests_to_source(test_path, index, tmpdir)

        assert "src.py::foo" in mapping
        assert sorted(mapping["src.py::foo"]) == [
            "TestA.test_one",
            "TestB.test_two",
            "test_top",
        ]


def test_map_tests_to_source_disambiguates_by_import_hint():
    """When multiple files define same name, imports should disambiguate mapping."""
    with tempfile.TemporaryDirectory() as tmpdir:
        a_path = os.path.join(tmpdir, "a.py")
        b_path = os.path.join(tmpdir, "b.py")
        test_path = os.path.join(tmpdir, "test_mod.py")

        with open(a_path, "w", encoding="utf-8") as f:
            f.write("def foo():\n    return 1\n")
        with open(b_path, "w", encoding="utf-8") as f:
            f.write("def foo():\n    return 2\n")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("from a import foo\n\ndef test_foo():\n    assert foo() == 1\n")

        index = build_source_function_index([a_path, b_path])
        mapping = map_tests_to_source(test_path, index, tmpdir)

        assert "a.py::foo" in mapping
        assert "b.py::foo" not in mapping
        assert mapping["a.py::foo"] == ["test_foo"]


def test_map_tests_to_source_unresolved_ambiguity_is_skipped():
    """Ambiguous naming-only matches should not be over-attributed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        a_path = os.path.join(tmpdir, "a.py")
        b_path = os.path.join(tmpdir, "b.py")
        test_path = os.path.join(tmpdir, "test_mod.py")

        with open(a_path, "w", encoding="utf-8") as f:
            f.write("def foo():\n    return 1\n")
        with open(b_path, "w", encoding="utf-8") as f:
            f.write("def foo():\n    return 2\n")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("def test_foo():\n    assert True\n")

        index = build_source_function_index([a_path, b_path])
        mapping = map_tests_to_source(test_path, index, tmpdir)

        assert "a.py::foo" not in mapping
        assert "b.py::foo" not in mapping


def test_map_tests_to_source_alias_call_maps_to_imported_symbol():
    """Alias calls resolve via imported symbol (`from a import foo as f`)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        a_path = os.path.join(tmpdir, "a.py")
        b_path = os.path.join(tmpdir, "b.py")
        test_path = os.path.join(tmpdir, "test_alias.py")

        with open(a_path, "w", encoding="utf-8") as f:
            f.write("def foo():\n    return 1\n")
        with open(b_path, "w", encoding="utf-8") as f:
            f.write("def foo():\n    return 2\n")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("from a import foo as f\n\ndef test_alias():\n    assert f() == 1\n")

        index = build_source_function_index([a_path, b_path])
        mapping = map_tests_to_source(test_path, index, tmpdir)

        assert mapping["a.py::foo"] == ["test_alias"]
        assert "b.py::foo" not in mapping


def test_map_tests_to_source_qualifier_import_sets_module_hint():
    """Qualified calls (`alias.foo()`) use qualifier imports for disambiguation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        a_path = os.path.join(tmpdir, "a.py")
        b_path = os.path.join(tmpdir, "b.py")
        test_path = os.path.join(tmpdir, "test_qual.py")

        with open(a_path, "w", encoding="utf-8") as f:
            f.write("def foo():\n    return 1\n")
        with open(b_path, "w", encoding="utf-8") as f:
            f.write("def foo():\n    return 2\n")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("import a as mod\n\ndef test_foo():\n    assert mod.foo() == 1\n")

        index = build_source_function_index([a_path, b_path])
        mapping = map_tests_to_source(test_path, index, tmpdir)

        assert mapping["a.py::foo"] == ["test_foo"]
        assert "b.py::foo" not in mapping


def test_map_tests_to_source_local_helper_shadowing_is_skipped():
    """Local helper defs with same name should not be mapped to source symbols."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "a.py")
        test_path = os.path.join(tmpdir, "test_local.py")

        with open(src_path, "w", encoding="utf-8") as f:
            f.write("def helper():\n    return 1\n")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write(
                "def helper():\n    return 99\n\ndef test_helper():\n    assert helper() == 99\n"
            )

        index = build_source_function_index([src_path])
        mapping = map_tests_to_source(test_path, index, tmpdir)

        assert "a.py::helper" not in mapping


def test_map_tests_to_source_async_paths_are_collected():
    """Async tests/helpers hit async visitor paths without crashing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "a.py")
        test_path = os.path.join(tmpdir, "test_async.py")

        with open(src_path, "w", encoding="utf-8") as f:
            f.write("async def ping():\n    return 1\n")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write(
                "from a import ping\n\n"
                "async def helper():\n"
                "    return 0\n\n"
                "class TestAsync:\n"
                "    async def test_ping(self):\n"
                "        assert await ping() == 1\n"
            )

        index = build_source_function_index([src_path])
        mapping = map_tests_to_source(test_path, index, tmpdir)

        assert mapping["a.py::ping"] == ["TestAsync.test_ping"]


def test_filter_candidates_module_hint_no_hint_returns_input():
    """Empty module hints should return input candidates unchanged."""
    candidates = ["/tmp/a.py", "/tmp/b.py"]
    assert _filter_candidates_by_module_hint(candidates, "", "/tmp") == candidates


def test_filter_candidates_module_hint_init_module():
    """`pkg/__init__.py` should map to module name `pkg`."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pkg_dir = os.path.join(tmpdir, "pkg")
        os.makedirs(pkg_dir, exist_ok=True)
        init_path = os.path.join(pkg_dir, "__init__.py")
        with open(init_path, "w", encoding="utf-8") as f:
            f.write("def foo():\n    return 1\n")

        filtered = _filter_candidates_by_module_hint([init_path], "pkg", tmpdir)
        assert filtered == [init_path]


def test_path_to_module_handles_relpath_value_error(monkeypatch):
    """ValueError from relpath falls back to basename."""
    import os as _os

    original = _os.path.relpath

    def _raise_value_error(path: str, root: str) -> str:
        raise ValueError("no relative path")

    monkeypatch.setattr(_os.path, "relpath", _raise_value_error)
    try:
        assert _path_to_module("/tmp/pkg/mod.py", "/tmp") == "mod"
    finally:
        monkeypatch.setattr(_os.path, "relpath", original)


def test_module_and_symbol_hint_helpers_without_dots():
    """Import helper parsing should preserve bare names without dots."""
    assert _module_hint_from_import("foo") == "foo"
    assert _symbol_name_from_import("foo") == "foo"
    assert _coerce_candidate_paths(None) == []


def test_test_function_collector_visits_async_defs():
    """Async test functions are collected with class qualification."""
    tree = ast.parse("class TestA:\n    async def test_one(self):\n        pass\n")
    collector = _TestFunctionCollector()
    collector.visit(tree)
    assert collector.tests[0][0] == "TestA.test_one"


def test_build_source_function_index_three_way_ambiguity_list_append():
    """Third duplicate symbol should append into existing ambiguity list."""
    with tempfile.TemporaryDirectory() as tmpdir:
        paths: list[str] = []
        for idx in range(3):
            path = os.path.join(tmpdir, f"m{idx}.py")
            with open(path, "w", encoding="utf-8") as f:
                f.write("def foo():\n    return 1\n")
            paths.append(path)

        index = build_source_function_index(paths)

        assert isinstance(index["foo"], list)
        assert len(index["foo"]) == 3


def test_map_tests_to_source_handles_empty_candidate_entries():
    """Defensive handling: malformed empty candidate entries should be ignored."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as test:
        test.write("from a import foo\n\ndef test_foo():\n    assert foo() == 1\n")
        test.flush()
        test_path = test.name

    try:
        mapping = map_tests_to_source(test_path, {"foo": []})
        assert mapping == {}
    finally:
        os.unlink(test_path)


def test_diagnostics_strategy_breakdown_call_graph():
    """Verify call graph strategy populates its branch of strategy_breakdown."""
    from lintgate.linters.test_effectiveness.types import MappingDiagnostics

    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "a.py")
        test_path = os.path.join(tmpdir, "test_call.py")

        with open(src_path, "w", encoding="utf-8") as f:
            f.write("def foo():\n    return 1\n")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("from a import foo\n\ndef test_foo():\n    foo()\n")

        index = build_source_function_index([src_path])
        diag = MappingDiagnostics()
        map_tests_to_source(test_path, index, tmpdir, diagnostics=diag)

        assert "call_graph" in diag.strategy_breakdown
        sd = diag.strategy_breakdown["call_graph"]
        assert sd.attempted > 0
        assert sd.mapped > 0


def test_diagnostics_strategy_breakdown_naming():
    """Verify naming strategy populates its branch of strategy_breakdown."""
    from lintgate.linters.test_effectiveness.types import MappingDiagnostics

    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "a.py")
        test_path = os.path.join(tmpdir, "test_naming.py")

        with open(src_path, "w", encoding="utf-8") as f:
            f.write("def my_func():\n    return 1\n")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("def test_my_func():\n    pass\n")

        index = build_source_function_index([src_path])
        diag = MappingDiagnostics()
        map_tests_to_source(test_path, index, tmpdir, diagnostics=diag)

        assert "naming" in diag.strategy_breakdown
        sd = diag.strategy_breakdown["naming"]
        assert sd.attempted > 0
        assert sd.mapped > 0


def test_diagnostics_unique_symbol_counts():
    """Verify normalized metrics collect unique attempts and drops."""
    from lintgate.linters.test_effectiveness.types import MappingDiagnostics

    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "a.py")
        test_path = os.path.join(tmpdir, "test_foo.py")

        with open(src_path, "w", encoding="utf-8") as f:
            f.write("def target():\n    return 1\n")

        with open(test_path, "w", encoding="utf-8") as f:
            f.write(
                "from a import target, missing\n\n"
                "def test_1():\n"
                "    target()\n"
                "    missing()\n\n"
                "def test_2():\n"
                "    target()\n"
                "    missing()\n"
            )

        index = build_source_function_index([src_path])
        diag = MappingDiagnostics()
        map_tests_to_source(test_path, index, tmpdir, diagnostics=diag)

        assert diag.unique_symbols_attempted >= 2
        assert diag.unique_symbols_mapped == 1
        assert diag.test_functions_examined == 2


def test_diagnostics_top_drop_examples_and_dominant_reason():
    """Verify dominant drop reason and top examples list works."""
    from lintgate.linters.test_effectiveness.types import MappingDiagnostics

    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "a.py")
        test_path = os.path.join(tmpdir, "test_foo.py")

        with open(src_path, "w", encoding="utf-8") as f:
            f.write("pass\n")

        with open(test_path, "w", encoding="utf-8") as f:
            f.write(
                "def test_miss_1(): missing_1()\n"
                "def test_miss_2(): missing_2()\n"
                "def test_miss_3(): missing_3()\n"
            )

        index = build_source_function_index([src_path])
        diag = MappingDiagnostics()
        map_tests_to_source(test_path, index, tmpdir, diagnostics=diag)

        assert diag.dominant_drop_reason == "no_candidate"
        assert diag.dominant_drop_pct == 1.0
        assert len(diag.top_drop_examples) > 0
        assert "symbol" in diag.top_drop_examples[0]
        assert "reason" in diag.top_drop_examples[0]
        assert "strategy" in diag.top_drop_examples[0]


def test_diagnostics_normalized_vs_raw():
    """Verify unique symbol metrics vs aggregate logic."""
    from lintgate.linters.test_effectiveness.types import MappingDiagnostics

    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "a.py")
        test_path = os.path.join(tmpdir, "test_foo.py")

        with open(src_path, "w", encoding="utf-8") as f:
            f.write("def t(): pass\n")

        with open(test_path, "w", encoding="utf-8") as f:
            f.write("from a import t\ndef test_1(): t()\ndef test_2(): t()\ndef test_3(): t()\n")

        index = build_source_function_index([src_path])
        diag = MappingDiagnostics()
        map_tests_to_source(test_path, index, tmpdir, diagnostics=diag)

        assert diag.attempted == 3
        assert diag.unique_symbols_mapped == 1
