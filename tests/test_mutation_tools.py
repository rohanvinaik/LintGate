import json
from unittest.mock import MagicMock, patch

import pytest

from mcp_tools.mutation_tools import _profile_survival_rate, register


class _FakeMCP:
    def tool(self):
        def _decorator(fn):
            return fn

        return _decorator


def _stub_helpers(**overrides):
    defaults = {
        "_validate_project_root": lambda p, **kw: p or "/tmp/test",
        "_json_dumps": lambda obj, **kw: json.dumps(obj),
    }
    defaults.update(overrides)
    return defaults


@pytest.fixture
def mock_engine():
    with patch("mcp_tools.mutation_tools._get_engine") as mock:
        engine = MagicMock()
        mock.return_value = engine
        yield engine


def test_mutation_run_sampling_tool(mock_engine):
    tools = register(_FakeMCP(), _stub_helpers())
    mock_engine.run_inline_sampling.return_value = []

    result = json.loads(tools["mutation_run_sampling"](path="/tmp", files=["a.py"]))

    assert "run_id" in result
    assert mock_engine.run_inline_sampling.called


def test_mutation_run_full_tool(mock_engine):
    tools = register(_FakeMCP(), _stub_helpers())
    mock_engine.run_background_profiling.return_value = []

    result = json.loads(tools["mutation_run_full"](path="/tmp", files=["a.py"]))

    assert "run_id" in result
    assert mock_engine.run_background_profiling.called


def test_mutation_get_state_tool(mock_engine):
    tools = register(_FakeMCP(), _stub_helpers())

    mock_state = MagicMock()
    mock_state.file_path = "a.py"
    mock_state.to_dict.return_value = {"func": "f"}
    mock_engine.state_manager.state = {"a.py::f": mock_state}

    result = json.loads(tools["mutation_get_state"](path="/tmp"))

    assert "a.py" in result["files"]
    assert len(result["files"]["a.py"]) == 1


def test_mutation_clear_state_tool(mock_engine):
    tools = register(_FakeMCP(), _stub_helpers())

    mock_engine.state_manager.state = {"a.py::f": MagicMock()}

    # Clear specific file
    result = json.loads(tools["mutation_clear_state"](path="/tmp", files=["a.py"]))
    assert "Cleared state for 1 files" in result["message"]
    assert mock_engine.state_manager.save.called

    # Clear all
    result = json.loads(tools["mutation_clear_state"](path="/tmp"))
    assert "Cleared all mutation state" in result["message"]
    assert mock_engine.state_manager.state == {}


def test_mutation_prescribe_tool(mock_engine, monkeypatch):
    tools = register(_FakeMCP(), _stub_helpers())

    mock_state = MagicMock()
    mock_state.file_path = "/tmp/src/a.py"
    mock_state.function_name = "f"
    from lintgate.mutation.state import CoverageDepth

    mock_state.depth = CoverageDepth.SAMPLED
    mock_state.survival_rate = 0.6
    mock_state.total = 10
    mock_state.survived_by_category = {"arithmetic": 3, "conditional": 2, "string": 1}
    mock_state.to_dict.return_value = {
        "func": "f",
        "survived_by_category": mock_state.survived_by_category,
    }

    mock_engine.state_manager.state = {"/tmp/src/a.py::f": mock_state}

    result = json.loads(tools["mutation_prescribe"](path="/tmp", file="a.py"))

    assert result["schema_version"] == 2
    assert len(result["profiles"]) == 1
    assert len(result["diagnoses"]) == 1
    assert result["gate_status"] in ["FAIL", "WARN", "PASS"]

    assert "prescriptions" in result
    assert isinstance(result["prescriptions"], list)
    assert len(result["prescriptions"]) > 0
    assert "category" in result["prescriptions"][0]
    assert "gate_lift_projection_percent" in result["prescriptions"][0]

    assert "next_actions" in result
    assert (
        "mutation_decompose" in result["next_actions"]
        or "mutation_refactor_loop" in result["next_actions"]
    )

    # Verify test_skeleton_hints is present
    assert "test_skeleton_hints" in result
    assert isinstance(result["test_skeleton_hints"], list)
    assert len(result["test_skeleton_hints"]) > 0


