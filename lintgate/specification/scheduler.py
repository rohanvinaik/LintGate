"""Background orchestration — priority-queue scheduler for mutation work.

Automatically schedules mutation sampling and profiling based on
priority signals (sigma, risk, freshness, gate candidacy) with
budget controls and auto-promote logic.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_STATE_FILE = "scheduler_state.json"
_FLOAT_ZERO_EPS = 1e-12


@dataclass
class SchedulerConfig:
    """Configuration for the mutation scheduler."""

    batch_size: int = 10
    budget_per_batch_s: float = 30.0
    cooldown_s: float = 60.0
    auto_promote: bool = True


@dataclass
class ScheduledItem:
    """A single item in the scheduler queue."""

    function_key: str
    file_path: str
    priority: float = 0.0
    tier: str = "sampling"  # "sampling" or "profiling"
    enqueued_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "function_key": self.function_key,
            "file_path": self.file_path,
            "priority": round(self.priority, 2),
            "tier": self.tier,
            "enqueued_at": self.enqueued_at,
        }


@dataclass
class SchedulerStatus:
    """Current state of the scheduler."""

    queue_depth: int = 0
    completed_count: int = 0
    sampling_pending: int = 0
    profiling_pending: int = 0
    estimated_remaining_s: float = 0.0
    last_batch_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "queue_depth": self.queue_depth,
            "completed_count": self.completed_count,
            "sampling_pending": self.sampling_pending,
            "profiling_pending": self.profiling_pending,
            "estimated_remaining_s": round(self.estimated_remaining_s, 1),
        }


class MutationScheduler:
    """Priority-queue scheduler for background mutation work."""

    def __init__(self, config: SchedulerConfig | None = None):
        self.config = config or SchedulerConfig()
        self._queue: list[ScheduledItem] = []
        self._completed: list[ScheduledItem] = []
        self._last_batch_at: float = 0.0

    def enqueue(
        self,
        function_key: str,
        file_path: str,
        sigma: int = 0,
        risk_score: float = 0.0,
        is_pure: bool = False,
        has_hints: bool = False,
        coverage_depth: str = "none",
        overlay_status: str = "",
        overlay_confidence: float = 0.0,
    ) -> None:
        """Add a function to the priority queue."""
        priority = _compute_priority(
            sigma=sigma,
            risk_score=risk_score,
            is_pure=is_pure,
            has_hints=has_hints,
            coverage_depth=coverage_depth,
            overlay_status=overlay_status,
            overlay_confidence=overlay_confidence,
        )
        tier = "profiling" if coverage_depth == "sampled" else "sampling"
        self._queue.append(
            ScheduledItem(
                function_key=function_key,
                file_path=file_path,
                priority=priority,
                tier=tier,
                enqueued_at=time.time(),
            )
        )
        self._queue.sort(key=lambda item: -item.priority)

    def next_batch(self) -> list[ScheduledItem]:
        """Pop the next batch of functions to process."""
        now = time.time()
        if self._last_batch_at and (now - self._last_batch_at) < self.config.cooldown_s:
            return []

        batch = self._queue[: self.config.batch_size]
        self._queue = self._queue[self.config.batch_size :]
        if batch:
            self._last_batch_at = now
        return batch

    def report_result(
        self,
        item: ScheduledItem,
        survival_rate: float = 0.0,
        budget_exhausted: bool = False,
    ) -> None:
        """Record a result and potentially auto-promote to profiling tier."""
        self._completed.append(item)

        if not self.config.auto_promote or item.tier != "sampling":
            return

        # Auto-promote logic
        if survival_rate <= _FLOAT_ZERO_EPS and not budget_exhausted:
            # Fully killed — no need to profile
            return

        if survival_rate > 0.2 or budget_exhausted:
            # Promote to profiling for deeper analysis
            self._queue.append(
                ScheduledItem(
                    function_key=item.function_key,
                    file_path=item.file_path,
                    priority=item.priority + 5,  # boost for promoted items
                    tier="profiling",
                    enqueued_at=time.time(),
                )
            )
            self._queue.sort(key=lambda item: -item.priority)

    def status(self) -> SchedulerStatus:
        """Current scheduler state."""
        sampling = sum(1 for i in self._queue if i.tier == "sampling")
        profiling = sum(1 for i in self._queue if i.tier == "profiling")
        # Rough estimate: 0.5s per sampling, 5s per profiling
        est = sampling * 0.5 + profiling * 5.0

        return SchedulerStatus(
            queue_depth=len(self._queue),
            completed_count=len(self._completed),
            sampling_pending=sampling,
            profiling_pending=profiling,
            estimated_remaining_s=est,
            last_batch_at=self._last_batch_at,
        )

    def save_state(self, state_dir: Path) -> None:
        """Persist scheduler state for cross-session resumption."""
        state_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "queue": [i.to_dict() for i in self._queue],
            "completed": [i.to_dict() for i in self._completed],
            "last_batch_at": self._last_batch_at,
        }
        state_file = state_dir / _STATE_FILE
        try:
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(state, f)
        except OSError:
            pass

    def load_state(self, state_dir: Path) -> bool:
        """Load scheduler state from disk. Returns True if loaded."""
        state_file = state_dir / _STATE_FILE
        if not state_file.exists():
            return False
        try:
            with open(state_file, encoding="utf-8") as f:
                state = json.load(f)
            self._queue = [_item_from_dict(d) for d in state.get("queue", [])]
            self._completed = [_item_from_dict(d) for d in state.get("completed", [])]
            self._last_batch_at = state.get("last_batch_at", 0.0)
            return True
        except (OSError, json.JSONDecodeError, KeyError):
            return False


def _compute_priority(
    sigma: int = 0,
    risk_score: float = 0.0,
    is_pure: bool = False,
    has_hints: bool = False,
    coverage_depth: str = "none",
    overlay_status: str = "",
    overlay_confidence: float = 0.0,
) -> float:
    """Compute scheduling priority for a function.

    Reconciliation-aware: CONTRADICTS with high confidence gets a boost
    (disagreement needs resolution), NO_EMPIRICAL_DATA gets a smaller
    boost (needs profiling).
    """
    score = 0.0
    score += min(sigma / 30.0, 1.0) * 40
    score += risk_score * 30

    if coverage_depth == "none":
        score += 20
    elif coverage_depth == "sampled":
        score += 5

    if is_pure and has_hints:
        score += 10

    # Reconciliation boost
    if overlay_status == "CONTRADICTS" and overlay_confidence >= 0.7:
        score += 15
    elif overlay_status == "NO_EMPIRICAL_DATA":
        score += 8

    return score


def _item_from_dict(d: dict) -> ScheduledItem:
    return ScheduledItem(
        function_key=d.get("function_key", ""),
        file_path=d.get("file_path", ""),
        priority=d.get("priority", 0.0),
        tier=d.get("tier", "sampling"),
        enqueued_at=d.get("enqueued_at", 0.0),
    )
