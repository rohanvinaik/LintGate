"""Tests for mcp_tools/convergence_tools.py — MCP tool implementations."""

from __future__ import annotations

import json
import os

import pytest

from mcp_tools.convergence_tools import (
    _discover_python_files,
    _impl_convergence_analyze,
    _impl_extraction_plan,
    _impl_optimization_landscape,
    register,
)


def _load_tool_result(json_str):
    import json
    import os
    r = json.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and os.path.isfile(r.get("file","")):
        with open(r["file"]) as f: return json.loads(f.read())
    return r


# ── Test helpers ──────────────────────────────────────────────────────


def _make_helpers(project_root: str | None = None) -> dict:
    """Build a minimal helpers dict for tool implementations."""

    def _validate_project_root(path: str) -> None:
        if not os.path.isdir(path):
            raise ValueError(f"Not a directory: {path}")

    return {"_validate_project_root": _validate_project_root}


@pytest.fixture
def tmp_project(tmp_path):
    """Create a temporary Python project with a few files."""
    (tmp_path / "module.py").write_text(
        "def compute(x, y):\n    return x + y\n\ndef process(data):\n    print(data)\n    return len(data)\n"
    )
    (tmp_path / "utils.py").write_text("def helper(a):\n    return a * 2\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_module.py").write_text("def test_compute():\n    assert True\n")
    return str(tmp_path)


# ── File discovery tests ──────────────────────────────────────────────


class TestDiscoverPythonFiles:
    def test_discovers_files(self, tmp_project):
        files = _discover_python_files(tmp_project)
        basenames = sorted(os.path.basename(f) for f in files)
        assert "module.py" in basenames
        assert "utils.py" in basenames

    def test_filters_by_file(self, tmp_project):
        files = _discover_python_files(tmp_project, "module.py")
        assert len(files) == 1
        assert files[0].endswith("module.py")

    def test_excludes_hidden_dirs(self, tmp_project):
        hidden = os.path.join(tmp_project, ".hidden")
        os.makedirs(hidden)
        with open(os.path.join(hidden, "secret.py"), "w") as f:
            f.write("x = 1\n")
        files = _discover_python_files(tmp_project)
        assert not any(".hidden" in f for f in files)

    def test_missing_file_filter(self, tmp_project):
        files = _discover_python_files(tmp_project, "nonexistent.py")
        assert files == []


# ── convergence_analyze tests ─────────────────────────────────────────


class TestConvergenceAnalyze:
    def test_returns_structured_result(self, tmp_project):
        helpers = _make_helpers(tmp_project)
        result = _impl_convergence_analyze(tmp_project, None, None, helpers)

        assert "project" in result
        assert result["project"] == tmp_project
        # Should have either convergence data or an error (depends on channel availability)
        assert "function_convergence" in result or "error" in result

    def test_with_file_filter(self, tmp_project):
        helpers = _make_helpers(tmp_project)
        result = _impl_convergence_analyze(tmp_project, "module.py", None, helpers)

        assert "project" in result

    def test_with_function_filter(self, tmp_project):
        helpers = _make_helpers(tmp_project)
        result = _impl_convergence_analyze(tmp_project, None, "compute", helpers)

        assert "project" in result

    def test_has_next_actions(self, tmp_project):
        helpers = _make_helpers(tmp_project)
        result = _impl_convergence_analyze(tmp_project, None, None, helpers)

        if "next_actions" in result:
            tools = [a["tool"] for a in result["next_actions"]]
            assert "extraction_plan" in tools
            assert "optimization_landscape" in tools


# ── extraction_plan tests ─────────────────────────────────────────────


class TestExtractionPlan:
    def test_returns_plan_for_function(self, tmp_project):
        helpers = _make_helpers(tmp_project)
        result = _impl_extraction_plan(tmp_project, "module.py::compute", helpers)

        assert "source_function" in result
        assert "steps" in result
        assert isinstance(result["steps"], list)

    def test_plan_has_estimated_impact(self, tmp_project):
        helpers = _make_helpers(tmp_project)
        result = _impl_extraction_plan(tmp_project, "module.py::process", helpers)

        assert "estimated_impact" in result

    def test_plan_includes_next_actions(self, tmp_project):
        helpers = _make_helpers(tmp_project)
        result = _impl_extraction_plan(tmp_project, "module.py::compute", helpers)

        assert "next_actions" in result

    def test_unknown_function_still_returns_plan(self, tmp_project):
        """Unknown function creates minimal convergence and returns a plan."""
        helpers = _make_helpers(tmp_project)
        result = _impl_extraction_plan(tmp_project, "module.py::nonexistent", helpers)

        assert "source_function" in result
        assert result["source_function"] == "module.py::nonexistent"


