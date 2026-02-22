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
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
            signals=HabitSignals.from_dict(data.get("signals", {})),
            active_files=list(data.get("active_files", [])),
            last_test_status=str(data.get("last_test_status", "")),
            compaction_count=int(data.get("compaction_count", 0)),
            last_compaction_event=int(data.get("last_compaction_event", 0)),
            entered_at_event=int(data.get("entered_at_event", 0)),
            total_events_in_habit=int(data.get("total_events_in_habit", 0)),
            user_message_detected=bool(data.get("user_message_detected", False)),
        )


# ── Signal Collector ─────────────────────────────────────────────────


def _compute_same_file_ratio(window: list[dict[str, Any]]) -> float:
    """Proportion of file ops targeting already-seen files."""
    seen_files: set[str] = set()
    file_ops = 0
    repeat_ops = 0
    for e in window:
        tool = e.get("tool", "")
        if tool in _INSPECT_TOOLS or tool in _MODIFY_TOOLS:
            file_ops += 1
            sig_str = e.get("sig", "")
            if sig_str:
                if sig_str in seen_files:
                    repeat_ops += 1
                seen_files.add(sig_str)
    return repeat_ops / max(file_ops, 1)


def _compute_inter_tool_gap_median(window: list[dict[str, Any]]) -> float:
    """Median of last 10 inter-tool time gaps."""
    timestamps = [e.get("ts", 0.0) for e in window if e.get("ts")]
    if len(timestamps) < 2:
        return 0.0
    gaps = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
    recent_gaps = gaps[-10:] if len(gaps) > 10 else gaps
    sorted_gaps = sorted(recent_gaps)
    mid = len(sorted_gaps) // 2
    if len(sorted_gaps) % 2 == 0 and len(sorted_gaps) >= 2:
        return float((sorted_gaps[mid - 1] + sorted_gaps[mid]) / 2)
    if sorted_gaps:
        return float(sorted_gaps[mid])
    return 0.0


def _compute_edit_streak(window: list[dict[str, Any]]) -> int:
    """Consecutive Edit/Write/MultiEdit at end of window."""
    streak = 0
    for e in reversed(window):
        if e.get("tool") in _MODIFY_TOOLS:
            streak += 1
        else:
            break
    return streak


def _detect_test_in_window(window: list[dict[str, Any]]) -> bool:
    """Check for pytest/test in recent Bash calls."""
    for e in window:
        if e.get("tool") == "Bash":
            cmd_sig = e.get("sig", "")
            if any(kw in cmd_sig.lower() for kw in ("pytest", "test")):
                return True
    return False


def update_signals(state: HabitModeState, action_history: list[dict[str, Any]]) -> None:
    """Compute all HabitSignals from the sliding window of tool events.

    Pure computation — no I/O, no side effects beyond state.signals mutation.
    Works with both compass.action_history (Path A) and minimal action ring (Path B).
    Each entry needs at least: {"tool": str, "ts": float, "intent": str}
    Optional: {"sig": str}
    """
    window = action_history[-WINDOW_SIZE:] if len(action_history) > WINDOW_SIZE else action_history
    if not window:
        return

    sig = state.signals
    n = len(window)

    # Count tool categories
    read_count = sum(1 for e in window if e.get("tool") in _INSPECT_TOOLS)
    edit_count = sum(1 for e in window if e.get("tool") in _MODIFY_TOOLS)
    task_count = sum(1 for e in window if e.get("tool") == "Task")

    sig.read_edit_ratio = read_count / max(edit_count, 1)

    # Intent percentages
    gather_count = sum(1 for e in window if e.get("intent") in _INTENT_GATHER)
    execute_count = sum(1 for e in window if e.get("intent") in _INTENT_EXECUTE)
    sig.gather_pct = gather_count / n
    sig.execute_pct = execute_count / n

    sig.sub_agent_freq = task_count / n
    sig.same_file_ratio = _compute_same_file_ratio(window)
    sig.inter_tool_gap_median = _compute_inter_tool_gap_median(window)
    sig.edit_streak = _compute_edit_streak(window)
    sig.test_in_last_n = _detect_test_in_window(window)


