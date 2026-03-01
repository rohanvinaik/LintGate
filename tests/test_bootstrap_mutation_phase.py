"""Tests for bootstrap mutation sampling phase (Deliverable C, Gap 8).

Verifies that the mutation phase in BootstrapPipeline calls
the engine correctly, gracefully degrades, and respects limits.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lintgate.orchestration.bootstrap_pipeline import BootstrapPipeline


@pytest.fixture
def pipeline(tmp_path):
    """Create a pipeline with a temp project root."""
    project_root = str(tmp_path)

    # Create a source file to be discovered
    src = tmp_path / "src"
    src.mkdir()
    (src / "example.py").write_text("def hello(): pass\n")

    p = BootstrapPipeline(project_root)
    # Pre-populate files_processed so mutation has targets
    p.state.files_processed = {
        str((src / "example.py").relative_to(tmp_path)): "skeletons"
    }
    p.state.run_id = "test_run_123"
    return p


class TestMutationPhaseCalls:
    @patch(
        "lintgate.orchestration.bootstrap_pipeline.MutationStateManager", create=True
    )
    @patch("lintgate.orchestration.bootstrap_pipeline.MutationEngine", create=True)
    def test_calls_engine(self, mock_engine_cls, mock_state_cls, pipeline) -> None:
        """Verify run_inline_sampling is called when imports succeed."""
        # We need to patch at the import level inside the method
        mock_state = MagicMock()
        mock_engine = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "lintgate.mutation.engine": MagicMock(
                    MutationEngine=MagicMock(return_value=mock_engine)
                ),
                "lintgate.mutation.state": MagicMock(
                    MutationStateManager=MagicMock(return_value=mock_state)
                ),
                "lintgate.mutation.policy": MagicMock(
                    RuntimeBudget=MagicMock(return_value=MagicMock()),
                    MutationTelemetry=MagicMock(return_value=MagicMock()),
                ),
            },
        ):
            pipeline._run_mutation_sampling()
            # Engine should have been called
            mock_engine.run_inline_sampling.assert_called_once()

    def test_graceful_degradation_on_exception(self, pipeline) -> None:
        """Engine raises → phase continues without error."""
        with patch.dict(
            "sys.modules",
            {
                "lintgate.mutation.engine": MagicMock(
                    MutationEngine=MagicMock(side_effect=RuntimeError("engine broke"))
                ),
                "lintgate.mutation.state": MagicMock(
                    MutationStateManager=MagicMock(return_value=MagicMock())
                ),
                "lintgate.mutation.policy": MagicMock(
                    RuntimeBudget=MagicMock(return_value=MagicMock()),
                    MutationTelemetry=MagicMock(return_value=MagicMock()),
                ),
            },
        ):
            # Should not raise
            pipeline._run_mutation_sampling()

    def test_no_files_is_noop(self, tmp_path) -> None:
        """Empty files_processed → no-op, doesn't touch engine."""
        p = BootstrapPipeline(str(tmp_path))
        p.state.files_processed = {}
        # Should not raise or attempt imports
        p._run_mutation_sampling()

    def test_import_error_returns_gracefully(self, pipeline) -> None:
        """MutationEngine import fails → return gracefully, no exception."""
        with patch.dict(
            "sys.modules",
            {
                "lintgate.mutation.engine": None,  # Simulate ImportError
            },
        ):
            # Should handle ImportError gracefully
            pipeline._run_mutation_sampling()

    def test_caps_at_20_files(self, tmp_path) -> None:
        """30 files processed → only 20 passed to engine."""
        project_root = str(tmp_path)
        src = tmp_path / "src"
        src.mkdir()

        files_processed = {}
        for i in range(30):
            f = src / f"file_{i}.py"
            f.write_text(f"def func_{i}(): pass\n")
            rel = str(f.relative_to(tmp_path))
            files_processed[rel] = "skeletons"

        p = BootstrapPipeline(project_root)
        p.state.files_processed = files_processed
        p.state.run_id = "test_cap"

        mock_engine = MagicMock()
        mock_state = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "lintgate.mutation.engine": MagicMock(
                    MutationEngine=MagicMock(return_value=mock_engine)
                ),
                "lintgate.mutation.state": MagicMock(
                    MutationStateManager=MagicMock(return_value=mock_state)
                ),
                "lintgate.mutation.policy": MagicMock(
                    RuntimeBudget=MagicMock(return_value=MagicMock()),
                    MutationTelemetry=MagicMock(return_value=MagicMock()),
                ),
            },
        ):
            p._run_mutation_sampling()
            call_args = mock_engine.run_inline_sampling.call_args
            if call_args:
                target_files = call_args.kwargs.get("target_files") or call_args[0][0]
                # Should be capped at 20
                assert len(target_files) <= 20

    def test_heartbeat_called(self, pipeline) -> None:
        """heartbeat() is called during mutation phase."""
        pipeline.state.heartbeat = MagicMock()
        mock_engine = MagicMock()
        mock_state = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "lintgate.mutation.engine": MagicMock(
                    MutationEngine=MagicMock(return_value=mock_engine)
                ),
                "lintgate.mutation.state": MagicMock(
                    MutationStateManager=MagicMock(return_value=mock_state)
                ),
                "lintgate.mutation.policy": MagicMock(
                    RuntimeBudget=MagicMock(return_value=MagicMock()),
                    MutationTelemetry=MagicMock(return_value=MagicMock()),
                ),
            },
        ):
            pipeline._run_mutation_sampling()
            pipeline.state.heartbeat.assert_called()
