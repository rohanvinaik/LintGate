"""Tests for tool_applicability_guide in onboarding_tools.py."""

from __future__ import annotations

import json

from mcp_tools.onboarding_tools import register


class MockMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


def test_tool_applicability_guide_schema():
    mcp = MockMCP()
    helpers = {"_json_dumps": None}
    tools = register(mcp, helpers)
    guide_func = tools["tool_applicability_guide"]

    result_str = guide_func()
    guide = json.loads(result_str)

    core_tools = [
        "controlplane_run",
        "lint_files",
        "lint_project",
        "lint_fix",
        "scaffold_config",
        "getting_started",
    ]

    for tool in core_tools:
        assert tool in guide
        assert "cadence" in guide[tool]
        assert "triggers" in guide[tool]
        assert "anti_patterns" in guide[tool]
        assert "purpose" in guide[tool]
        assert isinstance(guide[tool]["triggers"], list)
        assert isinstance(guide[tool]["anti_patterns"], list)


def test_tool_applicability_guide_content():
    mcp = MockMCP()
    helpers = {"_json_dumps": None}
    tools = register(mcp, helpers)
    guide_func = tools["tool_applicability_guide"]

    result_str = guide_func()
    guide = json.loads(result_str)

    assert "Every 3-5 tool uses" in guide["controlplane_run"]["cadence"]
    assert "After every edit" in guide["lint_files"]["cadence"]
    assert "Onboarding only" in guide["getting_started"]["cadence"]
