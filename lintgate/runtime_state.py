"""Canonical runtime state bus — single source of truth for all rendering surfaces.

All dynamic rendering surfaces (rule files, hook primers, MCP micro-refresh,
compaction capsules) project from this single RuntimeState object rather than
reaching into multiple independent state stores.

RuntimeState is a **projection**, not a replacement. Existing SessionMemory,
HabitModeState, TokenTrackerState remain the authoritative sources. This module
assembles a read-side aggregation via ``build_runtime_state()`` and persists it
to ``{project_root}/.lintgate/runtime_state.json`` for cross-surface consumption.

The ``generation`` field is a monotonic counter incremented on every save.
Dynamic rule files embed ``LG_GEN:<N>`` watermarks so readers can detect stale
state without full comparison.

All operations are fail-safe — corrupt or missing state returns None / fresh defaults.
"""

from __future__ import annotations

import json
import os
import random
import tempfile
import time
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintgate.compass import CompassState
    from lintgate.controlplane.session_memory import SessionMemory
    from lintgate.habit_mode import HabitModeState
    from lintgate.modes.execution_compass import ExecutionCompass
    from lintgate.token_tracker import TokenTrackerState

# ── Constants ────────────────────────────────────────────────────────

_STATE_DIR = ".lintgate"
_STATE_FILE = "runtime_state.json"
_STATE_LOCK_FILE = "runtime_state.lock"
_MAX_ACTIVE_FILES = 10
_MAX_DIRECTIVES = 8
_TRUE_NORTH_MAX_CHARS = 120
_LOCK_TIMEOUT_S = 0.08
_LOCK_BACKOFF_INITIAL_S = 0.002
_LOCK_BACKOFF_MAX_S = 0.02
_LOCK_JITTER_MAX_S = 0.006

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    _fcntl = None  # type: ignore[assignment]


# ── Data Model ───────────────────────────────────────────────────────


@dataclass
class RuntimeState:
    """Single source of truth for all rendering surfaces.

    Written by the state bus. Read by renderers, hooks, MCP micro-refresh.
    Generation ID increments on every save — readers detect stale state.
    """

    generation: int = 0
    session_id: str = ""
    timestamp: float = 0.0

    # Cognitive mode
    mode: str = "normal"  # "normal" | "theory" | "habit"
    habit_score: float = 0.0

    # Compass capsule (frozen directional state)
    true_north: str = ""  # <=120 chars
    toward: list[str] = field(default_factory=list)  # max 8
    away: list[str] = field(default_factory=list)  # max 8
    forbidden: list[str] = field(default_factory=list)  # max 8
    compass_hash: str = ""

    # Session context (dynamic, session-scoped)
    active_files: list[str] = field(default_factory=list)  # max 10, MRU
    last_test_status: str = ""  # "pass" | "fail" | ""
    focus_intent: str = ""  # 1-sentence current task
    blocking_issues: int = 0
    warning_issues: int = 0
    symbol_coverage_blockers: int = 0
    coherence_state: str = "stable"
    prediction_accuracy: float = -1.0  # -1 = no data yet

    # Token economics
    estimated_tokens_pct: float = 0.0
    compaction_count: int = 0
    tool_calls_total: int = 0

    # Behavioral signals (compact)
    top_constraint: str = ""  # Most relevant active constraint
    approach_failures: int = 0  # Recent failed approaches count

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-friendly dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeState:
        """Deserialize from dict, tolerating missing/extra keys."""
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


@dataclass(frozen=True)
class RuntimeStateWriteMeta:
    """Write metadata for runtime_state persistence operations."""

    written: bool
    lock_acquired: bool
    contention_count: int


# ── I/O ──────────────────────────────────────────────────────────────


def _state_path(project_root: str) -> Path:
    """Return the path to the runtime state file."""
    return Path(project_root) / _STATE_DIR / _STATE_FILE


