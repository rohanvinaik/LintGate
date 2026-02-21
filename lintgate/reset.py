"""Scoped state reset with dry-run default and safety guarantees.

Safety invariants:
- dry_run=True by default — returns what WOULD be deleted.
- NEVER auto-deletes CLAUDE.md or AGENTS.md.
- Errors are caught and collected, never raised.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lintgate.compass import COMPASS_PATH

# ── Paths ─────────────────────────────────────────────────────────────

_SESSION_DIR = Path.home() / ".claude" / "lintgate" / "session"
_HABIT_STATE_DIR = Path.home() / ".claude" / "lintgate" / "habit_state"


def _lintgate_home() -> Path:
    """Resolve LINTGATE_HOME with fallback to ~/.lintgate."""
    env = os.environ.get("LINTGATE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".lintgate"


_PROTECTED_NAMES = {"CLAUDE.md", "AGENTS.md"}


# ── Data ──────────────────────────────────────────────────────────────


@dataclass
class ResetReport:
    deleted: list[dict[str, Any]] = field(default_factory=list)  # [{path, type, size_bytes}]
    preserved: list[dict[str, Any]] = field(default_factory=list)  # [{path, reason}]
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"deleted": self.deleted, "preserved": self.preserved, "errors": self.errors}


# ── Helpers ───────────────────────────────────────────────────────────


def _project_hash(project_root: str) -> str:
    """Generate a stable hash for a project path (matches session_memory / habit_mode)."""
    return hashlib.sha256(project_root.encode()).hexdigest()[:16]


def _file_entry(path: Path, file_type: str, *, deletable: bool = True) -> dict[str, Any]:
    """Build a state-file descriptor."""
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {"path": str(path), "type": file_type, "size_bytes": size, "deletable": deletable}


def _safe_delete(path: Path, file_type: str, report: ResetReport, *, dry_run: bool) -> None:
    """Delete a single file, appending to *report*."""
    if not path.exists():
        return
    entry = _file_entry(path, file_type)
    if dry_run:
        report.deleted.append(entry)
        return
    try:
        path.unlink()
        report.deleted.append(entry)
    except OSError as exc:
        report.errors.append(f"Failed to delete {path}: {exc}")


def _add_protected(project_root: str, report: ResetReport) -> None:
    """Record protected files that will never be auto-deleted."""
    root = Path(project_root)
    for name in sorted(_PROTECTED_NAMES):
        candidate = root / ".claude" / name
        if candidate.exists():
            report.preserved.append({"path": str(candidate), "reason": "protected — never auto-deleted"})


# ── Public API ────────────────────────────────────────────────────────


def enumerate_project_state(project_root: str) -> list[dict[str, Any]]:
    """List all LintGate state files for this project: [{path, type, size_bytes, deletable}]."""
    entries: list[dict[str, Any]] = []
    root = Path(project_root)
    phash = _project_hash(os.path.realpath(project_root))

    # Compass
    compass = root / COMPASS_PATH
    if compass.exists():
        entries.append(_file_entry(compass, "compass"))

    # Config (not deletable — user-authored)
    config = root / ".claude" / "lintgate.yaml"
    if config.exists():
        entries.append(_file_entry(config, "config", deletable=False))

    # Session memory
    session_file = _SESSION_DIR / f"{phash}.json"
    if session_file.exists():
        entries.append(_file_entry(session_file, "session"))

    # Habit mode state
    habit_file = _HABIT_STATE_DIR / f"{phash}.json"
    if habit_file.exists():
        entries.append(_file_entry(habit_file, "habit_state"))

    # Protected files (listed but not deletable)
    for name in sorted(_PROTECTED_NAMES):
        candidate = root / ".claude" / name
        if candidate.exists():
            entries.append(_file_entry(candidate, "protected", deletable=False))

    return entries


def reset_compass_only(project_root: str, dry_run: bool = True) -> ResetReport:
    """Delete .claude/compass.yaml only."""
    report = ResetReport()
    compass = Path(project_root) / COMPASS_PATH
    _safe_delete(compass, "compass", report, dry_run=dry_run)
    _add_protected(project_root, report)
    return report


def reset_session_only(project_root: str, dry_run: bool = True) -> ResetReport:
    """Delete session memory for this project (in ~/.claude/lintgate/session/)."""
    report = ResetReport()
    phash = _project_hash(os.path.realpath(project_root))
    session_file = _SESSION_DIR / f"{phash}.json"
    _safe_delete(session_file, "session", report, dry_run=dry_run)
    _add_protected(project_root, report)
    return report


def reset_project(project_root: str, dry_run: bool = True) -> ResetReport:
    """Delete compass + session + habit mode state for this project.

    NEVER auto-deletes CLAUDE.md or AGENTS.md.
    """
    report = ResetReport()
    root = Path(project_root)
    phash = _project_hash(os.path.realpath(project_root))

    # Compass
    _safe_delete(root / COMPASS_PATH, "compass", report, dry_run=dry_run)

    # Session memory
    _safe_delete(_SESSION_DIR / f"{phash}.json", "session", report, dry_run=dry_run)

    # Habit mode state
    _safe_delete(_HABIT_STATE_DIR / f"{phash}.json", "habit_state", report, dry_run=dry_run)

    _add_protected(project_root, report)
    return report


def reset_global(dry_run: bool = True) -> ResetReport:
    """Delete all session memory, all habit mode state, and model profiles.

    NEVER auto-deletes any CLAUDE.md or AGENTS.md.
    """
    report = ResetReport()

    # All session files
    if _SESSION_DIR.is_dir():
        try:
            for f in sorted(_SESSION_DIR.iterdir()):
                if f.is_file() and f.suffix == ".json":
                    _safe_delete(f, "session", report, dry_run=dry_run)
        except OSError as exc:
            report.errors.append(f"Failed to list {_SESSION_DIR}: {exc}")

    # All habit state files
    if _HABIT_STATE_DIR.is_dir():
        try:
            for f in sorted(_HABIT_STATE_DIR.iterdir()):
                if f.is_file() and f.suffix == ".json":
                    _safe_delete(f, "habit_state", report, dry_run=dry_run)
        except OSError as exc:
            report.errors.append(f"Failed to list {_HABIT_STATE_DIR}: {exc}")

    # Model profiles
    profiles_path = _lintgate_home() / "model_profiles.json"
    _safe_delete(profiles_path, "model_profiles", report, dry_run=dry_run)

    return report
