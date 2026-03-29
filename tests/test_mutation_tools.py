"""Tests for mcp_tools/mutation_tools.py and mcp_tools/_mutation_impl.py."""

from __future__ import annotations

import json
import textwrap

from mcp_tools._mutation_impl import (
    generate_test_skeleton,
    prescription_for_category,
    resolve_function,
    walk_functions,
)


def _load_tool_result(json_str):
    import json as _j
    import os as _os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and _os.path.isfile(r.get("file","")):
        with open(r["file"]) as f: return _j.loads(f.read())
    return r


# ── generate_test_skeleton ──────────────────────────────────────────


class TestGenerateTestSkeleton:
    def test_bare_function_value(self):
        result = generate_test_skeleton("path.py::add", "VALUE")
        assert result["function"] == "path.py::add"
        assert result["category"] == "VALUE"
        assert "test_add_value_mutation" in result["test_name"]
        assert "result = add(" in result["skeleton"]
        assert "EXPECTED_VALUE" in result["skeleton"]

    def test_bare_function_swap(self):
        result = generate_test_skeleton("utils.py::merge", "SWAP")
        assert "merge(a, b) != merge(b, a)" in result["skeleton"]

    def test_bare_function_boundary(self):
        result = generate_test_skeleton("core.py::clamp", "BOUNDARY")
        assert "boundary - 1" in result["skeleton"]
        assert "boundary + 1" in result["skeleton"]

    def test_method_value(self):
        result = generate_test_skeleton("path.py::Parser.parse", "VALUE")
        assert "obj = Parser()" in result["skeleton"]
        assert "obj.parse(" in result["skeleton"]
        # Must NOT contain "Parser.parse(" as a direct call
        assert "Parser.parse(" not in result["skeleton"]

    def test_method_state(self):
        result = generate_test_skeleton("path.py::Cache.put", "STATE")
        assert "obj = Cache()" in result["skeleton"]
        assert "obj.put(" in result["skeleton"]
        # Must NOT contain double-qualified "obj.Cache.put"
        assert "obj.Cache.put" not in result["skeleton"]

    def test_method_swap(self):
        result = generate_test_skeleton("mod.py::Matrix.multiply", "SWAP")
        assert "obj = Matrix()" in result["skeleton"]
        assert "obj.multiply(a, b)" in result["skeleton"]

    def test_method_boundary(self):
        result = generate_test_skeleton("mod.py::Range.check", "BOUNDARY")
        assert "obj = Range()" in result["skeleton"]
        assert "obj.check(boundary" in result["skeleton"]

    def test_method_type(self):
        result = generate_test_skeleton("mod.py::Validator.validate", "TYPE")
        assert "obj = Validator()" in result["skeleton"]
        assert "obj.validate(valid_type)" in result["skeleton"]

    def test_unknown_category_fallback(self):
        result = generate_test_skeleton("x.py::func", "UNKNOWN")
        assert "pass" in result["skeleton"]

    def test_no_module_prefix(self):
        result = generate_test_skeleton("compute", "VALUE")
        assert "result = compute(" in result["skeleton"]


# ── prescription_for_category ───────────────────────────────────────


class TestPrescriptionForCategory:
    def test_known_categories(self):
        for cat in ("VALUE", "SWAP", "BOUNDARY", "STATE", "TYPE"):
            rx = prescription_for_category(cat)
            assert isinstance(rx, str)
            assert len(rx) > 10

    def test_unknown_category(self):
        rx = prescription_for_category("CUSTOM")
        assert "CUSTOM" in rx


# ── resolve_function ────────────────────────────────────────────────


