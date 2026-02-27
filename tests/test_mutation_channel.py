from unittest.mock import patch

import pytest

from lintgate.channels.mutation_channel import MutationChannel
from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent
from lintgate.mutation.state import CoverageDepth, FunctionMutationState


@pytest.fixture
def channel():
    return MutationChannel()


def test_mutation_channel_metadata(channel):
    assert channel.name == "mutation"
    assert channel.blocking_capable is True
    assert channel.timeout_ms == 15000


def test_mutation_channel_should_run(channel):
    config = ControlPlaneConfig()
    event_no_root = SupervisionEvent(project_root="")
    event_with_root = SupervisionEvent(project_root="/tmp")

    assert channel.should_run(event_no_root, config) is False
    assert channel.should_run(event_with_root, config) is True


def test_mutation_channel_execute_with_survivors(channel, tmp_path):
    config = ControlPlaneConfig()
    event = SupervisionEvent(project_root=str(tmp_path), files_changed=["logic.py"])

    # Mock state manager and engine
    mock_state = FunctionMutationState(
        file_path="logic.py",
        function_name="add",
        code_hash="abc",
        test_hash="def",
        total=10,
        killed=3,
        survived=7,
        depth=CoverageDepth.PROFILED,
    )

    with patch("lintgate.channels.mutation_channel.MutationStateManager") as mock_sm_cls:
        mock_sm = mock_sm_cls.return_value
        mock_sm.state = {"logic.py::add": mock_state}
        mock_sm.requires_run.return_value = False

        result = channel.execute(event, config)

    assert result.status == "fail"
    assert result.severity == "blocking"
    assert len(result.findings) >= 1
    assert result.findings[0].kind == "MUT002"
    assert "70.0%" in result.findings[0].message


def test_mutation_channel_execute_low_survival(channel, tmp_path):
    config = ControlPlaneConfig()
    event = SupervisionEvent(project_root=str(tmp_path), files_changed=["logic.py"])

    # Mock state manager and engine
    mock_state = FunctionMutationState(
        file_path="logic.py",
        function_name="add",
        code_hash="abc",
        test_hash="def",
        total=10,
        killed=9,
        survived=1,
        depth=CoverageDepth.PROFILED,
    )

    with patch("lintgate.channels.mutation_channel.MutationStateManager") as mock_sm_cls:
        mock_sm = mock_sm_cls.return_value
        mock_sm.state = {"logic.py::add": mock_state}
        mock_sm.requires_run.return_value = False

        result = channel.execute(event, config)

    assert result.status == "pass"
    assert result.severity == "informational"
    assert result.findings[0].kind == "MUT001"


def test_mutation_channel_insufficient_depth(channel, tmp_path):
    config = ControlPlaneConfig()
    event = SupervisionEvent(project_root=str(tmp_path), files_changed=["logic.py"])

    # Mock state manager with SAMPLED depth but survivors
    mock_state = FunctionMutationState(
        file_path="logic.py",
        function_name="add",
        code_hash="abc",
        test_hash="def",
        total=5,
        killed=3,
        survived=2,
        depth=CoverageDepth.SAMPLED,
    )

    with patch("lintgate.channels.mutation_channel.MutationStateManager") as mock_sm_cls:
        mock_sm = mock_sm_cls.return_value
        mock_sm.state = {"logic.py::add": mock_state}
        mock_sm.requires_run.return_value = False

        result = channel.execute(event, config)

    kinds = [f.kind for f in result.findings]
    assert "MUT003" in kinds
    assert (
        result.severity == "warning"
        if "MUT001" in kinds and mock_state.survival_rate > 0.3
        else "informational"
    )
    # Actually MUT001 for 2/5 (40%) survival is a warning


def test_mutation_channel_mutch007(channel, tmp_path):
    config = ControlPlaneConfig()
    event = SupervisionEvent(project_root=str(tmp_path), files_changed=["logic.py"])

    # Mock state manager with high entanglement (3+ categories, >50% survival)
    mock_state = FunctionMutationState(
        file_path="logic.py",
        function_name="add",
        code_hash="abc",
        test_hash="def",
        total=10,
        killed=4,
        survived=6,
        survived_by_category={"arithmetic": 2, "conditional": 2, "string": 2},
        depth=CoverageDepth.PROFILED,
    )

    with patch("lintgate.channels.mutation_channel.MutationStateManager") as mock_sm_cls:
        mock_sm = mock_sm_cls.return_value
        mock_sm.state = {"logic.py::add": mock_state}
        mock_sm.requires_run.return_value = False

        result = channel.execute(event, config)

    kinds = [f.kind for f in result.findings]
    assert "MUTCH007" in kinds

    # Verify suggestions mention decomposition
    mutch007_finding = next((f for f in result.findings if f.kind == "MUTCH007"), None)
    assert mutch007_finding is not None
    assert any(
        "mutation_decompose" in block
        for str_list in mutch007_finding.suggestions
        for block in [str_list]
    )
