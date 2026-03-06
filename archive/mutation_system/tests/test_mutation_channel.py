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

    with patch(
        "lintgate.channels.mutation_channel.MutationStateManager"
    ) as mock_sm_cls:
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

    with patch(
        "lintgate.channels.mutation_channel.MutationStateManager"
    ) as mock_sm_cls:
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

    with patch(
        "lintgate.channels.mutation_channel.MutationStateManager"
    ) as mock_sm_cls:
        mock_sm = mock_sm_cls.return_value
        mock_sm.state = {"logic.py::add": mock_state}
        mock_sm.requires_run.return_value = False

        result = channel.execute(event, config)

    kinds = [f.kind for f in result.findings]
    assert "MUT003" in kinds
    # #208: Sampled-depth severity cap — all findings from SAMPLED depth are informational
    assert result.severity == "informational"
    # MUT001 should exist but be capped at informational
    mut001 = next((f for f in result.findings if f.kind == "MUT001"), None)
    assert mut001 is not None
    assert mut001.severity == "informational"
    assert mut001.evidence["is_gateable"] is False


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

    with patch(
        "lintgate.channels.mutation_channel.MutationStateManager"
    ) as mock_sm_cls:
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


# --- #208: Coverage depth modulation tests ---


class TestIsGateable:
    """Verify is_gateable property on FunctionMutationState."""

    def test_profiled_is_gateable(self):
        state = FunctionMutationState(
            function_name="f",
            file_path="a.py",
            code_hash="h",
            test_hash="t",
            depth=CoverageDepth.PROFILED,
        )
        assert state.is_gateable is True

    def test_sampled_not_gateable(self):
        state = FunctionMutationState(
            function_name="f",
            file_path="a.py",
            code_hash="h",
            test_hash="t",
            depth=CoverageDepth.SAMPLED,
        )
        assert state.is_gateable is False

    def test_none_not_gateable(self):
        state = FunctionMutationState(
            function_name="f",
            file_path="a.py",
            code_hash="h",
            test_hash="t",
            depth=CoverageDepth.NONE,
        )
        assert state.is_gateable is False


class TestMUTCH005StaleData:
    """MUTCH005: stale mutation data when code_hash mismatches current file hash."""

    def test_mutch005_emitted_on_hash_mismatch(self, tmp_path):
        channel = MutationChannel()
        # Create a real file so file hash computation works
        (tmp_path / "logic.py").write_text("def add(a, b): return a + b\n")

        mock_state = FunctionMutationState(
            file_path="logic.py",
            function_name="add",
            code_hash="old_hash_abc",
            test_hash="t",
            depth=CoverageDepth.PROFILED,
            total=5,
            killed=5,
            survived=0,
        )

        config = ControlPlaneConfig()
        event = SupervisionEvent(project_root=str(tmp_path), files_changed=["logic.py"])

        with patch(
            "lintgate.channels.mutation_channel.MutationStateManager"
        ) as mock_sm_cls:
            mock_sm = mock_sm_cls.return_value
            mock_sm.state = {"logic.py::add": mock_state}
            mock_sm.requires_run.return_value = False
            result = channel.execute(event, config)

        mutch005 = [f for f in result.findings if f.kind == "MUTCH005"]
        assert len(mutch005) == 1
        assert "stale" in mutch005[0].message.lower()
        assert mutch005[0].evidence["is_gateable"] is False
        assert mutch005[0].severity == "informational"

    def test_mutch005_not_emitted_when_hash_matches(self, tmp_path):
        channel = MutationChannel()
        content = "def add(a, b): return a + b\n"
        (tmp_path / "logic.py").write_text(content)

        from lintgate.mutation.state import compute_content_hash

        matching_hash = compute_content_hash(content)

        mock_state = FunctionMutationState(
            file_path="logic.py",
            function_name="add",
            code_hash=matching_hash,
            test_hash="t",
            depth=CoverageDepth.PROFILED,
            total=5,
            killed=5,
            survived=0,
        )

        config = ControlPlaneConfig()
        event = SupervisionEvent(project_root=str(tmp_path), files_changed=["logic.py"])

        with patch(
            "lintgate.channels.mutation_channel.MutationStateManager"
        ) as mock_sm_cls:
            mock_sm = mock_sm_cls.return_value
            mock_sm.state = {"logic.py::add": mock_state}
            mock_sm.requires_run.return_value = False
            result = channel.execute(event, config)

        mutch005 = [f for f in result.findings if f.kind == "MUTCH005"]
        assert len(mutch005) == 0

    def test_mutch005_not_emitted_for_depth_none(self, tmp_path):
        channel = MutationChannel()
        (tmp_path / "logic.py").write_text("x = 1\n")

        mock_state = FunctionMutationState(
            file_path="logic.py",
            function_name="f",
            code_hash="old",
            test_hash="t",
            depth=CoverageDepth.NONE,
        )

        config = ControlPlaneConfig()
        event = SupervisionEvent(project_root=str(tmp_path), files_changed=["logic.py"])

        with patch(
            "lintgate.channels.mutation_channel.MutationStateManager"
        ) as mock_sm_cls:
            mock_sm = mock_sm_cls.return_value
            mock_sm.state = {"logic.py::f": mock_state}
            mock_sm.requires_run.return_value = False
            result = channel.execute(event, config)

        mutch005 = [f for f in result.findings if f.kind == "MUTCH005"]
        assert len(mutch005) == 0


