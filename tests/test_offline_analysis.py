"""Tests for offline analysis engine and MCP tools."""

from __future__ import annotations

import json
import os
import tempfile

from lintgate.offline_analysis import (
    ActionItem,
    _analyze_project_structure,
    _analyze_test_coverage,
    _build_action_plan,
    _detect_src_dirs,
    _load_prescriptive_state,
    run_full_analysis,
)


class TestProjectStructure:
    def test_detect_src_dirs(self, tmp_path):
        """Auto-detects directories containing Python files."""
        (tmp_path / "mylib").mkdir()
        (tmp_path / "mylib" / "__init__.py").write_text("")
        (tmp_path / "mylib" / "core.py").write_text("x = 1\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_core.py").write_text("")

        dirs = _detect_src_dirs(str(tmp_path))
        assert "mylib" in dirs
        assert "tests" not in dirs

    def test_detect_src_dirs_fallback(self, tmp_path):
        """Falls back to ['.'] when no packages found."""
        (tmp_path / "README.md").write_text("hello")
        dirs = _detect_src_dirs(str(tmp_path))
        assert dirs == ["."]

    def test_analyze_project_structure(self, tmp_path):
        """Collects file inventory and LOC counts."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "core.py").write_text("def f():\n    return 1\n")
        (pkg / "utils.py").write_text("x = 1\n")

        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_core.py").write_text("def test_f():\n    pass\n")

        result = _analyze_project_structure(str(tmp_path), ["pkg"], 500)
        assert result["total_source_files"] == 3  # __init__.py + core.py + utils.py
        assert result["total_test_files"] >= 1
        assert result["total_loc"] > 0


class TestTestCoverage:
    def test_coverage_mapping(self, tmp_path):
        """Maps source files to test files and computes ratios."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "core.py").write_text("def f():\n    return 1\n" * 10)
        (pkg / "utils.py").write_text("x = 1\n")

        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_core.py").write_text("def test_f():\n    pass\n" * 5)

        result = _analyze_test_coverage(str(tmp_path), ["pkg/core.py", "pkg/utils.py"])
        assert result["files_with_tests"] >= 1
        assert result["files_without_tests"] >= 1
        assert result["overall_ratio"] > 0


class TestActionPlan:
    def test_plan_ordering(self):
        """Action plan respects priority ordering."""
        analysis = {
            "lint": {
                "total_findings": 5,
                "auto_fixable": 3,
                "findings": [
                    {"severity": "blocking", "kind": "F821", "message": "undefined name", "file": "a.py", "line": 1},
                ],
                "auto_fixable_summary": [],
            },
            "specification": {
                "total_functions": 10,
                "under_specified_count": 2,
                "hotspot_functions": [
                    {"function_key": "mod::f", "estimated_sigma": 8, "assertion_count": 1, "source_file": "mod.py"},
                ],
                "under_specified_top": [
                    {"function_key": "mod::f", "estimated_sigma": 8, "assertion_count": 1, "source_file": "mod.py"},
                ],
            },
            "mutation": {"cached": False},
            "test_coverage": {
                "no_test_files": [{"file": "big.py", "src_loc": 500}],
                "low_coverage_files": [],
            },
            "performance": {"pure_count": 5, "pure_ratio": 0.5},
            "prescriptive": {"total_specs": 0},
        }

        plan = _build_action_plan(analysis)
        assert len(plan) > 0

        # P0/P1 items come before P2/P3
        priorities = [a["priority"] for a in plan]
        p0_p1 = [p for p in priorities if p.startswith("P0") or p.startswith("P1")]
        p2_p3 = [p for p in priorities if p.startswith("P2") or p.startswith("P3")]
        if p0_p1 and p2_p3:
            last_p1_idx = max(i for i, p in enumerate(priorities) if p.startswith("P0") or p.startswith("P1"))
            first_p2_idx = min(i for i, p in enumerate(priorities) if p.startswith("P2") or p.startswith("P3"))
            assert last_p1_idx < first_p2_idx

    def test_plan_has_dependencies(self):
        """Actions that depend on lint fixes have depends_on set."""
        analysis = {
            "lint": {"total_findings": 1, "auto_fixable": 1, "findings": [], "auto_fixable_summary": []},
            "specification": {"total_functions": 0, "under_specified_count": 0, "hotspot_functions": [], "under_specified_top": []},
            "mutation": {"cached": False},
            "test_coverage": {"no_test_files": [{"file": "a.py", "src_loc": 100}], "low_coverage_files": []},
            "performance": {"pure_count": 0, "pure_ratio": 0},
            "prescriptive": {"total_specs": 0},
        }
        plan = _build_action_plan(analysis)
        # The test creation action should depend on the auto-fix action
        test_actions = [a for a in plan if a["category"] == "missing_test_file"]
        if test_actions:
            assert len(test_actions[0].get("depends_on", [])) > 0


class TestFullAnalysis:
    def test_run_full_analysis_minimal(self, tmp_path):
        """Full analysis runs on a minimal project."""
        pkg = tmp_path / "mymod"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "core.py").write_text("def add(a, b):\n    return a + b\n")

        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_core.py").write_text("def test_add():\n    assert True\n")

        result = run_full_analysis(str(tmp_path), include_mutation=False)

        assert result["schema_version"] == "1"
        assert result["project"]["total_source_files"] >= 1
        assert "lint" in result
        assert "specification" in result
        assert "action_plan" in result
        assert result["elapsed_s"] >= 0

        # JSON-serializable
        serialized = json.dumps(result)
        assert len(serialized) > 0

    def test_run_full_analysis_empty_project(self, tmp_path):
        """Handles empty project gracefully."""
        result = run_full_analysis(str(tmp_path), include_mutation=False)
        assert result["project"]["total_source_files"] == 0
        assert isinstance(result["action_plan"], list)


class TestMCPToolImports:
    def test_generate_import(self):
        from mcp_server import offline_analysis_generate
        assert callable(offline_analysis_generate)

    def test_run_import(self):
        from mcp_server import offline_analysis_run
        assert callable(offline_analysis_run)
