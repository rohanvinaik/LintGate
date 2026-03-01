"""Phase 1: Classify what changed.

Inspects PostToolUse hook input (tool_name, tool_input, tool_output)
and produces a ChangeClassification describing what kind of change
occurred and its risk level.

Heuristics are lightweight — this runs on every tool use, so it must
be fast (< 10ms).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .types import ChangeClassification, DiffAnalysis, ProjectConfig

# ─── File type detection ─────────────────────────────────────────────────

_DOCS_EXTENSIONS = frozenset({".md", ".rst", ".txt", ".adoc", ".html", ".css"})
_CONFIG_EXTENSIONS = frozenset(
    {".yaml", ".yml", ".toml", ".json", ".ini", ".cfg", ".env"}
)
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

# Import statement patterns
_IMPORT_PATTERN = re.compile(r"^\s*(import |from \S+ import )")
_FUNC_SIG_PATTERN = re.compile(r"^\s*(async\s+)?def\s+\w+")
_CLASS_PATTERN = re.compile(r"^\s*class\s+\w+")


def classify_change(
    tool_name: str,
    tool_input: dict[str, Any],
    tool_output: str,
    cwd: str,
    config: ProjectConfig | None = None,
) -> ChangeClassification:
    """Classify a PostToolUse event into a ChangeClassification.

    Args:
        tool_name: "Write", "Edit", "MultiEdit", or "Bash"
        tool_input: Tool input dict from the hook
        tool_output: Tool output string (stdout for Bash, status for Write/Edit)
        cwd: Current working directory
        config: Optional project config for pipeline-critical path detection

    Returns:
        ChangeClassification with all fields populated
    """
    tool_input = _as_dict(tool_input)
    cwd = cwd if isinstance(cwd, str) and cwd else os.getcwd()

    # Extract changed files
    files_changed = _extract_changed_files(tool_name, tool_input, cwd)

    if not files_changed:
        return _classify_no_file_change(tool_name, tool_input)

    # Group by language
    files_by_language = _group_by_language(files_changed)

    # Analyze the diff (what actually changed in the text)
    diff = _analyze_diff(tool_name, tool_input)

    # Detect test files
    touches_test = any(_is_test_file(f) for f in files_changed)

    # Detect pipeline-critical paths
    touches_critical = False
    if config and config.pipeline_critical_paths:
        touches_critical = any(
            _matches_pipeline_path(f, config.pipeline_critical_paths, cwd)
            for f in files_changed
        )

    # Classify change kind
    change_kind = _classify_change_kind(files_changed, diff, tool_name, tool_input)

    # Classify risk level
    risk_level = _classify_risk(change_kind, diff, files_changed, touches_critical)

    return ChangeClassification(
        files_changed=files_changed,
        files_by_language=files_by_language,
        change_kind=change_kind,
        risk_level=risk_level,
        import_only=diff.import_only,
        function_signatures_changed=diff.function_signatures_changed,
        class_structure_changed=diff.class_structure_changed,
        touches_pipeline_critical=touches_critical,
        touches_test_files=touches_test,
        is_new_file=diff.is_new_file,
        lines_added=diff.lines_added,
        lines_removed=diff.lines_removed,
        tool_name=tool_name,
    )


def _classify_no_file_change(
    tool_name: str,
    tool_input: dict[str, Any],
) -> ChangeClassification:
    """Classify tool events where no concrete file path is available."""
    if tool_name == "Bash":
        command = _as_text(tool_input.get("command", ""))
        if _is_build_command(command):
            # Build/install commands can alter runtime dependencies without
            # touching source files directly.
            return ChangeClassification(
                change_kind="build",
                risk_level="moderate",
                tool_name=tool_name,
            )

    return ChangeClassification(risk_level="none", tool_name=tool_name)


# ─── File extraction ─────────────────────────────────────────────────────


def _extract_changed_files(
    tool_name: str, tool_input: dict[str, Any], cwd: str
) -> list[str]:
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


def _resolve_path(filepath: str, cwd: str) -> str:
    """Resolve a potentially relative path to absolute."""
    if not isinstance(filepath, str) or not filepath:
        return ""
    if not isinstance(cwd, str) or not cwd:
        cwd = os.getcwd()
    if os.path.isabs(filepath):
        return filepath
    return os.path.normpath(os.path.join(cwd, filepath))


# ─── Language detection ──────────────────────────────────────────────────


def _group_by_language(files: list[str]) -> dict[str, list[str]]:
    """Group files by detected programming language."""
    groups: dict[str, list[str]] = {}
    for f in files:
        ext = Path(f).suffix.lower()
        lang = _LANG_MAP.get(ext, "other")
        groups.setdefault(lang, []).append(f)
    return groups


# ─── File type checks ────────────────────────────────────────────────────


def _is_docs_file(filepath: str) -> bool:
    p = Path(filepath)
    return p.suffix.lower() in _DOCS_EXTENSIONS


def _is_config_file(filepath: str) -> bool:
    p = Path(filepath)
    return p.suffix.lower() in _CONFIG_EXTENSIONS or p.name in _CONFIG_NAMES


def _is_dependency_file(filepath: str) -> bool:
    return Path(filepath).name in _DEPENDENCY_NAMES


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


# ─── Change kind classification ──────────────────────────────────────────


def _classify_change_kind(
    files: list[str],
    diff: DiffAnalysis,
    tool_name: str,
    tool_input: dict[str, Any],
) -> str:
    """Determine what kind of change this is. Most specific wins."""

    # Build commands
    if tool_name == "Bash" and _is_build_command(
        _as_text(tool_input.get("command", ""))
    ):
        return "build"

    # Docs changes (all files are docs)
    if all(_is_docs_file(f) for f in files):
        return "docs"

    # Test changes (all files are tests)
    if all(_is_test_file(f) for f in files):
        return "test"

    # Config changes (all files are config)
    if all(_is_config_file(f) for f in files):
        return "config"

    # Dependency changes
    if any(_is_dependency_file(f) for f in files):
        return "dependency"

    # Import-only changes
    if diff.import_only:
        return "import"

    # Structural changes (class/function definitions added/removed/renamed)
    if diff.class_structure_changed or diff.function_signatures_changed:
        return "structural"

    # Default: logic change
    return "logic"


# ─── Risk level classification ────────────────────────────────────────────


def _classify_risk(
    kind: str,
    diff: DiffAnalysis,
    files: list[str],
    touches_critical: bool,
) -> str:
    """Determine risk level from change kind and context."""

    if not files:
        return "none"

    # Cosmetic: docs, formatting-only, comments
    if kind == "docs" or diff.formatting_only:
        return "cosmetic"

    # Architectural: any change to pipeline-critical paths, or 3+ file structural
    if touches_critical and kind not in ("docs", "config"):
        return "architectural"
    if len(files) >= 3 and kind == "structural":
        return "architectural"

    # Structural: function/class changes, new files
    if kind == "structural" or diff.is_new_file:
        return "structural"

    # Everything else: moderate
    return "moderate"


def _as_dict(value: Any) -> dict[str, Any]:
    """Coerce untrusted hook payload fragments to dict."""
    return value if isinstance(value, dict) else {}


def _as_text(value: Any) -> str:
    """Coerce arbitrary payload values to text for analysis."""
    return value if isinstance(value, str) else ""
