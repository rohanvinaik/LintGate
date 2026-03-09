"""Habit Mode — context window management for sustained execution.

When an LLM agent enters a sustained refactoring/execution phase,
the context window fills with stale file reads, raw test output,
and superseded diffs. Habit Mode detects this phase and provides
structured compaction snapshots — turning context loss into context
*refinement*. The model gets smarter after compact, not dumber.

Architecture: Shared core + storage adapters.
All signal computation, score calculation, mode transitions, and
compaction logic live in pure functions here. Path A (session-backed)
and Path B (file-backed) only differ in how they load/save state.

No LLM calls, no subprocess calls, no file I/O in the hot path.
All persistence via session_memory or standalone file-backed state.

This module is a facade — implementation lives in:
  _habit_types.py   — dataclasses + constants
  _habit_signals.py — signal computation, scoring, mode management
  _habit_compact.py — compaction snapshot building
  _habit_persist.py — session-backed and file-backed persistence
"""

# ── Types and constants ──────────────────────────────────────────────
# ── Compaction ───────────────────────────────────────────────────────
from lintgate._habit_compact import _enforce_snapshot_cap as _enforce_snapshot_cap  # noqa: F401
from lintgate._habit_compact import (  # noqa: F401
    build_compaction_snapshot as build_compaction_snapshot,
)

# ── Persistence ──────────────────────────────────────────────────────
from lintgate._habit_persist import _HABIT_STATE_DIR as _HABIT_STATE_DIR  # noqa: F401
from lintgate._habit_persist import _project_hash as _project_hash  # noqa: F401
from lintgate._habit_persist import _standalone_path as _standalone_path  # noqa: F401
from lintgate._habit_persist import load_habit_state as load_habit_state  # noqa: F401
from lintgate._habit_persist import (  # noqa: F401
    load_habit_state_standalone as load_habit_state_standalone,
)
from lintgate._habit_persist import load_standalone_extras as load_standalone_extras  # noqa: F401
from lintgate._habit_persist import save_habit_state as save_habit_state  # noqa: F401
from lintgate._habit_persist import (  # noqa: F401
    save_habit_state_standalone as save_habit_state_standalone,
)

# ── Signal computation, scoring, mode management ─────────────────────
from lintgate._habit_signals import _add_to_mru as _add_to_mru  # noqa: F401
from lintgate._habit_signals import _classify_user_message as _classify_user_message  # noqa: F401
from lintgate._habit_signals import (  # noqa: F401
    _compute_edit_streak as _compute_edit_streak,
)
from lintgate._habit_signals import (  # noqa: F401
    _compute_inter_tool_gap_median as _compute_inter_tool_gap_median,
)
from lintgate._habit_signals import (  # noqa: F401
    _compute_same_file_ratio as _compute_same_file_ratio,
)
from lintgate._habit_signals import (  # noqa: F401
    _detect_test_in_window as _detect_test_in_window,
)
from lintgate._habit_signals import compute_habit_score as compute_habit_score  # noqa: F401
from lintgate._habit_signals import declare_mode as declare_mode  # noqa: F401
from lintgate._habit_signals import detect_test_result as detect_test_result  # noqa: F401
from lintgate._habit_signals import quick_intent as quick_intent  # noqa: F401
from lintgate._habit_signals import signal_user_message as signal_user_message  # noqa: F401
from lintgate._habit_signals import track_active_files as track_active_files  # noqa: F401
from lintgate._habit_signals import update_mode as update_mode  # noqa: F401
from lintgate._habit_signals import update_signals as update_signals  # noqa: F401
from lintgate._habit_types import _INSPECT_TOOLS as _INSPECT_TOOLS  # noqa: F401
from lintgate._habit_types import _INTENT_EXECUTE as _INTENT_EXECUTE  # noqa: F401
from lintgate._habit_types import _INTENT_GATHER as _INTENT_GATHER  # noqa: F401
from lintgate._habit_types import _META_TOOLS as _META_TOOLS  # noqa: F401
from lintgate._habit_types import _MODIFY_TOOLS as _MODIFY_TOOLS  # noqa: F401
from lintgate._habit_types import _VERIFY_BASH_KEYWORDS as _VERIFY_BASH_KEYWORDS  # noqa: F401
from lintgate._habit_types import DEFAULT_SUSTAIN_CALLS as DEFAULT_SUSTAIN_CALLS  # noqa: F401
from lintgate._habit_types import MAX_ACTION_RING as MAX_ACTION_RING  # noqa: F401
from lintgate._habit_types import MAX_ACTIVE_FILES as MAX_ACTIVE_FILES  # noqa: F401
from lintgate._habit_types import SNAPSHOT_MAX_CHARS as SNAPSHOT_MAX_CHARS  # noqa: F401
from lintgate._habit_types import WINDOW_SIZE as WINDOW_SIZE  # noqa: F401
from lintgate._habit_types import HabitModeState as HabitModeState  # noqa: F401
from lintgate._habit_types import HabitSignals as HabitSignals  # noqa: F401