def load_runtime_state(project_root: str) -> RuntimeState | None:
    """Load RuntimeState from disk. Returns None if missing or corrupt."""
    path = _state_path(project_root)
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return RuntimeState.from_dict(data)
    except (OSError, json.JSONDecodeError, TypeError):
        return None


@contextmanager
def _runtime_state_lock(state_dir: Path):
    """Acquire a short exclusive lock for runtime state writes.

    Returns a dict with:
      - locked: bool
      - contention_count: int

    Locking is best-effort and fail-open.
    """
    lock_fd: int | None = None
    locked = False
    contention_count = 0
    try:
        lock_path = state_dir / _STATE_LOCK_FILE
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        if _fcntl is None:
            yield {"locked": True, "contention_count": 0}
            return

        deadline = time.monotonic() + _LOCK_TIMEOUT_S
        backoff_s = _LOCK_BACKOFF_INITIAL_S
        while time.monotonic() < deadline:
            try:
                _fcntl.flock(lock_fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                contention_count += 1
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                jitter = random.uniform(0.0, min(_LOCK_JITTER_MAX_S, backoff_s * 0.5))
                sleep_for = min(remaining, backoff_s + jitter)
                time.sleep(sleep_for)
                backoff_s = min(backoff_s * 1.7, _LOCK_BACKOFF_MAX_S)
        yield {"locked": locked, "contention_count": contention_count}
    except OSError:
        yield {"locked": False, "contention_count": contention_count}
    finally:
        if lock_fd is not None:
            if locked and _fcntl is not None:
                with suppress(OSError):
                    _fcntl.flock(lock_fd, _fcntl.LOCK_UN)
            with suppress(OSError):
                os.close(lock_fd)


def save_runtime_state_with_meta(
    project_root: str,
    state: RuntimeState,
) -> RuntimeStateWriteMeta:
    """Atomic write with generation increment + write metadata."""
    state_dir = Path(project_root) / _STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / _STATE_FILE

    with _runtime_state_lock(state_dir) as lock_info:
        if isinstance(lock_info, dict):
            locked = bool(lock_info.get("locked", False))
            contention_count = int(lock_info.get("contention_count", 0))
        else:
            # Backward compatibility for monkeypatched tests.
            locked = bool(lock_info)
            contention_count = 0

        if not locked:
            return RuntimeStateWriteMeta(
                written=False,
                lock_acquired=False,
                contention_count=contention_count,
            )

        state.generation += 1
        state.timestamp = time.time()

        try:
            fd, tmp_path = tempfile.mkstemp(dir=str(state_dir), suffix=".tmp", prefix="rs_")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(state.to_dict(), f, separators=(",", ":"))
                os.replace(tmp_path, str(path))
                return RuntimeStateWriteMeta(
                    written=True,
                    lock_acquired=True,
                    contention_count=contention_count,
                )
            except BaseException:
                # Clean up temp file on any error
                with suppress(OSError):
                    os.unlink(tmp_path)
                raise
        except OSError:
            return RuntimeStateWriteMeta(
                written=False,
                lock_acquired=True,
                contention_count=contention_count,
            )


def save_runtime_state(project_root: str, state: RuntimeState) -> bool:
    """Atomic write with generation increment.

    Increments generation before writing so every save produces a new
    generation number. Uses write-to-temp + rename for atomicity.

    Returns:
        True when state was written, False otherwise.
    """
    result = save_runtime_state_with_meta(project_root, state)
    return result.written


def delete_runtime_state(project_root: str) -> bool:
    """Remove runtime state artifacts. Returns True if any file was deleted."""
    state_dir = Path(project_root) / _STATE_DIR
    targets = (
        state_dir / _STATE_FILE,
        state_dir / _STATE_LOCK_FILE,
    )
    deleted_any = False
    for path in targets:
        try:
            path.unlink()
            deleted_any = True
        except OSError:
            continue

    if deleted_any:
        with suppress(OSError):
            state_dir.rmdir()
    return deleted_any


# ── Builder ──────────────────────────────────────────────────────────


def _project_session(state: RuntimeState, session: SessionMemory) -> None:
    """Project session memory fields into runtime state."""
    state.session_id = session.session_id

    mode_dict = session.behavior_compass.get("mode_state", {})
    state.mode = str(mode_dict.get("current", "normal") or "normal")

    if session.snapshots:
        latest = session.snapshots[-1]
        if latest.behavior.action_type == "bash" and "test" in latest.behavior.command_signature:
            state.last_test_status = "pass" if latest.behavior.exit_code == 0 else "fail"

        recent_predictions = [
            s.behavior.prediction_accuracy
            for s in session.snapshots[-10:]
            if s.behavior.prediction_accuracy is not None
        ]
        if recent_predictions:
            state.prediction_accuracy = round(sum(recent_predictions) / len(recent_predictions), 2)

    if session.coherence_trajectory:
        state.coherence_state = session.coherence_trajectory[-1]

    bc = session.behavior_compass
    state.approach_failures = bc.get("approach_failures", 0)
    constraints = bc.get("active_constraints", [])
    if constraints:
        state.top_constraint = str(constraints[0])[:80]


def _project_tracker(state: RuntimeState, tracker: TokenTrackerState) -> None:
    """Project token tracker fields into runtime state."""
    if tracker.context_window_size > 0:
        state.estimated_tokens_pct = round(
            tracker.estimated_tokens_used / tracker.context_window_size * 100, 1
        )
    state.tool_calls_total = tracker.tool_call_count


def build_runtime_state(
    project_root: str,
    *,
    session: SessionMemory | None = None,
    habit_state: HabitModeState | None = None,
    tracker: TokenTrackerState | None = None,
    compass: CompassState | None = None,
    exec_compass: ExecutionCompass | None = None,
    last_coherence_state: str = "",
    last_blocking: int | None = None,
    last_warnings: int | None = None,
) -> RuntimeState:
    """Assemble a RuntimeState from all available source objects.

    Each source is optional — missing sources produce default values.
    This is the single assembly point: callers provide whatever state
    they have, and this function projects it into the canonical form.
    """
    existing = load_runtime_state(project_root)
    state = existing if existing is not None else RuntimeState()

    if session is not None:
        _project_session(state, session)

    if habit_state is not None:
        state.habit_score = round(habit_state.habit_score, 2)
        state.active_files = list(habit_state.active_files)[:_MAX_ACTIVE_FILES]
        state.compaction_count = habit_state.compaction_count

    if exec_compass is not None:
        state.true_north = exec_compass.true_north[:_TRUE_NORTH_MAX_CHARS]
        state.toward = exec_compass.toward[:_MAX_DIRECTIVES]
        state.away = exec_compass.away[:_MAX_DIRECTIVES]
        state.forbidden = exec_compass.forbidden[:_MAX_DIRECTIVES]
    elif compass is not None:
        _populate_from_compass(state, compass)

    if last_coherence_state:
        state.coherence_state = last_coherence_state

    if last_blocking is not None:
        state.blocking_issues = last_blocking
    if last_warnings is not None:
        state.warning_issues = last_warnings

    if tracker is not None:
        _project_tracker(state, tracker)

    return state


def _populate_from_compass(state: RuntimeState, compass: CompassState) -> None:
    """Extract directive lists from a full CompassState."""
    problem_axis = compass.axes.get("problem")
    if problem_axis and problem_axis.summary:
        state.true_north = problem_axis.summary[:_TRUE_NORTH_MAX_CHARS]

    state.toward = [d.text for d in compass.directives if d.kind == "toward"][:_MAX_DIRECTIVES]
    state.away = [d.text for d in compass.directives if d.kind == "away"][:_MAX_DIRECTIVES]
    state.forbidden = [d.text for d in compass.directives if d.kind == "forbidden"][
        :_MAX_DIRECTIVES
    ]
