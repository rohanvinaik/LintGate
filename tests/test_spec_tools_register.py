"""Tests for mcp_tools/specification_tools.py — register function."""

from __future__ import annotations

from unittest.mock import MagicMock

from mcp_tools.specification_tools import register


class TestRegister:
    def test_returns_seven_tools(self):
        mcp = MagicMock()
        mcp.tool.return_value = lambda fn: fn
        helpers = {
            "_validate_project_root": lambda p: p,
            "_json_dumps": lambda obj, **kw: str(obj),
        }
        result = register(mcp, helpers)
        assert isinstance(result, dict)
        assert len(result) == 7

    def test_expected_tool_names(self):
        mcp = MagicMock()
        mcp.tool.return_value = lambda fn: fn
        helpers = {
            "_validate_project_root": lambda p: p,
            "_json_dumps": lambda obj, **kw: str(obj),
        }
        result = register(mcp, helpers)
        expected_names = {
            "spec_analyze",
            "spec_prescribe",
            "spec_composition",
            "spec_gate_check",
            "spec_file_analyze",
            "spec_file_prescribe",
            "spec_project_rollup",
        }
        assert set(result.keys()) == expected_names

    def test_tools_are_callable(self):
        mcp = MagicMock()
        mcp.tool.return_value = lambda fn: fn
        helpers = {
            "_validate_project_root": lambda p: p,
            "_json_dumps": lambda obj, **kw: str(obj),
        }
        result = register(mcp, helpers)
        for name, tool in result.items():
            assert callable(tool), f"{name} is not callable"

    def test_mcp_tool_decorator_called(self):
        mcp = MagicMock()
        mcp.tool.return_value = lambda fn: fn
        helpers = {
            "_validate_project_root": lambda p: p,
            "_json_dumps": lambda obj, **kw: str(obj),
        }
        register(mcp, helpers)
        assert mcp.tool.call_count == 7
