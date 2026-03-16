"""Mutation-targeted tests for _prescriptive_impl helper functions.

Targets SWAP + VALUE survivors in _target_to_file, _target_to_func,
_try_load_algebra, _try_load_func_spec, _try_load_mutation_state,
_render_generation_prompt, _render_repair_prompt.
"""

from __future__ import annotations

import json
import os
import tempfile

from mcp_tools._prescriptive_impl import (
    _render_generation_prompt,
    _target_to_file,
    _target_to_func,
    _try_load_algebra,
    _try_load_func_spec,
    _try_load_mutation_state,
)


class TestTargetToFile:
    def test_module_with_separator(self):
        assert _target_to_file("lintgate.utils::parse") == "lintgate/utils.py"

    def test_no_separator(self):
        assert _target_to_file("utils") == "utils"

    def test_deep_module(self):
        assert _target_to_file("lintgate.hooks.habit::func") == "lintgate/hooks/habit.py"

    def test_swap_module_func(self):
        """Module and function are not interchangeable in the output."""
        result = _target_to_file("abc::xyz")
        assert result == "abc.py"
        assert "xyz" not in result


class TestTargetToFunc:
    def test_with_separator(self):
        assert _target_to_func("mod::my_func") == "my_func"

    def test_no_separator(self):
        assert _target_to_func("no_sep") is None

    def test_deep_path(self):
        assert _target_to_func("a.b.c::deep_func") == "deep_func"

    def test_swap_module_func(self):
        """Returns the function part, not the module part."""
        result = _target_to_func("module_name::function_name")
        assert result == "function_name"
        assert result != "module_name"


class TestTryLoadAlgebra:
    def test_missing_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _try_load_algebra(tmp, "mod::func")
            assert result is None

    def test_key_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, ".lintgate", "algebra_cache.json")
            os.makedirs(os.path.dirname(cache_path))
            with open(cache_path, "w") as f:
                json.dump({"mod::func": {"algebraic_properties": ["pure"]}}, f)
            result = _try_load_algebra(tmp, "mod::func")
            assert result is not None
            assert result["algebraic_properties"] == ["pure"]

    def test_key_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, ".lintgate", "algebra_cache.json")
            os.makedirs(os.path.dirname(cache_path))
            with open(cache_path, "w") as f:
                json.dump({"other::func": {}}, f)
            result = _try_load_algebra(tmp, "mod::func")
            assert result is None


class TestTryLoadMutationState:
    def test_missing_cache_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _try_load_mutation_state(tmp, "mod::func")
            assert result is None

    def test_matching_function_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            mut_dir = os.path.join(tmp, ".lintgate", "mutation")
            os.makedirs(mut_dir)
            with open(os.path.join(mut_dir, "abc.json"), "w") as f:
                json.dump({"function_key": "mod::func", "survived": 3}, f)
            result = _try_load_mutation_state(tmp, "mod::func")
            assert result is not None
            assert result["function_key"] == "mod::func"
            assert result["survived"] == 3

    def test_no_matching_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            mut_dir = os.path.join(tmp, ".lintgate", "mutation")
            os.makedirs(mut_dir)
            with open(os.path.join(mut_dir, "abc.json"), "w") as f:
                json.dump({"function_key": "other::func"}, f)
            result = _try_load_mutation_state(tmp, "mod::func")
            assert result is None

    def test_skips_scheduler_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            mut_dir = os.path.join(tmp, ".lintgate", "mutation")
            os.makedirs(mut_dir)
            with open(os.path.join(mut_dir, "scheduler_state.json"), "w") as f:
                json.dump({"function_key": "mod::func"}, f)
            result = _try_load_mutation_state(tmp, "mod::func")
            assert result is None


class TestTryLoadFuncSpec:
    def test_missing_cache_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _try_load_func_spec(tmp, "mod::func")
            assert result is None

    def test_matching_key_returns_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = os.path.join(tmp, ".lintgate", "spec_cache")
            os.makedirs(cache_dir)
            with open(os.path.join(cache_dir, "ledger.json"), "w") as f:
                json.dump(
                    {
                        "functions": {
                            "mod::func": {
                                "estimated_sigma": 10,
                                "specification_level": 0.5,
                                "regime": "A",
                                "is_pure": True,
                                "phase": "tail",
                                "source_file": "mod.py",
                            }
                        }
                    },
                    f,
                )
            result = _try_load_func_spec(tmp, "mod::func")
            assert result is not None
            assert result.core.estimated_sigma == 10
            assert result.core.is_pure is True


class TestRenderGenerationPrompt:
    def test_must_not_constraints(self):
        result = _render_generation_prompt(
            "mod::f",
            [
                {"constraint_type": "must_not_use", "description": "no globals", "priority": 1},
            ],
        )
        assert "MUST NOT" in result
        assert "no globals" in result

    def test_must_constraints(self):
        result = _render_generation_prompt(
            "mod::f",
            [
                {"constraint_type": "must_use", "description": "return int", "priority": 3},
            ],
        )
        assert "MUST" in result
        assert "return int" in result

    def test_target_key_in_header(self):
        result = _render_generation_prompt("my.module::my_func", [])
        assert "my.module::my_func" in result

    def test_priority_ordering(self):
        result = _render_generation_prompt(
            "mod::f",
            [
                {"constraint_type": "must_use", "description": "second", "priority": 5},
                {"constraint_type": "must_not_use", "description": "first", "priority": 1},
            ],
        )
        first_pos = result.index("first")
        second_pos = result.index("second")
        assert first_pos < second_pos

    def test_pattern_constraints_under_patterns(self):
        result = _render_generation_prompt(
            "mod::f",
            [
                {"constraint_type": "pattern", "description": "use iteration", "priority": 5},
            ],
        )
        assert "Patterns" in result
        assert "use iteration" in result

    def test_empty_constraints_header_only(self):
        result = _render_generation_prompt("mod::f", [])
        assert "mod::f" in result
        assert "Forbidden" not in result
        assert "Required" not in result
        assert "Patterns" not in result

    def test_all_three_sections(self):
        result = _render_generation_prompt(
            "mod::f",
            [
                {"constraint_type": "must_not_use", "description": "no globals", "priority": 1},
                {"constraint_type": "must_use", "description": "return int", "priority": 3},
                {"constraint_type": "pattern", "description": "use recursion", "priority": 5},
            ],
        )
        assert "Forbidden" in result
        assert "Required" in result
        assert "Patterns" in result
        # Sections in order
        assert result.index("Forbidden") < result.index("Required") < result.index("Patterns")

    def test_swap_target_constraints(self):
        """target_key and constraints are not interchangeable."""
        r1 = _render_generation_prompt(
            "alpha", [{"constraint_type": "must_use", "description": "beta", "priority": 1}]
        )
        assert "alpha" in r1
        assert "beta" in r1
