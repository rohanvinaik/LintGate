"""Tests for mcp_tools/_mutation_tools_impl.py — mutation tool implementations."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from mcp_tools._mutation_tools_impl import (
    _build_mutation_context,
    _collect_prescriptions,
    _resolve_refactor_targets,
    impl_clear_state,
    impl_decompose,
    impl_get_state,
    impl_prescribe,
    impl_prescribe_tests,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _make_helpers(project_root="/project"):
    return {
        "_validate_project_root": lambda p: project_root,
        "_json_dumps": lambda obj, **kw: json.dumps(obj),
    }


# ── _collect_prescriptions (pure) ────────────────────────────────────


class TestCollectPrescriptions:
    def test_empty_states(self):
        result = _collect_prescriptions([])
        assert result == []

    def test_no_survivors(self):
        states = [
            {
                "function_key": "mod.py::func",
                "per_category": [
                    {"category": "VALUE", "survived": 0, "survival_rate": 0.0},
                ],
            }
        ]
        result = _collect_prescriptions(states)
        assert result == []

    def test_one_survivor(self):
        states = [
            {
                "function_key": "mod.py::func",
                "per_category": [
                    {"category": "VALUE", "survived": 3, "survival_rate": 0.6},
                    {"category": "SWAP", "survived": 0, "survival_rate": 0.0},
                ],
            }
        ]
        result = _collect_prescriptions(states)
        assert len(result) == 1
        assert result[0]["function"] == "mod.py::func"
        assert result[0]["category"] == "VALUE"
        assert result[0]["survived"] == 3
        assert result[0]["survival_rate"] == 0.6
        assert "exact-value" in result[0]["action"]

    def test_multiple_survivors_across_functions(self):
        states = [
            {
                "function_key": "a.py::f1",
                "per_category": [
                    {"category": "VALUE", "survived": 2, "survival_rate": 0.5},
                ],
            },
            {
                "function_key": "b.py::f2",
                "per_category": [
                    {"category": "BOUNDARY", "survived": 1, "survival_rate": 0.3},
                    {"category": "STATE", "survived": 4, "survival_rate": 0.8},
                ],
            },
        ]
        result = _collect_prescriptions(states)
        assert len(result) == 3
        categories = [p["category"] for p in result]
        assert "VALUE" in categories
        assert "BOUNDARY" in categories
        assert "STATE" in categories

    def test_missing_survival_rate_defaults_zero(self):
        states = [
            {
                "function_key": "mod.py::f",
                "per_category": [
                    {"category": "TYPE", "survived": 1},
                ],
            }
        ]
        result = _collect_prescriptions(states)
        assert result[0]["survival_rate"] == 0


# ── _resolve_refactor_targets (pure) ─────────────────────────────────


class TestResolveRefactorTargets:
    def test_with_func_node_and_function(self):
        node = MagicMock()
        result = _resolve_refactor_targets("/path/file.py", node, "my_func")
        assert result == [("my_func", node)]

    @patch("mcp_tools._mutation_tools_impl.parse_file", return_value=None)
    def test_parse_error_returns_string(self, mock_parse):
        result = _resolve_refactor_targets("/path/file.py", None, None)
        assert isinstance(result, str)
        assert "Parse error" in result

    @patch("mcp_tools._mutation_tools_impl.walk_functions")
    @patch("mcp_tools._mutation_tools_impl.parse_file")
    def test_walks_functions_when_no_specific(self, mock_parse, mock_walk):
        mock_parse.return_value = MagicMock()
        mock_walk.return_value = [("func_a", MagicMock()), ("func_b", MagicMock())]
        result = _resolve_refactor_targets("/path/file.py", None, None)
        assert len(result) == 2
        assert result[0][0] == "func_a"


# ── impl_decompose ──────────────────────────────────────────────────


class TestImplDecompose:
    @patch("mcp_tools._mutation_tools_impl.iter_cached_states")
    @patch("mcp_tools._mutation_tools_impl.get_cache_dir")
    def test_no_candidates(self, mock_cache_dir, mock_states):
        mock_cache_dir.return_value = Path("/cache")
        mock_states.return_value = [
            {
                "function_key": "f.py::func",
                "per_category": [
                    {"category": "VALUE", "survived": 1},
                ],
            }
        ]
        result = json.loads(impl_decompose(_make_helpers(), ".", "f.py", None, "analyze"))
        assert result["candidates"] == []

    @patch("mcp_tools._mutation_tools_impl.iter_cached_states")
    @patch("mcp_tools._mutation_tools_impl.get_cache_dir")
    def test_finds_candidates_with_two_plus_surviving(self, mock_cache_dir, mock_states):
        mock_cache_dir.return_value = Path("/cache")
        mock_states.return_value = [
            {
                "function_key": "f.py::complex_func",
                "per_category": [
                    {"category": "VALUE", "survived": 2},
                    {"category": "SWAP", "survived": 1},
                    {"category": "BOUNDARY", "survived": 0},
                ],
            }
        ]
        result = json.loads(impl_decompose(_make_helpers(), ".", "f.py", None, "analyze"))
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["function"] == "f.py::complex_func"
        assert "VALUE" in result["candidates"][0]["surviving_categories"]
        assert "SWAP" in result["candidates"][0]["surviving_categories"]
        assert result["mode"] == "analyze"


# ── impl_get_state ───────────────────────────────────────────────────


class TestImplGetState:
    @patch("mcp_tools._mutation_tools_impl.get_cache_dir")
    def test_no_cache_dir(self, mock_cache_dir):
        mock_cache_dir.return_value = Path("/nonexistent/cache/dir")
        result = json.loads(impl_get_state(_make_helpers(), ".", None, None))
        assert "No mutation data yet" in result["note"]
        assert "next_actions" in result

    @patch("mcp_tools._mutation_tools_impl.iter_cached_states")
    @patch("mcp_tools._mutation_tools_impl.get_cache_dir")
    def test_with_cached_states(self, mock_cache_dir, mock_states):
        cache_dir = MagicMock()
        cache_dir.exists.return_value = True
        mock_cache_dir.return_value = cache_dir
        mock_states.return_value = [{"function_key": "f.py::add", "survival_rate": 0.5}]
        result = json.loads(impl_get_state(_make_helpers(), ".", "f.py", None))
        assert result["total_functions"] == 1
        assert len(result["states"]) == 1


# ── impl_prescribe ───────────────────────────────────────────────────


class TestImplPrescribe:
    @patch("mcp_tools._mutation_tools_impl.iter_cached_states")
    @patch("mcp_tools._mutation_tools_impl.get_cache_dir")
    def test_no_states(self, mock_cache_dir, mock_states):
        mock_cache_dir.return_value = Path("/cache")
        mock_states.return_value = []
        result = json.loads(impl_prescribe(_make_helpers(), ".", None, None))
        assert "No mutation data" in result["note"]

    @patch("mcp_tools._mutation_tools_impl.iter_cached_states")
    @patch("mcp_tools._mutation_tools_impl.get_cache_dir")
    def test_with_survivors(self, mock_cache_dir, mock_states):
        mock_cache_dir.return_value = Path("/cache")
        mock_states.return_value = [
            {
                "function_key": "mod.py::process",
                "per_category": [
                    {"category": "VALUE", "survived": 5, "survival_rate": 1.0},
                ],
            }
        ]
        result = json.loads(impl_prescribe(_make_helpers(), ".", "mod.py", None))
        assert result["total_prescriptions"] == 1
        assert result["prescriptions"][0]["category"] == "VALUE"
        assert len(result["next_actions"]) >= 1

    @patch("mcp_tools._mutation_tools_impl.iter_cached_states")
    @patch("mcp_tools._mutation_tools_impl.get_cache_dir")
    def test_no_survivors_empty_prescriptions(self, mock_cache_dir, mock_states):
        mock_cache_dir.return_value = Path("/cache")
        mock_states.return_value = [
            {
                "function_key": "mod.py::f",
                "per_category": [
                    {"category": "VALUE", "survived": 0},
                ],
            }
        ]
        result = json.loads(impl_prescribe(_make_helpers(), ".", None, None))
        assert result["total_prescriptions"] == 0
        assert result["next_actions"] == []


# ── impl_prescribe_tests ────────────────────────────────────────────


class TestImplPrescribeTests:
    @patch("mcp_tools._mutation_tools_impl.generate_test_skeleton")
    @patch("mcp_tools._mutation_tools_impl.iter_cached_states")
    @patch("mcp_tools._mutation_tools_impl.get_cache_dir")
    def test_generates_skeletons(self, mock_cache_dir, mock_states, mock_skel):
        mock_cache_dir.return_value = Path("/cache")
        mock_states.return_value = [
            {
                "function_key": "mod.py::func",
                "per_category": [
                    {"category": "VALUE", "survived": 2},
                ],
            }
        ]
        mock_skel.return_value = {
            "function": "mod.py::func",
            "category": "VALUE",
            "test_name": "test_func_value_mutation",
            "skeleton": "def test_func_value_mutation(): ...",
        }
        result = json.loads(impl_prescribe_tests(_make_helpers(), ".", "mod.py", None))
        assert len(result["skeletons"]) == 1
        assert result["skeletons"][0]["category"] == "VALUE"
        assert len(result["next_actions"]) >= 1

    @patch("mcp_tools._mutation_tools_impl.iter_cached_states")
    @patch("mcp_tools._mutation_tools_impl.get_cache_dir")
    def test_no_survivors_no_skeletons(self, mock_cache_dir, mock_states):
        mock_cache_dir.return_value = Path("/cache")
        mock_states.return_value = [
            {
                "function_key": "mod.py::f",
                "per_category": [
                    {"category": "VALUE", "survived": 0},
                ],
            }
        ]
        result = json.loads(impl_prescribe_tests(_make_helpers(), ".", "mod.py", None))
        assert result["skeletons"] == []
        assert result["next_actions"] == []


# ── impl_clear_state ─────────────────────────────────────────────────


class TestImplClearState:
    @patch("mcp_tools._mutation_tools_impl.get_cache_dir")
    def test_no_cache_dir(self, mock_cache_dir):
        mock_cache_dir.return_value = Path("/nonexistent")
        result = json.loads(impl_clear_state(_make_helpers(), ".", None))
        assert "No mutation state to clear" in result["note"]

    def test_clears_json_files(self, tmp_path):
        cache_dir = tmp_path / "mutation_cache"
        cache_dir.mkdir()
        (cache_dir / "func1.json").write_text('{"function_key": "f.py::func1"}')
        (cache_dir / "func2.json").write_text('{"function_key": "f.py::func2"}')
        (cache_dir / "scheduler_state.json").write_text("{}")

        with patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=cache_dir):
            result = json.loads(impl_clear_state(_make_helpers(), ".", None))

        assert result["cleared"] == 2
        assert (cache_dir / "scheduler_state.json").exists()  # preserved

    def test_clears_only_matching_file(self, tmp_path):
        cache_dir = tmp_path / "mutation_cache"
        cache_dir.mkdir()
        (cache_dir / "a.json").write_text('{"function_key": "target.py::f"}')
        (cache_dir / "b.json").write_text('{"function_key": "other.py::g"}')

        with patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=cache_dir):
            result = json.loads(impl_clear_state(_make_helpers(), ".", "target.py"))

        assert result["cleared"] == 1
        assert not (cache_dir / "a.json").exists()
        assert (cache_dir / "b.json").exists()


# ── _build_mutation_context ──────────────────────────────────────────


class TestBuildMutationContext:
    @patch("mcp_tools._mutation_tools_impl.discover_test_files", return_value=["test_a.py"])
    @patch("mcp_tools._mutation_tools_impl.detect_purity_map", return_value={"f": True})
    @patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=Path("/cache"))
    def test_builds_context(self, mock_cache, mock_purity, mock_tests):
        ctx = _build_mutation_context("/project", "/project/src/mod.py")
        assert ctx.full_path == "/project/src/mod.py"
        assert ctx.rel_path == os.path.relpath("/project/src/mod.py", "/project")
        assert ctx.cache_dir == Path("/cache")
        assert ctx.purity_map == {"f": True}
        assert ctx.test_files == ["test_a.py"]