class TestMUTCH006SampledAdvisory:
    """MUTCH006: sampled-depth advisory signal."""

    def test_mutch006_emitted_for_sampled_with_survivors(self, tmp_path):
        channel = MutationChannel()
        config = ControlPlaneConfig()
        event = SupervisionEvent(project_root=str(tmp_path), files_changed=["logic.py"])

        mock_state = FunctionMutationState(
            file_path="logic.py",
            function_name="add",
            code_hash="abc",
            test_hash="def",
            total=10,
            killed=7,
            survived=3,
            depth=CoverageDepth.SAMPLED,
        )

        with patch(
            "lintgate.channels.mutation_channel.MutationStateManager"
        ) as mock_sm_cls:
            mock_sm = mock_sm_cls.return_value
            mock_sm.state = {"logic.py::add": mock_state}
            mock_sm.requires_run.return_value = False
            result = channel.execute(event, config)

        mutch006 = [f for f in result.findings if f.kind == "MUTCH006"]
        assert len(mutch006) == 1
        assert mutch006[0].severity == "informational"
        assert mutch006[0].evidence["is_gateable"] is False
        assert "not gateable" in mutch006[0].message.lower()

    def test_mutch006_not_emitted_for_profiled(self, tmp_path):
        channel = MutationChannel()
        config = ControlPlaneConfig()
        event = SupervisionEvent(project_root=str(tmp_path), files_changed=["logic.py"])

        mock_state = FunctionMutationState(
            file_path="logic.py",
            function_name="add",
            code_hash="abc",
            test_hash="def",
            total=10,
            killed=7,
            survived=3,
            depth=CoverageDepth.PROFILED,
        )

        with patch(
            "lintgate.channels.mutation_channel.MutationStateManager"
        ) as mock_sm_cls:
            mock_sm = mock_sm_cls.return_value
            mock_sm.state = {"logic.py::add": mock_state}
            mock_sm.requires_run.return_value = False
            result = channel.execute(event, config)

        mutch006 = [f for f in result.findings if f.kind == "MUTCH006"]
        assert len(mutch006) == 0

    def test_mutch006_not_emitted_low_survival(self, tmp_path):
        channel = MutationChannel()
        config = ControlPlaneConfig()
        event = SupervisionEvent(project_root=str(tmp_path), files_changed=["logic.py"])

        mock_state = FunctionMutationState(
            file_path="logic.py",
            function_name="add",
            code_hash="abc",
            test_hash="def",
            total=10,
            killed=9,
            survived=1,  # 10% survival < 20% threshold
            depth=CoverageDepth.SAMPLED,
        )

        with patch(
            "lintgate.channels.mutation_channel.MutationStateManager"
        ) as mock_sm_cls:
            mock_sm = mock_sm_cls.return_value
            mock_sm.state = {"logic.py::add": mock_state}
            mock_sm.requires_run.return_value = False
            result = channel.execute(event, config)

        mutch006 = [f for f in result.findings if f.kind == "MUTCH006"]
        assert len(mutch006) == 0


