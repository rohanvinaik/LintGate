"""Tests for project-wide mutation redundancy analysis."""

from __future__ import annotations

import json

from mcp_tools.redundancy_tools import (
    _compute_unique_kills,
    _greedy_covering_set,
)

def _load_tool_result(json_str):
    import json, os
    r = json.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and os.path.isfile(r.get("file","")):
        with open(r["file"]) as f: return json.loads(f.read())
    return r



class TestComputeUniqueKills:
    def test_unique_kills_basic(self):
        test_kills = {
            "test_a": {"m1", "m2"},
            "test_b": {"m2", "m3"},
            "test_c": {"m3"},
        }
        unique = _compute_unique_kills(test_kills)
        # m1 is only killed by test_a → 1 unique kill
        assert unique["test_a"] == 1
        # m2 killed by both test_a and test_b → 0 unique for each from m2
        # m3 killed by test_b and test_c → 0 unique
        assert unique["test_b"] == 0
        assert unique["test_c"] == 0

    def test_all_unique(self):
        test_kills = {
            "test_a": {"m1"},
            "test_b": {"m2"},
        }
        unique = _compute_unique_kills(test_kills)
        assert unique["test_a"] == 1
        assert unique["test_b"] == 1

    def test_all_redundant(self):
        test_kills = {
            "test_a": {"m1", "m2"},
            "test_b": {"m1", "m2"},
        }
        unique = _compute_unique_kills(test_kills)
        assert unique["test_a"] == 0
        assert unique["test_b"] == 0


class TestGreedyCoveringSet:
    def test_minimal_cover(self):
        test_kills = {
            "test_a": {"m1", "m2", "m3"},
            "test_b": {"m2", "m3"},
            "test_c": {"m4"},
        }
        all_mutants = {"m1", "m2", "m3", "m4"}
        cover = _greedy_covering_set(test_kills, all_mutants)
        # test_a covers 3, test_c covers 1 — minimal set is [test_a, test_c]
        assert set(cover) == {"test_a", "test_c"}

    def test_single_test_covers_all(self):
        test_kills = {
            "test_a": {"m1", "m2"},
            "test_b": {"m1"},
        }
        all_mutants = {"m1", "m2"}
        cover = _greedy_covering_set(test_kills, all_mutants)
        assert cover == ["test_a"]

    def test_empty_input(self):
        cover = _greedy_covering_set({}, set())
        assert cover == []


class TestToolRegistration:
    def test_tool_registered(self):
        from mcp_server import test_redundancy_project

        assert callable(test_redundancy_project)

    def test_no_profiled_data(self, tmp_path):
        class FakeMCP:
            def tool(self):
                def dec(fn):
                    self._fn = fn
                    return fn

                return dec

        fake = FakeMCP()
        from mcp_tools.redundancy_tools import register

        tools = register(fake, {"_validate_project_root": lambda p: p})
        result = _load_tool_result(tools["test_redundancy_project"](path=str(tmp_path)))
        assert result["status"] == "no_profiled_data"

    def test_with_mock_profiled_data(self, tmp_path):
        # Create fake mutation cache
        cache_dir = tmp_path / ".lintgate" / "mutation"
        cache_dir.mkdir(parents=True)

        state1 = {
            "function_key": "mod.py::func_a",
            "coverage_depth": "profiled",
            "survival_rate": 0.0,
            "total_mutants": 3,
            "kill_matrix": {
                "VALUE:line10": ["test_a", "test_b"],
                "SWAP:line15": ["test_a"],
                "BOUNDARY:line20": ["test_b", "test_c"],
            },
        }
        (cache_dir / "mod_py__func_a.json").write_text(json.dumps(state1))

        class FakeMCP:
            def tool(self):
                def dec(fn):
                    self._fn = fn
                    return fn

                return dec

        fake = FakeMCP()
        from mcp_tools.redundancy_tools import register

        tools = register(fake, {"_validate_project_root": lambda p: p})
        result = _load_tool_result(tools["test_redundancy_project"](path=str(tmp_path)))

        assert result["status"] == "analyzed"
        assert result["profiled_functions"] == 1
        assert result["total_mutants"] == 3
        assert result["total_tests_in_matrix"] == 3
        # test_a kills SWAP uniquely, test_c has no unique kills
        zero = {z["test"] for z in result["zero_unique_kill_tests"]}
        assert "test_c" in zero
        assert "test_a" not in zero
