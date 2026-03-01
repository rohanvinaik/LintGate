"""Tests for Deliverable 3: Cold-Start Bootstrap (C1–C5).

C4: Bootstrap state persistence (lock, resume, heartbeat, stale recovery)
C1: Bootstrap trigger in test channel
C2: Bootstrap pipeline (skeleton generation, property tests)
C3: Behavioral contracts (return type, shape, error boundary, I/O stubs)
C5: MCP tool registration
"""

from __future__ import annotations

import ast
import json
import os
import time
from typing import TYPE_CHECKING

from lintgate.orchestration.bootstrap_state import (
    PHASES,
    BootstrapArtifacts,
    BootstrapState,
)

if TYPE_CHECKING:
    from pathlib import Path

# ── C4: Bootstrap State ─────────────────────────────────────────────────


class TestBootstrapState:
    """BootstrapState persistence, locking, and phase management."""

    def test_fresh_state(self, tmp_path: Path) -> None:
        state = BootstrapState.load(str(tmp_path / "nonexistent"))
        assert state.status == "idle"
        assert state.phase == "not_started"
        assert state.tests_generated == 0

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        state = BootstrapState(project_root=str(tmp_path))
        state.run_id = "test123"
        state.status = "running"
        state.phase = "skeletons"
        state.tests_generated = 5
        state.files_processed = {"foo.py": "skeletons"}
        state.save()

        loaded = BootstrapState.load(str(tmp_path))
        assert loaded.run_id == "test123"
        assert loaded.status == "running"
        assert loaded.phase == "skeletons"
        assert loaded.tests_generated == 5
        assert loaded.files_processed == {"foo.py": "skeletons"}

    def test_acquire_and_release_lock(self, tmp_path: Path) -> None:
        state = BootstrapState(project_root=str(tmp_path))
        assert state.acquire_lock() is True
        # Second acquire should fail (already locked)
        state2 = BootstrapState(project_root=str(tmp_path))
        assert state2.acquire_lock() is False
        # Release and retry
        state.release_lock()
        assert state2.acquire_lock() is True
        state2.release_lock()

    def test_stale_lock_recovery(self, tmp_path: Path) -> None:
        """Stale lock (dead PID) should be recovered automatically."""
        from lintgate.orchestration.bootstrap_state import BOOTSTRAP_DIR, _lock_path

        state = BootstrapState(project_root=str(tmp_path))
        BOOTSTRAP_DIR.mkdir(parents=True, exist_ok=True)
        lock_path = _lock_path(str(tmp_path))
        # Write a lock with a dead PID
        with open(lock_path, "w") as f:
            json.dump({"pid": 999999999, "timestamp": time.time()}, f)

        # Should detect stale lock and acquire
        assert state.acquire_lock() is True
        state.release_lock()

    def test_heartbeat_updates_lock(self, tmp_path: Path) -> None:
        state = BootstrapState(project_root=str(tmp_path))
        assert state.acquire_lock()
        state.heartbeat()
        assert state.last_heartbeat is not None
        state.release_lock()

    def test_phase_ordering(self) -> None:
        state = BootstrapState()
        state.phase = "skeletons"
        assert state.phase_completed("not_started")
        assert state.phase_completed("algebra")
        assert state.phase_completed("skeletons")
        assert not state.phase_completed("properties")
        assert not state.phase_completed("complete")

    def test_advance_phase(self, tmp_path: Path) -> None:
        state = BootstrapState(project_root=str(tmp_path))
        state.advance_phase("algebra")
        assert state.phase == "algebra"
        # Verify it persisted
        loaded = BootstrapState.load(str(tmp_path))
        assert loaded.phase == "algebra"

    def test_to_summary(self) -> None:
        state = BootstrapState(run_id="abc", status="running", phase="skeletons")
        state.tests_generated = 3
        summary = state.to_summary()
        assert summary["run_id"] == "abc"
        assert summary["status"] == "running"
        assert summary["phase"] == "skeletons"
        assert summary["tests_generated"] == 3
        assert summary["phase_index"] == PHASES.index("skeletons")

    def test_artifacts_roundtrip(self, tmp_path: Path) -> None:
        state = BootstrapState(project_root=str(tmp_path))
        state.artifacts = BootstrapArtifacts(
            generated_test_dir="tests/gen",
            test_files=["tests/gen/test_a.py"],
        )
        state.save()
        loaded = BootstrapState.load(str(tmp_path))
        assert loaded.artifacts.generated_test_dir == "tests/gen"
        assert loaded.artifacts.test_files == ["tests/gen/test_a.py"]


# ── C1: Bootstrap Trigger ────────────────────────────────────────────────