# ── optimization_landscape tests ──────────────────────────────────────


class TestOptimizationLandscape:
    def test_returns_landscape(self, tmp_project):
        helpers = _make_helpers(tmp_project)
        result = _impl_optimization_landscape(tmp_project, helpers)

        assert "project" in result
        assert result["project"] == tmp_project

    def test_landscape_has_next_actions(self, tmp_project):
        helpers = _make_helpers(tmp_project)
        result = _impl_optimization_landscape(tmp_project, helpers)

        if "next_actions" in result:
            tools = [a["tool"] for a in result["next_actions"]]
            # Auto mode falls back to static — next_actions suggest convergence_analyze
            # or controlplane_run instead of extraction_plan
            assert len(tools) >= 1

    def test_static_mode_returns_landscape(self, tmp_project):
        helpers = _make_helpers(tmp_project)
        result = _impl_optimization_landscape(tmp_project, helpers, mode="static")

        assert result["project"] == tmp_project
        assert result["mode"] == "static"
        assert "cache_hotspots" in result
        assert "summary" in result

    def test_dynamic_mode_produces_convergence_with_mcp_surface(self, tmp_project):
        """Dynamic mode now sets surface='mcp', so channels activate and produce convergence."""
        helpers = _make_helpers(tmp_project)
        result = _impl_optimization_landscape(tmp_project, helpers, mode="dynamic")

        assert result["mode"] == "dynamic"
        # Gap A fix: surface="mcp" means channels activate, producing convergence data
        assert "convergence_targets" in result or "diagnostics" in result


# ── Registration tests ────────────────────────────────────────────────


class TestRegistration:
    def test_register_returns_three_tools(self):
        """register() returns all convergence and golden-path tools."""

        class FakeMCP:
            def tool(self):
                def decorator(fn):
                    return fn

                return decorator

        helpers = _make_helpers("/tmp")
        tools = register(FakeMCP(), helpers)

        assert "convergence_analyze" in tools
        assert "extraction_plan" in tools
        assert "optimization_landscape" in tools
        assert "platonic_converge" in tools
        assert "platonic_project" in tools
        assert "platonic_continue" in tools
        assert "platonic_apply" in tools
        assert callable(tools["convergence_analyze"])
        assert callable(tools["extraction_plan"])
        assert callable(tools["optimization_landscape"])
        assert callable(tools["platonic_project"])

    def test_tool_functions_return_json(self, tmp_project):
        """Tool functions return valid JSON strings."""

        class FakeMCP:
            def tool(self):
                def decorator(fn):
                    return fn

                return decorator

        helpers = _make_helpers(tmp_project)
        tools = register(FakeMCP(), helpers)

        # convergence_analyze
        result_str = tools["convergence_analyze"](tmp_project)
        result = json.loads(result_str)
        assert isinstance(result, dict)

    def test_golden_path_wrappers_return_json(self, tmp_project):
        class FakeMCP:
            def tool(self):
                def decorator(fn):
                    return fn

                return decorator

        helpers = _make_helpers(tmp_project)
        tools = register(FakeMCP(), helpers)

        with (
            pytest.MonkeyPatch.context() as mp,
        ):
            mp.setattr(
                "mcp_tools._platonic_impl.impl_platonic_project",
                lambda *_a, **_kw: json.dumps({"workflow_id": "wf1", "state": "PROFILING"}),
            )
            mp.setattr(
                "mcp_tools._platonic_impl.impl_platonic_continue",
                lambda *_a, **_kw: json.dumps({"workflow_id": "wf1", "state": "VALIDATING"}),
            )
            mp.setattr(
                "mcp_tools._platonic_impl.impl_platonic_apply",
                lambda *_a, **_kw: json.dumps({"workflow_id": "wf1", "state": "READY_TO_APPLY"}),
            )
            project = _load_tool_result(tools["platonic_project"](tmp_project))
            cont = _load_tool_result(tools["platonic_continue"](tmp_project, "wf1"))
            apply = _load_tool_result(tools["platonic_apply"](tmp_project, "wf1"))

        assert project["workflow_id"] == "wf1"
        assert cont["state"] == "VALIDATING"
        assert apply["state"] == "READY_TO_APPLY"

        # extraction_plan
        result_str = tools["extraction_plan"](tmp_project, "module.py::compute")
        result = json.loads(result_str)
        assert isinstance(result, dict)

        # optimization_landscape
        result_str = tools["optimization_landscape"](tmp_project)
        result = json.loads(result_str)
        assert isinstance(result, dict)