# Tests for _profile_survival_rate helper
class TestProfileSurvivalRate:
    """Tests for the _profile_survival_rate helper function."""

    def test_profile_survival_rate_none_profile(self):
        """Test with None profile returns 0.0."""
        assert _profile_survival_rate(None) == 0.0

    def test_profile_survival_rate_with_survival_rate_field(self):
        """Test with profile that has survival_rate field."""
        profile = {"survival_rate": 0.5}
        assert _profile_survival_rate(profile) == 0.5

    def test_profile_survival_rate_with_survival_rate_invalid(self):
        """Test with invalid survival_rate field returns 0.0."""
        profile = {"survival_rate": "invalid"}
        assert _profile_survival_rate(profile) == 0.0

    def test_profile_survival_rate_computed_from_counts(self):
        """Test survival rate computed from total/survived when field missing."""
        profile = {"total": 10, "survived": 3}
        assert _profile_survival_rate(profile) == 0.3

    def test_profile_survival_rate_zero_total(self):
        """Test with zero total returns 1.0 (no mutants = perfect score)."""
        profile = {"total": 0, "survived": 0}
        assert _profile_survival_rate(profile) == 1.0


class TestTestSkeletonHints:
    """Tests for test_skeleton_hints mapping."""

    def test_get_hints_for_arithmetic(self):
        """Test hints for arithmetic category."""
        from lintgate.mutation.prescriptions import get_test_skeleton_hints

        hints = get_test_skeleton_hints(["arithmetic"])
        assert len(hints) > 0
        # Check structured hint format
        assert all(isinstance(h, dict) for h in hints)
        assert all("hint" in h for h in hints)
        assert any("zero" in h["hint"].lower() for h in hints)

    def test_get_hints_for_conditional(self):
        """Test hints for conditional category."""
        from lintgate.mutation.prescriptions import get_test_skeleton_hints

        hints = get_test_skeleton_hints(["conditional"])
        assert len(hints) > 0
        assert all(isinstance(h, dict) for h in hints)
        assert any("branch" in h["hint"].lower() for h in hints)

    def test_get_hints_for_string(self):
        """Test hints for string category."""
        from lintgate.mutation.prescriptions import get_test_skeleton_hints

        hints = get_test_skeleton_hints(["string"])
        assert len(hints) > 0
        assert all(isinstance(h, dict) for h in hints)
        assert any("empty" in h["hint"].lower() for h in hints)

    def test_get_hints_with_decomposition(self):
        """Test hints include decomposition when specified."""
        from lintgate.mutation.prescriptions import get_test_skeleton_hints

        hints = get_test_skeleton_hints(
            ["arithmetic", "conditional", "string"], include_decomposition=True
        )
        assert len(hints) > 0
        # Should include decomposition hints
        assert any("extract" in h["hint"].lower() or "separate" in h["hint"].lower() for h in hints)

    def test_get_hints_unknown_category(self):
        """Test fallback for unknown category."""
        from lintgate.mutation.prescriptions import get_test_skeleton_hints

        hints = get_test_skeleton_hints(["unknown_category"])
        assert len(hints) > 0
        # Should have fallback hints
        assert any(
            "review" in h["hint"].lower() or "test coverage" in h["hint"].lower() for h in hints
        )

    def test_get_hints_with_decomposition_axes(self):
        """Test hints generated per decomposition axis."""
        from lintgate.mutation.prescriptions import get_test_skeleton_hints

        # Simulate decomposition axes
        axes = [
            {"category": "arithmetic", "line_start": 10, "line_end": 15, "dominance_ratio": 0.8},
            {"category": "conditional", "line_start": 20, "line_end": 30, "dominance_ratio": 0.7},
        ]

        hints = get_test_skeleton_hints([], decomposition_axes=axes)
        assert len(hints) > 0
        # Should have hints with line range context
        assert any("line" in h.get("hint", "") for h in hints)

    def test_hints_include_archetype(self):
        """Test that hints include archetype field."""
        from lintgate.mutation.prescriptions import get_test_skeleton_hints

        hints = get_test_skeleton_hints(["arithmetic"])
        assert len(hints) > 0
        assert all("archetype" in h for h in hints)
        assert all("test_type" in h for h in hints)