def track_active_files(
    state: HabitModeState,
    tool_name: str,
    tool_input: dict[str, Any] | str,
) -> None:
    """Extract file paths from tool input and maintain MRU active_files list.

    Capped at MAX_ACTIVE_FILES entries.
    """
    if isinstance(tool_input, str):
        return

    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not file_path or not isinstance(file_path, str):
        # Also check for 'files' list (lint_files tool)
        files = tool_input.get("files", [])
        if isinstance(files, list):
            for fp in files[:5]:  # Cap extraction at 5
                if isinstance(fp, str) and fp:
                    _add_to_mru(state.active_files, fp)
        return

    _add_to_mru(state.active_files, file_path)


def _add_to_mru(files: list[str], path: str) -> None:
    """Add a path to MRU list, moving to front if already present."""
    if path in files:
        files.remove(path)
    files.insert(0, path)
    while len(files) > MAX_ACTIVE_FILES:
        files.pop()


def detect_test_result(
    state: HabitModeState,
    tool_output: str,
    command_sig: str,
) -> None:
    """Detect pass/fail from Bash test output.

    Cheap keyword matching — not precise, but good enough for habit mode.
    """
    if not command_sig:
        return
    sig_lower = command_sig.lower()
    if "pytest" not in sig_lower and "test" not in sig_lower:
        return

    out_lower = tool_output.lower() if tool_output else ""
    if not out_lower:
        return

    # Check for pass/fail keywords
    if "passed" in out_lower and "failed" not in out_lower and "error" not in out_lower:
        state.last_test_status = "pass"
    elif "failed" in out_lower or "error" in out_lower:
        state.last_test_status = "fail"


# ── Mode Detector ────────────────────────────────────────────────────

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

# Default thresholds
DEFAULT_ENTER_SCORE = 0.70
DEFAULT_EXIT_SCORE = 0.40
DEFAULT_SUSTAIN_CALLS = 5


def compute_habit_score(state: HabitModeState) -> float:
    """Compute weighted composite habit score (0.0-1.0).

    Each signal contributes a 0.0-1.0 component multiplied by its weight.
    Thresholds define "full" (1.0) and "half" (0.5) contribution.
    """
    sig = state.signals
    score = 0.0

    # read_edit_ratio < 2.0 → full, < 3.0 → half
    if sig.read_edit_ratio < 2.0:
        score += _SCORE_WEIGHTS["read_edit_ratio"] * 1.0
    elif sig.read_edit_ratio < 3.0:
        score += _SCORE_WEIGHTS["read_edit_ratio"] * 0.5

    # execute_pct > 0.5 → full, > 0.3 → half
    if sig.execute_pct > 0.5:
        score += _SCORE_WEIGHTS["execute_pct"] * 1.0
    elif sig.execute_pct > 0.3:
        score += _SCORE_WEIGHTS["execute_pct"] * 0.5

    # edit_streak >= 3 → full, >= 2 → half
    if sig.edit_streak >= 3:
        score += _SCORE_WEIGHTS["edit_streak"] * 1.0
    elif sig.edit_streak >= 2:
        score += _SCORE_WEIGHTS["edit_streak"] * 0.5

    # sub_agent_freq < 0.05 → full, < 0.15 → half
    if sig.sub_agent_freq < 0.05:
        score += _SCORE_WEIGHTS["sub_agent_freq"] * 1.0
    elif sig.sub_agent_freq < 0.15:
        score += _SCORE_WEIGHTS["sub_agent_freq"] * 0.5

    # inter_tool_gap_median < 3s → full, < 5s → half
    if sig.inter_tool_gap_median > 0:
        if sig.inter_tool_gap_median < 3.0:
            score += _SCORE_WEIGHTS["inter_tool_gap"] * 1.0
        elif sig.inter_tool_gap_median < 5.0:
            score += _SCORE_WEIGHTS["inter_tool_gap"] * 0.5

    # same_file_ratio > 0.6 → full, > 0.4 → half
    if sig.same_file_ratio > 0.6:
        score += _SCORE_WEIGHTS["same_file_ratio"] * 1.0
    elif sig.same_file_ratio > 0.4:
        score += _SCORE_WEIGHTS["same_file_ratio"] * 0.5

    # declared → binary full
    if state.declared:
        score += _SCORE_WEIGHTS["declared"] * 1.0

    return min(score, 1.0)


