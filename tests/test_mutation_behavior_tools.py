"""Mutation gap tests for mcp_tools/behavior_tools.py.

Targets:
- register — BOUNDARY + SWAP + VALUE survivors
  Tests the tool registration function and its returned dict structure.
  Verifies the inner @mcp.tool() handlers delegate correctly to impl functions.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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


# ── hygiene_check delegation ─────────────────────────────────────────────


@patch("mcp_tools.behavior_tools.impl_hygiene_check")
def test_hygiene_check_delegates_to_impl(mock_impl: MagicMock) -> None:
    from mcp_tools.behavior_tools import register

    mcp = MagicMock()
    mcp.tool.return_value = lambda fn: fn
    helpers = {"key": "value"}

    tools = register(mcp, helpers)
    mock_impl.return_value = '{"status": "ok"}'

    result = tools["hygiene_check"](path="/project", planned_action="pip install x")
    mock_impl.assert_called_once_with(helpers, "/project", "pip install x")
    assert result == '{"status": "ok"}'


# ── constraint_check delegation ──────────────────────────────────────────


@patch("mcp_tools.behavior_tools.impl_constraint_check")
def test_constraint_check_delegates_to_impl(mock_impl: MagicMock) -> None:
    from mcp_tools.behavior_tools import register

    mcp = MagicMock()
    mcp.tool.return_value = lambda fn: fn
    helpers = {"key": "value"}

    tools = register(mcp, helpers)
    mock_impl.return_value = '{"coverage_gap": 0}'

    result = tools["constraint_check"](
        path="/project",
        planned_action="run tests",
        known_constraints=["fixture needed"],
    )
    mock_impl.assert_called_once_with(
        helpers, "/project", "run tests", ["fixture needed"]
    )
    assert result == '{"coverage_gap": 0}'


@patch("mcp_tools.behavior_tools.impl_constraint_check")
def test_constraint_check_none_constraints(mock_impl: MagicMock) -> None:
    from mcp_tools.behavior_tools import register

    mcp = MagicMock()
    mcp.tool.return_value = lambda fn: fn
    helpers = {}

    tools = register(mcp, helpers)
    mock_impl.return_value = "{}"

    tools["constraint_check"](
        path="/project",
        planned_action="edit file",
        known_constraints=None,
    )
    mock_impl.assert_called_once_with(helpers, "/project", "edit file", None)


# ── prediction_register delegation ───────────────────────────────────────


@patch("mcp_tools.behavior_tools.impl_prediction_register")
def test_prediction_register_delegates_to_impl(mock_impl: MagicMock) -> None:
    from mcp_tools.behavior_tools import register

    mcp = MagicMock()
    mcp.tool.return_value = lambda fn: fn
    helpers = {}

    tools = register(mcp, helpers)
    mock_impl.return_value = '{"registered": true}'

    result = tools["prediction_register"](
        path="/project",
        planned_action="pytest tests/",
        prediction="Tests pass",
        prediction_type="exit_code",
        prediction_value=0,
    )
    mock_impl.assert_called_once_with(
        helpers, "/project", "pytest tests/", "Tests pass", "exit_code", 0
    )
    assert result == '{"registered": true}'


@patch("mcp_tools.behavior_tools.impl_prediction_register")
def test_prediction_register_string_value(mock_impl: MagicMock) -> None:
    from mcp_tools.behavior_tools import register

    mcp = MagicMock()
    mcp.tool.return_value = lambda fn: fn
    helpers = {}

    tools = register(mcp, helpers)
    mock_impl.return_value = "{}"

    tools["prediction_register"](
        path="/project",
        planned_action="grep for pattern",
        prediction="Pattern found",
        prediction_type="stdout_contains",
        prediction_value="match_text",
    )
    mock_impl.assert_called_once_with(
        helpers, "/project", "grep for pattern", "Pattern found",
        "stdout_contains", "match_text",
    )


# ── global_memory_status delegation ──────────────────────────────────────


@patch("mcp_tools.behavior_tools.impl_global_memory_status")
def test_global_memory_status_delegates_to_impl(mock_impl: MagicMock) -> None:
    from mcp_tools.behavior_tools import register

    mcp = MagicMock()
    mcp.tool.return_value = lambda fn: fn
    helpers = {}

    tools = register(mcp, helpers)
    mock_impl.return_value = '{"sessions": 5}'

    result = tools["global_memory_status"](path="/project")
    mock_impl.assert_called_once_with(helpers, "/project")
    assert result == '{"sessions": 5}'


# ── global_memory_reset delegation ───────────────────────────────────────


@patch("mcp_tools.behavior_tools.impl_global_memory_reset")
def test_global_memory_reset_delegates_to_impl(mock_impl: MagicMock) -> None:
    from mcp_tools.behavior_tools import register

    mcp = MagicMock()
    mcp.tool.return_value = lambda fn: fn
    helpers = {}

    tools = register(mcp, helpers)
    mock_impl.return_value = '{"reset": true}'

    result = tools["global_memory_reset"](path="/project")
    mock_impl.assert_called_once_with(helpers, "/project")
    assert result == '{"reset": true}'


# ── behavior_precheck delegation ─────────────────────────────────────────


@patch("mcp_tools.behavior_tools.impl_behavior_precheck")
def test_behavior_precheck_delegates_to_impl(mock_impl: MagicMock) -> None:
    from mcp_tools.behavior_tools import register

    mcp = MagicMock()
    mcp.tool.return_value = lambda fn: fn
    helpers = {}

    tools = register(mcp, helpers)
    mock_impl.return_value = '{"deprecated": true}'

    result = tools["behavior_precheck"](
        path="/project",
        planned_action="run tests",
        known_constraints=["constraint1"],
        prediction="will pass",
        prediction_type="exit_code",
        prediction_value=0,
    )
    # The impl receives helpers, the internal _tools dict, and all args
    assert mock_impl.call_count == 1
    call_args = mock_impl.call_args
    # First two positional args are helpers and _tools dict
    assert call_args[0][0] == helpers
    assert isinstance(call_args[0][1], dict)  # _tools dict
    assert set(call_args[0][1].keys()) == {
        "constraint_check", "prediction_register", "hygiene_check"
    }
    assert call_args[0][2] == "/project"
    assert call_args[0][3] == "run tests"
    assert result == '{"deprecated": true}'
