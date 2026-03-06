"""Phase 4 tests: Monty Hall post-generation filter at function granularity.

Tests cover:
1. extract_func_name_from_mutant_id() — parsing various mutant ID formats
2. _filter_mutants_by_category() — per-function filtering vs file-level fallback
3. run_inline_sampling() — builds per_function_categories from AST
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lintgate.mutation.engine import MutationEngine
from lintgate.mutation.mutant_parsing import extract_func_name_from_mutant_id
from lintgate.mutation.policy import RuntimeBudget

# ── extract_func_name_from_mutant_id ────────────────────────────────


class TestExtractFuncNameFromMutantId:
    """Extract simple function name from mutmut mutant IDs."""

    def test_module_level_function(self):
        result = extract_func_name_from_mutant_id("mod.sub.x_compute__mutmut_3")
        assert result == "compute"

    def test_class_method(self):
        result = extract_func_name_from_mutant_id(
            "mod.sub.x\u01c1MyClass\u01c1run__mutmut_1"
        )
        assert result == "run"

    def test_private_function(self):
        result = extract_func_name_from_mutant_id("mod.sub.x__private__mutmut_2")
        assert result == "_private"

    def test_dunder_method(self):
        result = extract_func_name_from_mutant_id("mod.x___init____mutmut_1")
        assert result == "__init__"

    def test_deeply_nested_module(self):
        result = extract_func_name_from_mutant_id(
            "a.b.c.d.e.x_helper__mutmut_42"
        )
        assert result == "helper"

    def test_no_mutmut_suffix_returns_none(self):
        assert extract_func_name_from_mutant_id("garbage") is None

    def test_empty_string_returns_none(self):
        assert extract_func_name_from_mutant_id("") is None

    def test_class_with_underscore_prefix(self):
        result = extract_func_name_from_mutant_id(
            "mod.x\u01c1_PrivateClass\u01c1visit_Call__mutmut_5"
        )
        assert result == "visit_Call"

    def test_mutmut_index_1(self):
        """First mutant in a function (index 1)."""
        result = extract_func_name_from_mutant_id("pkg.mod.x_foo__mutmut_1")
        assert result == "foo"

    def test_func_name_with_numbers(self):
        result = extract_func_name_from_mutant_id("mod.x_step2_validate__mutmut_3")
        assert result == "step2_validate"


# ── _filter_mutants_by_category: per-function filtering ──────────────


class TestFilterMutantsByCategory:
    """Per-function category filtering (Phase 4).

    _filter_mutants_by_category reads source from disk and checks for libcst.
    We patch both to isolate the filtering logic.
    """

    def setup_method(self):
        budget = RuntimeBudget(enabled=True)
        sm = MagicMock()
        self.engine = MutationEngine(state_manager=sm, budget=budget)

    def _run_filter(self, cat_map, per_func, relevant_categories, telemetry=None):
        """Helper: run _filter_mutants_by_category with necessary patches."""
        import lintgate.mutation.engine as eng_mod

        # Ensure `cst` is truthy so the early return is skipped
        with (
            patch.object(eng_mod, "cst", True),
            patch("pathlib.Path.read_text", return_value="# dummy source"),
            patch.object(self.engine, "_build_mutant_category_map", return_value=cat_map),
        ):
            return self.engine._filter_mutants_by_category(
                paths=["mod.py"],
                relevant_categories=relevant_categories,
                telemetry=telemetry,
                per_function_categories=per_func,
            )

    def test_per_function_filtering_includes_matching(self):
        """Mutant in function A with matching category → included."""
        cat_map = {
            "mod.x_add__mutmut_1": "arithmetic",
            "mod.x_add__mutmut_2": "conditional",
            "mod.x_check__mutmut_1": "conditional",
        }
        per_func = {
            "add": {"arithmetic"},      # add only cares about arithmetic
            "check": {"conditional"},    # check only cares about conditional
        }

        mutants, active = self._run_filter(
            cat_map, per_func, {"arithmetic", "conditional"},
        )

        assert active is True
        # add__mutmut_1 (arithmetic ∈ add's set) → included
        # add__mutmut_2 (conditional ∉ add's set) → excluded
        # check__mutmut_1 (conditional ∈ check's set) → included
        assert "mod.x_add__mutmut_1" in mutants
        assert "mod.x_add__mutmut_2" not in mutants
        assert "mod.x_check__mutmut_1" in mutants
        assert len(mutants) == 2

    def test_per_function_filtering_excludes_irrelevant(self):
        """Mutant whose category doesn't match its function → excluded."""
        cat_map = {
            "mod.x_add__mutmut_1": "string",  # string mutant in arithmetic function
        }
        per_func = {"add": {"arithmetic"}}

        mutants, active = self._run_filter(
            cat_map, per_func, {"arithmetic", "string"},
        )

        assert "mod.x_add__mutmut_1" not in mutants

    def test_unknown_function_falls_back_to_file_level(self):
        """Mutant in unknown function falls back to file-level categories."""
        cat_map = {
            "mod.x_mystery__mutmut_1": "arithmetic",
        }
        per_func = {"add": {"conditional"}}  # "mystery" not in per_func

        mutants, active = self._run_filter(
            cat_map, per_func, {"arithmetic"},  # file-level includes arithmetic
        )

        # Should fall through to file-level check and be included
        assert "mod.x_mystery__mutmut_1" in mutants

    def test_unparseable_mutant_id_falls_back_to_file_level(self):
        """Mutant with unparseable ID falls back to file-level filtering."""
        cat_map = {
            "weird_id_no_pattern": "arithmetic",
        }
        per_func = {"add": {"conditional"}}

        mutants, active = self._run_filter(
            cat_map, per_func, {"arithmetic"},
        )

        # func_name is None → falls through to file-level check
        assert "weird_id_no_pattern" in mutants

    def test_none_per_func_uses_file_level_only(self):
        """When per_function_categories is None, use file-level filtering."""
        cat_map = {
            "mod.x_add__mutmut_1": "arithmetic",
            "mod.x_add__mutmut_2": "string",
        }

        mutants, active = self._run_filter(
            cat_map, None, {"arithmetic"},
        )

        assert "mod.x_add__mutmut_1" in mutants
        assert "mod.x_add__mutmut_2" not in mutants

    def test_telemetry_counts_skipped_per_function(self):
        """Excluded mutants increment telemetry.mutants_skipped_policy."""
        cat_map = {
            "mod.x_add__mutmut_1": "string",  # not in add's categories
        }
        per_func = {"add": {"arithmetic"}}
        telemetry = MagicMock()
        telemetry.mutants_skipped_policy = 0

        self._run_filter(
            cat_map, per_func, {"arithmetic", "string"}, telemetry=telemetry,
        )

        assert telemetry.mutants_skipped_policy == 1

    def test_two_functions_different_categories(self):
        """Two functions A and B with disjoint categories are filtered independently."""
        cat_map = {
            "mod.x_compute__mutmut_1": "arithmetic",
            "mod.x_compute__mutmut_2": "conditional",
            "mod.x_validate__mutmut_1": "conditional",
            "mod.x_validate__mutmut_2": "arithmetic",
        }
        per_func = {
            "compute": {"arithmetic"},    # only arithmetic for compute
            "validate": {"conditional"},  # only conditional for validate
        }

        mutants, active = self._run_filter(
            cat_map, per_func, {"arithmetic", "conditional"},
        )

        assert "mod.x_compute__mutmut_1" in mutants      # arithmetic ∈ compute
        assert "mod.x_compute__mutmut_2" not in mutants   # conditional ∉ compute
        assert "mod.x_validate__mutmut_1" in mutants      # conditional ∈ validate
        assert "mod.x_validate__mutmut_2" not in mutants  # arithmetic ∉ validate
        assert len(mutants) == 2


