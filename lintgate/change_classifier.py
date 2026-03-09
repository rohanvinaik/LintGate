"""Phase 1: Classify what changed.

Inspects PostToolUse hook input (tool_name, tool_input, tool_output)
and produces a ChangeClassification describing what kind of change
occurred and its risk level.

Heuristics are lightweight — this runs on every tool use, so it must
be fast (< 10ms).
"""

from __future__ import annotations

import os
from typing import Any

from .change_classifiers._change_diff_analysis import (
    _analyze_diff,
    _extract_changed_files,
    _group_by_language,
)
from .change_classifiers._change_diff_analysis import (
    _as_text,
)
from .change_classifiers.file_type_utils import (
    _is_build_command,
    _is_config_file,
    _is_dependency_file,
    _is_docs_file,
    _is_readonly_bash,
    _is_test_file,
    _matches_pipeline_path,
    _resolve_path,
)
from .types import ChangeClassification, DiffAnalysis, ProjectConfig

# ─── Re-exports for backward compatibility ───────────────────────────────
# All names that downstream code imports from lintgate.change_classifier
# are re-exported here so existing imports continue to work.
__all__ = [
    "classify_change",
    "_analyze_diff",
    "_as_text",
    "_classify_change_kind",
    "_classify_no_file_change",
    "_classify_risk",
    "_extract_changed_files",
    "_group_by_language",
    "_is_build_command",
    "_is_config_file",
    "_is_dependency_file",
    "_is_docs_file",
    "_is_readonly_bash",
    "_is_test_file",
    "_matches_pipeline_path",
    "_resolve_path",
]


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
            _matches_pipeline_path(f, config.pipeline_critical_paths, cwd) for f in files_changed
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


# ─── Change kind classification ──────────────────────────────────────────


def _classify_change_kind(
    files: list[str],
    diff: DiffAnalysis,
    tool_name: str,
    tool_input: dict[str, Any],
) -> str:
    """Determine what kind of change this is. Most specific wins."""

    # Build commands
    if tool_name == "Bash" and _is_build_command(_as_text(tool_input.get("command", ""))):
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
