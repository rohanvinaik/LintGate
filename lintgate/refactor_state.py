"""Session-aware refactor checkpointing (#199).

Persistent file-level progress tracking for multi-session refactoring work.
State is stored in .lintgate/refactor_state.json and survives context-window
compactions and session handoffs.

State lifecycle:
1. Start — agent calls refactor_thesis after initial controlplane_run
2. Work — after fixing each file, calls refactor_checkpoint
3. Compact — habit_compact includes refactor state in snapshot
4. Resume — new session calls refactor_resume for structured state
5. Complete — when all files are completed/skipped, state is archived

No LLM calls, no I/O beyond the state file.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FileProgress:
    """Progress tracking for a single file in the refactoring session."""

    status: str = "pending"  # pending | in_progress | completed | skipped
    initial_findings: int = 0
    remaining_findings: int = 0
    patterns_applied: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PatternRecord:
    """Tracking for a refactoring pattern applied across files."""

    description: str = ""
    files_applied: list[str] = field(default_factory=list)
    count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RefactorState:
    """Persistent refactoring session state."""

    session_id: str = ""
    started_at: str = ""
    updated_at: str = ""
    thesis: str = ""
    files: dict[str, FileProgress] = field(default_factory=dict)
    applied_patterns: dict[str, PatternRecord] = field(default_factory=dict)
    last_controlplane_run: str = ""
    last_finding_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "thesis": self.thesis,
            "files": {k: v.to_dict() for k, v in self.files.items()},
            "applied_patterns": {k: v.to_dict() for k, v in self.applied_patterns.items()},
            "last_controlplane_run": self.last_controlplane_run,
            "last_finding_counts": self.last_finding_counts,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> RefactorState:
        files = {}
        for k, v in data.get("files", {}).items():
            files[k] = FileProgress(**v)
        patterns = {}
        for k, v in data.get("applied_patterns", {}).items():
            patterns[k] = PatternRecord(**v)
        return RefactorState(
            session_id=data.get("session_id", ""),
            started_at=data.get("started_at", ""),
            updated_at=data.get("updated_at", ""),
            thesis=data.get("thesis", ""),
            files=files,
            applied_patterns=patterns,
            last_controlplane_run=data.get("last_controlplane_run", ""),
            last_finding_counts=data.get("last_finding_counts", {}),
        )


def _state_path(project_root: str) -> Path:
    """Return the path to the refactor state file."""
    return Path(project_root) / ".lintgate" / "refactor_state.json"


def _archive_path(project_root: str) -> Path:
    """Return the path to the archive directory for completed refactor states."""
    return Path(project_root) / ".lintgate" / "refactor_archive"


def load_state(project_root: str) -> RefactorState | None:
    """Load the current refactor state, or None if no active session."""
    path = _state_path(project_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return RefactorState.from_dict(data)
    except (json.JSONDecodeError, OSError, TypeError, KeyError):
        return None


def save_state(project_root: str, state: RefactorState) -> None:
    """Save refactor state to disk."""
    path = _state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = _now_iso()
    path.write_text(json.dumps(state.to_dict(), indent=2) + "\n")


def _now_iso() -> str:
    """Return current time as ISO 8601 string."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _generate_session_id() -> str:
    """Generate a short session ID."""
    import hashlib

    return hashlib.sha256(str(time.time()).encode()).hexdigest()[:12]


# ── Public API ────────────────────────────────────────────────────────


def checkpoint(
    project_root: str,
    file_path: str,
    status: str,
    patterns_applied: list[str] | None = None,
    notes: str = "",
    initial_findings: int | None = None,
    remaining_findings: int | None = None,
) -> RefactorState:
    """Record progress on a file during a refactoring session.

    Creates a new refactor session if none exists. Updates the file's
    progress record and pattern tracking.

    Args:
        project_root: Absolute path to the project root.
        file_path: Relative path to the file being refactored.
        status: One of "completed", "in_progress", "skipped", "pending".
        patterns_applied: List of refactoring pattern names applied.
        notes: Free-text notes about the refactoring.
        initial_findings: Initial finding count (set once, not overwritten).
        remaining_findings: Current remaining finding count.

    Returns:
        The updated RefactorState.
    """
    if status not in ("pending", "in_progress", "completed", "skipped"):
        raise ValueError(f"Invalid status: {status}")

    state = load_state(project_root)
    if state is None:
        state = RefactorState(
            session_id=_generate_session_id(),
            started_at=_now_iso(),
        )

    # Update file progress
    fp = state.files.get(file_path, FileProgress())

    fp.status = status
    if initial_findings is not None and fp.initial_findings == 0:
        fp.initial_findings = initial_findings
    if remaining_findings is not None:
        fp.remaining_findings = remaining_findings
    if notes:
        fp.notes = notes

    # Track patterns
    if patterns_applied:
        for pattern in patterns_applied:
            if pattern not in fp.patterns_applied:
                fp.patterns_applied.append(pattern)
            # Update global pattern record
            pr = state.applied_patterns.get(pattern, PatternRecord())
            if file_path not in pr.files_applied:
                pr.files_applied.append(file_path)
                pr.count += 1
            state.applied_patterns[pattern] = pr

    state.files[file_path] = fp
    save_state(project_root, state)
    return state


