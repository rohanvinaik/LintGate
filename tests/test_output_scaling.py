"""Tests for output_budget scaling in analysis tools."""

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


def test_lint_get_details_budget_scaling():
    """Verify lint_get_details respects output_budget."""
    mock_details = {
        "project": "/tmp/test",
        "blocking_issues": [
            {
                "kind": "COMPLEXITY",
                "file": "app.py",
                "severity": "blocking",
                "message": "Function is too complex\nMore details here",
                "hint": "Refactor it",
                "evidence": {"complexity": 15},
            }
        ],
    }

    mcp = MockMCP()
    helpers = {"_json_dumps": lambda x, mode: json.dumps(x)}
    tools = register_lint(mcp, helpers)
    lint_get_details_func = tools["lint_get_details"]

    with patch("lintgate.state.load_run_details", return_value=mock_details):
        # Case 1: Standard (Default)
        res_std = json.loads(lint_get_details_func(run_id="run_123", output_budget="standard"))
        issue_std = res_std["issues"][0]
        assert "Function is too complex\nMore details here" in issue_std["message"]
        assert "hint" in issue_std
        assert "evidence" in issue_std
        assert res_std["output_metadata"]["output_budget"] == "standard"

        # Case 2: Minimal
        res_min = json.loads(lint_get_details_func(run_id="run_123", output_budget="minimal"))
        issue_min = res_min["issues"][0]
        assert "Function is too complex" in issue_min["message"]
        assert "\n" not in issue_min["message"]
        assert "hint" not in issue_min
        assert "evidence" not in issue_min
        assert res_min["output_metadata"]["output_budget"] == "minimal"


def test_controlplane_get_details_budget_scaling():
    """Verify controlplane_get_details respects output_budget."""
    mock_details = {
        "channels": {
            "lint": {
                "metrics": {"violations": 1},
                "findings": [
                    {
                        "kind": "security_bandit",
                        "file": "main.py",
                        "severity": "blocking",
                        "message": "Security risk found\nInsecure call to subprocess",
                        "evidence": {"vulnerability": "shell=True"},
                    }
                ],
            }
        }
    }

    mcp = MockMCP()
    helpers = {"_json_dumps": lambda x: json.dumps(x)}
    tools = register_cp(mcp, helpers)
    cp_get_details_func = tools["controlplane_get_details"]

    with patch("lintgate.state.load_controlplane_run", return_value=mock_details):
        # Case 1: Minimal
        res_min = json.loads(
            cp_get_details_func(
                run_id="cp_123",
                channel="lint",
                sections=["findings", "evidence"],
                output_budget="minimal",
            )
        )
        finding_min = res_min["findings"][0]
        assert "Security risk found" in finding_min["message"]
        assert "\n" not in finding_min["message"]
        assert "evidence" not in res_min  # sections=["evidence"] should be ignored in minimal
        assert "evidence" not in finding_min
        assert res_min["output_metadata"]["output_budget"] == "minimal"

        # Case 2: Standard
        res_std = json.loads(
            cp_get_details_func(
                run_id="cp_123",
                channel="lint",
                sections=["findings", "evidence"],
                output_budget="standard",
            )
        )
        assert "evidence" in res_std
        assert res_std["output_metadata"]["output_budget"] == "standard"
