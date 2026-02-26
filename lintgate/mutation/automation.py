"""Automation handling for background mutation execution and debouncing."""

import threading
import time

from lintgate.mutation.engine import MutationEngine
from lintgate.mutation.policy import MutationTelemetry


class MutationOrchestrator:
    """Manages background queuing and debouncing of mutation tasks."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_once()
            return cls._instance

    def _init_once(self):
        self._queued_files: set[str] = set()
        self._last_run: dict[str, float] = {}
        self._debounce_seconds = 30.0

        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def enqueue(self, file_path: str):
        """Request a mutation run for a file, applying debounce logic."""
        with self._lock:
            now = time.time()
            last = self._last_run.get(file_path, 0.0)

            # Debounce: don't requeue if we just ran it recently
            if now - last < self._debounce_seconds:
                return

            self._queued_files.add(file_path)

    def _worker_loop(self):
        """Background loop to drain the queue and run sampled mutations."""
        while True:
            time.sleep(2.0)  # Gentle polling

            with self._lock:
                if not self._queued_files:
                    continue
                # Take one file to process
                file_to_process = self._queued_files.pop()
                self._last_run[file_to_process] = time.time()

            try:
                # Late import to avoid circular dependencies
                import os

                from lintgate.mutation.policy import RuntimeBudget
                from lintgate.mutation.state import MutationStateManager
                from lintgate.state import MUTATION_CACHE_DIR

                state_path = os.path.join(MUTATION_CACHE_DIR, "state.json")
                state_manager = MutationStateManager(state_path)
                budget = RuntimeBudget()
                engine = MutationEngine(state_manager, budget)

                telemetry = MutationTelemetry("background_trigger")
                engine.run_inline_sampling([file_to_process], telemetry)
            except Exception as e:
                # Failsafe for background thread
                print(f"[MutationOrchestrator] Error processing {file_to_process}: {e}")

global_orchestrator = MutationOrchestrator()