def set_thesis(project_root: str, thesis: str) -> RefactorState:
    """Record or update the agent's structural thesis about the codebase.

    Creates a new refactor session if none exists.

    Args:
        project_root: Absolute path to the project root.
        thesis: The agent's thesis about the codebase structure.

    Returns:
        The updated RefactorState.
    """
    state = load_state(project_root)
    if state is None:
        state = RefactorState(
            session_id=_generate_session_id(),
            started_at=_now_iso(),
        )
    state.thesis = thesis
    save_state(project_root, state)
    return state


def resume(project_root: str) -> dict[str, Any]:
    """Load refactor state and provide a structured summary for session resumption.

    Returns a structured dict with:
    - thesis: the agent's recorded understanding
    - file_summary: counts by status
    - files: per-file progress
    - patterns: applied patterns with frequency
    - finding_trend: current vs. initial findings
    - recommended_next: suggested next file to work on
    """
    state = load_state(project_root)
    if state is None:
        return {
            "active": False,
            "message": "No active refactoring session. Use refactor_thesis to start one.",
        }

    # File summary
    status_counts: dict[str, int] = {
        "completed": 0,
        "in_progress": 0,
        "pending": 0,
        "skipped": 0,
    }
    total_initial = 0
    total_remaining = 0
    for fp in state.files.values():
        status_counts[fp.status] = status_counts.get(fp.status, 0) + 1
        total_initial += fp.initial_findings
        total_remaining += fp.remaining_findings

    # Find recommended next file
    recommended_next = _recommend_next_file(state)

    # Format patterns
    patterns_summary = {}
    for name, pr in state.applied_patterns.items():
        patterns_summary[name] = {
            "count": pr.count,
            "files": pr.files_applied,
        }
        if pr.description:
            patterns_summary[name]["description"] = pr.description

    result: dict[str, Any] = {
        "active": True,
        "session_id": state.session_id,
        "started_at": state.started_at,
        "updated_at": state.updated_at,
        "thesis": state.thesis,
        "file_summary": status_counts,
        "total_files": len(state.files),
        "finding_trend": {
            "initial": total_initial,
            "remaining": total_remaining,
            "resolved": total_initial - total_remaining,
        },
        "files": {k: v.to_dict() for k, v in state.files.items()},
        "patterns": patterns_summary,
    }

    if state.last_controlplane_run:
        result["last_controlplane_run"] = state.last_controlplane_run

    if state.last_finding_counts:
        result["last_finding_counts"] = state.last_finding_counts

    if recommended_next:
        result["recommended_next"] = recommended_next

    return result


def update_finding_counts(
    project_root: str,
    run_id: str,
    finding_counts: dict[str, int],
) -> None:
    """Auto-update finding counts after a controlplane_run.

    Called from controlplane_run integration. Does not create a new session.
    """
    state = load_state(project_root)
    if state is None:
        return
    state.last_controlplane_run = run_id
    state.last_finding_counts = finding_counts
    save_state(project_root, state)


def update_file_findings(
    project_root: str,
    file_path: str,
    remaining_findings: int,
) -> None:
    """Auto-update per-file remaining findings after a lint run.

    Called from lint_files integration. Does not create a new session.
    """
    state = load_state(project_root)
    if state is None:
        return
    if file_path in state.files:
        state.files[file_path].remaining_findings = remaining_findings
        save_state(project_root, state)


def archive_if_complete(project_root: str) -> bool:
    """Archive the refactor state if all files are completed or skipped.

    Returns True if archived, False otherwise.
    """
    state = load_state(project_root)
    if state is None:
        return False

    if not state.files:
        return False

    all_done = all(fp.status in ("completed", "skipped") for fp in state.files.values())
    if not all_done:
        return False

    # Archive
    archive_dir = _archive_path(project_root)
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_file = archive_dir / f"refactor_{state.session_id}.json"
    state_path = _state_path(project_root)

    shutil.copy2(str(state_path), str(archive_file))
    state_path.unlink()
    return True


def _recommend_next_file(state: RefactorState) -> str | None:
    """Recommend the next file to work on.

    Priority:
    1. in_progress files (continue where you left off)
    2. pending files with the most initial findings (biggest impact)
    """
    # First: any in_progress files
    in_progress = [f for f, fp in state.files.items() if fp.status == "in_progress"]
    if in_progress:
        return in_progress[0]

    # Second: pending files sorted by initial findings (descending)
    pending = [(f, fp) for f, fp in state.files.items() if fp.status == "pending"]
    if pending:
        pending.sort(key=lambda x: -x[1].initial_findings)
        return pending[0][0]

    return None
