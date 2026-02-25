"""Tests for remediation guidance integration in detail tools."""

from __future__ import annotations

import json
from unittest.mock import patch

from mcp_tools.controlplane_tools import register as register_cp
from mcp_tools.lint_tools import register as register_lint


class MockMCP:
    def tool(self):
        def decorator(func):
            return func

        return decorator


def test_lint_get_details_includes_remediation():
    """Verify that lint_get_details attaches next_action to issues."""
    mock_details = {
        "project": "/tmp/test",
        "blocking_issues": [{"kind": "COMPLEXITY", "file": "app.py", "severity": "blocking"}],
    }

    mcp = MockMCP()
    helpers = {"_json_dumps": lambda x, mode: json.dumps(x)}
    tools = register_lint(mcp, helpers)
    lint_get_details_func = tools["lint_get_details"]

    with patch("lintgate.state.load_run_details", return_value=mock_details):
        result_str = lint_get_details_func(run_id="run_123")
        result = json.loads(result_str)

        issues = result["issues"]
        assert len(issues) == 1
        assert "next_action" in issues[0]
        assert issues[0]["next_action"]["tool"] == "lint_files"


def test_controlplane_get_details_includes_remediation():
    """Verify that controlplane_get_details attaches next_action to findings."""
    mock_details = {
        "channels": {
            "lint": {
                "findings": [{"kind": "security_bandit", "file": "main.py", "severity": "blocking"}]
            }
        }
    }

    mcp = MockMCP()
    helpers = {"_json_dumps": lambda x: json.dumps(x)}
    tools = register_cp(mcp, helpers)
    cp_get_details_func = tools["controlplane_get_details"]

    with patch("lintgate.state.load_controlplane_run", return_value=mock_details):
        result_str = cp_get_details_func(run_id="cp_123", channel="lint", sections=["findings"])
        result = json.loads(result_str)

        findings = result["findings"]
        assert len(findings) == 1
        assert "next_action" in findings[0]
        assert findings[0]["next_action"]["tool"] == "controlplane_run"
        assert findings[0]["next_action"]["args"]["strictness"] == "strict"