def update_mode(
    state: HabitModeState,
    event_counter: int,
    *,
    enter_score: float = DEFAULT_ENTER_SCORE,
    exit_score: float = DEFAULT_EXIT_SCORE,
    sustain_calls: int = DEFAULT_SUSTAIN_CALLS,
) -> str | None:
    """Update habit mode state based on current score.

    Returns "enter", "exit", or None.

    Hysteresis:
    - Enter: habit_score >= enter_score sustained for sustain_calls consecutive events
    - Exit: habit_score < exit_score OR user_message_detected (instant)
    """
    state.habit_score = compute_habit_score(state)

    # Check for user message override (instant exit)
    if state.user_message_detected and state.active:
        state.active = False
        state.sustain_counter = 0
        state.declared = False
        state.user_message_detected = False
        return "exit"

    # Clear user message flag even if not active
    state.user_message_detected = False

    if state.active:
        state.total_events_in_habit += 1
        # Check exit condition
        if state.habit_score < exit_score:
            state.active = False
            state.sustain_counter = 0
            return "exit"
        return None
    else:
        # Check enter condition with sustain
        if state.habit_score >= enter_score:
            state.sustain_counter += 1
            if state.sustain_counter >= sustain_calls:
                state.active = True
                state.entered_at_event = event_counter
                state.total_events_in_habit = 0
                return "enter"
        else:
            state.sustain_counter = 0
        return None


# ── User Message Handling ────────────────────────────────────────────

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


def _classify_user_message(text: str) -> str:
    """Classify a user message as directive, continuation, or clarification.

    Returns: "directive", "continuation", or "clarification"

    Rules:
    - Short (<= 15 chars) and matches continuation keywords → continuation
    - Contains directive keywords → directive
    - Long (> 50 chars) or multi-sentence → directive
    - Short with ? → clarification
    - Default: directive (safe fallback)
    """
    stripped = text.strip().lower()

    if not stripped:
        return "continuation"

    # Check continuation first (short confirmations)
    if len(stripped) <= 15 and stripped.rstrip("!.") in _CONTINUATION_KEYWORDS:
        return "continuation"

    # Check directive keywords
    for kw in _DIRECTIVE_KEYWORDS:
        if kw in stripped:
            return "directive"

    # Long messages are likely new instructions
    if len(stripped) > 50:
        return "directive"

    # Multi-sentence detection
    sentences = [s.strip() for s in stripped.split(".") if s.strip()]
    if len(sentences) > 1:
        return "directive"

    # Short questions → clarification
    if stripped.endswith("?") and len(stripped) <= 50:
        return "clarification"

    # Default to directive for safety
    return "directive"


def signal_user_message(state: HabitModeState, message_text: str) -> str:
    """Handle a user message in the context of habit mode.

    Graded response:
    - "directive" → catastrophic reset (active→False, sustain→0, declared→False, score→0.0)
    - "continuation" → no effect
    - "clarification" → gentle decay (-0.15 from habit_score)

    Returns the classification string.
    """
    msg_type = _classify_user_message(message_text)

    if msg_type == "directive":
        state.user_message_detected = True
        # If currently active, immediately collapse
        if state.active:
            state.active = False
            state.sustain_counter = 0
            state.declared = False
            state.habit_score = 0.0
    elif msg_type == "clarification":
        state.habit_score = max(0.0, state.habit_score - 0.15)
    # "continuation" → no effect

    return msg_type