class TestResolveFunction:
    def test_file_not_found(self, tmp_path):
        full, node, err = resolve_function(str(tmp_path), "missing.py", None)
        assert err is not None
        assert "not found" in err.lower()

    def test_function_not_found(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("def foo(): pass\n")
        full, node, err = resolve_function(str(tmp_path), "mod.py", "bar")
        assert err is not None
        assert "bar" in err

    def test_function_found(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("def foo(): pass\n")
        full, node, err = resolve_function(str(tmp_path), "mod.py", "foo")
        assert err is None
        assert node is not None
        assert node.name == "foo"

    def test_no_function_specified(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("x = 1\n")
        full, node, err = resolve_function(str(tmp_path), "mod.py", None)
        assert err is None
        assert node is None


# ── walk_functions ──────────────────────────────────────────────────


class TestWalkFunctions:
    def test_top_level_and_methods(self, tmp_path):
        src = textwrap.dedent("""\
            def top():
                pass

            class Foo:
                def method(self):
                    pass
        """)
        f = tmp_path / "mod.py"
        f.write_text(src)
        import ast

        tree = ast.parse(src)
        results = walk_functions(tree)
        names = [name for name, _ in results]
        assert "top" in names
        assert "Foo.method" in names


# ── _impl integration (next_actions wiring) ─────────────────────────


class _FakeMCP:
    def tool(self):
        def _decorator(fn):
            return fn

        return _decorator


def _stub_helpers(**overrides):
    defaults = {
        "_validate_project_root": lambda p, **kw: p or "/tmp/test",
        "_json_dumps": lambda obj, **kw: json.dumps(obj),
    }
    defaults.update(overrides)
    return defaults


class TestImplGetState:
    def test_empty_cache(self, tmp_path):
        from mcp_tools.mutation_tools import _impl_get_state

        helpers = _stub_helpers()
        helpers["_validate_project_root"] = lambda p, **kw: str(tmp_path)
        result = _load_tool_result(_impl_get_state(helpers, str(tmp_path), None, None))
        assert "No mutation data" in result.get("note", "")
        assert "next_actions" in result


class TestImplPrescribe:
    def test_empty_cache(self, tmp_path):
        from mcp_tools.mutation_tools import _impl_prescribe

        helpers = _stub_helpers()
        helpers["_validate_project_root"] = lambda p, **kw: str(tmp_path)
        result = _load_tool_result(_impl_prescribe(helpers, str(tmp_path), None, None))
        assert "No mutation data" in result.get("note", "")

    def test_with_cached_data(self, tmp_path):
        from mcp_tools._mutation_impl import get_cache_dir, save_cached_state
        from mcp_tools.mutation_tools import _impl_prescribe

        cache_dir = get_cache_dir(str(tmp_path))
        save_cached_state(
            cache_dir,
            "test.py::func",
            {
                "function_key": "test.py::func",
                "per_category": [
                    {"category": "VALUE", "survived": 2, "survival_rate": 0.5},
                    {"category": "SWAP", "survived": 0, "survival_rate": 0.0},
                ],
            },
        )
        helpers = _stub_helpers()
        helpers["_validate_project_root"] = lambda p, **kw: str(tmp_path)
        result = _load_tool_result(_impl_prescribe(helpers, str(tmp_path), None, None))
        assert result["total_prescriptions"] == 1
        assert result["prescriptions"][0]["category"] == "VALUE"
        assert "next_actions" in result
        tools = [a["tool"] for a in result["next_actions"]]
        assert "mutation_prescribe_tests" in tools


class TestImplPrescribeTests:
    def test_with_cached_data(self, tmp_path):
        from mcp_tools._mutation_impl import get_cache_dir, save_cached_state
        from mcp_tools.mutation_tools import _impl_prescribe_tests

        cache_dir = get_cache_dir(str(tmp_path))
        save_cached_state(
            cache_dir,
            "test.py::func",
            {
                "function_key": "test.py::func",
                "per_category": [
                    {"category": "BOUNDARY", "survived": 1, "survival_rate": 0.3},
                ],
            },
        )
        helpers = _stub_helpers()
        helpers["_validate_project_root"] = lambda p, **kw: str(tmp_path)
        result = _load_tool_result(_impl_prescribe_tests(helpers, str(tmp_path), "", None))
        assert len(result["skeletons"]) == 1
        assert result["skeletons"][0]["category"] == "BOUNDARY"
        assert "next_actions" in result
        tools = [a["tool"] for a in result["next_actions"]]
        assert "mutation_validate_tests" in tools


class TestImplClearState:
    def test_empty_cache(self, tmp_path):
        from mcp_tools.mutation_tools import _impl_clear_state

        helpers = _stub_helpers()
        helpers["_validate_project_root"] = lambda p, **kw: str(tmp_path)
        result = _load_tool_result(_impl_clear_state(helpers, str(tmp_path), None))
        assert "No mutation state" in result.get("note", "")

    def test_clears_files(self, tmp_path):
        from mcp_tools._mutation_impl import get_cache_dir, save_cached_state
        from mcp_tools.mutation_tools import _impl_clear_state

        cache_dir = get_cache_dir(str(tmp_path))
        save_cached_state(cache_dir, "a.py::f", {"function_key": "a.py::f"})
        save_cached_state(cache_dir, "b.py::g", {"function_key": "b.py::g"})

        helpers = _stub_helpers()
        helpers["_validate_project_root"] = lambda p, **kw: str(tmp_path)
        result = _load_tool_result(_impl_clear_state(helpers, str(tmp_path), None))
        assert result["cleared"] == 2
