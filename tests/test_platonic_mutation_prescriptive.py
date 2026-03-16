"""Prescriptive spec tests for run_mutation_sampling.

Target: lintgate.testing.platonic_mutation::run_mutation_sampling
Problem class: pure
Spec claims:
  - must return list[dict] always, never raise
  - empty list on any error
  - budget_ms, max_per_category, seed must flow through to engine
  - JSON string results must be parsed to list
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from lintgate.testing.platonic_mutation import run_mutation_sampling

# Lazy imports inside run_mutation_sampling — patch at source modules
_PATCHES = {
    "resolve": "mcp_tools._mutation_impl.resolve_function",
    "run_with_tests": "mcp_tools._mutation_impl.run_on_functions_with_tests",
    "build_ctx": "mcp_tools._mutation_tools_impl._build_mutation_context",
    "engine": "lintgate.specification.mutation_engine.run_function_sampling",
    "filter": "lintgate.specification.mutation_filter.filter_categories",
    "key_fn": "lintgate.keys.canonical_function_key",
}


# ── Claim 0: must return list[dict] always, never raise ──────────────


class TestReturnTypeContract:
    def test_returns_list_on_success(self):
        """Happy path returns a list."""
        mock_results = [{"function_key": "mod::f", "category": "VALUE"}]
        with (
            patch(_PATCHES["resolve"], return_value=("/full/path.py", MagicMock(), None)),
            patch(_PATCHES["build_ctx"], return_value=MagicMock()),
            patch(_PATCHES["run_with_tests"], return_value=mock_results),
            patch(_PATCHES["engine"]),
            patch(_PATCHES["filter"]),
            patch(_PATCHES["key_fn"]),
        ):
            result = run_mutation_sampling("/project", "mod.py")
            assert isinstance(result, list)
            assert result == mock_results

    def test_returns_list_on_resolve_error(self):
        """When resolve_function returns an error and no full path, returns []."""
        with (
            patch(_PATCHES["resolve"], return_value=(None, None, "file not found")),
            patch(_PATCHES["build_ctx"]),
            patch(_PATCHES["run_with_tests"]),
            patch(_PATCHES["engine"]),
            patch(_PATCHES["filter"]),
            patch(_PATCHES["key_fn"]),
        ):
            result = run_mutation_sampling("/project", "nonexistent.py")
            assert isinstance(result, list)
            assert result == []

    def test_resolve_with_full_but_error_continues(self):
        """When resolve returns err but also a full path, continues execution."""
        mock_results = [{"function_key": "mod::f"}]
        with (
            patch(_PATCHES["resolve"], return_value=("/full/path.py", None, "warning")),
            patch(_PATCHES["build_ctx"], return_value=MagicMock()),
            patch(_PATCHES["run_with_tests"], return_value=mock_results),
            patch(_PATCHES["engine"]),
            patch(_PATCHES["filter"]),
            patch(_PATCHES["key_fn"]),
        ):
            result = run_mutation_sampling("/project", "mod.py")
            assert isinstance(result, list)
            assert result == mock_results


# ── Claim 1: empty list on any error ─────────────────────────────────


class TestErrorResilience:
    def test_import_error_returns_empty_list(self):
        """ImportError in lazy imports → []."""
        with patch(_PATCHES["resolve"], side_effect=ImportError("no module")):
            result = run_mutation_sampling("/project", "mod.py")
            assert result == []

    def test_runtime_error_in_context_returns_empty_list(self):
        """RuntimeError during _build_mutation_context → []."""
        with (
            patch(_PATCHES["resolve"], return_value=("/full/path.py", MagicMock(), None)),
            patch(_PATCHES["build_ctx"], side_effect=RuntimeError("boom")),
            patch(_PATCHES["engine"]),
            patch(_PATCHES["filter"]),
            patch(_PATCHES["key_fn"]),
        ):
            result = run_mutation_sampling("/project", "mod.py")
            assert result == []

    def test_exception_in_engine_returns_empty_list(self):
        """Exception in run_on_functions_with_tests → []."""
        with (
            patch(_PATCHES["resolve"], return_value=("/full/path.py", MagicMock(), None)),
            patch(_PATCHES["build_ctx"], return_value=MagicMock()),
            patch(_PATCHES["run_with_tests"], side_effect=Exception("engine failure")),
            patch(_PATCHES["engine"]),
            patch(_PATCHES["filter"]),
            patch(_PATCHES["key_fn"]),
        ):
            result = run_mutation_sampling("/project", "mod.py")
            assert result == []


# ── Claim 2: budget_ms, max_per_category, seed flow through ──────────


class TestParameterPassthrough:
    def test_budget_ms_flows_to_engine(self):
        """budget_ms parameter reaches run_function_sampling via the lambda."""
        captured_kwargs: dict = {}

        def capture_sampling(_node, _key, _cats, _tests, _orig, **kwargs):
            captured_kwargs.update(kwargs)
            return {"function_key": _key, "survived": 0}

        def invoke_lambda(_ctx, _func_node, _target, runner, _filter_fn, _key_fn):
            return runner(MagicMock(), "mod::f", ["VALUE"], [], "src")

        with (
            patch(_PATCHES["resolve"], return_value=("/full/path.py", MagicMock(), None)),
            patch(_PATCHES["build_ctx"], return_value=MagicMock()),
            patch(_PATCHES["run_with_tests"], side_effect=invoke_lambda),
            patch(_PATCHES["engine"], side_effect=capture_sampling),
            patch(_PATCHES["filter"]),
            patch(_PATCHES["key_fn"]),
        ):
            run_mutation_sampling("/project", "mod.py", budget_ms=9999)
            assert captured_kwargs.get("budget_ms") == 9999

    def test_max_per_category_flows_to_engine(self):
        """max_per_category reaches run_function_sampling."""
        captured_kwargs: dict = {}

        def capture_sampling(_node, _key, _cats, _tests, _orig, **kwargs):
            captured_kwargs.update(kwargs)
            return {"function_key": _key}

        def invoke_lambda(_ctx, _func_node, _target, runner, _filter_fn, _key_fn):
            return runner(MagicMock(), "mod::f", ["VALUE"], [], "src")

        with (
            patch(_PATCHES["resolve"], return_value=("/full/path.py", MagicMock(), None)),
            patch(_PATCHES["build_ctx"], return_value=MagicMock()),
            patch(_PATCHES["run_with_tests"], side_effect=invoke_lambda),
            patch(_PATCHES["engine"], side_effect=capture_sampling),
            patch(_PATCHES["filter"]),
            patch(_PATCHES["key_fn"]),
        ):
            run_mutation_sampling("/project", "mod.py", max_per_category=7)
            assert captured_kwargs.get("max_per_category") == 7

    def test_seed_flows_to_engine(self):
        """seed parameter reaches run_function_sampling."""
        captured_kwargs: dict = {}

        def capture_sampling(_node, _key, _cats, _tests, _orig, **kwargs):
            captured_kwargs.update(kwargs)
            return {"function_key": _key}

        def invoke_lambda(_ctx, _func_node, _target, runner, _filter_fn, _key_fn):
            return runner(MagicMock(), "mod::f", ["VALUE"], [], "src")

        with (
            patch(_PATCHES["resolve"], return_value=("/full/path.py", MagicMock(), None)),
            patch(_PATCHES["build_ctx"], return_value=MagicMock()),
            patch(_PATCHES["run_with_tests"], side_effect=invoke_lambda),
            patch(_PATCHES["engine"], side_effect=capture_sampling),
            patch(_PATCHES["filter"]),
            patch(_PATCHES["key_fn"]),
        ):
            run_mutation_sampling("/project", "mod.py", seed=42)
            assert captured_kwargs.get("seed") == 42

    def test_default_budget_ms(self):
        """Default budget_ms=5000 flows through."""
        captured_kwargs: dict = {}

        def capture_sampling(_node, _key, _cats, _tests, _orig, **kwargs):
            captured_kwargs.update(kwargs)
            return {"function_key": _key}

        def invoke_lambda(_ctx, _func_node, _target, runner, _filter_fn, _key_fn):
            return runner(MagicMock(), "mod::f", ["VALUE"], [], "src")

        with (
            patch(_PATCHES["resolve"], return_value=("/full/path.py", MagicMock(), None)),
            patch(_PATCHES["build_ctx"], return_value=MagicMock()),
            patch(_PATCHES["run_with_tests"], side_effect=invoke_lambda),
            patch(_PATCHES["engine"], side_effect=capture_sampling),
            patch(_PATCHES["filter"]),
            patch(_PATCHES["key_fn"]),
        ):
            run_mutation_sampling("/project", "mod.py")
            assert captured_kwargs.get("budget_ms") == 5000


# ── Claim 3: JSON string results must be parsed to list ──────────────


class TestJsonStringParsing:
    def test_json_string_result_parsed(self):
        """When engine returns a JSON string of a list, it gets parsed."""
        json_result = json.dumps([{"function_key": "mod::f", "survived": 0}])
        with (
            patch(_PATCHES["resolve"], return_value=("/full/path.py", MagicMock(), None)),
            patch(_PATCHES["build_ctx"], return_value=MagicMock()),
            patch(_PATCHES["run_with_tests"], return_value=json_result),
            patch(_PATCHES["engine"]),
            patch(_PATCHES["filter"]),
            patch(_PATCHES["key_fn"]),
        ):
            result = run_mutation_sampling("/project", "mod.py")
            assert isinstance(result, list)
            assert len(result) == 1
            assert result[0]["function_key"] == "mod::f"

    def test_invalid_json_string_returns_empty(self):
        """When engine returns a non-JSON string, returns []."""
        with (
            patch(_PATCHES["resolve"], return_value=("/full/path.py", MagicMock(), None)),
            patch(_PATCHES["build_ctx"], return_value=MagicMock()),
            patch(_PATCHES["run_with_tests"], return_value="not valid json {{{"),
            patch(_PATCHES["engine"]),
            patch(_PATCHES["filter"]),
            patch(_PATCHES["key_fn"]),
        ):
            result = run_mutation_sampling("/project", "mod.py")
            assert result == []

    def test_json_string_of_dict_returns_empty(self):
        """When engine returns a JSON string of a dict (not list), returns []."""
        with (
            patch(_PATCHES["resolve"], return_value=("/full/path.py", MagicMock(), None)),
            patch(_PATCHES["build_ctx"], return_value=MagicMock()),
            patch(_PATCHES["run_with_tests"], return_value='{"key": "value"}'),
            patch(_PATCHES["engine"]),
            patch(_PATCHES["filter"]),
            patch(_PATCHES["key_fn"]),
        ):
            result = run_mutation_sampling("/project", "mod.py")
            assert result == []

    def test_non_list_non_string_returns_empty(self):
        """When engine returns something other than list or string, returns []."""
        with (
            patch(_PATCHES["resolve"], return_value=("/full/path.py", MagicMock(), None)),
            patch(_PATCHES["build_ctx"], return_value=MagicMock()),
            patch(_PATCHES["run_with_tests"], return_value=42),
            patch(_PATCHES["engine"]),
            patch(_PATCHES["filter"]),
            patch(_PATCHES["key_fn"]),
        ):
            result = run_mutation_sampling("/project", "mod.py")
            assert result == []

    def test_none_result_returns_empty(self):
        """When engine returns None, returns []."""
        with (
            patch(_PATCHES["resolve"], return_value=("/full/path.py", MagicMock(), None)),
            patch(_PATCHES["build_ctx"], return_value=MagicMock()),
            patch(_PATCHES["run_with_tests"], return_value=None),
            patch(_PATCHES["engine"]),
            patch(_PATCHES["filter"]),
            patch(_PATCHES["key_fn"]),
        ):
            result = run_mutation_sampling("/project", "mod.py")
            assert result == []


# ── Extra: generated_test_files passthrough ──────────────────────────


class TestGeneratedTestFilesPassthrough:
    def test_extra_test_files_passed_to_context(self):
        """generated_test_files reaches _build_mutation_context."""
        with (
            patch(_PATCHES["resolve"], return_value=("/full/path.py", MagicMock(), None)),
            patch(_PATCHES["build_ctx"], return_value=MagicMock()) as mock_ctx,
            patch(_PATCHES["run_with_tests"], return_value=[]),
            patch(_PATCHES["engine"]),
            patch(_PATCHES["filter"]),
            patch(_PATCHES["key_fn"]),
        ):
            run_mutation_sampling(
                "/project", "mod.py", generated_test_files=["tests/test_extra.py"]
            )
            mock_ctx.assert_called_once()
            call_kwargs = mock_ctx.call_args
            # extra_test_files should be in the call
            assert call_kwargs[1].get("extra_test_files") == ["tests/test_extra.py"] or (
                len(call_kwargs[0]) > 2 and call_kwargs[0][2] == ["tests/test_extra.py"]
            )
