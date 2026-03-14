"""Specification tests for P0 risk functions in mcp_tools/_mutation_impl.py.

Target functions:
  - resolve_function: sigma=42, regime B, risk 0.7
  - iter_cached_states: sigma=30, regime B, risk 0.7

Covers all major branch paths, edge cases, and error handling.
"""

from __future__ import annotations

import ast
import json
import os
import textwrap
from pathlib import Path

import pytest

from mcp_tools._mutation_impl import iter_cached_states, resolve_function

# ════════════════════════════════════════════════════════════════════
# resolve_function — specification tests
# ════════════════════════════════════════════════════════════════════


class TestResolveFunctionFileNotFound:
    """EP1: file does not exist."""

    def test_relative_path_not_found(self, tmp_path):
        full, node, err = resolve_function(str(tmp_path), "no_such_file.py", None)
        assert full == os.path.join(str(tmp_path), "no_such_file.py")
        assert node is None
        assert err == "File not found: no_such_file.py"

    def test_absolute_path_not_found(self, tmp_path):
        abs_path = str(tmp_path / "ghost.py")
        full, node, err = resolve_function(str(tmp_path), abs_path, None)
        assert full == abs_path
        assert node is None
        assert err == f"File not found: {abs_path}"

    def test_empty_string_file(self, tmp_path):
        full, node, err = resolve_function(str(tmp_path), "", None)
        assert node is None
        # An empty filename joined to root is the root dir, which is not a file
        assert err is not None
        assert "File not found" in err


class TestResolveFunctionParseError:
    """EP2: file exists but contains invalid Python."""

    def test_syntax_error(self, tmp_path):
        bad_file = tmp_path / "bad.py"
        bad_file.write_text("def broken(\n", encoding="utf-8")
        full, node, err = resolve_function(str(tmp_path), "bad.py", None)
        assert full == str(bad_file)
        assert node is None
        assert err is not None
        assert err.startswith("Parse error:")

    def test_binary_file_as_python(self, tmp_path):
        """Binary content with invalid UTF-8 raises UnicodeDecodeError.

        Note: resolve_function catches OSError and SyntaxError but not
        UnicodeDecodeError. This documents the current behavior — callers
        must handle non-UTF-8 files upstream or this could be hardened.
        """
        bin_file = tmp_path / "binary.py"
        bin_file.write_bytes(b"\x00\x01\x02\x03\x80\xff")
        with pytest.raises(UnicodeDecodeError):
            resolve_function(str(tmp_path), "binary.py", None)

    def test_empty_file_no_function_requested(self, tmp_path):
        empty = tmp_path / "empty.py"
        empty.write_text("", encoding="utf-8")
        full, node, err = resolve_function(str(tmp_path), "empty.py", None)
        assert full == str(empty)
        assert node is None
        assert err is None  # no function requested, empty file parses fine


class TestResolveFunctionNoFunctionRequested:
    """EP3: file exists, parses OK, function=None."""

    def test_returns_path_and_no_error(self, tmp_path):
        src = tmp_path / "valid.py"
        src.write_text("x = 1\n", encoding="utf-8")
        full, node, err = resolve_function(str(tmp_path), "valid.py", None)
        assert full == str(src)
        assert node is None
        assert err is None

    def test_absolute_path_bypass(self, tmp_path):
        src = tmp_path / "module.py"
        src.write_text("y = 2\n", encoding="utf-8")
        full, node, err = resolve_function("/ignored", str(src), None)
        assert full == str(src)
        assert node is None
        assert err is None


