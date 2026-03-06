"""Automation handling for background mutation execution and debouncing."""

from __future__ import annotations

import heapq
import os
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum

from lintgate.mutation.engine import MutationEngine
from lintgate.mutation.policy import MutationTelemetry


class Tier2Priority(IntEnum):
    """Priority levels for Tier 2 background profiling queue.

    Queue is ephemeral (session-scoped, not persisted). All references use
    enum names, so renumbering is safe.
    """

    TANGLED = 0  # MUTCH011: multi-category high survival — highest payoff
    UNSPECIFIED = 1  # No mutation data — needs baseline
    NO_DATA = 2  # MUTCH008: pure function with zero mutation state (backward compat)
    CONFIRMED_GAP = 3  # MUT003/MUTCH006: sampled data shows survival gaps
    NEARLY_SPECIFIED = 4  # MUTCH009: close to full spec — quick win
    FUZZY_CANDIDATE = 5  # MUTCH010: needs confirmation profiling
    STALE = 6  # MUTCH005: code_hash mismatch on existing profiled data
    REFRESH = 7  # Profiled data exists but old


@dataclass(order=True)
class Tier2QueueItem:
    """A prioritized item in the Tier 2 background profiling queue.

    Sorts by (priority, enqueue_time) naturally via ``order=True``.
    """

    priority: Tier2Priority
    enqueue_time: float = field(default_factory=time.time)
    file_path: str = field(compare=False, default="")
    reason: str = field(compare=False, default="")