# ── Declaration API ──────────────────────────────────────────────────


def declare_mode(
    state: HabitModeState,
    mode: str,
    event_counter: int,
) -> str | None:
    """Agent self-declares mode. Immediate — skips sustain wait.

    Args:
        state: Current habit mode state.
        mode: "habit" or "standard"
        event_counter: Current event counter.

    Returns: "enter", "exit", or None.
    """
    if mode == "habit":
        if not state.active:
            state.active = True
            state.declared = True
            state.entered_at_event = event_counter
            state.total_events_in_habit = 0
            state.sustain_counter = DEFAULT_SUSTAIN_CALLS  # Skip sustain
            state.habit_score = compute_habit_score(state)
            return "enter"
        else:
            state.declared = True
            return None
    elif mode == "standard":
        was_active = state.active
        state.active = False
        state.declared = False
        state.sustain_counter = 0
        return "exit" if was_active else None
    return None


# ── Quick Intent (for standalone path) ───────────────────────────────


def quick_intent(tool_name: str) -> str:
    """Cheap intent classification for standalone path (no command_sig).

    Maps tool names to intent categories without parsing Bash commands.
    """
    if tool_name in _INSPECT_TOOLS:
        return "inspect"
    if tool_name in _MODIFY_TOOLS:
        return "modify"
    if tool_name in _META_TOOLS:
        return "meta"
    if tool_name == "Bash":
        return "execute"
    return "unknown"


# ── Compaction Strategy ──────────────────────────────────────────────

# Per-section token budgets (approximate)
_SECTION_BUDGETS = {
    "mode": 50,
    "active_context": 100,
    "theory_digest": 1500,
    "lint_state": 400,
    "behavioral_trajectory": 300,
    "recurring_issues": 150,
    "session_history": 100,
    "coherence_trajectory": 30,
    "token_state": 50,
    "tool_guidance": 300,
}

# Truncation order: low priority → high priority (truncated first)
_TRUNCATION_ORDER = [
    "session_history",
    "recurring_issues",
    "behavioral_trajectory",
    "lint_state",
    "coherence_trajectory",
    # These are NEVER truncated:
    # "mode", "active_context", "token_state", "tool_guidance", "theory_digest"
]