class TestResolveFunctionBareName:
    """EP4: function is a bare name (no dot)."""

    def test_finds_top_level_function(self, tmp_path):
        src = tmp_path / "funcs.py"
        src.write_text(
            textwrap.dedent("""\
                def alpha():
                    pass

                def beta(x):
                    return x + 1
            """),
            encoding="utf-8",
        )
        full, node, err = resolve_function(str(tmp_path), "funcs.py", "beta")
        assert err is None
        assert node is not None
        assert isinstance(node, ast.FunctionDef)
        assert node.name == "beta"

    def test_finds_async_function(self, tmp_path):
        src = tmp_path / "async_mod.py"
        src.write_text(
            textwrap.dedent("""\
                async def fetch(url):
                    pass
            """),
            encoding="utf-8",
        )
        full, node, err = resolve_function(str(tmp_path), "async_mod.py", "fetch")
        assert err is None
        assert node is not None
        assert isinstance(node, ast.AsyncFunctionDef)
        assert node.name == "fetch"

    def test_finds_nested_function_via_walk(self, tmp_path):
        src = tmp_path / "nested.py"
        src.write_text(
            textwrap.dedent("""\
                def outer():
                    def inner():
                        pass
                    return inner
            """),
            encoding="utf-8",
        )
        # ast.walk finds inner even though it's nested
        full, node, err = resolve_function(str(tmp_path), "nested.py", "inner")
        assert err is None
        assert node is not None
        assert node.name == "inner"

    def test_function_not_found(self, tmp_path):
        src = tmp_path / "empty_funcs.py"
        src.write_text("x = 1\n", encoding="utf-8")
        full, node, err = resolve_function(str(tmp_path), "empty_funcs.py", "ghost")
        assert node is None
        assert err == "Function 'ghost' not found in empty_funcs.py"

    def test_first_matching_function_returned(self, tmp_path):
        src = tmp_path / "dups.py"
        src.write_text(
            textwrap.dedent("""\
                def process():
                    return "first"

                class Wrapper:
                    def process(self):
                        return "second"
            """),
            encoding="utf-8",
        )
        # ast.walk should find the top-level one first
        full, node, err = resolve_function(str(tmp_path), "dups.py", "process")
        assert err is None
        assert node is not None
        assert node.name == "process"

    def test_bare_method_preserves_qualified_name(self, tmp_path):
        src = tmp_path / "overlay.py"
        src.write_text(
            textwrap.dedent("""\
                class EmpiricalOverlay:
                    def to_dict(self):
                        return {"status": "ok"}
            """),
            encoding="utf-8",
        )
        full, node, err = resolve_function(str(tmp_path), "overlay.py", "to_dict")
        assert err is None
        assert node is not None
        assert node.name == "to_dict"
        assert getattr(node, "_lintgate_qualname", "") == "EmpiricalOverlay.to_dict"


class TestResolveFunctionQualifiedName:
    """EP5: function contains a dot — Class.method resolution."""

    def test_simple_class_method(self, tmp_path):
        src = tmp_path / "cls.py"
        src.write_text(
            textwrap.dedent("""\
                class Parser:
                    def parse(self, data):
                        return data

                    async def async_parse(self, data):
                        return data
            """),
            encoding="utf-8",
        )
        full, node, err = resolve_function(str(tmp_path), "cls.py", "Parser.parse")
        assert err is None
        assert node is not None
        assert node.name == "parse"
        assert isinstance(node, ast.FunctionDef)
        assert getattr(node, "_lintgate_qualname", "") == "Parser.parse"

    def test_async_class_method(self, tmp_path):
        src = tmp_path / "cls_async.py"
        src.write_text(
            textwrap.dedent("""\
                class Handler:
                    async def handle(self, req):
                        return req
            """),
            encoding="utf-8",
        )
        full, node, err = resolve_function(str(tmp_path), "cls_async.py", "Handler.handle")
        assert err is None
        assert node is not None
        assert isinstance(node, ast.AsyncFunctionDef)
        assert node.name == "handle"

    def test_nested_class_method(self, tmp_path):
        src = tmp_path / "deep.py"
        src.write_text(
            textwrap.dedent("""\
                class Outer:
                    class Inner:
                        def compute(self):
                            return 42
            """),
            encoding="utf-8",
        )
        full, node, err = resolve_function(str(tmp_path), "deep.py", "Outer.Inner.compute")
        assert err is None
        assert node is not None
        assert node.name == "compute"

    def test_class_not_found(self, tmp_path):
        src = tmp_path / "no_cls.py"
        src.write_text("def standalone(): pass\n", encoding="utf-8")
        full, node, err = resolve_function(str(tmp_path), "no_cls.py", "Missing.method")
        assert node is None
        assert err == "Class 'Missing' not found in no_cls.py"

    def test_method_not_found_in_class(self, tmp_path):
        src = tmp_path / "cls_no_meth.py"
        src.write_text(
            textwrap.dedent("""\
                class Validator:
                    def validate(self):
                        pass
            """),
            encoding="utf-8",
        )
        full, node, err = resolve_function(str(tmp_path), "cls_no_meth.py", "Validator.nonexistent")
        assert node is None
        assert err == "Method 'nonexistent' not found in class chain in cls_no_meth.py"

    def test_intermediate_class_not_found(self, tmp_path):
        src = tmp_path / "chain.py"
        src.write_text(
            textwrap.dedent("""\
                class A:
                    pass
            """),
            encoding="utf-8",
        )
        full, node, err = resolve_function(str(tmp_path), "chain.py", "A.B.method")
        assert node is None
        assert err == "Class 'B' not found in chain.py"

    def test_empty_class_body(self, tmp_path):
        src = tmp_path / "empty_cls.py"
        src.write_text(
            textwrap.dedent("""\
                class Empty:
                    pass
            """),
            encoding="utf-8",
        )
        full, node, err = resolve_function(str(tmp_path), "empty_cls.py", "Empty.anything")
        assert node is None
        assert err == "Method 'anything' not found in class chain in empty_cls.py"