class MutationOrchestrator:
    """Manages background queuing and debouncing of mutation tasks.

    Tier 1 (sampling) runs first — fast and always preferred.
    Tier 2 (full profiling) runs only when the Tier 1 queue is empty,
    respecting session caps, debounce intervals, and dedup guards.
    Both tiers execute serially to prevent pyproject.toml race conditions.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_once()
            return cls._instance

    def _init_once(self):
        # Tier 1 state
        self._queued_files: set[str] = set()
        self._last_run: dict[str, float] = {}
        self._debounce_seconds = 30.0
        self._project_root: str | None = None

        # Tier 2 state
        self._tier2_queue: list[Tier2QueueItem] = []
        self._tier2_in_progress: str | None = None
        self._tier2_session_count: int = 0
        self._tier2_max_per_session: int = 3
        self._tier2_timeout_s: int = 300
        self._tier2_debounce_seconds: float = 120.0
        self._tier2_last_run: dict[str, float] = {}

        from lintgate.mutation.engine import SOURCE_PROTECTION_ACTIVE

        self._worker_thread: threading.Thread | None = None
        if SOURCE_PROTECTION_ACTIVE:
            self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker_thread.start()

    def configure_tier2(
        self,
        *,
        max_per_session: int | None = None,
        timeout_s: int | None = None,
        debounce_s: float | None = None,
    ) -> None:
        """Update Tier 2 parameters from channel config."""
        with self._lock:
            if max_per_session is not None:
                self._tier2_max_per_session = max_per_session
            if timeout_s is not None:
                self._tier2_timeout_s = timeout_s
            if debounce_s is not None:
                self._tier2_debounce_seconds = debounce_s

    def enqueue(self, file_path: str, project_root: str | None = None):
        """Request a Tier 1 (sampling) mutation run for a file."""
        from lintgate.mutation.engine import SOURCE_PROTECTION_ACTIVE, _is_mutant_path

        if not SOURCE_PROTECTION_ACTIVE or _is_mutant_path(file_path):
            return
        with self._lock:
            now = time.time()
            last = self._last_run.get(file_path, 0.0)

            if now - last < self._debounce_seconds:
                return

            self._queued_files.add(file_path)
            if project_root is not None:
                self._project_root = project_root

    def enqueue_tier2(
        self,
        file_path: str,
        priority: Tier2Priority,
        reason: str = "",
        project_root: str | None = None,
    ) -> bool:
        """Enqueue a file for Tier 2 (full) background profiling.

        Returns True if accepted, False if rejected by guards.
        """
        from lintgate.mutation.engine import SOURCE_PROTECTION_ACTIVE, _is_mutant_path

        # Guard 1: mutant path check
        if not SOURCE_PROTECTION_ACTIVE or _is_mutant_path(file_path):
            return False

        with self._lock:
            # Guard 2: session cap
            if self._tier2_session_count >= self._tier2_max_per_session:
                return False

            # Guard 3: debounce — reject if same file ran recently
            now = time.time()
            last = self._tier2_last_run.get(file_path, 0.0)
            if now - last < self._tier2_debounce_seconds:
                return False

            # Guard 4: dedup — reject if file already in queue
            for item in self._tier2_queue:
                if item.file_path == file_path:
                    return False

            # Guard 5: dedup — reject if file is currently being profiled
            if self._tier2_in_progress == file_path:
                return False

            # Guard 6: file must exist
            if not os.path.isfile(file_path):
                return False

            if project_root is not None:
                self._project_root = project_root

            item = Tier2QueueItem(
                priority=priority,
                enqueue_time=now,
                file_path=file_path,
                reason=reason,
            )
            heapq.heappush(self._tier2_queue, item)
            return True

    def _worker_loop(self):
        """Background loop: drain Tier 1 first, then Tier 2."""
        while True:
            time.sleep(2.0)  # Gentle polling

            # --- Tier 1 first (fast, always preferred) ---
            with self._lock:
                if self._queued_files:
                    file_to_process = self._queued_files.pop()
                    self._last_run[file_to_process] = time.time()
                    project_root = self._project_root
                else:
                    file_to_process = None
                    project_root = None

            if file_to_process is not None:
                self._run_tier1(file_to_process, project_root)
                continue

            # --- Tier 2 only when Tier 1 queue is empty ---
            with self._lock:
                can_run_tier2 = (
                    len(self._tier2_queue) > 0
                    and self._tier2_in_progress is None
                    and self._tier2_session_count < self._tier2_max_per_session
                )
                if can_run_tier2:
                    item = heapq.heappop(self._tier2_queue)
                    self._tier2_in_progress = item.file_path
                    project_root = self._project_root
                else:
                    item = None

            if item is not None:
                try:
                    self._run_tier2(item.file_path, project_root)
                finally:
                    with self._lock:
                        self._tier2_in_progress = None
                        self._tier2_session_count += 1
                        self._tier2_last_run[item.file_path] = time.time()

    def _run_tier1(self, file_path: str, project_root: str | None) -> None:
        """Execute a Tier 1 (sampling) mutation run for one file.

        When the predictor is available, uses targeted verification to only
        run mutants in uncertain categories. Falls back to full inline
        sampling if the predictor is unavailable or all predictions are
        confident.
        """
        try:
            from lintgate.mutation.engine import SOURCE_PROTECTION_ACTIVE

            if not SOURCE_PROTECTION_ACTIVE:
                return

            from lintgate.mutation.policy import RuntimeBudget
            from lintgate.mutation.state import MutationStateManager
            from lintgate.state import MUTATION_CACHE_DIR

            state_path = os.path.join(MUTATION_CACHE_DIR, "state.json")
            state_manager = MutationStateManager(state_path)
            budget = RuntimeBudget()
            engine = MutationEngine(state_manager, budget)

            telemetry = MutationTelemetry("background_trigger")

            # Try prediction-guided targeted verification first.
            # Only use predictions when we have at least a teff manifest —
            # without it, every function predicts UNSPECIFIED/0.95 which
            # silently skips all mutation execution.
            used_predictions = False
            try:
                from lintgate.mutation.predictor import predict_for_file

                # Try to load cached teff manifest from disk
                teff_manifest = None
                try:
                    from lintgate.linters.test_effectiveness.manifest import (
                        build_test_effectiveness_manifest,
                    )

                    teff_manifest = build_test_effectiveness_manifest(
                        project_root or ".", [file_path], []
                    )
                    if teff_manifest and not teff_manifest.functions:
                        teff_manifest = None
                except Exception:
                    pass

                if teff_manifest is not None:
                    predictions = predict_for_file(
                        file_path,
                        property_manifest=None,
                        teff_manifest=teff_manifest,
                        project_root=project_root,
                    )
                    if any(p.needs_verification for p in predictions.values()):
                        engine.run_targeted_verification(
                            predictions,
                            [file_path],
                            telemetry,
                            project_root=project_root,
                        )
                        used_predictions = True
                    elif predictions:
                        # All predictions confident AND we had real signal
                        used_predictions = True
            except Exception:
                pass  # Fall back to original inline sampling

            if not used_predictions:
                engine.run_inline_sampling(
                    [file_path], telemetry, project_root=project_root
                )
        except Exception as e:
            print(f"[MutationOrchestrator] Tier 1 error for {file_path}: {e}")

    def _run_tier2(self, file_path: str, project_root: str | None) -> None:
        """Execute a Tier 2 (full profiling) mutation run for one file.

        When the predictor and test-impact mapping are available, uses
        targeted verification with test filtering. Falls back to full
        background profiling otherwise.
        """
        try:
            from lintgate.mutation.engine import SOURCE_PROTECTION_ACTIVE

            if not SOURCE_PROTECTION_ACTIVE:
                return

            from lintgate.mutation.policy import RuntimeBudget
            from lintgate.mutation.state import MutationStateManager
            from lintgate.state import MUTATION_CACHE_DIR

            state_path = os.path.join(MUTATION_CACHE_DIR, "state.json")
            state_manager = MutationStateManager(state_path)
            budget = RuntimeBudget()
            engine = MutationEngine(state_manager, budget)

            telemetry = MutationTelemetry("tier2_background")

            # Load test-impact mapping (useful regardless of prediction path)
            test_mapping: dict[str, list[str]] | None = None
            try:
                from lintgate.mutation.test_impact import load_test_impact_mapping

                if project_root:
                    test_mapping = load_test_impact_mapping(project_root)
            except Exception:
                pass

            # Try prediction-guided targeted verification.
            # Only trust predictions when we have a teff manifest —
            # without it, every function predicts UNSPECIFIED/0.95.
            used_predictions = False
            try:
                from lintgate.mutation.predictor import predict_for_file

                teff_manifest = None
                try:
                    from lintgate.linters.test_effectiveness.manifest import (
                        build_test_effectiveness_manifest,
                    )

                    teff_manifest = build_test_effectiveness_manifest(
                        project_root or ".", [file_path], []
                    )
                    if teff_manifest and not teff_manifest.functions:
                        teff_manifest = None
                except Exception:
                    pass

                if teff_manifest is not None:
                    predictions = predict_for_file(
                        file_path,
                        property_manifest=None,
                        teff_manifest=teff_manifest,
                        project_root=project_root,
                    )
                    if any(p.needs_verification for p in predictions.values()):
                        engine.run_targeted_verification(
                            predictions,
                            [file_path],
                            telemetry,
                            project_root=project_root,
                            test_mapping=test_mapping or {},
                        )
                        used_predictions = True
                    elif predictions:
                        used_predictions = True
            except Exception:
                pass

            if not used_predictions:
                engine.run_background_profiling(
                    target_files=[file_path],
                    test_mapping=test_mapping or {},
                    telemetry=telemetry,
                    project_root=project_root,
                )

            # Post-profiling: classify specification level for profiled functions
            from lintgate.mutation.prescriptions import classify_specification_level

            classified = False
            for state in state_manager.state.values():
                if state.file_path == file_path:
                    state.spec_level = classify_specification_level(state)
                    classified = True
            if classified:
                state_manager.save()
        except Exception as e:
            print(f"[MutationOrchestrator] Tier 2 error for {file_path}: {e}")


global_orchestrator = MutationOrchestrator()
