from unittest.mock import MagicMock, patch

from lintgate.hook_controlplane import deliver_behavioral_findings


def test_deliver_behavioral_findings_claude_code():
    findings = [{"kind": "test_finding", "message": "High alert", "severity": "blocking"}]
    session = MagicMock()
    session.behavior_compass = {}

    with patch("lintgate.renderers.build_default_registry") as mock_registry_build:
        mock_registry = MagicMock()
        mock_registry_build.return_value = mock_registry

        # Mock host detection to return Claude Code
        mock_registry.detect_runtime_hosts.return_value = ["claude_code"]
        mock_adapter = MagicMock()
        mock_adapter.capabilities.preferred_delivery_channels = ["hook_text"]
        mock_adapter.capabilities.supports_intervention = True
        mock_registry.get_adapter.return_value = mock_adapter

        advisory, pending = deliver_behavioral_findings(session, findings, MagicMock(), "/tmp")

        assert advisory is not None
        assert "🛑 Behavioral block: High alert" in advisory
        assert (
            "[lintgate metadata | authority: blocking | channel: hook_text | reliability: 1.0]"
            in advisory
        )
        assert len(pending) == 0


def test_deliver_behavioral_findings_mcp_only():
    findings = [{"kind": "test_finding", "message": "Nudge alert", "severity": "nudge"}]
    session = MagicMock()
    session.behavior_compass = {}

    with patch("lintgate.renderers.build_default_registry") as mock_registry_build:
        mock_registry = MagicMock()
        mock_registry_build.return_value = mock_registry

        # Mock host detection to return MCP only
        mock_registry.detect_runtime_hosts.return_value = ["mcp_only"]
        mock_adapter = MagicMock()
        mock_adapter.capabilities.preferred_delivery_channels = ["mcp_status"]
        mock_adapter.capabilities.supports_intervention = False
        mock_registry.get_adapter.return_value = mock_adapter

        advisory, pending = deliver_behavioral_findings(session, findings, MagicMock(), "/tmp")

        assert advisory is None
        assert len(pending) == 1
        assert pending[0]["kind"] == "test_finding"
        assert session.behavior_compass["pending_behavioral_findings"] == pending


def test_runtime_state_population_with_pending_findings():
    from lintgate.runtime_state import build_runtime_state

    session = MagicMock()
    session.behavior_compass = {"pending_behavioral_findings": [{"kind": "foo", "message": "bar"}]}

    with patch("lintgate.runtime_state.load_runtime_state", return_value=None):
        state = build_runtime_state("/tmp", session=session)
        assert len(state.pending_behavioral_findings) == 1
        assert state.pending_behavioral_findings[0]["kind"] == "foo"


def test_micro_refresh_attaches_behavior_status():
    from mcp_tools.micro_refresh import attach_session_context

    runtime = MagicMock()
    runtime.pending_behavioral_findings = [{"message": "Slow down"}]
    runtime.generation = 1
    runtime.mode = "normal"
    runtime.active_files = []
    runtime.blocking_issues = 0
    runtime.coherence_state = "stable"
    runtime.last_test_status = ""
    runtime.estimated_tokens_pct = 10.0

    with patch("lintgate.runtime_state.load_runtime_state", return_value=runtime):
        result = attach_session_context({}, "/tmp")
        assert "behavior_status" in result
        assert result["behavior_status"]["pending_count"] == 1
        assert result["behavior_status"]["hint"] == "Slow down"