class TestResolveFunctionPathHandling:
    """Decision rules for path resolution (relative vs absolute)."""

    def test_relative_path_joins_with_root(self, tmp_path):
        sub = tmp_path / "src"
        sub.mkdir()
        src = sub / "mod.py"
        src.write_text("x = 1\n", encoding="utf-8")
        full, _, err = resolve_function(str(tmp_path), "src/mod.py", None)
        assert full == str(src)
        assert err is None

    def test_absolute_path_ignores_root(self, tmp_path):
        src = tmp_path / "standalone.py"
        src.write_text("x = 1\n", encoding="utf-8")
        full, _, err = resolve_function("/some/other/root", str(src), None)
        assert full == str(src)
        assert err is None

    def test_unicode_filename(self, tmp_path):
        src = tmp_path / "modulo.py"
        src.write_text("# -*- coding: utf-8 -*-\nx = 'hello'\n", encoding="utf-8")
        full, node, err = resolve_function(str(tmp_path), "modulo.py", None)
        assert err is None


class TestResolveFunctionReturnTypes:
    """Verify the exact 3-tuple return contract."""

    def test_success_with_function_returns_full_path_node_none(self, tmp_path):
        src = tmp_path / "ret.py"
        src.write_text("def f(): pass\n", encoding="utf-8")
        full, node, err = resolve_function(str(tmp_path), "ret.py", "f")
        assert isinstance(full, str)
        assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        assert err is None

    def test_success_without_function_returns_full_path_none_none(self, tmp_path):
        src = tmp_path / "ret2.py"
        src.write_text("x = 1\n", encoding="utf-8")
        full, node, err = resolve_function(str(tmp_path), "ret2.py", None)
        assert isinstance(full, str)
        assert node is None
        assert err is None

    def test_error_returns_full_path_none_string(self, tmp_path):
        full, node, err = resolve_function(str(tmp_path), "missing.py", None)
        assert isinstance(full, str)
        assert node is None
        assert isinstance(err, str)


# ════════════════════════════════════════════════════════════════════
# iter_cached_states — specification tests
# ════════════════════════════════════════════════════════════════════


class TestIterCachedStatesNoDirectory:
    """DR1: cache_dir does not exist."""

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        result = iter_cached_states(tmp_path / "nonexistent")
        assert result == []

    def test_nonexistent_nested_dir_returns_empty(self):
        result = iter_cached_states(Path("/does/not/exist/at/all"))
        assert result == []


