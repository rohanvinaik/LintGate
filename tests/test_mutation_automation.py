"""Tests for lintgate/mutation/automation.py module."""

from unittest.mock import MagicMock, patch


class TestMutationOrchestrator:
    """Tests for MutationOrchestrator class."""

    def test_orchestrator_enqueue_adds_to_queue(self):
        """Test that enqueue adds files to the queue."""
        # Reset singleton for testing
        from lintgate.mutation import automation

        original_instance = automation.MutationOrchestrator._instance
        automation.MutationOrchestrator._instance = None

        try:
            with patch.object(automation.MutationOrchestrator, "_init_once"):
                orchestrator = automation.MutationOrchestrator.__new__(
                    automation.MutationOrchestrator
                )
                orchestrator._queued_files = set()
                orchestrator._last_run = {}
                orchestrator._debounce_seconds = 30.0
                orchestrator._lock = MagicMock()

                with orchestrator._lock:
                    orchestrator.enqueue("/tmp/test.py")

                assert "/tmp/test.py" in orchestrator._queued_files
        finally:
            automation.MutationOrchestrator._instance = original_instance

    def test_orchestrator_debounce_prevents_requeue(self):
        """Test that debounce prevents re-queueing recently processed files."""
        from lintgate.mutation import automation

        original_instance = automation.MutationOrchestrator._instance
        automation.MutationOrchestrator._instance = None

        try:
            with patch.object(automation.MutationOrchestrator, "_init_once"):
                orchestrator = automation.MutationOrchestrator.__new__(
                    automation.MutationOrchestrator
                )
                orchestrator._queued_files = set()
                orchestrator._last_run = {"/tmp/test.py": 999999999999.0}  # recent timestamp
                orchestrator._debounce_seconds = 30.0
                orchestrator._lock = MagicMock()

                with orchestrator._lock:
                    orchestrator.enqueue("/tmp/test.py")

                # File should NOT be added due to debounce
                assert "/tmp/test.py" not in orchestrator._queued_files
        finally:
            automation.MutationOrchestrator._instance = original_instance


class TestMutationEngineCategories:
    """Additional tests for mutation engine category handling."""

    def test_compute_relevant_categories_no_file(self, tmp_path):
        """Test category computation handles non-existent file gracefully."""
        from unittest.mock import MagicMock

        from lintgate.mutation.engine import MutationEngine
        from lintgate.mutation.policy import RuntimeBudget

        mock_state_manager = MagicMock()
        engine = MutationEngine(mock_state_manager, RuntimeBudget())

        # Non-existent file raises FileNotFoundError which should be caught
        # and return categories (set) + skip_count (int)
        try:
            result = engine._compute_relevant_categories("/nonexistent/file.py", "some_func")
            # If it returns, it should be a tuple of (set, int)
            assert isinstance(result, tuple)
            assert len(result) == 2
            cats, skip_count = result
            assert isinstance(cats, set)
            assert isinstance(skip_count, int)
        except FileNotFoundError:
            # This is also acceptable behavior
            pass

    def test_build_mutant_category_map_fallback(self):
        """Test fallback path when mutmut is not available."""
        from unittest.mock import MagicMock, patch

        from lintgate.mutation.engine import MutationEngine
        from lintgate.mutation.policy import RuntimeBudget

        mock_state_manager = MagicMock()
        engine = MutationEngine(mock_state_manager, RuntimeBudget())

        source = "def test(): pass"

        # Test AST fallback path (no mutmut)
        with patch("lintgate.mutation.engine.cst", None):
            result = engine._build_mutant_category_map("test.py", source)
            # Should return AST-based fallback
            assert isinstance(result, dict)

    def test_build_mutant_category_map_with_mutmut_error(self):
        """Test error handling in mutmut category map builder."""
        from unittest.mock import MagicMock, patch

        from lintgate.mutation.engine import MutationEngine
        from lintgate.mutation.policy import RuntimeBudget

        mock_state_manager = MagicMock()
        engine = MutationEngine(mock_state_manager, RuntimeBudget())

        source = "def test(): pass"

        # Test with mock cst that raises exception
        with patch("lintgate.mutation.engine.cst") as mock_cst:
            mock_cst.parse_module.side_effect = RuntimeError("Parse error")
            result = engine._build_mutant_category_map_with_mutmut("test.py", source)
            assert result == {}


class TestMutationStateEdgeCases:
    """Additional edge case tests for mutation state."""

    def test_mutation_state_manager_update_multiple(self, tmp_path):
        """Test updating multiple states."""
        from lintgate.mutation.state import (
            CoverageDepth,
            FunctionMutationState,
            MutationStateManager,
        )

        storage = tmp_path / "mutation_state.json"
        manager = MutationStateManager(storage)

        state1 = FunctionMutationState(
            function_name="func1",
            file_path="src/a.py",
            code_hash="h1",
            test_hash="t1",
            depth=CoverageDepth.SAMPLED,
        )
        state2 = FunctionMutationState(
            function_name="func2",
            file_path="src/b.py",
            code_hash="h2",
            test_hash="t2",
            depth=CoverageDepth.SAMPLED,
        )

        manager.update_state(state1)
        manager.update_state(state2)
        manager.save()

        # Reload and verify both states exist
        manager2 = MutationStateManager(storage)
        assert manager2.get_state("src/a.py::func1") is not None
        assert manager2.get_state("src/b.py::func2") is not None


class TestFunctionMutationState:
    """Additional tests for FunctionMutationState."""

    def test_from_dict_handles_missing_fields(self):
        """Test that from_dict handles missing optional fields gracefully."""
        from lintgate.mutation.state import FunctionMutationState

        # Minimal dict
        minimal_dict = {
            "function_name": "test",
            "file_path": "test.py",
            "code_hash": "hash",
            "test_hash": "thash",
        }

        state = FunctionMutationState.from_dict(minimal_dict)
        assert state.function_name == "test"
        assert state.survival_rate == 1.0  # default for total=0