def build_compaction_snapshot(
    state: HabitModeState,
    project_root: str,
    *,
    session_memory: dict[str, Any] | None = None,
    compass: dict[str, Any] | None = None,
    last_lint_run: dict[str, Any] | None = None,
    theory_pack: dict[str, Any] | None = None,
    issue_memory: dict[str, Any] | None = None,
    token_estimate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the structured compaction snapshot.

    Produces a JSON document with 10 sections, subject to hard per-section
    token budgets and deterministic truncation if snapshot exceeds
    SNAPSHOT_MAX_CHARS.

    Args:
        state: Current habit mode state.
        project_root: Project path.
        session_memory: Serialized SessionMemory dict (optional).
        compass: Serialized BehaviorCompass dict (optional).
        last_lint_run: Last lint run details dict (optional).
        theory_pack: Theory pack from build_theory_pack (optional).
        issue_memory: Issue memory data (optional).
        token_estimate: Token tracker usage summary (optional).

    Returns:
        Structured snapshot dict ready for injection post-compaction.
    """
    snapshot: dict[str, Any] = {}

    # 1. Mode (50 tokens — fixed structure, never exceeds)
    snapshot["mode"] = {
        "active": state.active,
        "habit_score": round(state.habit_score, 2),
        "declared": state.declared,
        "compaction_number": state.compaction_count + 1,
    }

    # 2. Active context (100 tokens — cap at 10 files)
    active_files = state.active_files[:10]
    # Truncate to basenames if too many chars
    total_chars = sum(len(f) for f in active_files)
    if total_chars > 400:
        active_files = [f.rsplit("/", 1)[-1] for f in active_files]
    snapshot["active_context"] = {
        "files": active_files,
        "last_test_status": state.last_test_status,
    }

    # 3. Theory digest (1500 tokens — already capped by build_theory_pack)
    if theory_pack:
        snapshot["theory_digest"] = theory_pack
    else:
        snapshot["theory_digest"] = None

    # 4. Lint state (400 tokens — cap at 5 blocking issues)
    if last_lint_run:
        blocking_issues = []
        for issue in last_lint_run.get("issues", [])[:5]:
            msg = str(issue.get("message", ""))[:80]
            blocking_issues.append(
                {
                    "file": issue.get("file", ""),
                    "line": issue.get("line"),
                    "kind": issue.get("kind", ""),
                    "message": msg,
                }
            )
        snapshot["lint_state"] = {
            "blocking_count": last_lint_run.get("blocking_count", 0),
            "warning_count": last_lint_run.get("warning_count", 0),
            "issues": blocking_issues,
        }
    else:
        snapshot["lint_state"] = None

    # 5. Behavioral trajectory (300 tokens — top 3 constraints, top 2 errors)
    if compass:
        hypotheses = compass.get("hypotheses", [])
        # Sort by confidence desc, take top 3
        top_hyps = sorted(hypotheses, key=lambda h: h.get("confidence", 0), reverse=True)[:3]
        hyp_summaries = [
            {"claim": h.get("claim", "")[:80], "confidence": h.get("confidence", 0)}
            for h in top_hyps
        ]
        # Top 2 errors from error_memory
        error_mem = compass.get("error_memory", {})
        top_errors = sorted(
            error_mem.items(),
            key=lambda kv: kv[1].get("count", 0) if isinstance(kv[1], dict) else 0,
            reverse=True,
        )[:2]
        error_summaries = [
            {"sig": k[:60], "count": v.get("count", 0) if isinstance(v, dict) else 0}
            for k, v in top_errors
        ]
        # Prediction accuracy
        coverage = compass.get("coverage", {})
        snapshot["behavioral_trajectory"] = {
            "top_constraints": hyp_summaries,
            "top_errors": error_summaries,
            "prediction_recall": coverage.get("prediction_recall", 0.0),
        }
    else:
        snapshot["behavioral_trajectory"] = None

    # 6. Recurring issues (150 tokens — top 3)
    if issue_memory:
        recurrent = issue_memory.get("recurrent_issues", [])[:3]
        snapshot["recurring_issues"] = recurrent
    else:
        snapshot["recurring_issues"] = None

    # 7. Session history (100 tokens — last 2 snapshots)
    if session_memory:
        snapshots = session_memory.get("snapshots", [])
        recent = snapshots[-2:] if len(snapshots) >= 2 else snapshots
        snapshot["session_history"] = [
            {
                "coherence": s.get("coherence_state", ""),
                "blocking": s.get("blocking_count", 0),
                "findings": s.get("finding_count", 0),
            }
            for s in recent
        ]
    else:
        snapshot["session_history"] = None

    # 8. Coherence trajectory (30 tokens — last 3 states)
    if session_memory:
        trajectory = session_memory.get("coherence_trajectory", [])
        snapshot["coherence_trajectory"] = trajectory[-3:]
    else:
        snapshot["coherence_trajectory"] = None

    # 9. Token state (50 tokens — fixed structure)
    if token_estimate:
        snapshot["token_state"] = {
            "estimated_used": token_estimate.get("estimated_tokens_used", 0),
            "tool_calls": token_estimate.get("tool_call_count", 0),
            "lines_written": token_estimate.get("lines_written", 0),
        }
    else:
        snapshot["token_state"] = None

    # 10. Tool guidance (300 tokens — max 4 injections)
    injections = _build_tool_injections(state, compass, last_lint_run, session_memory)
    snapshot["tool_guidance"] = injections

    # Focus directive
    focus_files = ", ".join(state.active_files[:3]) if state.active_files else "none"
    test_str = f"Test: {state.last_test_status}." if state.last_test_status else ""
    snapshot["focus_directive"] = f"You are in Habit Mode. Focus: [{focus_files}]. {test_str}"

    # Enforce hard cap
    _enforce_snapshot_cap(snapshot)

    return snapshot


def _enforce_snapshot_cap(snapshot: dict[str, Any]) -> None:
    """Truncate sections in priority order if snapshot exceeds SNAPSHOT_MAX_CHARS."""
    serialized = json.dumps(snapshot, separators=(",", ":"))
    if len(serialized) <= SNAPSHOT_MAX_CHARS:
        return

    # Truncate in reverse priority order
    for section_key in _TRUNCATION_ORDER:
        if section_key in snapshot and snapshot[section_key] is not None:
            snapshot[section_key] = None
            serialized = json.dumps(snapshot, separators=(",", ":"))
            if len(serialized) <= SNAPSHOT_MAX_CHARS:
                return


def _build_tool_injections(
    state: HabitModeState,
    compass: dict[str, Any] | None,
    last_lint_run: dict[str, Any] | None,
    session_memory: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build conditional tool injection recommendations.

    Based on behavioral metrics, suggest underused LintGate tools.
    Max 4 injections, 80 char reasons.
    """
    injections: list[dict[str, Any]] = []

    # Priority 1: edit_streak > 5 AND no test → suggest prediction_register
    if state.signals.edit_streak > 5 and not state.signals.test_in_last_n:
        injections.append(
            {
                "tool": "prediction_register",
                "priority": 1,
                "reason": "5+ edits without test. Register a prediction before running tests."[:80],
            }
        )

    # Priority 1: blocking lint issues > 3 → suggest lint_fix
    if last_lint_run:
        blocking = last_lint_run.get("blocking_count", 0)
        if blocking > 3:
            injections.append(
                {
                    "tool": "lint_fix",
                    "priority": 1,
                    "reason": f"{blocking} blocking lint issues. Auto-fix safe issues."[:80],
                }
            )

    # Priority 2: recent coherence = "systemic" → suggest controlplane_run
    if session_memory:
        trajectory = session_memory.get("coherence_trajectory", [])
        if trajectory and trajectory[-1] == "systemic":
            injections.append(
                {
                    "tool": "controlplane_run",
                    "priority": 2,
                    "reason": "Systemic coherence state detected. Run full analysis."[:80],
                }
            )

    # Priority 1: 2+ recent failed approaches → suggest constraint_check
    if compass:
        approaches = compass.get("approaches", [])
        recent_failed = sum(
            1 for a in approaches[-5:] if isinstance(a, dict) and a.get("outcome") == "failed"
        )
        if recent_failed >= 2:
            injections.append(
                {
                    "tool": "constraint_check",
                    "priority": 1,
                    "reason": f"{recent_failed} recent failed approaches. Check constraints."[:80],
                }
            )

    # Priority 3: always include habit_status reference
    if len(injections) < 4:
        injections.append(
            {
                "tool": "habit_status",
                "priority": 3,
                "reason": "Check habit mode state and token economics."[:80],
            }
        )

    # Cap at 4
    injections.sort(key=lambda x: x.get("priority", 3))
    return injections[:4]


# ── Session-backed persistence (Path A) ──────────────────────────────


def load_habit_state(behavior_compass_dict: dict[str, Any]) -> HabitModeState:
    """Load habit state from session.behavior_compass dict."""
    data = behavior_compass_dict.get("habit_mode", {})
    return HabitModeState.from_dict(data)


def save_habit_state(behavior_compass_dict: dict[str, Any], state: HabitModeState) -> None:
    """Save habit state into session.behavior_compass dict."""
    behavior_compass_dict["habit_mode"] = state.to_dict()


# ── File-backed persistence (Path B) ─────────────────────────────────

_HABIT_STATE_DIR = Path.home() / ".claude" / "lintgate" / "habit_state"


def _project_hash(project_root: str) -> str:
    """Generate stable hash for a project path."""
    return hashlib.sha256(project_root.encode()).hexdigest()[:16]


def _standalone_path(project_root: str) -> Path:
    """Get standalone state file path for a project."""
    return _HABIT_STATE_DIR / f"{_project_hash(project_root)}.json"


def load_habit_state_standalone(
    project_root: str,
) -> tuple[HabitModeState, list[dict[str, Any]]]:
    """Load standalone file-backed habit state.

    Returns (HabitModeState, action_ring) tuple.
    Action ring is a minimal 30-entry buffer of {tool, ts, intent} dicts.
    On corruption or missing file, returns fresh state.
    """
    state_path = _standalone_path(project_root)
    if not state_path.exists():
        return HabitModeState(), []

    try:
        with open(state_path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return HabitModeState(), []

        state = HabitModeState.from_dict(data.get("habit_state", {}))
        action_ring = data.get("action_ring", [])
        if not isinstance(action_ring, list):
            action_ring = []

        return state, action_ring
    except (json.JSONDecodeError, OSError, KeyError):
        return HabitModeState(), []


def load_standalone_extras(project_root: str) -> dict[str, Any]:
    """Load non-core standalone payload fields.

    Returns a dict with optional keys:
    - token_tracker: serialized TokenTrackerState
    - config_overrides: standalone habit_configure overrides
    - habit_last_snapshot: latest auto-compaction snapshot
    - write_scheduler: serialized WriteScheduler state
    """
    state_path = _standalone_path(project_root)
    if not state_path.exists():
        return {}

    try:
        with open(state_path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        extras: dict[str, Any] = {}
        for key in (
            "token_tracker",
            "config_overrides",
            "habit_last_snapshot",
            "write_scheduler",
        ):
            if key in data:
                extras[key] = data[key]
        return extras
    except (json.JSONDecodeError, OSError):
        return {}


def _load_existing_standalone(state_path: Path) -> dict[str, Any]:
    """Load existing standalone state file, returning empty dict on failure."""
    if not state_path.exists():
        return {}
    try:
        with open(state_path) as f:
            loaded = json.load(f)
        return loaded if isinstance(loaded, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _merge_optional_field(
    data: dict[str, Any],
    existing: dict[str, Any],
    key: str,
    new_value: dict[str, Any] | None,
) -> None:
    """Merge an optional dict field: prefer new_value, fall back to existing."""
    if new_value is not None and isinstance(new_value, dict):
        data[key] = new_value
    elif isinstance(existing.get(key), dict):
        data[key] = existing[key]


def save_habit_state_standalone(
    project_root: str,
    state: HabitModeState,
    action_ring: list[dict[str, Any]],
    *,
    tracker_dict: dict[str, Any] | None = None,
    config_overrides: dict[str, Any] | None = None,
    last_snapshot: dict[str, Any] | None = None,
    scheduler_dict: dict[str, Any] | None = None,
) -> None:
    """Save standalone file-backed habit state.

    Args:
        project_root: Project path.
        state: Current habit mode state.
        action_ring: Minimal action ring buffer.
        tracker_dict: Optional serialized TokenTrackerState.
        config_overrides: Optional standalone config overrides.
        last_snapshot: Optional latest compaction snapshot.
        scheduler_dict: Optional serialized WriteScheduler state.
    """
    try:
        _HABIT_STATE_DIR.mkdir(parents=True, exist_ok=True)
        state_path = _standalone_path(project_root)
        existing = _load_existing_standalone(state_path)

        data: dict[str, Any] = {
            "habit_state": state.to_dict(),
            "action_ring": action_ring[-MAX_ACTION_RING:],
        }
        _merge_optional_field(data, existing, "token_tracker", tracker_dict)
        _merge_optional_field(data, existing, "config_overrides", config_overrides)
        _merge_optional_field(data, existing, "habit_last_snapshot", last_snapshot)
        _merge_optional_field(data, existing, "write_scheduler", scheduler_dict)

        with open(state_path, "w") as f:
            json.dump(data, f, separators=(",", ":"))
    except OSError:
        pass  # Non-fatal