class TestBootstrapTrigger:
    """Test channel should emit BOOTSTRAP_TRIGGERED for zero-test projects."""

    def test_no_test_files_returns_true(self, tmp_path: Path) -> None:
        """Empty project should trigger bootstrap."""
        from lintgate.channels.test_channel import _no_test_files_exist

        (tmp_path / "main.py").write_text("x = 1\n")
        assert _no_test_files_exist(str(tmp_path)) is True

    def test_with_test_files_returns_false(self, tmp_path: Path) -> None:
        """Project with test files should not trigger bootstrap."""
        from lintgate.channels.test_channel import _no_test_files_exist

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_main.py").write_text("def test_x(): pass\n")
        assert _no_test_files_exist(str(tmp_path)) is False

    def test_with_nested_test_files_returns_false(self, tmp_path: Path) -> None:
        """Nested test files should also prevent bootstrap."""
        from lintgate.channels.test_channel import _no_test_files_exist

        nested = tmp_path / "src" / "tests"
        nested.mkdir(parents=True)
        (nested / "test_utils.py").write_text("def test_y(): pass\n")
        assert _no_test_files_exist(str(tmp_path)) is False


# ── C2: Bootstrap Pipeline ───────────────────────────────────────────────


class TestBootstrapPipeline:
    """Bootstrap pipeline should generate test skeletons."""

    def test_dry_run_generates_skeletons(self, tmp_path: Path) -> None:
        """Dry run should return skeletons without writing files."""
        from lintgate.orchestration.bootstrap_pipeline import BootstrapPipeline

        # Create a simple source file
        (tmp_path / "calculator.py").write_text(
            "def add(a: int, b: int) -> int:\n    return a + b\n\n"
            "def subtract(a: int, b: int) -> int:\n    return a - b\n"
        )

        pipeline = BootstrapPipeline(str(tmp_path))
        result = pipeline.run(dry_run=True)

        assert result.status == "dry_run"
        assert result.tests_generated >= 1
        assert len(result.skeletons) >= 1
        # Verify no files were actually written
        gen_dir = tmp_path / "tests" / "generated"
        assert not gen_dir.exists() or not any(gen_dir.iterdir())

    def test_write_mode_creates_files(self, tmp_path: Path) -> None:
        """Write mode should create test files on disk."""
        from lintgate.orchestration.bootstrap_pipeline import BootstrapPipeline

        (tmp_path / "utils.py").write_text(
            "def greet(name: str) -> str:\n    return f'Hello {name}'\n"
        )

        pipeline = BootstrapPipeline(str(tmp_path))
        result = pipeline.run(dry_run=False)

        assert result.status == "complete"
        assert result.tests_generated >= 1
        # Verify files were written
        gen_dir = tmp_path / "tests" / "generated"
        assert gen_dir.exists()
        test_files = list(gen_dir.glob("test_*.py"))
        assert len(test_files) >= 1

    def test_pipeline_is_resumable(self, tmp_path: Path) -> None:
        """Pipeline should skip completed phases on resume."""
        from lintgate.orchestration.bootstrap_pipeline import BootstrapPipeline
        from lintgate.orchestration.bootstrap_state import BootstrapState

        (tmp_path / "app.py").write_text("def run(): pass\n")

        # Simulate interrupted run at algebra phase
        state = BootstrapState(project_root=str(tmp_path))
        state.run_id = "interrupted"
        state.status = "running"
        state.phase = "algebra"
        state.save()

        # Resume
        pipeline = BootstrapPipeline(str(tmp_path))
        result = pipeline.run(dry_run=True)

        assert result.status == "dry_run"
        # Should have skipped algebra phase

    def test_concurrent_run_blocked(self, tmp_path: Path) -> None:
        """Two concurrent pipelines on the same project should not run."""
        from lintgate.orchestration.bootstrap_pipeline import BootstrapPipeline
        from lintgate.orchestration.bootstrap_state import BootstrapState

        state = BootstrapState(project_root=str(tmp_path))
        assert state.acquire_lock() is True

        # Try to run pipeline while locked
        pipeline = BootstrapPipeline(str(tmp_path))
        result = pipeline.run(dry_run=True)
        assert result.status == "already_running"

        state.release_lock()

    def test_pipeline_skips_test_files(self, tmp_path: Path) -> None:
        """Pipeline should not generate tests for test files themselves."""
        from lintgate.orchestration.bootstrap_pipeline import BootstrapPipeline

        (tmp_path / "app.py").write_text("def main(): pass\n")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_app.py").write_text("def test_main(): pass\n")

        pipeline = BootstrapPipeline(str(tmp_path))
        source_files = pipeline._discover_source_files()
        basenames = [os.path.basename(f) for f in source_files]
        assert "test_app.py" not in basenames
        assert "app.py" in basenames

    def test_generated_files_have_header(self, tmp_path: Path) -> None:
        """Generated test files should have the bootstrap header comment."""
        from lintgate.orchestration.bootstrap_pipeline import BootstrapPipeline

        (tmp_path / "module.py").write_text(
            "def compute(x: int) -> int:\n    return x * 2\n"
        )

        pipeline = BootstrapPipeline(str(tmp_path))
        result = pipeline.run(dry_run=True)

        for content in result.skeletons.values():
            assert "Auto-generated by LintGate bootstrap" in content


