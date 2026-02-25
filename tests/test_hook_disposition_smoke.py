"""Smoke test for DispositionEnforcer integration in hook_posttooluse."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from lintgate.controlplane.types import (
    ControlPlaneConfig,
    DispositionEnforcementConfig,
)
from lintgate.hook_posttooluse import _run_controlplane


def test_hook_injects_disposition_nudge():
    """Verify that _run_controlplane injects enforcer nudges into advisory."""
    input_data = {"tool_name": "bash", "tool_input": "ls", "tool_output": "file.py"}
    config = MagicMock()
    cp_config = ControlPlaneConfig(
        enabled=True, disposition_enforcement=DispositionEnforcementConfig(enabled=True)
    )
    cwd = "/test"

    # Mock session and advisory from setup
    mock_session = MagicMock()
    mock_session.behavior_compass = {
        "enforcement": {
            "flags": {"needs_lint": True},
            "rules": {},
            "counters": {"events": 0},
        }
    }

    with (
        patch(
            "lintgate.hook_controlplane.setup_session_and_gate",
            return_value=(mock_session, "[Existing Advisory]"),
        ),
        patch("lintgate.controlplane.runtime.run_mesh"),
        patch(
            "lintgate.controlplane.reporter.format_mesh_report",
            return_value={"systemMessage": "Mesh Result"},
        ),
        patch(
            "lintgate.hook_posttooluse._finalize_report",
            side_effect=lambda r, adv, s, c: (
                {
                    "systemMessage": (adv + "\n\n" + r["systemMessage"])
                    if adv
                    else r["systemMessage"]
                },
                {},
            ),
        ),
        patch("builtins.print") as mock_print,
        patch("sys.exit"),
    ):
        _run_controlplane(input_data, config, cp_config, cwd, start=0.0)

        # Verify print was called with combined advisory
        assert mock_print.called
        output_json = json.loads(mock_print.call_args[0][0])
        system_msg = output_json.get("systemMessage", "")

        assert "[Existing Advisory]" in system_msg
        assert "PROTIP: You've made several edits without running validation" in system_msg
        assert "Mesh Result" in system_msg