# Additional tests for uncovered paths
class TestMutationRunSamplingEdgeCases:
    """Tests for mutation_run_sampling edge cases."""

    def test_mutation_run_sampling_empty_files_error(self, mock_engine):
        """Test error case when no files provided."""
        tools = register(_FakeMCP(), _stub_helpers())

        result = json.loads(tools["mutation_run_sampling"]("/tmp"))

        assert "error" in result
        assert "Please provide specific files" in result["error"]


class TestMutationRunFullEdgeCases:
    """Tests for mutation_run_full edge cases."""

    def test_mutation_run_full_empty_files_uses_discovery(self, mock_engine):
        """Test that empty files triggers file discovery."""
        tools = register(_FakeMCP(), _stub_helpers())
        mock_engine.run_background_profiling.return_value = []

        # Mock the file discovery - patch where it's imported, not where it's used
        with patch("lintgate.channels.performance_channel._discover_python_files") as mock_discover:
            mock_discover.return_value = ["discovered.py"]
            result = json.loads(tools["mutation_run_full"]("/tmp"))

        assert "run_id" in result
        assert mock_engine.run_background_profiling.called


class TestMutationGetStateEdgeCases:
    """Tests for mutation_get_state edge cases."""

    def test_mutation_get_state_with_file_filter(self, mock_engine):
        """Test filtering by specific file."""
        tools = register(_FakeMCP(), _stub_helpers())

        mock_state1 = MagicMock()
        mock_state1.file_path = "a.py"
        mock_state1.to_dict.return_value = {"func": "f1"}

        mock_state2 = MagicMock()
        mock_state2.file_path = "b.py"
        mock_state2.to_dict.return_value = {"func": "f2"}

        mock_engine.state_manager.state = {
            "a.py::f1": mock_state1,
            "b.py::f2": mock_state2,
        }

        result = json.loads(tools["mutation_get_state"](path="/tmp", file="a.py"))

        assert "a.py" in result["files"]
        assert "b.py" not in result["files"]

    def test_mutation_get_state_includes_survivor_sites(self, mock_engine):
        """Test that survivor_sites are included in the output."""
        tools = register(_FakeMCP(), _stub_helpers())

        # Create state with survivor_sites
        from lintgate.mutation.state import FunctionMutationState, SurvivorSite

        state = FunctionMutationState(
            function_name="test_func",
            file_path="test.py",
            code_hash="abc",
            test_hash="def",
            survived=2,
            total=5,
            survivor_sites=[
                SurvivorSite(line=10, column=5, category="arithmetic", mutant_id="mut_1"),
                SurvivorSite(line=15, column=3, category="conditional", mutant_id="mut_2"),
            ],
        )

        mock_engine.state_manager.state = {"test.py::test_func": state}

        result = json.loads(tools["mutation_get_state"](path="/tmp"))

        assert "test.py" in result["files"]
        func_data = result["files"]["test.py"][0]
        assert "survivor_sites" in func_data
        assert len(func_data["survivor_sites"]) == 2
        assert func_data["survivor_sites"][0]["line"] == 10
        assert func_data["survivor_sites"][1]["category"] == "conditional"

    def test_mutation_get_state_backward_compat_no_survivor_sites(self, mock_engine):
        """Test backward compatibility: to_dict() always includes survivor_sites."""
        tools = register(_FakeMCP(), _stub_helpers())

        # Create actual FunctionMutationState with survivor_sites
        # This tests that to_dict() always includes survivor_sites even when empty
        from lintgate.mutation.state import FunctionMutationState

        # State without survivor_sites (creates empty list default)
        state = FunctionMutationState(
            function_name="legacy_func",
            file_path="legacy.py",
            code_hash="abc",
            test_hash="def",
        )

        mock_engine.state_manager.state = {"legacy.py::legacy_func": state}

        result = json.loads(tools["mutation_get_state"](path="/tmp"))

        assert "legacy.py" in result["files"]
        func_data = result["files"]["legacy.py"][0]
        # to_dict() should always include survivor_sites (as empty list)
        assert "survivor_sites" in func_data
        assert func_data["survivor_sites"] == []