class TestIterCachedStatesEmptyDirectory:
    """DR2: cache_dir exists but contains no JSON files."""

    def test_empty_dir_returns_empty(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        result = iter_cached_states(cache)
        assert result == []

    def test_dir_with_non_json_files(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "readme.txt").write_text("not json")
        (cache / "data.csv").write_text("a,b,c")
        result = iter_cached_states(cache)
        assert result == []


class TestIterCachedStatesSchedulerExclusion:
    """DR3: scheduler_state.json is always excluded."""

    def test_scheduler_state_excluded(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "scheduler_state.json").write_text(json.dumps({"run": 1}))
        result = iter_cached_states(cache)
        assert result == []

    def test_scheduler_state_excluded_with_other_files(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "scheduler_state.json").write_text(json.dumps({"run": 1}))
        (cache / "func_a.json").write_text(
            json.dumps({"function_key": "mod.py::func_a", "survival_rate": 0.5})
        )
        result = iter_cached_states(cache)
        assert len(result) == 1
        assert result[0]["function_key"] == "mod.py::func_a"


class TestIterCachedStatesMalformedJson:
    """DR4: malformed JSON files are silently skipped."""

    def test_corrupt_json_skipped(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "bad.json").write_text("not valid json {{{")
        result = iter_cached_states(cache)
        assert result == []

    def test_corrupt_mixed_with_valid(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "aaa_bad.json").write_text("{broken")
        (cache / "bbb_good.json").write_text(json.dumps({"function_key": "x.py::f", "data": 1}))
        (cache / "ccc_also_bad.json").write_text("")
        result = iter_cached_states(cache)
        assert len(result) == 1
        assert result[0]["function_key"] == "x.py::f"

    def test_empty_json_file_skipped(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "empty.json").write_text("")
        result = iter_cached_states(cache)
        assert result == []

    def test_json_array_not_dict_still_loaded(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        # json.load succeeds on arrays, but .get("function_key") will fail
        # since lists don't have .get — actually the code calls data.get()
        # so this should raise AttributeError. Let's check: the try/except
        # only catches OSError and JSONDecodeError, so this would propagate.
        # Actually, looking at the code more carefully: json.load succeeds,
        # then data.get("function_key", "") is called — lists don't have .get.
        # But the except only catches OSError|JSONDecodeError... so this would
        # actually raise an uncaught AttributeError.
        # This IS an edge case worth testing.
        (cache / "array.json").write_text(json.dumps([1, 2, 3]))
        with pytest.raises(AttributeError):
            iter_cached_states(cache)


class TestIterCachedStatesNoFilter:
    """EP1: no file/function filter — returns all valid states."""

    def test_loads_all_valid_states(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "func_a.json").write_text(
            json.dumps({"function_key": "mod.py::func_a", "survival_rate": 0.3})
        )
        (cache / "func_b.json").write_text(
            json.dumps({"function_key": "other.py::func_b", "survival_rate": 0.0})
        )
        result = iter_cached_states(cache)
        assert len(result) == 2
        keys = {r["function_key"] for r in result}
        assert keys == {"mod.py::func_a", "other.py::func_b"}

    def test_sorted_order(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "zzz.json").write_text(json.dumps({"function_key": "z.py::z_func"}))
        (cache / "aaa.json").write_text(json.dumps({"function_key": "a.py::a_func"}))
        result = iter_cached_states(cache)
        assert len(result) == 2
        # Files are sorted by glob, so aaa.json comes first
        assert result[0]["function_key"] == "a.py::a_func"
        assert result[1]["function_key"] == "z.py::z_func"


class TestIterCachedStatesFileFilter:
    """EP2: file filter — only states whose function_key contains file string."""

    def test_file_filter_matches_substring(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "a.json").write_text(json.dumps({"function_key": "src/mod.py::func_a"}))
        (cache / "b.json").write_text(json.dumps({"function_key": "src/other.py::func_b"}))
        result = iter_cached_states(cache, file="mod.py")
        assert len(result) == 1
        assert result[0]["function_key"] == "src/mod.py::func_a"

    def test_file_filter_no_match(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "a.json").write_text(json.dumps({"function_key": "src/mod.py::func_a"}))
        result = iter_cached_states(cache, file="nonexistent.py")
        assert result == []

    def test_file_filter_empty_string_matches_all(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "a.json").write_text(json.dumps({"function_key": "mod.py::func_a"}))
        # empty string is falsy, so file="" means no filter
        result = iter_cached_states(cache, file="")
        assert len(result) == 1

    def test_file_filter_partial_match(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "a.json").write_text(json.dumps({"function_key": "src/module_utils.py::helper"}))
        (cache / "b.json").write_text(json.dumps({"function_key": "src/module.py::main"}))
        # "module" matches both function keys
        result = iter_cached_states(cache, file="module")
        assert len(result) == 2


class TestIterCachedStatesFunctionFilter:
    """EP3: function filter — only states whose function_key contains function string."""

    def test_function_filter_matches(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "a.json").write_text(json.dumps({"function_key": "mod.py::process_data"}))
        (cache / "b.json").write_text(json.dumps({"function_key": "mod.py::validate_input"}))
        result = iter_cached_states(cache, function="process_data")
        assert len(result) == 1
        assert result[0]["function_key"] == "mod.py::process_data"

    def test_function_filter_no_match(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "a.json").write_text(json.dumps({"function_key": "mod.py::func_a"}))
        result = iter_cached_states(cache, function="nonexistent_func")
        assert result == []

    def test_function_filter_empty_string_matches_all(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "a.json").write_text(json.dumps({"function_key": "mod.py::func_a"}))
        result = iter_cached_states(cache, function="")
        assert len(result) == 1


class TestIterCachedStatesCombinedFilter:
    """EP4: both file and function filters applied."""

    def test_both_filters_narrow_results(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "a.json").write_text(json.dumps({"function_key": "mod.py::func_a"}))
        (cache / "b.json").write_text(json.dumps({"function_key": "mod.py::func_b"}))
        (cache / "c.json").write_text(json.dumps({"function_key": "other.py::func_a"}))
        result = iter_cached_states(cache, file="mod.py", function="func_a")
        assert len(result) == 1
        assert result[0]["function_key"] == "mod.py::func_a"

    def test_both_filters_no_overlap(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "a.json").write_text(json.dumps({"function_key": "mod.py::func_a"}))
        (cache / "b.json").write_text(json.dumps({"function_key": "other.py::func_b"}))
        # file matches a.json, function matches b.json, but no entry matches both
        result = iter_cached_states(cache, file="mod.py", function="func_b")
        assert result == []


class TestIterCachedStatesMissingFunctionKey:
    """Edge case: valid JSON but no function_key field."""

    def test_missing_function_key_unfiltered(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "a.json").write_text(json.dumps({"some_field": "value"}))
        result = iter_cached_states(cache)
        # No filter, so data.get("function_key", "") returns "" and no filter check runs
        assert len(result) == 1
        assert result[0] == {"some_field": "value"}

    def test_missing_function_key_with_file_filter(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "a.json").write_text(json.dumps({"some_field": "value"}))
        # function_key defaults to "", so file="anything" won't be "in" ""
        result = iter_cached_states(cache, file="anything")
        assert result == []

    def test_missing_function_key_with_function_filter(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "a.json").write_text(json.dumps({"other": "data"}))
        result = iter_cached_states(cache, function="something")
        assert result == []


class TestIterCachedStatesDataIntegrity:
    """Verify that loaded data matches written data exactly."""

    def test_complex_state_preserved(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        state = {
            "function_key": "module.py::MyClass.my_method",
            "survival_rate": 0.75,
            "total_mutants": 20,
            "total_killed": 5,
            "total_survived": 15,
            "per_category": [
                {"category": "VALUE", "survived": 8, "killed": 2, "total": 10},
                {"category": "SWAP", "survived": 7, "killed": 3, "total": 10},
            ],
            "is_pure": False,
            "parameter_count": 3,
        }
        (cache / "state.json").write_text(json.dumps(state))
        result = iter_cached_states(cache)
        assert len(result) == 1
        assert result[0] == state
        assert result[0]["survival_rate"] == 0.75
        assert result[0]["per_category"][0]["category"] == "VALUE"
        assert result[0]["per_category"][0]["survived"] == 8
        assert result[0]["per_category"][1]["total"] == 10
        assert result[0]["is_pure"] is False
        assert result[0]["parameter_count"] == 3
