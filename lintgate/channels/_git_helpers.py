"""Git subprocess helpers and working tree context collection.

Extracted from git_channel.py to keep the main channel file under 400 lines.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


def _is_git_repo(project_root: str) -> bool:
    """Check if the directory is inside a git repository."""
    git_dir = Path(project_root) / ".git"
    if git_dir.exists():
        return True
    # Check parent directories
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=project_root,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _collect_branch_name(project_root: str) -> str:
    """Get current git branch name. Returns empty string on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=project_root,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return ""


def _collect_file_status(project_root: str) -> tuple[list[str], list[str]]:
    """Parse git status --porcelain into (modified, untracked) file lists."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=project_root,
        )
        if result.returncode == 0:
            modified: list[str] = []
            untracked: list[str] = []
            for line in result.stdout.splitlines():
                if len(line) < 4:
                    continue
                status = line[:2]
                filepath = line[3:].strip().strip('"')
                if status == "??":
                    untracked.append(filepath)
                else:
                    modified.append(filepath)
            return modified, untracked
    except (subprocess.TimeoutExpired, OSError):
        pass
    return [], []


def _collect_loc_delta(project_root: str) -> tuple[int, int]:
    """Get uncommitted insertions and deletions from git diff --stat HEAD."""
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=project_root,
        )
        if result.returncode == 0:
            return _parse_diff_stat_totals(result.stdout)
    except (subprocess.TimeoutExpired, OSError):
        pass
    return 0, 0


def _parse_diff_stat_totals(stat_output: str) -> tuple[int, int]:
    """Parse git diff --stat output for total insertions and deletions.

    Returns (insertions, deletions). Returns (0, 0) if no summary line found.
    """
    for line in stat_output.splitlines():
        if "insertions" not in line and "deletions" not in line:
            continue
        ins_match = re.search(r"(\d+) insertion", line)
        del_match = re.search(r"(\d+) deletion", line)
        return (
            int(ins_match.group(1)) if ins_match else 0,
            int(del_match.group(1)) if del_match else 0,
        )
    return 0, 0


def collect_working_tree_context(project_root: str) -> dict:
    """Collect working tree state for git-aware scope signaling.

    Returns a dict with:
    - branch: current branch name
    - modified_files: list of modified tracked files
    - untracked_files: list of untracked files
    - modified_count / untracked_count: counts
    - uncommitted_loc_delta: estimated net line changes (insertions - deletions)
    - large_uncommitted_diff: True if uncommitted changes exceed threshold
    """
    context: dict = {
        "branch": "",
        "modified_files": [],
        "untracked_files": [],
        "modified_count": 0,
        "untracked_count": 0,
        "uncommitted_loc_delta": 0,
        "large_uncommitted_diff": False,
    }

    if not _is_git_repo(project_root):
        return context

    context["branch"] = _collect_branch_name(project_root)

    modified, untracked = _collect_file_status(project_root)
    context["modified_files"] = modified
    context["untracked_files"] = untracked
    context["modified_count"] = len(modified)
    context["untracked_count"] = len(untracked)

    ins, dels = _collect_loc_delta(project_root)
    context["uncommitted_loc_delta"] = ins - dels
    total_uncommitted = len(modified) + len(untracked)
    context["large_uncommitted_diff"] = total_uncommitted > 10 or (ins + dels) > 500

    return context


def classify_finding_scope(
    finding_file: str | None,
    modified_files: list[str],
    untracked_files: list[str],
    project_root: str,
) -> str:
    """Classify a finding's scope as committed, uncommitted, or new_file.

    Returns:
        ``"committed"``   -- file has no uncommitted changes
        ``"uncommitted"`` -- file has uncommitted modifications
        ``"new_file"``    -- file is untracked (not yet committed)
        ``"unknown"``     -- file path not available
    """
    if not finding_file:
        return "unknown"

    # Normalize to relative path for matching
    if os.path.isabs(finding_file):
        try:
            rel = os.path.relpath(finding_file, project_root)
        except ValueError:
            return "unknown"
    else:
        rel = finding_file

    # Normalize separators
    rel = rel.replace(os.sep, "/")

    if rel in untracked_files:
        return "new_file"
    if rel in modified_files:
        return "uncommitted"
    return "committed"
