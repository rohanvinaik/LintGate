"""Habit Mode bootstrap — seed habit state from historical session data.

Parses Claude Code session JSONL files and converts tool call history into
the action ring format that the habit system expects. Produces per-project
baseline habit state, error memory, and token calibration.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from lintgate._habit_persist import (
    _HABIT_STATE_DIR,
    _project_hash,
    save_habit_state_standalone,
)
from lintgate._habit_signals import compute_habit_score, update_signals
from lintgate._habit_types import HabitModeState

# Intent mapping (mirrors resolve_intent / quick_intent)
_INSPECT_TOOLS = frozenset({"Read", "Grep", "Glob", "WebFetch", "WebSearch", "LSP"})
_MODIFY_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
_META_TOOLS = frozenset({"Task", "TaskCreate", "TaskUpdate", "TodoWrite", "Agent"})
_VERIFY_KEYWORDS = frozenset({"pytest", "test", "check", "lint", "mypy", "ruff"})


def _quick_intent(tool_name: str, input_data: dict) -> str:
    """Map tool name + input to intent category."""
    if tool_name in _INSPECT_TOOLS:
        return "inspect"
    if tool_name in _MODIFY_TOOLS:
        return "modify"
    if tool_name in _META_TOOLS:
        return "meta"
    if tool_name == "Bash":
        cmd = input_data.get("command", "")
        if any(kw in cmd.lower() for kw in _VERIFY_KEYWORDS):
            return "verify"
        return "execute"
    return "unknown"


def _tool_call_to_action(tc) -> dict[str, Any]:
    """Convert a ToolCall to an action ring entry.

    Matches exact live Path B format: {tool, ts, sig, intent} + optional {exit, err}.
    """
    sig = tc.input_data.get("file_path") or tc.input_data.get("command", "")[:80] or ""
    entry: dict[str, Any] = {
        "tool": tc.tool_name,
        "ts": tc.timestamp,
        "sig": sig,
        "intent": _quick_intent(tc.tool_name, tc.input_data),
    }
    if tc.exit_code is not None:
        entry["exit"] = tc.exit_code
    if tc.error_text:
        entry["err"] = tc.error_text[:200]
    return entry


def _accumulate_error_memory(error_memory: dict[str, dict[str, Any]], tc) -> None:
    """Record error signature from a failed Bash tool call."""
    if tc.tool_name != "Bash":
        return
    if not ((tc.exit_code is not None and tc.exit_code != 0) or tc.error_text):
        return
    err_sig = (tc.error_text or "").split("\n")[0].strip()[:120]
    if not err_sig:
        return
    if err_sig not in error_memory:
        error_memory[err_sig] = {
            "count": 0,
            "first_seen": tc.timestamp,
            "last_seen": tc.timestamp,
        }
    error_memory[err_sig]["count"] += 1
    error_memory[err_sig]["last_seen"] = max(error_memory[err_sig]["last_seen"], tc.timestamp)


def _collect_actions(
    sessions: list,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], int, int]:
    """Collect actions, error memory, and token/char counts from sessions.

    Returns (all_actions, error_memory, total_tokens, total_chars).
    """
    all_actions: list[dict[str, Any]] = []
    error_memory: dict[str, dict[str, Any]] = {}
    total_tokens = 0
    total_chars = 0

    for session in sessions:
        for exchange in session.exchanges:
            for tc in exchange.tool_calls:
                all_actions.append(_tool_call_to_action(tc))
                _accumulate_error_memory(error_memory, tc)
            if exchange.user_text:
                total_chars += len(exchange.user_text)
        usage = session.total_token_usage
        total_tokens += usage.get("input", 0) + usage.get("output", 0)

    return all_actions, error_memory, total_tokens, total_chars


class HabitBootstrapper:
    """Bootstrap habit state from historical Claude Code sessions."""

    def bootstrap_project(self, sessions: list) -> dict[str, Any]:
        """Bootstrap habit state for one project from parsed SessionRecords.

        Args:
            sessions: List of SessionRecord objects for the same project.

        Returns:
            Summary dict with project_path, action_count, habit_score, etc.
        """
        if not sessions:
            return {"error": "no sessions"}

        project_path = sessions[0].project_path or sessions[0].cwd
        if not project_path:
            return {"error": "no project path"}

        all_actions, error_memory, total_tokens, total_chars = _collect_actions(sessions)

        # Sort by timestamp, take last 30 as action ring
        all_actions.sort(key=lambda a: a.get("ts", 0))
        action_ring = all_actions[-30:]

        # Compute habit signals and score
        state = HabitModeState()
        state.active = False
        state.declared = False
        if action_ring:
            update_signals(state, action_ring)
            state.habit_score = compute_habit_score(state)

        # Token calibration
        calibration_factor = total_tokens / max(total_chars, 1) if total_chars else 0.25
        tracker_dict: dict[str, Any] = {
            "calibration_factor": round(calibration_factor, 4),
            "calibration_count": 1,
            "estimated_tokens_used": 0,
            "tool_call_count": 0,
            "tool_calls_since_compact": 0,
            "lines_written": 0,
        }

        # Save habit state
        save_habit_state_standalone(
            project_path,
            state,
            action_ring,
            tracker_dict=tracker_dict,
        )

        # Save error memory as separate file
        if error_memory:
            self._save_error_memory(project_path, error_memory)

        return {
            "project_path": project_path,
            "sessions_count": len(sessions),
            "total_actions": len(all_actions),
            "action_ring_size": len(action_ring),
            "habit_score": round(state.habit_score, 3),
            "signals": state.signals.to_dict(),
            "error_signatures": len(error_memory),
            "calibration_factor": round(calibration_factor, 4),
            "total_tokens": total_tokens,
        }

    def bootstrap_all(self, sessions_root: Path | None = None) -> list[dict[str, Any]]:
        """Bootstrap all projects from session data.

        Args:
            sessions_root: Path to ~/.claude/projects (default).

        Returns:
            List of per-project summary dicts.
        """
        try:
            from mneme.ingest.session_parser import iter_sessions
        except ImportError:
            return [{"error": "mneme.ingest.session_parser not importable"}]

        # Group sessions by project
        projects: dict[str, list] = defaultdict(list)
        for session in iter_sessions(sessions_root):
            key = session.project_path or session.cwd or "unknown"
            projects[key].append(session)

        results = []
        for sessions in projects.values():
            summary = self.bootstrap_project(sessions)
            results.append(summary)

        return results

    @staticmethod
    def _save_error_memory(project_root: str, error_memory: dict) -> None:
        """Save error memory as a separate JSON file."""
        try:
            _HABIT_STATE_DIR.mkdir(parents=True, exist_ok=True)
            error_path = _HABIT_STATE_DIR / f"{_project_hash(project_root)}_errors.json"
            with open(error_path, "w") as f:
                json.dump(error_memory, f, separators=(",", ":"))
        except OSError:
            pass


def load_error_bootstrap(project_root: str) -> dict[str, Any]:
    """Load bootstrapped error memory for a project.

    Returns dict of {error_sig: {count, first_seen, last_seen}} or empty dict.
    """
    error_path = _HABIT_STATE_DIR / f"{_project_hash(project_root)}_errors.json"
    if not error_path.exists():
        return {}
    try:
        with open(error_path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}
