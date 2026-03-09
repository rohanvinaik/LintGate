"""Diff analysis helpers for the change classifier.

Extracts changed files from tool events, groups files by language,
and analyses the textual diff to detect structural / import-only /
formatting-only changes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..types import DiffAnalysis
from .file_type_utils import (
    _LANG_MAP,
    _is_build_command,
    _is_readonly_bash,
    _resolve_path,
)

# ─── Diff-specific regex patterns ────────────────────────────────────────

_IMPORT_PATTERN = re.compile(r"^\s*(import |from \S+ import )")
_FUNC_SIG_PATTERN = re.compile(r"^\s*(async\s+)?def\s+\w+")
_CLASS_PATTERN = re.compile(r"^\s*class\s+\w+")


# ─── Helpers ─────────────────────────────────────────────────────────────


def _as_text(value: Any) -> str:
    """Coerce arbitrary payload values to text for analysis."""
    return value if isinstance(value, str) else ""


# ─── File extraction ─────────────────────────────────────────────────────


def _extract_changed_files(tool_name: str, tool_input: dict[str, Any], cwd: str) -> list[str]:
    """Extract the list of files affected by this tool use."""

    if tool_name in ("Write", "Edit", "MultiEdit"):
        fp = tool_input.get("file_path", "")
        if fp:
            resolved = _resolve_path(fp, cwd)
            if resolved:
                return [resolved]

    if tool_name == "Bash":
        # For Bash, we can't always know what files changed.
        # Check for common patterns in the command.
        command = _as_text(tool_input.get("command", ""))

        # Skip read-only commands
        if _is_readonly_bash(command):
            return []

        # Build commands affect the whole project — but we don't
        # lint individual files for those, we just flag it
        if _is_build_command(command):
            return []  # Handled separately via change_kind="build"

        # For other bash commands, we don't have file-level granularity
        return []

    return []


# ─── Language detection ──────────────────────────────────────────────────


def _group_by_language(files: list[str]) -> dict[str, list[str]]:
    """Group files by detected programming language."""
    groups: dict[str, list[str]] = {}
    for f in files:
        ext = Path(f).suffix.lower()
        lang = _LANG_MAP.get(ext, "other")
        groups.setdefault(lang, []).append(f)
    return groups


# ─── Diff analysis ───────────────────────────────────────────────────────


def _analyze_diff(tool_name: str, tool_input: dict[str, Any]) -> DiffAnalysis:
    """Analyze the actual text changes from Write/Edit/MultiEdit."""

    if tool_name == "Edit":
        old_text = _as_text(tool_input.get("old_string", ""))
        new_text = _as_text(tool_input.get("new_string", ""))
        is_new = False
    elif tool_name == "Write":
        old_text = ""
        new_text = _as_text(tool_input.get("content", ""))
        is_new = True
    elif tool_name == "MultiEdit":
        edits = tool_input.get("edits", [])
        if not isinstance(edits, list):
            edits = []
        old_text = "\n".join(
            _as_text(e.get("old_string", "")) for e in edits if isinstance(e, dict)
        )
        new_text = "\n".join(
            _as_text(e.get("new_string", "")) for e in edits if isinstance(e, dict)
        )
        is_new = False
    elif tool_name == "Bash":
        command = _as_text(tool_input.get("command", ""))
        return DiffAnalysis(is_build_command=_is_build_command(command))
    else:
        return DiffAnalysis.empty()

    # Line-level analysis
    old_lines = set(old_text.strip().splitlines()) if old_text else set()
    new_lines = set(new_text.strip().splitlines()) if new_text else set()
    added = new_lines - old_lines
    removed = old_lines - new_lines
    changed_lines = added | removed

    # Check if only import statements changed
    import_only = bool(changed_lines) and all(
        _IMPORT_PATTERN.match(line) for line in changed_lines if line.strip()
    )

    # Check if function signatures changed
    func_sigs_changed = any(_FUNC_SIG_PATTERN.match(line) for line in changed_lines)

    # Check if class definitions changed
    class_changed = any(_CLASS_PATTERN.match(line) for line in changed_lines)

    # Check if only formatting/whitespace changed
    formatting_only = False
    if old_text and new_text:
        # Strip all whitespace and compare
        old_stripped = re.sub(r"\s+", "", old_text)
        new_stripped = re.sub(r"\s+", "", new_text)
        formatting_only = old_stripped == new_stripped

    return DiffAnalysis(
        lines_added=len(added),
        lines_removed=len(removed),
        import_only=import_only,
        function_signatures_changed=func_sigs_changed,
        class_structure_changed=class_changed,
        is_new_file=is_new,
        formatting_only=formatting_only,
    )
