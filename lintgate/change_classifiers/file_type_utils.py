"""Helper functions for classifying file types and change characteristics."""

from __future__ import annotations

import functools
import os
import re
from pathlib import Path

# ─── Constants ───────────────────────────────────────────────────────────

_DOCS_EXTENSIONS = frozenset({".md", ".rst", ".txt", ".adoc", ".html", ".css"})
_CONFIG_EXTENSIONS = frozenset({".yaml", ".yml", ".toml", ".json", ".ini", ".cfg", ".env"})
_CONFIG_NAMES = frozenset(
    {
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
        ".flake8",
        ".pylintrc",
        "mypy.ini",
        "tox.ini",
        "Makefile",
        "Dockerfile",
        ".dockerignore",
        ".gitignore",
        ".editorconfig",
        "ruff.toml",
    }
)
_DEPENDENCY_NAMES = frozenset(
    {
        "requirements.txt",
        "requirements-dev.txt",
        "requirements.in",
        "Pipfile",
        "Pipfile.lock",
        "poetry.lock",
        "uv.lock",
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "Cargo.toml",
        "Cargo.lock",
        "go.mod",
        "go.sum",
    }
)
_TEST_PATTERNS = (
    re.compile(r"test[s]?/"),
    re.compile(r"test_\w+\.py$"),
    re.compile(r"\w+_test\.py$"),
    re.compile(r"__tests__/"),
    re.compile(r"\.test\.\w+$"),
    re.compile(r"\.spec\.\w+$"),
)

# Language detection by extension
_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".rb": "ruby",
    ".swift": "swift",
}

# Build commands that modify the project
_BUILD_COMMAND_PATTERNS = (
    re.compile(r"^\s*pip\s+install"),
    re.compile(r"^\s*pip3\s+install"),
    re.compile(r"^\s*uv\s+(pip\s+)?install"),
    re.compile(r"^\s*npm\s+install"),
    re.compile(r"^\s*yarn\s+(add|install)"),
    re.compile(r"^\s*pnpm\s+(add|install)"),
    re.compile(r"^\s*cargo\s+(build|add)"),
    re.compile(r"^\s*make\b"),
    re.compile(r"^\s*cmake\b"),
)

# Read-only bash commands that never need linting
_READONLY_PREFIXES = (
    "ls ",
    "cat ",
    "head ",
    "tail ",
    "grep ",
    "rg ",
    "find ",
    "tree ",
    "git status",
    "git log",
    "git diff",
    "git show",
    "git branch",
    "echo ",
    "wc ",
    "du ",
    "df ",
    "which ",
    "type ",
    "file ",
    "python -c",
    "python3 -c",
    "pwd",
    "env ",
    "printenv",
)


# ─── File type checks ────────────────────────────────────────────────────


def _resolve_path(filepath: str, cwd: str) -> str:
    """Resolve a potentially relative path to absolute."""
    # Explicitly handle 'None' as string due to test casting
    if filepath == "None":
        filepath = ""
    if cwd == "None":
        cwd = ""

    if not isinstance(filepath, str) or not filepath:
        return ""
    if not isinstance(cwd, str) or not cwd:
        cwd = os.getcwd()
    if os.path.isabs(filepath):
        return filepath
    return os.path.normpath(os.path.join(cwd, filepath))


def _is_docs_file(filepath: str) -> bool:
    p = Path(filepath)
    return p.suffix.lower() in _DOCS_EXTENSIONS


def _is_config_file(filepath: str) -> bool:
    p = Path(filepath)
    return p.suffix.lower() in _CONFIG_EXTENSIONS or p.name in _CONFIG_NAMES


def _is_dependency_file(filepath: str) -> bool:
    return Path(filepath).name in _DEPENDENCY_NAMES


@functools.cache
def _is_test_file(filepath: str) -> bool:
    return any(pat.search(filepath) for pat in _TEST_PATTERNS)


def _is_readonly_bash(command: str) -> bool:
    """Detect read-only bash commands that don't need linting."""
    if not isinstance(command, str):
        return False
    cmd = command.strip()
    if not cmd:
        return True
    return any(cmd.startswith(p) for p in _READONLY_PREFIXES)


def _is_build_command(command: str) -> bool:
    """Detect build/install commands."""
    if not isinstance(command, str):
        return False
    return any(pat.search(command) for pat in _BUILD_COMMAND_PATTERNS)


def _matches_pipeline_path(filepath: str, critical_paths: list[str], cwd: str) -> bool:
    """Check if a file matches any pipeline-critical path pattern."""
    if not isinstance(filepath, str) or not filepath:
        return False
    if not isinstance(cwd, str) or not cwd:
        return False

    # Normalize to relative path from project root
    try:
        rel = os.path.relpath(filepath, cwd)
    except ValueError:
        return False

    for pattern in critical_paths:
        # Pattern can be a directory (ends with /) or a specific file
        if pattern.endswith("/"):
            if rel.startswith(pattern) or rel.startswith(pattern.rstrip("/")):
                return True
        else:
            if rel == pattern:
                return True

    return False
