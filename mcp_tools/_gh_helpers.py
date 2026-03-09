"""Shared helpers for GitHub tools (gh CLI, repo detection, wiki operations).

Extracted from gh_tools.py. Used by _gh_organize_impl and _gh_wiki_impl.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

# GitHub remote detection pattern (reused from quality_helpers.py)
_GITHUB_REMOTE_RE = re.compile(
    r"(?:github\.com)[:/]([^/]+)/([^/\s]+?)(?:\.git)?(?:\s|$)",
)


def _run_gh(args: list[str], cwd: str | None = None) -> dict[str, Any]:
    """Run a ``gh`` CLI command and return parsed JSON or error dict."""
    try:
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=30,
        )
        if proc.returncode != 0:
            return {"error": proc.stderr.strip() or f"gh exited with {proc.returncode}"}
        if not proc.stdout.strip():
            return {}
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {"raw": proc.stdout.strip()}
    except FileNotFoundError:
        return {"error": "gh CLI not found. Install: https://cli.github.com/"}
    except subprocess.TimeoutExpired:
        return {"error": "gh command timed out after 30s"}


def _detect_repo(project_root: str) -> dict[str, str]:
    """Detect GitHub owner/repo from git remote."""
    try:
        proc = subprocess.run(
            ["git", "-C", project_root, "remote", "-v"],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in proc.stdout.splitlines():
            match = _GITHUB_REMOTE_RE.search(line)
            if match:
                return {"owner": match.group(1), "repo": match.group(2)}
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return {"owner": "", "repo": ""}


def _repo_full_name(project_root: str) -> str:
    """Return 'owner/repo' string or empty string."""
    info = _detect_repo(project_root)
    if info["owner"] and info["repo"]:
        return f"{info['owner']}/{info['repo']}"
    return ""


def _clone_wiki(repo_full: str, target_dir: str) -> dict[str, Any]:
    """Clone wiki repo (shallow) into target_dir."""
    url = f"https://github.com/{repo_full}.wiki.git"
    try:
        subprocess.run(
            ["git", "clone", "--depth=1", url, target_dir],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return {"ok": True}
    except subprocess.CalledProcessError as exc:
        return {"error": f"Wiki clone failed: {exc.stderr.strip()}"}
    except subprocess.TimeoutExpired:
        return {"error": "Wiki clone timed out after 30s"}


def _push_wiki(wiki_dir: str, message: str) -> dict[str, Any]:
    """Stage all changes, commit, and push in a wiki clone."""
    try:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=wiki_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        # Check if there's anything to commit
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=wiki_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        if not status.stdout.strip():
            return {"ok": True, "message": "No changes to push"}
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=wiki_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "push"],
            cwd=wiki_dir,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return {"ok": True}
    except subprocess.CalledProcessError as exc:
        return {"error": f"Wiki push failed: {exc.stderr.strip()}"}
    except subprocess.TimeoutExpired:
        return {"error": "Wiki push timed out after 30s"}