class TestMutationDecompose:
    """Tests for mutation_decompose tool."""

    def test_mutation_decompose_tool_returns_structure(self, mock_engine):
        """Test mutation_decompose returns expected structure."""
        tools = register(_FakeMCP(), _stub_helpers())

        # Mock the state to have empty state
        mock_engine.state_manager.state = {}

        result = json.loads(tools["mutation_decompose"](path="/tmp"))

        # Verify structure - should have schema_version and expected keys
        assert result["schema_version"] == 2
        assert "decomposition_candidates" in result
        assert "already_tractable" in result
        assert "summary" in result
        # New fields for integration
        assert "decomposition_plans" in result
        assert "plan_available" in result

    def test_mutation_decompose_with_survivor_sites_creates_plan(self, mock_engine):
        """Test that survivor_sites trigger decomposition plan creation."""
        tools = register(_FakeMCP(), _stub_helpers())

        # Create state with survivor_sites and high survival
        from lintgate.mutation.state import CoverageDepth, FunctionMutationState, SurvivorSite

        state = FunctionMutationState(
            function_name="complex_func",
            file_path="test.py",
            code_hash="abc",
            test_hash="def",
            depth=CoverageDepth.PROFILED,
            survived=6,
            total=10,
            survived_by_category={"arithmetic": 3, "conditional": 2, "string": 1},
            survivor_sites=[
                SurvivorSite(line=10, column=5, category="arithmetic", mutant_id="mut_1"),
                SurvivorSite(line=12, column=5, category="arithmetic", mutant_id="mut_2"),
                SurvivorSite(line=14, column=5, category="arithmetic", mutant_id="mut_3"),
                SurvivorSite(line=25, column=3, category="conditional", mutant_id="mut_4"),
            ],
        )

        mock_engine.state_manager.state = {"test.py::complex_func": state}

        result = json.loads(tools["mutation_decompose"](path="/tmp"))

        # Should have plan_available=true when survivor_sites present
        assert result["plan_available"] is True
        assert "decomposition_plans" in result

    def test_mutation_decompose_fallback_no_survivor_sites(self, mock_engine):
        """Test fallback when no survivor_sites available."""
        tools = register(_FakeMCP(), _stub_helpers())

        # Create state without survivor_sites
        from lintgate.mutation.state import CoverageDepth, FunctionMutationState

        state = FunctionMutationState(
            function_name="simple_func",
            file_path="test.py",
            code_hash="abc",
            test_hash="def",
            depth=CoverageDepth.PROFILED,
            survived=0,
            total=0,
        )

        mock_engine.state_manager.state = {"test.py::simple_func": state}

        result = json.loads(tools["mutation_decompose"](path="/tmp"))

        # Should have plan_available=false when no survivor_sites
        assert result["plan_available"] is False
        assert result["decomposition_plans"] == []


