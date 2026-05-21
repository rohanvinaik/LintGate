"""Mutation gap tests for mcp_tools/behavior_tools.py.

Targets:
- register — BOUNDARY + SWAP + VALUE survivors
  Tests the tool registration function and its returned dict structure.

Note: Post-Phase-2a, behavior_tools.py is a thin subprocess wrapper around
scripts/behavior_check.py. The former impl_* delegation pattern is gone, so
the delegation-verification tests that lived here were removed. Subprocess
argv assembly is covered in tests/test_mcp_behavior_tools.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

# ── register — VALUE assertions on returned tool dict ────────────────────


def test_register_returns_dict_with_all_tool_names() -> None:
    from mcp_tools.behavior_tools import register

    mcp = MagicMock()
    mcp.tool.return_value = lambda fn: fn
    helpers = MagicMock()

    result = register(mcp, helpers)
    assert isinstance(result, dict)
    expected_keys = {
        "hygiene_check",
        "constraint_check",
        "prediction_register",
        "behavior_precheck",
        "global_memory_status",
        "global_memory_reset",
    }
    assert set(result.keys()) == expected_keys


def test_register_returns_exactly_6_tools() -> None:
    from mcp_tools.behavior_tools import register

    mcp = MagicMock()
    mcp.tool.return_value = lambda fn: fn
    helpers = MagicMock()

    result = register(mcp, helpers)
    assert len(result) == 6


def test_register_calls_mcp_tool_decorator_6_times() -> None:
    from mcp_tools.behavior_tools import register

    mcp = MagicMock()
    mcp.tool.return_value = lambda fn: fn
    helpers = MagicMock()

    register(mcp, helpers)
    assert mcp.tool.call_count == 6


def test_register_returned_tools_are_callable() -> None:
    from mcp_tools.behavior_tools import register

    mcp = MagicMock()
    mcp.tool.return_value = lambda fn: fn
    helpers = MagicMock()

    result = register(mcp, helpers)
    for name, tool_fn in result.items():
        assert callable(tool_fn), f"{name} should be callable"
