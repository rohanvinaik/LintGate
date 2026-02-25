from unittest.mock import patch

import pytest

from lintgate.channels.mutation_channel import MutationChannel
from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent
from lintgate.mutation.state import CoverageDepth, FunctionMutationState, SignalQuality


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
    # Include signal_quality=PROFILED for expected blocking behavior
    mock_state = FunctionMutationState(
        file_path="logic.py",
        function_name="add",
        code_hash="abc",
        test_hash="def",
        total=10,
        killed=3,
        survived=7,
        depth=CoverageDepth.PROFILED,
        signal_quality=SignalQuality.PROFILED,
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
    # Use sampled_high to test warning behavior (vs sampled_low which is advisory only)
    mock_state = FunctionMutationState(
        file_path="logic.py",
        function_name="add",
        code_hash="abc",
        test_hash="def",
        total=5,
        killed=3,
        survived=2,
        depth=CoverageDepth.SAMPLED,
        signal_quality=SignalQuality.SAMPLED_HIGH,
    )

    with patch("lintgate.channels.mutation_channel.MutationStateManager") as mock_sm_cls:
        mock_sm = mock_sm_cls.return_value
        mock_sm.state = {"logic.py::add": mock_state}
        mock_sm.requires_run.return_value = False

        result = channel.execute(event, config)

    kinds = [f.kind for f in result.findings]
    assert "MUT003" in kinds
    # With sampled_high at 40% survival, should be warning
    assert result.severity == "warning"


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


def test_mutation_channel_mutch007_with_survivor_sites(channel, tmp_path):
    """Test that MUTCH007 includes decomposition axes when survivor_sites are present."""
    from lintgate.mutation.state import SurvivorSite

    config = ControlPlaneConfig()
    event = SupervisionEvent(project_root=str(tmp_path), files_changed=["logic.py"])

    # Mock state with survivor_sites that should trigger decomposition axes
    mock_state = FunctionMutationState(
        file_path="logic.py",
        function_name="complex_func",
        code_hash="abc",
        test_hash="def",
        total=10,
        killed=3,
        survived=7,
        survived_by_category={"arithmetic": 3, "conditional": 2, "string": 2},
        depth=CoverageDepth.PROFILED,
        survivor_sites=[
            SurvivorSite(
                line=10, column=1, category="arithmetic", mutant_id="mut_1", operator="add"
            ),
            SurvivorSite(
                line=12, column=1, category="arithmetic", mutant_id="mut_2", operator="sub"
            ),
            SurvivorSite(
                line=14, column=1, category="arithmetic", mutant_id="mut_3", operator="mult"
            ),
            SurvivorSite(
                line=25, column=1, category="conditional", mutant_id="mut_4", operator="conditional"
            ),
            SurvivorSite(
                line=26, column=1, category="conditional", mutant_id="mut_5", operator="conditional"
            ),
        ],
    )

    with patch("lintgate.channels.mutation_channel.MutationStateManager") as mock_sm_cls:
        mock_sm = mock_sm_cls.return_value
        mock_sm.state = {"logic.py::complex_func": mock_state}
        mock_sm.requires_run.return_value = False

        result = channel.execute(event, config)

    kinds = [f.kind for f in result.findings]
    assert "MUTCH007" in kinds

    # Verify decomposition axes are included in evidence
    mutch007_finding = next((f for f in result.findings if f.kind == "MUTCH007"), None)
    assert mutch007_finding is not None
    # Should have decomposition_axes when survivor_sites are present
    assert "decomposition_axes" in mutch007_finding.evidence
    assert isinstance(mutch007_finding.evidence["decomposition_axes"], list)


def test_mutation_channel_signal_quality_profiled_is_authoritative(channel, tmp_path):
    """Test that profiled signal quality allows blocking severity."""
    config = ControlPlaneConfig()
    event = SupervisionEvent(project_root=str(tmp_path), files_changed=["logic.py"])

    mock_state = FunctionMutationState(
        file_path="logic.py",
        function_name="add",
        code_hash="abc",
        test_hash="def",
        total=10,
        killed=1,
        survived=9,  # 90% survival
        depth=CoverageDepth.PROFILED,
        signal_quality=SignalQuality.PROFILED,
    )

    with patch("lintgate.channels.mutation_channel.MutationStateManager") as mock_sm_cls:
        mock_sm = mock_sm_cls.return_value
        mock_sm.state = {"logic.py::add": mock_state}
        mock_sm.requires_run.return_value = False

        result = channel.execute(event, config)

    # Profiled should be blocking
    assert result.severity == "blocking"
    assert result.status == "fail"


def test_mutation_channel_signal_quality_sampled_high_warning(channel, tmp_path):
    """Test that sampled_high downgrades blocking to warning."""
    config = ControlPlaneConfig()
    event = SupervisionEvent(project_root=str(tmp_path), files_changed=["logic.py"])

    mock_state = FunctionMutationState(
        file_path="logic.py",
        function_name="add",
        code_hash="abc",
        test_hash="def",
        total=10,
        killed=1,
        survived=9,  # 90% survival
        depth=CoverageDepth.SAMPLED,
        signal_quality=SignalQuality.SAMPLED_HIGH,
    )

    with patch("lintgate.channels.mutation_channel.MutationStateManager") as mock_sm_cls:
        mock_sm = mock_sm_cls.return_value
        mock_sm.state = {"logic.py::add": mock_state}
        mock_sm.requires_run.return_value = False

        result = channel.execute(event, config)

    # sampled_high should be warning (not blocking)
    assert result.severity == "warning"
    # Should include profiling recommendation
    finding = result.findings[0]
    assert "mutation_run_full" in finding.suggestions[-1]


def test_mutation_channel_signal_quality_sampled_low_advisory(channel, tmp_path):
    """Test that sampled_low is advisory only (never blocking)."""
    config = ControlPlaneConfig()
    event = SupervisionEvent(project_root=str(tmp_path), files_changed=["logic.py"])

    mock_state = FunctionMutationState(
        file_path="logic.py",
        function_name="add",
        code_hash="abc",
        test_hash="def",
        total=10,
        killed=1,
        survived=9,  # 90% survival - would be blocking for profiled
        depth=CoverageDepth.SAMPLED,
        signal_quality=SignalQuality.SAMPLED_LOW,
    )

    with patch("lintgate.channels.mutation_channel.MutationStateManager") as mock_sm_cls:
        mock_sm = mock_sm_cls.return_value
        mock_sm.state = {"logic.py::add": mock_state}
        mock_sm.requires_run.return_value = False

        result = channel.execute(event, config)

    # sampled_low should be informational (not warning or blocking)
    assert result.severity == "informational"
    # Message should include suppression note
    finding = result.findings[0]
    assert "Gating suppressed by policy" in finding.message
    assert "signal quality is sampled_low" in finding.message


def test_mutation_channel_signal_quality_in_evidence(channel, tmp_path):
    """Test that signal_quality is included in evidence."""
    config = ControlPlaneConfig()
    event = SupervisionEvent(project_root=str(tmp_path), files_changed=["logic.py"])

    mock_state = FunctionMutationState(
        file_path="logic.py",
        function_name="add",
        code_hash="abc",
        test_hash="def",
        total=10,
        killed=3,
        survived=7,
        depth=CoverageDepth.PROFILED,
        signal_quality=SignalQuality.PROFILED,
    )

    with patch("lintgate.channels.mutation_channel.MutationStateManager") as mock_sm_cls:
        mock_sm = mock_sm_cls.return_value
        mock_sm.state = {"logic.py::add": mock_state}
        mock_sm.requires_run.return_value = False

        result = channel.execute(event, config)

    # Check evidence includes signal_quality
    finding = result.findings[0]
    assert "signal_quality" in finding.evidence
    assert finding.evidence["signal_quality"] == "profiled"
