"""Tests for model risk-based output_budget auto-selection."""

from __future__ import annotations

import json
from unittest.mock import patch

from lintgate.controlplane.model_profiles import (
    ModelProfile,
    recommend_output_budget,
)
from mcp_tools.controlplane_tools import register as register_cp


class MockMCP:
    def tool(self):
        def decorator(func):
            return func

        return decorator


def test_recommend_output_budget_logic():
    """Verify recommendation logic based on premature_action risk."""
    # Case 1: No profile -> standard
    assert recommend_output_budget(None) == "standard"

    # Case 2: Low confidence profile -> standard
    low_conf = ModelProfile(model_key="test-model", confidence=0.1)
    with patch("lintgate.controlplane.model_profiles.get_profile", return_value=low_conf):
        assert recommend_output_budget("test-model") == "standard"

    # Case 3: High risk profile -> minimal
    high_risk = ModelProfile(
        model_key="risky-model", confidence=1.0, signal_risk={"premature_action": 0.8}
    )
    with patch("lintgate.controlplane.model_profiles.get_profile", return_value=high_risk):
        assert recommend_output_budget("risky-model") == "minimal"

    # Case 4: Low risk profile -> standard
    low_risk = ModelProfile(
        model_key="safe-model", confidence=1.0, signal_risk={"premature_action": 0.2}
    )
    with patch("lintgate.controlplane.model_profiles.get_profile", return_value=low_risk):
        assert recommend_output_budget("safe-model") == "standard"


def test_controlplane_auto_select_budget():
    """Verify controlplane_run auto-selects budget when omitted."""
    mcp = MockMCP()
    helpers = {
        "_validate_project_root": lambda x: x,
        "_json_dumps": lambda x, mode=None: json.dumps(x),
        "_build_onboarding_status": lambda x: {"config_state": "config_enabled"},
    }

    # Mocking internal dependencies of _impl_controlplane_run to avoid full mesh run
    with patch("mcp_tools.controlplane_tools._impl_controlplane_run") as mock_impl:
        mock_impl.return_value = "{}"

        tools = register_cp(mcp, helpers)
        cp_run_func = tools["controlplane_run"]

        # Scenario: High risk model detected via env
        high_risk = ModelProfile(
            model_key="anthropic:claude-3-haiku",
            confidence=1.0,
            signal_risk={"premature_action": 0.9},
        )

        with (
            patch(
                "lintgate.hook_controlplane.resolve_event_model_key",
                return_value="anthropic:claude-3-haiku",
            ),
            patch("lintgate.controlplane.model_profiles.get_profile", return_value=high_risk),
        ):
            cp_run_func(path="/tmp", output_budget=None)

            # Check that _impl_controlplane_run was called with "minimal"
            _, args, _ = mock_impl.mock_calls[0]
            assert args[6] == "minimal"  # output_budget is the 7th arg (index 6)