# ── run_inline_sampling: per-function categories built from AST ──────


class TestRunInlineSamplingPerFunctionCategories:
    """run_inline_sampling builds per_function_categories from file AST."""

    def setup_method(self):
        budget = RuntimeBudget(enabled=True, max_inline_ms_per_function=30000)
        sm = MagicMock()
        sm.state = {}
        self.engine = MutationEngine(state_manager=sm, budget=budget)

    def test_per_function_categories_passed_to_execute(self, tmp_path):
        """run_inline_sampling passes per_function_categories to _execute_mutmut."""
        source = "def add(a, b): return a + b\ndef check(x): return x > 0\n"
        test_file = tmp_path / "example.py"
        test_file.write_text(source)

        captured_kwargs = {}

        def mock_execute(*, paths, depth, test_filter, relevant_categories,
                         per_function_categories, telemetry):
            captured_kwargs["per_function_categories"] = per_function_categories
            captured_kwargs["relevant_categories"] = relevant_categories
            return True

        telemetry = MagicMock()
        telemetry.inline_time_ms_spent = 0

        with (
            patch.object(self.engine, "_execute_mutmut", side_effect=mock_execute),
            patch.object(self.engine, "_build_function_states", return_value=[]),
            patch.object(self.engine, "_parse_mutmut_results", return_value={}),
        ):
            self.engine.run_inline_sampling(
                target_files=[str(test_file)],
                telemetry=telemetry,
            )

        pfc = captured_kwargs.get("per_function_categories")
        assert pfc is not None
        assert "add" in pfc
        assert "check" in pfc
        # Both should have category sets (computed by _compute_relevant_categories)
        assert isinstance(pfc["add"], set)
        assert isinstance(pfc["check"], set)
