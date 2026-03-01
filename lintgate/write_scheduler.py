"""Write scheduler — cadence control for dynamic rule file writes.

Prevents prompt cache thrash by controlling when dynamic rule files
(``lg_session.md``, ``lg_focus.md``) are rewritten. Every rewrite changes
file mtime, causing the host to re-tokenize its system prompt. This
scheduler batches writes using dirty-flag + cooldown logic.

Triggers are classified as **immediate** (bypass cooldown) or **cadenced**
(respect cooldown). The scheduler is stateless between calls — all state
lives in the ``WriteScheduler`` dataclass persisted alongside session memory.

All operations are fail-safe. A corrupted scheduler resets to defaults.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

# ── Trigger classification ───────────────────────────────────────────

# Immediate triggers bypass cooldown — used for state transitions that
# the model must see on its very next turn.
_IMMEDIATE_TRIGGERS = frozenset(
    {
        "mode_transition",
        "compass_violation",
        "compaction",
        "session_start",
    }
)

# Cadenced triggers respect cooldown — used for incremental state updates.
_CADENCED_TRIGGERS = frozenset(
    {
        "tool_call",
        "lint_complete",
        "timer",
    }
)


# ── Data Model ───────────────────────────────────────────────────────


@dataclass
class WriteScheduler:
    """Cadence control state for dynamic rule file writes.

    Persisted in ``session.behavior_compass["write_scheduler"]``
    alongside other session state.
    """

    last_write_time: float = 0.0
    last_write_generation: int = 0
    writes_this_session: int = 0
    dirty: bool = False
    tool_calls_since_write: int = 0

    # Configurable thresholds
    min_interval_s: float = 30.0  # Minimum seconds between cadenced writes
    max_interval_s: float = 300.0  # Force write after this many seconds idle
    tool_call_interval: int = 10  # Write every N tool calls (if dirty)

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_write_time": self.last_write_time,
            "last_write_generation": self.last_write_generation,
            "writes_this_session": self.writes_this_session,
            "dirty": self.dirty,
            "tool_calls_since_write": self.tool_calls_since_write,
            "min_interval_s": self.min_interval_s,
            "max_interval_s": self.max_interval_s,
            "tool_call_interval": self.tool_call_interval,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WriteScheduler:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


# ── Decision logic ───────────────────────────────────────────────────


def should_write(
    scheduler: WriteScheduler,
    current_generation: int,
    trigger: str,
    now: float | None = None,
) -> bool:
    """Determine whether to write dynamic rule files now.

    Args:
        scheduler: Current scheduler state.
        current_generation: The RuntimeState generation to write.
        trigger: What caused this check (see trigger sets above).
        now: Current time (injectable for testing).

    Returns:
        True if dynamic rule files should be written.
    """
    if now is None:
        now = time.time()

    # Nothing changed — never write
    if current_generation == scheduler.last_write_generation:
        return False

    # Immediate triggers always write
    if trigger in _IMMEDIATE_TRIGGERS:
        return True

    # From here on, only cadenced triggers apply
    if trigger not in _CADENCED_TRIGGERS:
        return False

    # Must be dirty to write on cadenced triggers
    if not scheduler.dirty:
        return False

    elapsed = now - scheduler.last_write_time

    # Force write if max interval exceeded
    if elapsed >= scheduler.max_interval_s:
        return True

    # Respect cooldown for normal cadenced writes
    if elapsed < scheduler.min_interval_s:
        return False

    # Tool-call cadence: write every N tool calls
    if (
        trigger == "tool_call"
        and scheduler.tool_calls_since_write >= scheduler.tool_call_interval
    ):
        return True

    # Lint complete: always write after cooldown if dirty
    if trigger == "lint_complete":
        return True

    # Timer trigger: write if past cooldown
    return trigger == "timer"


def record_write(
    scheduler: WriteScheduler,
    generation: int,
    now: float | None = None,
) -> None:
    """Record that a write just happened. Call after successful file write."""
    if now is None:
        now = time.time()
    scheduler.last_write_time = now
    scheduler.last_write_generation = generation
    scheduler.writes_this_session += 1
    scheduler.dirty = False
    scheduler.tool_calls_since_write = 0


def mark_dirty(scheduler: WriteScheduler) -> None:
    """Mark the scheduler as having pending changes to write."""
    scheduler.dirty = True


def record_tool_call(scheduler: WriteScheduler) -> None:
    """Increment the tool call counter since last write."""
    scheduler.tool_calls_since_write += 1