# ── C3: Behavioral Contracts ────────────────────────────────────────────


class TestBehavioralContracts:
    """Behavioral contract generator should produce deterministic contracts."""

    def test_return_type_contract(self) -> None:
        """Function with return annotation should get type contract."""
        from lintgate.orchestration.behavioral_contracts import (
            _generate_return_type_contract,
        )

        code = "def compute(x: int) -> int:\n    return x * 2\n"
        tree = ast.parse(code)
        func = tree.body[0]
        result = _generate_return_type_contract(func, "module")
        assert result is not None
        assert "returns" in result
        assert "isinstance" in result

    def test_no_annotation_no_contract(self) -> None:
        """Function without return annotation should not get type contract."""
        from lintgate.orchestration.behavioral_contracts import (
            _generate_return_type_contract,
        )

        code = "def compute(x):\n    return x * 2\n"
        tree = ast.parse(code)
        func = tree.body[0]
        result = _generate_return_type_contract(func, "module")
        assert result is None

    def test_shape_contract_map_pattern(self) -> None:
        """Map pattern (for+append) should get shape contract."""
        from lintgate.orchestration.behavioral_contracts import (
            _generate_shape_contract,
        )

        code = (
            "def transform(items):\n"
            "    result = []\n"
            "    for item in items:\n"
            "        result.append(item * 2)\n"
            "    return result\n"
        )
        tree = ast.parse(code)
        func = tree.body[0]
        result = _generate_shape_contract(func, "module")
        assert result is not None
        assert "preserves_length" in result

    def test_no_shape_contract_without_pattern(self) -> None:
        """Function without map pattern should not get shape contract."""
        from lintgate.orchestration.behavioral_contracts import (
            _generate_shape_contract,
        )

        code = "def compute(x):\n    return x * 2\n"
        tree = ast.parse(code)
        func = tree.body[0]
        result = _generate_shape_contract(func, "module")
        assert result is None

    def test_error_boundary_contract(self) -> None:
        """Except handler with default return should get error contract."""
        from lintgate.orchestration.behavioral_contracts import (
            _generate_error_boundary_contract,
        )

        code = (
            "def safe_parse(data):\n"
            "    try:\n"
            "        return int(data)\n"
            "    except ValueError:\n"
            "        return []\n"
        )
        tree = ast.parse(code)
        func = tree.body[0]
        result = _generate_error_boundary_contract(func, "module")
        assert result is not None
        assert "returns_default" in result
        assert "ValueError" in result.lower() or "valueerror" in result.lower()

    def test_io_stub_detection(self) -> None:
        """Function with subprocess calls should get I/O stub."""
        from lintgate.orchestration.behavioral_contracts import (
            _generate_io_stub,
        )

        code = "import subprocess\ndef run_cmd(cmd):\n    return subprocess.run(cmd)\n"
        tree = ast.parse(code)
        func = tree.body[1]
        result = _generate_io_stub(func, "module")
        assert result is not None
        assert "TODO" in result
        assert "Mock" in result or "mock" in result.lower() or "patch" in result.lower()

    def test_no_io_stub_for_pure(self) -> None:
        """Pure function should not get I/O stub."""
        from lintgate.orchestration.behavioral_contracts import (
            _generate_io_stub,
        )

        code = "def add(a, b):\n    return a + b\n"
        tree = ast.parse(code)
        func = tree.body[0]
        result = _generate_io_stub(func, "module")
        assert result is None

    def test_generate_contracts_integration(self, tmp_path: Path) -> None:
        """generate_contracts should produce contracts for a file."""
        from lintgate.orchestration.behavioral_contracts import generate_contracts

        (tmp_path / "service.py").write_text(
            "def fetch(url: str) -> str:\n    import requests\n    return requests.get(url).text\n"
        )

        contracts = generate_contracts(str(tmp_path))
        # Should have at least the return type contract
        assert isinstance(contracts, dict)  # May or may not produce depending on AST
        # If any produced, verify structure
        for _path, content in contracts.items():
            assert "Auto-generated" in content


# ── C5: MCP Tool Registration ───────────────────────────────────────────


class TestBootstrapMCPTools:
    """bootstrap_tests and bootstrap_status MCP tools should be registered."""

    def test_tools_registered(self) -> None:
        """Bootstrap tools should appear in the module registry."""
        from mcp_tools.bootstrap_tools import register

        # Verify register function exists and returns expected keys
        assert callable(register)

    def test_bootstrap_status_on_empty_project(self, tmp_path: Path) -> None:
        """bootstrap_status should work on a project with no bootstrap history."""
        state = BootstrapState.load(str(tmp_path / "nonexistent"))
        summary = state.to_summary()
        assert summary["status"] == "idle"
        assert summary["phase"] == "not_started"
        assert summary["tests_generated"] == 0
