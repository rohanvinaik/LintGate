"""Tests for the dedicated impl_validate_tests implementation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from mcp_tools._mutation_tools_impl import impl_validate_tests


def _load_tool_result(json_str):
    import json as _j
    import os as _os

    r = _j.loads(json_str)
    if (
        isinstance(r, dict)
        and "file" in r
        and "analysis_id" in r
        and _os.path.isfile(r.get("file", ""))
    ):
        with open(r["file"]) as f:
            return _j.loads(f.read())
    return r


def _make_helpers(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "_validate_project_root": lambda p: "/test/project",
        "_json_dumps": lambda d, **kw: json.dumps(d),
        "_json_loads": lambda s: json.loads(s),
    }
    base.update(overrides)
    return base


# ── Output shape ──────────────────────────────────────────────────


@patch("mcp_tools._mutation_tools_impl.build_test_impact_map")
@patch("mcp_tools._mutation_tools_impl.discover_test_files", return_value=["t.py"])
@patch("mcp_tools._mutation_tools_impl._profile_targets")
@patch("mcp_tools._mutation_tools_impl._resolve_refactor_targets")
@patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=Path("/tmp/c"))
@patch("mcp_tools._mutation_tools_impl.resolve_function")
def test_validate_tests_output_shape(
    mock_resolve, mock_cache, mock_targets, mock_profile, mock_discover, mock_impact
):
    mock_resolve.return_value = ("/test/project/foo.py", None, None)
    mock_targets.return_value = [("func_a", MagicMock())]
    mock_profile.return_value = [
        {
            "function_key": "foo.py::func_a",
            "total_mutants": 10,
            "total_killed": 8,
            "survival_rate": 0.2,
            "survival_delta": -0.3,
            "per_category": [],
        }
    ]
    # Impact map returns refs for the function
    mock_map = MagicMock()
    mock_map.tests_for.return_value = [
        MagicMock(test_function="test_func_a"),
    ]
    mock_map.to_dict.return_value = {
        "total_test_functions": 1,
        "total_source_functions_covered": 1,
        "mappings": {},
    }
    mock_impact.return_value = mock_map

    helpers = _make_helpers()
    result_raw = impl_validate_tests(helpers, "/test", "foo.py", None)
    result = _load_tool_result(result_raw)

    # Must have validation-specific keys
    assert "validation_confidence" in result["results"][0]
    assert "mapped_tests" in result["results"][0]
    assert "mapping_warnings" in result
    assert "impact_map_summary" in result

    # Confidence should be 1.0: has coverage AND improved (delta < 0)
    assert result["results"][0]["validation_confidence"] == 1.0
    assert result["results"][0]["mapped_tests"] == ["test_func_a"]
    assert result["mapping_warnings"] == []


# ── Differs from refactor_loop ────────────────────────────────────


@patch("mcp_tools._mutation_tools_impl.build_test_impact_map")
@patch("mcp_tools._mutation_tools_impl.discover_test_files", return_value=["t.py"])
@patch("mcp_tools._mutation_tools_impl._profile_targets")
@patch("mcp_tools._mutation_tools_impl._resolve_refactor_targets")
@patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=Path("/tmp/c"))
@patch("mcp_tools._mutation_tools_impl.resolve_function")
def test_validate_differs_from_refactor_loop(
    mock_resolve, mock_cache, mock_targets, mock_profile, mock_discover, mock_impact
):
    mock_resolve.return_value = ("/test/project/foo.py", None, None)
    mock_targets.return_value = [("func_a", MagicMock())]
    mock_profile.return_value = [
        {
            "function_key": "foo.py::func_a",
            "total_mutants": 5,
            "total_killed": 3,
            "survival_rate": 0.4,
            "per_category": [],
        }
    ]
    mock_map = MagicMock()
    mock_map.tests_for.return_value = []
    mock_map.to_dict.return_value = {
        "total_test_functions": 0,
        "total_source_functions_covered": 0,
        "mappings": {},
    }
    mock_impact.return_value = mock_map

    helpers = _make_helpers()

    validate_raw = impl_validate_tests(helpers, "/test", "foo.py", None)
    validate_result = _load_tool_result(validate_raw)

    # impl_validate_tests produces fields that impl_refactor_loop does not
    assert "mapping_warnings" in validate_result
    assert "impact_map_summary" in validate_result
    assert "validation_confidence" in validate_result["results"][0]
    assert "mapped_tests" in validate_result["results"][0]


# ── Confidence scoring ────────────────────────────────────────────


@patch("mcp_tools._mutation_tools_impl.build_test_impact_map")
@patch("mcp_tools._mutation_tools_impl.discover_test_files", return_value=[])
@patch("mcp_tools._mutation_tools_impl._profile_targets")
@patch("mcp_tools._mutation_tools_impl._resolve_refactor_targets")
@patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=Path("/tmp/c"))
@patch("mcp_tools._mutation_tools_impl.resolve_function")
def test_validate_confidence_no_coverage_no_improvement(
    mock_resolve, mock_cache, mock_targets, mock_profile, mock_discover, mock_impact
):
    """No impact map coverage AND no improvement -> confidence 0.0."""
    mock_resolve.return_value = ("/test/project/foo.py", None, None)
    mock_targets.return_value = [("func_a", MagicMock())]
    mock_profile.return_value = [
        {
            "function_key": "foo.py::func_a",
            "total_mutants": 5,
            "total_killed": 3,
            "survival_rate": 0.4,
            "per_category": [],
        }
    ]
    mock_map = MagicMock()
    mock_map.tests_for.return_value = []
    mock_map.to_dict.return_value = {
        "total_test_functions": 0,
        "total_source_functions_covered": 0,
        "mappings": {},
    }
    mock_impact.return_value = mock_map

    helpers = _make_helpers()
    result_raw = impl_validate_tests(helpers, "/test", "foo.py", None)
    result = _load_tool_result(result_raw)
    assert result["results"][0]["validation_confidence"] == 0.0


@patch("mcp_tools._mutation_tools_impl.build_test_impact_map")
@patch("mcp_tools._mutation_tools_impl.discover_test_files", return_value=[])
@patch("mcp_tools._mutation_tools_impl._profile_targets")
@patch("mcp_tools._mutation_tools_impl._resolve_refactor_targets")
@patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=Path("/tmp/c"))
@patch("mcp_tools._mutation_tools_impl.resolve_function")
def test_validate_confidence_improved_but_no_coverage(
    mock_resolve, mock_cache, mock_targets, mock_profile, mock_discover, mock_impact
):
    """Improved (delta < 0) but no impact map coverage -> confidence 0.5."""
    mock_resolve.return_value = ("/test/project/foo.py", None, None)
    mock_targets.return_value = [("func_a", MagicMock())]
    mock_profile.return_value = [
        {
            "function_key": "foo.py::func_a",
            "total_mutants": 5,
            "total_killed": 4,
            "survival_rate": 0.2,
            "survival_delta": -0.2,
            "per_category": [],
        }
    ]
    mock_map = MagicMock()
    mock_map.tests_for.return_value = []
    mock_map.to_dict.return_value = {
        "total_test_functions": 0,
        "total_source_functions_covered": 0,
        "mappings": {},
    }
    mock_impact.return_value = mock_map

    helpers = _make_helpers()
    result_raw = impl_validate_tests(helpers, "/test", "foo.py", None)
    result = _load_tool_result(result_raw)
    assert result["results"][0]["validation_confidence"] == 0.5
    assert "foo.py::func_a" in result["mapping_warnings"]
