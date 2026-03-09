"""Canonical file discovery for LintGate.

Single source of truth for "what files belong to this project."
Primary path: git ls-files (respects .gitignore automatically).
Fallback path: os.walk with CANONICAL_EXCLUDE_DIRS.

Every LintGate tool that needs to discover project files should call
discover_project_files() instead of rolling its own os.walk.
"""

from __future__ import annotations

import logging
import os
import subprocess

from .path_filters import is_backup_like_directory

_log = logging.getLogger(__name__)

# Superset of all exclusion sets previously scattered across the codebase.
CANONICAL_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        # Version control
        ".git",
        # Python caches / build artifacts
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
        ".nox",
        ".eggs",
        "dist",
        "build",
        # Virtual environments
        ".venv",
        "venv",
        "env",
        # JavaScript / Node
        "node_modules",
        # Mutation testing artifacts
        "mutants",
        ".mutmut",
        ".mutmut-cache",
    }
)


def should_skip_dir(dirname: str) -> bool:
    """Return True if *dirname* should be excluded from project file discovery.

    Checks:
    1. CANONICAL_EXCLUDE_DIRS membership
    2. Hidden directories (start with '.')
    3. Backup-like directories (via path_filters)
    """
    if not dirname:
        return False
    if dirname in CANONICAL_EXCLUDE_DIRS:
        return True
    if dirname.startswith("."):
        return True
    return is_backup_like_directory(dirname)


def discover_project_files(
    project_root: str,
    suffix: str = ".py",
    limit: int | None = None,
    extra_exclude_dirs: frozenset[str] | None = None,
    source_paths: list[str] | None = None,
) -> list[str]:
    """Discover project files with the given suffix.

    Primary path: ``git ls-files`` — automatically respects .gitignore,
    returns only repo-owned + untracked-but-not-ignored files.

    Fallback path (non-git repos or git failure): ``os.walk`` with
    CANONICAL_EXCLUDE_DIRS pruning.

    Args:
        project_root: Absolute path to the project root directory.
        suffix: File extension to match (default ".py").
        limit: Maximum number of files to return. None = unlimited.
        extra_exclude_dirs: Additional directory names to skip on top
            of CANONICAL_EXCLUDE_DIRS.
        source_paths: If set, constrain discovery to these subdirectories
            (relative to project_root). Only files under these paths are
            returned.

    Returns:
        Sorted list of absolute file paths.
    """
    if not project_root or not os.path.isdir(project_root):
        return []

    project_root = os.path.abspath(project_root)

    files = _git_discover(project_root, suffix)
    if files is None:
        # git not available or not a git repo — fall back to os.walk
        files = _walk_discover(project_root, suffix, extra_exclude_dirs)
    elif extra_exclude_dirs:
        # git ls-files doesn't know about extra_exclude_dirs — post-filter
        files = [
            f for f in files if not _path_contains_excluded_dir(f, project_root, extra_exclude_dirs)
        ]

    # Apply source_paths constraint
    if source_paths:
        allowed_prefixes = tuple(
            os.path.join(project_root, sp.rstrip(os.sep)) + os.sep for sp in source_paths
        )
        files = [f for f in files if any(f.startswith(p) for p in allowed_prefixes)]

    # Filter site-packages / dist-packages (symlink resolution)
    files = [
        f
        for f in files
        if "/site-packages/" not in os.path.realpath(f)
        and "/dist-packages/" not in os.path.realpath(f)
    ]

    # Filter files with mutmut trampoline artifacts (in-place instrumentation)
    clean_files = []
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                head = fh.read(2048)
            if "_mutmut_trampoline" not in head:
                clean_files.append(f)
            else:
                _log.warning("Excluded mutmut-instrumented file from discovery: %s", f)
        except OSError:
            clean_files.append(f)  # Can't read -> keep it, let downstream handle
    files = clean_files

    files.sort()

    if limit is not None and len(files) > limit:
        files = files[:limit]

    return files


def _path_contains_excluded_dir(
    filepath: str, project_root: str, exclude_dirs: frozenset[str]
) -> bool:
    """Return True if any component of the relative path is in *exclude_dirs*.

    Used to post-filter git ls-files results against extra_exclude_dirs,
    since git discovery doesn't know about project-level exclusion sets.
    """
    rel = os.path.relpath(filepath, project_root)
    parts = rel.split(os.sep)
    # Check directory components only (exclude the filename itself)
    return any(part in exclude_dirs for part in parts[:-1])


def _git_discover(project_root: str, suffix: str) -> list[str] | None:
    """Use git ls-files to discover tracked + untracked-but-not-ignored files.

    Returns None if git is unavailable or project_root is not in a git repo.
    """
    glob_pattern = f"*{suffix}" if suffix else "*"
    try:
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                glob_pattern,
            ],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        return None

    files: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        full = os.path.join(project_root, line)
        if os.path.isfile(full):
            files.append(full)

    return files


def _walk_discover(
    project_root: str,
    suffix: str,
    extra_exclude_dirs: frozenset[str] | None = None,
) -> list[str]:
    """Fallback discovery using os.walk with CANONICAL_EXCLUDE_DIRS pruning."""
    exclude = CANONICAL_EXCLUDE_DIRS
    if extra_exclude_dirs:
        exclude = exclude | extra_exclude_dirs

    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(project_root):
        # Prune in-place for efficiency
        dirnames[:] = [
            d
            for d in dirnames
            if d not in exclude and not d.startswith(".") and not is_backup_like_directory(d)
        ]
        for fname in filenames:
            if fname.endswith(suffix):
                files.append(os.path.join(dirpath, fname))

    return files
