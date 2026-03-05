"""C2: Bootstrap pipeline — generate tests for cold-start projects.

Deterministic, resumable pipeline that generates test skeletons,
property tests for pure functions, and behavioral contracts for
impure functions. No LLM calls — fully AST-based.

Execution model:
- Lock-guarded (only one pipeline per project)
- Phase-ordered (algebra → skeletons → properties → contracts → mutation → complete)
- Incremental (per-file tracking in ``files_processed``)
- Heartbeat-aware (long phases call ``heartbeat()`` to prevent stale lock)
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .bootstrap_state import BootstrapState


@dataclass
class BootstrapResult:
    """Result of a bootstrap pipeline run."""

    status: str  # "complete" | "already_running" | "failed" | "dry_run"
    phase: str = "not_started"
    tests_generated: int = 0
    test_files: list[str] = field(default_factory=list)
    skeletons: dict[str, str] = field(default_factory=dict)  # path → content (dry_run)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a compact summary suitable for MCP tool output.

        Skeleton file contents are summarised (path + line count) rather
        than included verbatim to avoid flooding the tool response.
        """
        d: dict[str, Any] = {
            "status": self.status,
            "phase": self.phase,
            "tests_generated": self.tests_generated,
            "test_files": self.test_files,
        }
        if self.error:
            d["error"] = self.error
        if self.skeletons:
            # Summary only — show what would be generated without dumping content
            d["skeleton_summary"] = [
                {
                    "path": path,
                    "lines": content.count("\n") + 1,
                    "functions": content.count("def test_"),
                }
                for path, content in self.skeletons.items()
            ]
            d["skeleton_count"] = len(self.skeletons)
        return d


