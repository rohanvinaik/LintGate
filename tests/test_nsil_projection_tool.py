"""Tests for NSIL projection tool."""

from unittest.mock import MagicMock

import pytest

from lintgate.nsil import projection
from lintgate.nsil.state_schema import InferenceStateSnapshot


def test_project_from_controlplane_stable():
    """Test projection from ControlPlane with stable state."""
    mock_mesh = MagicMock()
    mock_coherence = MagicMock()
    mock_coherence.state = "stable"
    mock_mesh.coherence = mock_coherence
    mock_mesh.channel_results = []

    snapshot = projection.project_from_controlplane(mock_mesh)
    assert snapshot.gate_status == "pass"
    assert snapshot.risk_level == "low"


def test_project_from_controlplane_systemic():
    """Test projection from ControlPlane with systemic failure."""
    mock_mesh = MagicMock()
    mock_coherence = MagicMock()
    mock_coherence.state = "systemic"
    mock_mesh.coherence = mock_coherence
    mock_mesh.channel_results = []

    snapshot = projection.project_from_controlplane(mock_mesh)
    assert snapshot.gate_status == "fail"
    assert snapshot.risk_level == "critical"


def test_project_from_controlplane_degraded():
    """Test projection from ControlPlane with degraded state."""
    mock_mesh = MagicMock()
    mock_coherence = MagicMock()
    mock_coherence.state = "degraded"
    mock_mesh.coherence = mock_coherence
    mock_mesh.channel_results = []

    snapshot = projection.project_from_controlplane(mock_mesh)
    assert snapshot.gate_status == "degraded"
    assert snapshot.risk_level == "high"


def test_project_from_controlplane_none():
    """Test projection with None returns default snapshot."""
    snapshot = projection.project_from_controlplane(None)
    assert snapshot.gate_status == "unknown"
    assert snapshot.risk_level == "unknown"
    assert snapshot.blocking_findings == []


def test_project_from_session_memory_none():
    """Test session projection with None returns default."""
    snapshot = projection.project_from_session_memory(None)
    assert snapshot.active_constraints == []
    assert snapshot.prediction_accuracy == 0.0


def test_project_from_mutation_state_none():
    """Test mutation projection with None returns default."""
    snapshot = projection.project_from_mutation_state(None)
    assert snapshot.mutation_summary == {}


def test_project_from_mutation_state_with_data():
    """Test mutation projection with state manager data."""
    mock_manager = MagicMock()
    mock_state = MagicMock()
    mock_state.killed = 8
    mock_state.total = 10
    mock_manager.state = {"func1": mock_state}

    snapshot = projection.project_from_mutation_state(mock_manager)
    assert snapshot.mutation_summary["total_functions"] == 1
    assert snapshot.mutation_summary["killed"] == 8
    assert snapshot.mutation_summary["survival_rate"] == 0.2


def test_load_and_project_returns_valid_structure():
    """Test load_and_project returns the expected structure."""
    # Run with mock project (no real state files) - will return defaults
    result = projection.load_and_project(path=".", format="json_flat", token_budget=500)

    # Verify required fields
    assert "snapshot" in result
    assert "format" in result
    assert "token_budget" in result
    assert "truncated_fields" in result
    assert "sources_used" in result
    assert result["format"] == "json_flat"
    assert result["token_budget"] == 500
    assert isinstance(result["sources_used"], dict)


def test_load_and_project_low_budget():
    """Test low budget still returns parseable output."""
    result = projection.load_and_project(path=".", format="json_flat", token_budget=50)

    # Must be parseable even under extreme budget
    assert "snapshot" in result
    assert isinstance(result["snapshot"], dict)


def test_nsil_state_snapshot_tool():
    """Test the MCP tool directly."""
    from mcp_tools.nsil_tools import nsil_state_snapshot

    # Run with mock project (no real state files)
    result = nsil_state_snapshot(path=".", format="json_flat", token_budget=120)

    # Verify response shape
    assert all(
        k in result
        for k in ["snapshot", "format", "token_budget", "truncated_fields", "sources_used"]
    )
    assert result["format"] == "json_flat"
    assert result["token_budget"] == 120
    assert isinstance(result["truncated_fields"], list)
    assert isinstance(result["sources_used"], dict)


def test_nsil_state_snapshot_json_flat():
    """Test JSON flat format output."""
    from mcp_tools.nsil_tools import nsil_state_snapshot

    result = nsil_state_snapshot(path=".", format="json_flat", token_budget=500)
    assert "snapshot" in result
    assert "gate_status" in result["snapshot"]
    assert "risk_level" in result["snapshot"]


def test_nsil_state_snapshot_structured_text():
    """Test structured_text format output."""
    from mcp_tools.nsil_tools import nsil_state_snapshot

    result = nsil_state_snapshot(path=".", format="structured_text", token_budget=500)
    assert "snapshot" in result


def test_nsil_state_snapshot_kv_pairs():
    """Test kv_pairs format output."""
    from mcp_tools.nsil_tools import nsil_state_snapshot

    result = nsil_state_snapshot(path=".", format="kv_pairs", token_budget=500)
    assert "snapshot" in result


def test_nsil_state_snapshot_invalid_format():
    """Test invalid format raises ValueError."""
    from mcp_tools.nsil_tools import nsil_state_snapshot

    with pytest.raises(ValueError):
        nsil_state_snapshot(path=".", format="invalid_format", token_budget=500)


def test_nsil_state_snapshot_zero_budget():
    """Test zero budget raises ValueError."""
    from mcp_tools.nsil_tools import nsil_state_snapshot

    with pytest.raises(ValueError):
        nsil_state_snapshot(path=".", format="json_flat", token_budget=0)


def test_merge_snapshots():
    """Test snapshot merging."""
    base = InferenceStateSnapshot(gate_status="pass", risk_level="low")
    update = InferenceStateSnapshot(
        gate_status="fail", risk_level="high", blocking_findings=["issue1"]
    )

    merged = projection._merge_snapshots(base, update)
    assert merged.gate_status == "fail"
    assert merged.risk_level == "high"
    assert merged.blocking_findings == ["issue1"]


def test_merge_snapshots_unknown_override():
    """Test that unknown values don't override known ones."""
    base = InferenceStateSnapshot(gate_status="pass", risk_level="low")
    update = InferenceStateSnapshot(gate_status="unknown", risk_level="unknown")

    merged = projection._merge_snapshots(base, update)
    assert merged.gate_status == "pass"
    assert merged.risk_level == "low"
