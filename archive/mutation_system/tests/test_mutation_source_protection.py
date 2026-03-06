"""Tests for mutation pipeline source protection and path validation."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from lintgate.mutation.engine import MutationEngine, _is_mutant_path
from lintgate.mutation.policy import RuntimeBudget
from lintgate.mutation.state import CoverageDepth, MutationStateManager

# ---------------------------------------------------------------------------
# _is_mutant_path
# ---------------------------------------------------------------------------


class TestIsMutantPath:
    def test_mutants_subdir(self):
        assert _is_mutant_path("mutants/foo.py") is True

    def test_relative_mutants(self):
        assert _is_mutant_path("./mutants/bar.py") is True

    def test_nested_mutants(self):
        assert _is_mutant_path("/project/mutants/lintgate/engine.py") is True

    def test_normal_source_file(self):
        assert _is_mutant_path("lintgate/mutation/engine.py") is False

    def test_mutants_in_filename_not_dir(self):
        # "mutants.py" is a file, not a directory component
        assert _is_mutant_path("lintgate/mutants.py") is False

    def test_empty_path(self):
        assert _is_mutant_path("") is False


# ---------------------------------------------------------------------------
# pyproject.toml backup and restore
# ---------------------------------------------------------------------------


class TestPyprojectBackupRestore:
    """Verify pyproject.toml is always restored after _execute_mutmut."""

    @pytest.fixture()
    def engine(self, tmp_path):
        state_path = tmp_path / "state.json"
        sm = MutationStateManager(str(state_path))
        budget = RuntimeBudget(enabled=False)
        # Write a minimal pyproject.toml in the working dir
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.mutmut]\npaths_to_mutate = ["lintgate/"]\n', "utf-8"
        )
        return MutationEngine(sm, budget), tmp_path, pyproject

    def test_pyproject_restored_after_success(self, engine, monkeypatch):
        eng, tmp_path, pyproject = engine
        original = pyproject.read_text("utf-8")
        monkeypatch.chdir(tmp_path)

        # Enable budget so _execute_mutmut proceeds
        eng.budget = RuntimeBudget(enabled=True)

        with patch.object(
            eng, "_filter_mutants_by_category", return_value=([], False)
        ), patch.object(eng, "_build_mutmut_command", return_value=None):
            result = eng._execute_mutmut(
                paths=["lintgate/foo.py"],
                depth=CoverageDepth.SAMPLED,
                test_filter=None,
            )

        assert result is True
        assert pyproject.read_text("utf-8") == original

    def test_pyproject_restored_after_failure(self, engine, monkeypatch):
        eng, tmp_path, pyproject = engine
        original = pyproject.read_text("utf-8")
        monkeypatch.chdir(tmp_path)

        eng.budget = RuntimeBudget(enabled=True)

        with patch.object(
            eng,
            "_filter_mutants_by_category",
            side_effect=subprocess.SubprocessError("boom"),
        ):
            result = eng._execute_mutmut(
                paths=["lintgate/foo.py"],
                depth=CoverageDepth.SAMPLED,
                test_filter=None,
            )

        assert result is False
        assert pyproject.read_text("utf-8") == original

    def test_backup_created_and_removed(self, engine, monkeypatch):
        eng, tmp_path, pyproject = engine
        monkeypatch.chdir(tmp_path)
        backup_path = pyproject.with_suffix(".toml.lintgate-backup")

        eng.budget = RuntimeBudget(enabled=True)

        backup_existed_during_run = False

        def check_backup(*args, **kwargs):
            nonlocal backup_existed_during_run
            backup_existed_during_run = backup_path.exists()
            return [], False

        with patch.object(
            eng, "_filter_mutants_by_category", side_effect=check_backup
        ), patch.object(eng, "_build_mutmut_command", return_value=None):
            eng._execute_mutmut(
                paths=["lintgate/foo.py"],
                depth=CoverageDepth.SAMPLED,
                test_filter=None,
            )

        assert backup_existed_during_run is True
        assert not backup_path.exists()


# ---------------------------------------------------------------------------
# Recovery on init
# ---------------------------------------------------------------------------


class TestRecoveryOnInit:
    def test_stale_backup_restored(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        # Simulate a stale backup left by a killed process
        original_content = '[tool.mutmut]\npaths_to_mutate = ["lintgate/"]\n'
        scoped_content = '[tool.mutmut]\npaths_to_mutate = ["lintgate/foo.py"]\n'

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(scoped_content, "utf-8")

        backup = tmp_path / "pyproject.toml.lintgate-backup"
        backup.write_text(original_content, "utf-8")

        state_path = tmp_path / "state.json"
        sm = MutationStateManager(str(state_path))
        budget = RuntimeBudget(enabled=False)

        # Engine init should detect and restore
        MutationEngine(sm, budget)

        assert pyproject.read_text("utf-8") == original_content
        assert not backup.exists()


# ---------------------------------------------------------------------------
# Orchestrator enqueue guard
# ---------------------------------------------------------------------------


class TestEnqueueRejectsMutantPaths:
    def test_mutant_path_ignored(self):
        from lintgate.mutation.automation import MutationOrchestrator

        orch = MutationOrchestrator.__new__(MutationOrchestrator)
        orch._queued_files = set()
        orch._last_run = {}
        orch._debounce_seconds = 30.0
        orch._project_root = None
        orch._lock = __import__("threading").Lock()

        orch.enqueue("mutants/lintgate/foo.py", project_root="/project")
        assert len(orch._queued_files) == 0

    def test_normal_path_accepted(self):
        from lintgate.mutation.automation import MutationOrchestrator

        orch = MutationOrchestrator.__new__(MutationOrchestrator)
        orch._queued_files = set()
        orch._last_run = {}
        orch._debounce_seconds = 30.0
        orch._project_root = None
        orch._lock = __import__("threading").Lock()

        orch.enqueue("lintgate/foo.py", project_root="/project")
        assert "lintgate/foo.py" in orch._queued_files


# ---------------------------------------------------------------------------
# MCP tool impl guards
# ---------------------------------------------------------------------------


class TestImplRunSamplingFiltersMutants:
    def test_mutant_files_stripped(self):
        from mcp_tools.mutation_tools import _impl_run_sampling

        engine = MagicMock()
        engine.run_inline_sampling.return_value = []

        result = _impl_run_sampling(
            engine,
            files=["lintgate/foo.py", "mutants/lintgate/foo.py"],
            project_root="/project",
        )

        assert result is not None
        # Only the non-mutant file should have been passed
        call_args = engine.run_inline_sampling.call_args
        assert call_args[0][0] == ["lintgate/foo.py"]

    def test_all_mutant_files_returns_none(self):
        from mcp_tools.mutation_tools import _impl_run_sampling

        engine = MagicMock()

        result = _impl_run_sampling(
            engine,
            files=["mutants/lintgate/foo.py"],
            project_root="/project",
        )

        assert result is None
        engine.run_inline_sampling.assert_not_called()

    def test_impl_run_full_filters_mutants(self):
        from mcp_tools.mutation_tools import _impl_run_full

        engine = MagicMock()
        engine.run_background_profiling.return_value = []

        _impl_run_full(
            engine,
            project_root="/project",
            files=["lintgate/foo.py", "mutants/lintgate/foo.py"],
        )

        call_args = engine.run_background_profiling.call_args
        assert call_args[0][0] == ["lintgate/foo.py"]