class BootstrapPipeline:
    """Resumable pipeline that generates tests for cold-start projects.

    V1 generates test skeletons and property tests. Behavioral contracts
    and mutation sampling are added in later phases.
    """

    def __init__(self, project_root: str):
        self.project_root = os.path.abspath(project_root)
        self.state = BootstrapState.load(self.project_root)

    def run(self, dry_run: bool = False, force: bool = False) -> BootstrapResult:
        """Execute pipeline. Idempotent — skips completed phases on resume.

        Args:
            dry_run: Return generated content without writing to disk.
            force: Overwrite existing generated test files.
        """
        if not self.state.acquire_lock():
            return BootstrapResult(status="already_running")

        try:
            # Initialize run
            if self.state.status != "running":
                self.state.run_id = uuid.uuid4().hex[:12]
                self.state.status = "running"
                self.state.started_at = time.time()
                self.state.error = None
                self.state.save()

            # Collected skeletons for dry_run output
            skeletons: dict[str, str] = {}

            # Phase 1: Algebra survey (build property manifest)
            manifest = None
            if not self.state.phase_completed("algebra"):
                manifest = self._run_algebra_survey()
                self.state.advance_phase("algebra")
            else:
                # Reload manifest for later phases
                manifest = self._run_algebra_survey()

            # Phase 2: Skeleton generation (per-file, incremental)
            if not self.state.phase_completed("skeletons"):
                source_files = self._discover_source_files()
                for source_file in source_files:
                    rel_path = os.path.relpath(source_file, self.project_root)
                    if self.state.files_processed.get(rel_path) == "skeletons":
                        continue  # Already done

                    skeleton_content = self._generate_skeleton(source_file)
                    if skeleton_content:
                        test_path = self._compute_test_path(source_file)
                        if dry_run:
                            skeletons[test_path] = skeleton_content
                        else:
                            self._write_if_new(test_path, skeleton_content, force)
                            self.state.artifacts.test_files.append(test_path)

                        self.state.tests_generated += 1
                        self.state.files_processed[rel_path] = "skeletons"
                        self.state.heartbeat()

                self.state.advance_phase("skeletons")

            # Phase 3: Property tests for pure functions
            if not self.state.phase_completed("properties"):
                if manifest is not None:
                    pure_tests = self._generate_property_tests(manifest)
                    for test_path, content in pure_tests.items():
                        if dry_run:
                            skeletons[test_path] = content
                        else:
                            self._write_if_new(test_path, content, force)
                            self.state.artifacts.test_files.append(test_path)
                        self.state.tests_generated += 1
                        self.state.heartbeat()

                self.state.advance_phase("properties")

            # Phase 4: Behavioral contracts (added by C3)
            if not self.state.phase_completed("contracts"):
                contracts = self._generate_behavioral_contracts(manifest)
                for test_path, content in contracts.items():
                    if dry_run:
                        skeletons[test_path] = content
                    else:
                        self._append_to_file(test_path, content)
                    self.state.heartbeat()

                self.state.advance_phase("contracts")

            # Phase 5: Mutation sampling (added by C2+)
            if not self.state.phase_completed("mutation"):
                self._run_mutation_sampling()
                self.state.advance_phase("mutation")

            # Complete
            self.state.status = "complete"
            self.state.phase = "complete"
            self.state.save()

            result_status = "dry_run" if dry_run else "complete"
            return BootstrapResult(
                status=result_status,
                phase="complete",
                tests_generated=self.state.tests_generated,
                test_files=self.state.artifacts.test_files,
                skeletons=skeletons if dry_run else {},
            )

        except Exception as e:
            self.state.status = "failed"
            self.state.error = str(e)
            self.state.save()
            return BootstrapResult(
                status="failed",
                phase=self.state.phase,
                error=str(e),
            )

        finally:
            self.state.release_lock()

    # ── Phase Implementations ───────────────────────────────────────────

    def _run_algebra_survey(self) -> Any:
        """Build the algebraic property manifest for the project."""
        try:
            from lintgate.channels.performance_channel import _discover_python_files
            from lintgate.linters.performance_checks.manifest import build_manifest

            py_files = _discover_python_files(self.project_root)
            if py_files:
                return build_manifest(self.project_root, py_files)
        except Exception:
            pass
        return None

    def _discover_source_files(self) -> list[str]:
        """Discover Python source files (excluding tests, configs, __init__)."""
        from lintgate.discovery import discover_project_files

        all_py = discover_project_files(self.project_root)
        source_files: list[str] = []
        for f in all_py:
            name = os.path.splitext(os.path.basename(f))[0]
            if name.startswith("test_") or name.endswith("_test"):
                continue
            if name in ("__init__", "setup", "conftest"):
                continue
            source_files.append(f)
        return source_files

    def _generate_skeleton(self, source_file: str) -> str | None:
        """Generate a test skeleton for a single source file."""
        try:
            from lintgate.controlplane.skeleton_generator import generate_test_skeleton

            content = generate_test_skeleton(
                source_file,
                project_root=self.project_root,
            )
            if content and content.strip():
                # Add bootstrap header
                header = "# Auto-generated by LintGate bootstrap — review before relying on\n"
                return header + content
        except Exception:
            pass
        return None

    def _compute_test_path(self, source_file: str) -> str:
        """Compute test file path in the generated test directory."""
        from lintgate.controlplane.skeleton_generator import generate_test_path

        # Use the skeleton generator's path logic but redirect to generated/ dir
        default_path = generate_test_path(source_file, self.project_root)

        # Redirect to tests/generated/ namespace
        gen_dir = os.path.join(
            self.project_root,
            self.state.artifacts.generated_test_dir,
        )
        basename = os.path.basename(default_path)
        return os.path.join(gen_dir, basename)

    def _generate_property_tests(self, manifest: Any) -> dict[str, str]:
        """Generate Hypothesis property tests for pure functions."""
        if manifest is None:
            return {}

        try:
            from lintgate.integrations.hypothesis_bridge import (
                generate_hypothesis_template,
            )
            from lintgate.linters.performance_checks.algebra_types import PropertyKind

            gen_dir = os.path.join(
                self.project_root,
                self.state.artifacts.generated_test_dir,
            )
            results: dict[str, str] = {}

            for name, func in manifest.functions.items():
                if not func.purity.is_pure:
                    continue
                interesting = sum(
                    1 for p in func.properties if p.kind != PropertyKind.PURE
                )
                if interesting == 0:
                    continue

                template = generate_hypothesis_template(name, func)
                if template:
                    # Build test file path
                    safe_name = name.replace(".", "_")
                    test_path = os.path.join(gen_dir, f"test_props_{safe_name}.py")
                    header = "# Auto-generated by LintGate bootstrap — review before relying on\n"
                    results[test_path] = header + template

            return results
        except Exception:
            return {}

    def _generate_behavioral_contracts(self, manifest: Any) -> dict[str, str]:
        """Generate behavioral contracts for impure functions.

        Delegates to the behavioral_contracts module (C3).
        Returns empty dict if C3 is not yet implemented.
        """
        try:
            from .behavioral_contracts import generate_contracts

            return generate_contracts(self.project_root, manifest)
        except (ImportError, Exception):
            return {}

    def _run_mutation_sampling(self) -> None:
        """Run mutation sampling against generated tests.

        Uses MutationEngine.run_inline_sampling (Tier 1, fast)
        capped at 20 source files. Stores results via MutationStateManager.
        Gracefully degrades if mutation infrastructure is unavailable.
        """
        try:
            from lintgate.mutation.engine import MutationEngine
            from lintgate.mutation.state import MutationStateManager
        except ImportError:
            return  # Mutation infrastructure not available

        # Get source files that have generated tests
        source_files = [
            os.path.join(self.project_root, rel_path)
            for rel_path in self.state.files_processed
            if os.path.isfile(os.path.join(self.project_root, rel_path))
        ]
        if not source_files:
            return

        try:
            state_dir = os.path.join(self.project_root, ".lintgate")
            os.makedirs(state_dir, exist_ok=True)
            state_manager = MutationStateManager(
                os.path.join(state_dir, "mutation_state.json")
            )

            # Build telemetry and budget objects
            from lintgate.mutation.policy import MutationTelemetry, RuntimeBudget

            budget = RuntimeBudget()
            telemetry = MutationTelemetry(run_id=self.state.run_id or "bootstrap")

            engine = MutationEngine(state_manager, budget)
            engine.run_inline_sampling(
                target_files=source_files[:20],  # Cap for performance
                telemetry=telemetry,
                project_root=self.project_root,
            )
            self.state.heartbeat()

            state_manager.save()
        except Exception:
            pass  # Graceful degradation — mutation is optional

    # ── File I/O ────────────────────────────────────────────────────────

    def _write_if_new(self, path: str, content: str, force: bool = False) -> bool:
        """Write file only if it doesn't exist (or force=True)."""
        if os.path.exists(path) and not force:
            return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return True

    def _append_to_file(self, path: str, content: str) -> None:
        """Append content to an existing test file, or create new."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "a" if os.path.exists(path) else "w"
        with open(path, mode) as f:
            if mode == "a":
                f.write("\n\n")
            f.write(content)
