"""Tests for specification helper functions."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from mcp_tools._specification_helpers import (
    _format_functions,
    filter_by_function,
    load_mutation_cache,
    resolve_py_files,
    validate_file_in_project,
)


class TestValidateFileInProject:
    def test_path_traversal_blocked(self, tmp_path):
        with pytest.raises(ValueError, match="escapes project root"):
            validate_file_in_project(str(tmp_path), "../../etc/passwd")

    def test_valid_absolute(self, tmp_path):
        f = tmp_path / "mod.py"
        f.touch()
        result = validate_file_in_project(str(tmp_path), str(f))
        assert result == str(f)

    def test_valid_relative(self, tmp_path):
        f = tmp_path / "mod.py"
        f.touch()
        result = validate_file_in_project(str(tmp_path), "mod.py")
        assert os.path.basename(result) == "mod.py"


class TestResolvePyFiles:
    def test_single_file_returns_list(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("x = 1")
        result = resolve_py_files(str(tmp_path), "mod.py")
        assert isinstance(result, list)
        assert len(result) == 1

    def test_not_found_returns_error(self, tmp_path):
        result = resolve_py_files(str(tmp_path), "nonexistent.py")
        assert isinstance(result, dict)
        assert "error" in result


class TestLoadMutationCache:
    def test_no_dir_returns_none(self, tmp_path):
        result = load_mutation_cache(str(tmp_path))
        assert result is None

    def test_loads_json_files(self, tmp_path):
        cache_dir = tmp_path / ".lintgate" / "mutation"
        cache_dir.mkdir(parents=True)
        import json

        (cache_dir / "func1.json").write_text(
            json.dumps({"function_key": "mod.py::func1", "survival_rate": 0.5})
        )
        result = load_mutation_cache(str(tmp_path))
        assert result is not None
        assert "mod.py::func1" in result

    def test_skips_scheduler_state(self, tmp_path):
        cache_dir = tmp_path / ".lintgate" / "mutation"
        cache_dir.mkdir(parents=True)
        import json

        (cache_dir / "scheduler_state.json").write_text(json.dumps({"queue": []}))
        result = load_mutation_cache(str(tmp_path))
        assert result is None


class TestFilterByFunction:
    def test_none_returns_all(self):
        ledger = MagicMock()
        ledger.functions = {"a": 1, "b": 2}
        result = filter_by_function(ledger, None)
        assert len(result) == 2

    def test_case_insensitive(self):
        ledger = MagicMock()
        ledger.functions = {"MyFunc": 1, "other": 2}
        result = filter_by_function(ledger, "myfunc")
        assert "MyFunc" in result
        assert "other" not in result


def _make_func_spec(spec_level=0.5, sigma=10, testability_score=0.8):
    """Create a minimal mock FunctionSpec for _format_functions."""
    fs = MagicMock()
    fs.core.estimated_sigma = sigma
    fs.core.sigma_confidence = 0.9
    fs.core.regime = "A"
    fs.core.specification_level = spec_level
    fs.core.phase = "bulk"
    fs.core.is_pure = False
    fs.risk.risk_score = 0.3
    fs.risk.priority_band = "P1"
    fs.testability.testability_score = testability_score
    fs.design_signals.boundary_points = 2
    fs.design_signals.equivalence_partitions = 1
    fs.design_signals.decision_rule_count = 3
    fs.design_signals.predicate_effect_links = 1
    fs.optimization_hints = []
    fs.stop_criteria_met = False
    return fs


class TestTestMappedSemantics:
    """test_mapped must reflect empirical test linkage (mutation cache),
    NOT design-for-testability (testability_score)."""

    def test_test_mapped_false_without_cache(self):
        """High testability_score but no mutation_cache → test_mapped=False."""
        fs = _make_func_spec(testability_score=0.9)
        matching = {"mod.py::func": fs}
        result = _format_functions(matching, mutation_cache=None)
        assert result["mod.py::func"]["test_mapped"] is False

    def test_test_mapped_true_with_cache_entry(self):
        """Function with matching mutation cache entry → test_mapped=True."""
        fs = _make_func_spec(testability_score=0.0)
        matching = {"mod.py::func": fs}
        cache = {"mod.py::func": {"survival_rate": 0.4}}
        result = _format_functions(matching, mutation_cache=cache)
        assert result["mod.py::func"]["test_mapped"] is True

    def test_test_mapped_false_with_cache_no_match(self):
        """Mutation cache exists but has no entry for this function → test_mapped=False."""
        fs = _make_func_spec(testability_score=0.8)
        matching = {"mod.py::func": fs}
        cache = {"other.py::other_func": {"survival_rate": 0.2}}
        result = _format_functions(matching, mutation_cache=cache)
        assert result["mod.py::func"]["test_mapped"] is False

    def test_test_mapped_empty_cache(self):
        """Empty mutation cache dict → test_mapped=False."""
        fs = _make_func_spec(testability_score=0.8)
        matching = {"mod.py::func": fs}
        result = _format_functions(matching, mutation_cache={})
        assert result["mod.py::func"]["test_mapped"] is False