class TestSampledSeverityCap:
    """#208: SAMPLED depth findings never exceed informational severity."""

    def test_high_survival_sampled_capped_at_informational(self, tmp_path):
        """Even 90% survival from SAMPLED depth stays informational."""
        channel = MutationChannel()
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
        )

        with patch(
            "lintgate.channels.mutation_channel.MutationStateManager"
        ) as mock_sm_cls:
            mock_sm = mock_sm_cls.return_value
            mock_sm.state = {"logic.py::add": mock_state}
            mock_sm.requires_run.return_value = False
            result = channel.execute(event, config)

        mut_finding = next(
            (f for f in result.findings if f.kind in ("MUT001", "MUT002")), None
        )
        assert mut_finding is not None
        assert mut_finding.severity == "informational"
        assert result.status == "pass"  # No blocking findings

    def test_profiled_high_survival_still_blocks(self, tmp_path):
        """PROFILED depth with high survival should still be blocking."""
        channel = MutationChannel()
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
        )

        with patch(
            "lintgate.channels.mutation_channel.MutationStateManager"
        ) as mock_sm_cls:
            mock_sm = mock_sm_cls.return_value
            mock_sm.state = {"logic.py::add": mock_state}
            mock_sm.requires_run.return_value = False
            result = channel.execute(event, config)

        mut_finding = next((f for f in result.findings if f.kind == "MUT002"), None)
        assert mut_finding is not None
        assert mut_finding.severity == "blocking"
        assert result.status == "fail"


class TestIsGateableInEvidence:
    """Verify is_gateable field appears in evidence of all mutation findings."""

    def test_mut001_has_is_gateable(self, tmp_path):
        channel = MutationChannel()
        config = ControlPlaneConfig()
        event = SupervisionEvent(project_root=str(tmp_path), files_changed=["logic.py"])

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

        with patch(
            "lintgate.channels.mutation_channel.MutationStateManager"
        ) as mock_sm_cls:
            mock_sm = mock_sm_cls.return_value
            mock_sm.state = {"logic.py::add": mock_state}
            mock_sm.requires_run.return_value = False
            result = channel.execute(event, config)

        mut001 = next((f for f in result.findings if f.kind == "MUT001"), None)
        assert mut001 is not None
        assert "is_gateable" in mut001.evidence
        assert mut001.evidence["is_gateable"] is True  # PROFILED

    def test_mut003_has_is_gateable_false(self, tmp_path):
        channel = MutationChannel()
        config = ControlPlaneConfig()
        event = SupervisionEvent(project_root=str(tmp_path), files_changed=["logic.py"])

        mock_state = FunctionMutationState(
            file_path="logic.py",
            function_name="add",
            code_hash="abc",
            test_hash="def",
            total=5,
            killed=3,
            survived=2,  # 40% > 20%
            depth=CoverageDepth.SAMPLED,
        )

        with patch(
            "lintgate.channels.mutation_channel.MutationStateManager"
        ) as mock_sm_cls:
            mock_sm = mock_sm_cls.return_value
            mock_sm.state = {"logic.py::add": mock_state}
            mock_sm.requires_run.return_value = False
            result = channel.execute(event, config)

        mut003 = next((f for f in result.findings if f.kind == "MUT003"), None)
        assert mut003 is not None
        assert mut003.evidence["is_gateable"] is False

    def test_mutch007_has_is_gateable(self, tmp_path):
        channel = MutationChannel()
        config = ControlPlaneConfig()
        event = SupervisionEvent(project_root=str(tmp_path), files_changed=["logic.py"])

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

        with patch(
            "lintgate.channels.mutation_channel.MutationStateManager"
        ) as mock_sm_cls:
            mock_sm = mock_sm_cls.return_value
            mock_sm.state = {"logic.py::add": mock_state}
            mock_sm.requires_run.return_value = False
            result = channel.execute(event, config)

        mutch007 = next((f for f in result.findings if f.kind == "MUTCH007"), None)
        assert mutch007 is not None
        assert "is_gateable" in mutch007.evidence
        assert mutch007.evidence["is_gateable"] is True  # PROFILED
