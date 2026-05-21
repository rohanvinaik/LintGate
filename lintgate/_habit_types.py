"""Habit Mode types and constants.

Shared dataclasses and classification sets used by all habit sub-modules.
Kept in a single leaf module to avoid circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ── Workflow Mode (orthogonal to habit/standard economy mode) ────────

class WorkflowMode(str, Enum):
    """Session workflow mode — controls signal surface and hook behavior.

    Orthogonal to EconomyMode (habit/standard) which controls token/compaction.
    A session can be habit+surgical or standard+refactor, etc.
    """

    SURGICAL = "surgical"       # Narrow edits, trusted codebase, silent-on-clean
    REFACTOR = "refactor"       # Structural changes, full channel output
    GREENFIELD = "greenfield"   # New code, prescriptive spec pipeline
    EXPLORE = "explore"         # Read-heavy, relaxed linting
    DEBUG_SPIRAL = "debug_spiral"  # Recovery mode, constraint-first

    @classmethod
    def from_str(cls, value: str | None) -> WorkflowMode | None:
        """Parse a string to WorkflowMode, returning None for invalid/empty."""
        if not value:
            return None
        try:
            return cls(value.lower().strip())
        except ValueError:
            return None

    @classmethod
    def valid_names(cls) -> list[str]:
        """Return list of valid mode names for error messages."""
        return [m.value for m in cls]

# ── Tool classification sets ─────────────────────────────────────────

_INSPECT_TOOLS = frozenset({"Read", "Grep", "Glob", "WebFetch", "WebSearch"})
_MODIFY_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
_META_TOOLS = frozenset({"Task", "TodoWrite", "AskUserQuestion"})
_VERIFY_BASH_KEYWORDS = frozenset({"pytest", "test", "ruff", "mypy", "flake8"})

# Intent categories (subset of command_normalization.INTENT_CATEGORIES)
_INTENT_GATHER = frozenset({"inspect", "unknown"})
_INTENT_EXECUTE = frozenset({"modify", "verify", "execute"})

# ── Sliding window size ──────────────────────────────────────────────

WINDOW_SIZE = 20
MAX_ACTION_RING = 30
MAX_ACTIVE_FILES = 20

# ── Compaction budget ────────────────────────────────────────────────

SNAPSHOT_MAX_CHARS = 12000  # ~3000 tokens hard cap

# ── Mode Detector thresholds ─────────────────────────────────────────

# Weight table for habit score computation
_SCORE_WEIGHTS = {
    "read_edit_ratio": 0.25,
    "execute_pct": 0.20,
    "edit_streak": 0.15,
    "sub_agent_freq": 0.10,
    "inter_tool_gap": 0.10,
    "same_file_ratio": 0.10,
    "declared": 0.10,
}

DEFAULT_ENTER_SCORE = 0.70
DEFAULT_EXIT_SCORE = 0.40
DEFAULT_SUSTAIN_CALLS = 5

# ── User Message classification keywords ─────────────────────────────

# Continuation keywords — these DON'T collapse habit mode
_CONTINUATION_KEYWORDS = frozenset(
    {
        "yes",
        "ok",
        "okay",
        "continue",
        "go",
        "go ahead",
        "proceed",
        "sure",
        "yep",
        "yeah",
        "confirmed",
        "do it",
        "y",
        "k",
    }
)

# Directive keywords — these ALWAYS collapse habit mode
_DIRECTIVE_KEYWORDS = frozenset(
    {
        "stop",
        "wait",
        "hold",
        "cancel",
        "abort",
        "undo",
        "instead",
        "actually",
        "never mind",
        "scratch that",
    }
)


# ── Data Structures ──────────────────────────────────────────────────


@dataclass
class HabitSignals:
    """Raw signals computed from the tool event stream.

    All computed from a sliding window of recent tool events.
    Cheap arithmetic — no LLM calls, no I/O.
    """

    read_edit_ratio: float = 0.0  # Reads / max(Edits, 1) over window
    gather_pct: float = 0.0  # % of recent calls by inspect/unknown intent
    execute_pct: float = 0.0  # % of recent calls by modify/verify/execute intent
    same_file_ratio: float = 0.0  # Proportion of file ops to already-seen files
    inter_tool_gap_median: float = 0.0  # Median seconds between calls (last 10 gaps)
    sub_agent_freq: float = 0.0  # % of recent calls that are Task tool
    edit_streak: int = 0  # Consecutive Edit/Write calls at end of window
    test_in_last_n: bool = False  # pytest/test in recent Bash calls

    def to_dict(self) -> dict[str, Any]:
        return {
            "read_edit_ratio": round(self.read_edit_ratio, 2),
            "gather_pct": round(self.gather_pct, 2),
            "execute_pct": round(self.execute_pct, 2),
            "same_file_ratio": round(self.same_file_ratio, 2),
            "inter_tool_gap_median": round(self.inter_tool_gap_median, 2),
            "sub_agent_freq": round(self.sub_agent_freq, 2),
            "edit_streak": self.edit_streak,
            "test_in_last_n": self.test_in_last_n,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HabitSignals:
        return cls(
            read_edit_ratio=float(data.get("read_edit_ratio", 0.0)),
            gather_pct=float(data.get("gather_pct", 0.0)),
            execute_pct=float(data.get("execute_pct", 0.0)),
            same_file_ratio=float(data.get("same_file_ratio", 0.0)),
            inter_tool_gap_median=float(data.get("inter_tool_gap_median", 0.0)),
            sub_agent_freq=float(data.get("sub_agent_freq", 0.0)),
            edit_streak=int(data.get("edit_streak", 0)),
            test_in_last_n=bool(data.get("test_in_last_n", False)),
        )


@dataclass
class HabitModeState:
    """Top-level habit mode state.

    Serialized into session.behavior_compass["habit_mode"] (Path A)
    or standalone file (Path B).
    """

    active: bool = False
    habit_score: float = 0.0
    sustain_counter: int = 0
    declared: bool = False  # Agent self-declared via declare_mode
    workflow_mode: str = ""  # Orthogonal to habit/standard — see WorkflowMode enum

    signals: HabitSignals = field(default_factory=HabitSignals)
    active_files: list[str] = field(default_factory=list)  # MRU order, max 20
    last_test_status: str = ""  # "pass", "fail", or ""

    compaction_count: int = 0
    last_compaction_event: int = 0
    entered_at_event: int = 0
    total_events_in_habit: int = 0

    # User message detection
    user_message_detected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "habit_score": round(self.habit_score, 3),
            "sustain_counter": self.sustain_counter,
            "declared": self.declared,
            "workflow_mode": self.workflow_mode,
            "signals": self.signals.to_dict(),
            "active_files": self.active_files,
            "last_test_status": self.last_test_status,
            "compaction_count": self.compaction_count,
            "last_compaction_event": self.last_compaction_event,
            "entered_at_event": self.entered_at_event,
            "total_events_in_habit": self.total_events_in_habit,
            "user_message_detected": self.user_message_detected,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HabitModeState:
        if not data:
            return cls()
        return cls(
            active=bool(data.get("active", False)),
            habit_score=float(data.get("habit_score", 0.0)),
            sustain_counter=int(data.get("sustain_counter", 0)),
            declared=bool(data.get("declared", False)),
            workflow_mode=str(data.get("workflow_mode", "")),
            signals=HabitSignals.from_dict(data.get("signals", {})),
            active_files=list(data.get("active_files", [])),
            last_test_status=str(data.get("last_test_status", "")),
            compaction_count=int(data.get("compaction_count", 0)),
            last_compaction_event=int(data.get("last_compaction_event", 0)),
            entered_at_event=int(data.get("entered_at_event", 0)),
            total_events_in_habit=int(data.get("total_events_in_habit", 0)),
            user_message_detected=bool(data.get("user_message_detected", False)),
        )