class TestMutationRefactorLoop:
    """Tests for mutation_refactor_loop tool."""

    def test_mutation_refactor_loop_no_existing_state(self, mock_engine):
        """Test mutation_refactor_loop when no state exists."""
        tools = register(_FakeMCP(), _stub_helpers())

        # No state in the engine
        mock_engine.state_manager.state = {}

        result = json.loads(
            tools["mutation_refactor_loop"](
                path="/tmp",
                file="nonexistent.py",
                function="nonexistent",
                reprofile=False,
            )
        )

        # Should return empty before_profile
        assert result["before_profile"] is None
        assert result["after_profile"] is None
        # Should return stable empty-delta schema (not empty dict)
        assert result["delta"]["survival_rate_change"] == 0.0
        assert result["delta"]["mutants_survived_change"] == 0
        assert result["delta"]["category_deltas"] == {}

    def test_mutation_refactor_loop_with_matching_file(self, mock_engine):
        """Test mutation_refactor_loop with a matching file but no state."""
        tools = register(_FakeMCP(), _stub_helpers())

        # Empty state but file exists in path
        mock_engine.state_manager.state = {}

        result = json.loads(tools["mutation_refactor_loop"](path="/tmp", file="a.py"))

        # Should return with None profiles since no matching state
        assert "before_profile" in result
        assert "after_profile" in result

    def test_mutation_refactor_loop_reprofile_failure(self, mock_engine):
        """Test mutation_refactor_loop when reprofile fails."""
        import unittest.mock as mock

        tools = register(_FakeMCP(), _stub_helpers())

        # Create mock state
        mock_state = MagicMock()
        mock_state.file_path = "/tmp/src/test.py"
        mock_state.function_name = "test_func"
        mock_state.to_dict.return_value = {
            "function_name": "test_func",
            "file_path": "/tmp/src/test.py",
            "survived": 5,
            "total": 10,
            "survived_by_category": {"arithmetic": 3, "conditional": 2},
        }

        mock_engine.state_manager.state = {"/tmp/src/test.py::test_func": mock_state}

        # Make run_inline_sampling raise an exception
        mock_engine.run_inline_sampling = mock.MagicMock(side_effect=RuntimeError("mutmut failed"))

        result = json.loads(
            tools["mutation_refactor_loop"](
                path="/tmp",
                file="test.py",
                function="test_func",
                reprofile=True,
            )
        )

        # Should have before_profile but no after_profile
        assert result["before_profile"] is not None
        assert result["after_profile"] is None
        # Should have reprofile_error
        assert "reprofile_error" in result
        assert result["reprofile_error"]["type"] == "RuntimeError"
        assert "mutmut failed" in result["reprofile_error"]["message"]

    def test_mutation_refactor_loop_mode_parameter_sampled(self, mock_engine):
        """Test mutation_refactor_loop with explicit sampled mode."""
        import unittest.mock as mock

        tools = register(_FakeMCP(), _stub_helpers())

        # Create mock state with proper attributes for diagnosis
        mock_state = MagicMock()
        mock_state.file_path = "/tmp/src/test.py"
        mock_state.function_name = "test_func"
        mock_state.survival_rate = 0.5
        mock_state.total = 10
        mock_state.survived = 5
        mock_state.survived_by_category = {"arithmetic": 3, "conditional": 2}
        mock_state.to_dict.return_value = {
            "function_name": "test_func",
            "file_path": "/tmp/src/test.py",
            "survived": 5,
            "total": 10,
            "survived_by_category": {"arithmetic": 3, "conditional": 2},
        }

        mock_engine.state_manager.state = {"/tmp/src/test.py::test_func": mock_state}

        # Mock run_inline_sampling to return a result
        mock_engine.run_inline_sampling = mock.MagicMock(return_value=[mock_state])
        mock_engine.run_background_profiling = mock.MagicMock()

        json.loads(
            tools["mutation_refactor_loop"](
                path="/tmp",
                file="test.py",
                function="test_func",
                reprofile=True,
                mode="sampled",
            )
        )

        # Should call run_inline_sampling for sampled mode
        mock_engine.run_inline_sampling.assert_called_once()
        mock_engine.run_background_profiling.assert_not_called()

    def test_mutation_refactor_loop_mode_parameter_profiled(self, mock_engine):
        """Test mutation_refactor_loop with explicit profiled mode."""
        import unittest.mock as mock

        tools = register(_FakeMCP(), _stub_helpers())

        # Create mock state with proper attributes for diagnosis
        mock_state = MagicMock()
        mock_state.file_path = "/tmp/src/test.py"
        mock_state.function_name = "test_func"
        mock_state.survival_rate = 0.5
        mock_state.total = 10
        mock_state.survived = 5
        mock_state.survived_by_category = {"arithmetic": 3, "conditional": 2}
        mock_state.to_dict.return_value = {
            "function_name": "test_func",
            "file_path": "/tmp/src/test.py",
            "survived": 5,
            "total": 10,
            "survived_by_category": {"arithmetic": 3, "conditional": 2},
        }

        mock_engine.state_manager.state = {"/tmp/src/test.py::test_func": mock_state}

        # Mock both methods
        mock_engine.run_inline_sampling = mock.MagicMock()
        mock_engine.run_background_profiling = mock.MagicMock(return_value=[mock_state])

        json.loads(
            tools["mutation_refactor_loop"](
                path="/tmp",
                file="test.py",
                function="test_func",
                reprofile=True,
                mode="profiled",
            )
        )

        # Should call run_background_profiling for profiled mode
        mock_engine.run_background_profiling.assert_called_once()
        mock_engine.run_inline_sampling.assert_not_called()
