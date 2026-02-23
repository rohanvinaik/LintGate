"""Tests for test_effectiveness_tools — MCP tool responses."""

from __future__ import annotations

import json
import os
import tempfile

import pytest


@pytest.fixture()
def temp_project():
    """Create a minimal project with source and test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Source file
        src_dir = os.path.join(tmpdir, "src")
        os.makedirs(src_dir)
        with open(os.path.join(src_dir, "calculator.py"), "w") as f:
            f.write("def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n")

        # Test file
        test_dir = os.path.join(tmpdir, "tests")
        os.makedirs(test_dir)
        with open(os.path.join(test_dir, "test_calculator.py"), "w") as f:
            f.write(
                "from src.calculator import add, subtract\n"
                "\n"
                "def test_add():\n"
                "    result = add(1, 2)\n"
                "    assert result == 3\n"
                "\n"
                "def test_subtract():\n"
                "    result = subtract(5, 3)\n"
                "    assert result is not None\n"
            )

        yield tmpdir


@pytest.fixture()
def helpers():
    """Minimal helpers dict matching MCP tool expectations."""

    def _validate_project_root(path):
        return os.path.abspath(path)

    def _json_dumps(data, output_mode=None):
        return json.dumps(data, indent=2)

    return {
        "_validate_project_root": _validate_project_root,
        "_json_dumps": _json_dumps,
    }


class TestAnalyzeTestStrength:
    """Tests for analyze_test_strength MCP tool."""

    def test_returns_json(self, temp_project, helpers):
        from mcp_tools.test_effectiveness_tools import register

        class FakeMCP:
            def tool(self):
                def decorator(func):
                    return func

                return decorator

        mcp = FakeMCP()
        tools = register(mcp, helpers)
        result_str = tools["analyze_test_strength"](path=temp_project)
        result = json.loads(result_str)

        assert "summary" in result or "error" in result or "note" in result

    def test_with_function_filter(self, temp_project, helpers):
        from mcp_tools.test_effectiveness_tools import register

        class FakeMCP:
            def tool(self):
                def decorator(func):
                    return func

                return decorator

        mcp = FakeMCP()
        tools = register(mcp, helpers)
        result_str = tools["analyze_test_strength"](path=temp_project, function_filter="add")
        result = json.loads(result_str)
        assert "error" not in result


class TestInspectTestAssertions:
    """Tests for inspect_test_assertions MCP tool."""

    def test_returns_classified_assertions(self, temp_project, helpers):
        from mcp_tools.test_effectiveness_tools import register

        class FakeMCP:
            def tool(self):
                def decorator(func):
                    return func

                return decorator

        mcp = FakeMCP()
        tools = register(mcp, helpers)
        test_file = os.path.join(temp_project, "tests", "test_calculator.py")
        result_str = tools["inspect_test_assertions"](path=temp_project, test_file=test_file)
        result = json.loads(result_str)

        assert "test_functions" in result
        assert "summary" in result
        assert result["summary"]["total_tests"] >= 1

    def test_missing_file_returns_error(self, temp_project, helpers):
        from mcp_tools.test_effectiveness_tools import register

        class FakeMCP:
            def tool(self):
                def decorator(func):
                    return func

                return decorator

        mcp = FakeMCP()
        tools = register(mcp, helpers)
        result_str = tools["inspect_test_assertions"](path=temp_project, test_file="nonexistent.py")
        result = json.loads(result_str)
        assert "error" in result
