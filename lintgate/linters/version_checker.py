"""Tool-version consistency checker.

Detects missing or mismatched lint tool versions relative to project metadata.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

from ..config import load_config
from ..types import LinterContext, LintIssue
from ..versioning import collect_required_version_specs, inspect_tool_versions
from .base import BaseLinter


class VersionChecker(BaseLinter):
    """Checks lint-tool version compatibility with project requirements."""

    name = "version_checker"
    tier = 1
    timeout_ms = 3000
    required_tool = None

    def run(self, ctx: LinterContext) -> Iterator[LintIssue]:
        project_config = load_config(ctx.project_root)
        requirements = collect_required_version_specs(
            ctx.project_root,
            project_config.tool_version_requirements,
        )
        observations = inspect_tool_versions(requirements)

        for item in observations:
            status = item.get("status", "ok")
            if status == "ok":
                continue

            tool = str(item.get("tool", "tool"))
            required = str(item.get("required_specifier", "") or "")
            installed = item.get("installed_version")
            source_path = _source_file_path(item.get("requirement_sources", []), ctx.project_root)

            severity = "warning"
            if status == "missing" and required:
                severity = "blocking"
            if status == "mismatch":
                severity = "blocking"

            message = str(item.get("message", f"Version issue detected for {tool}"))
            if installed:
                message = f"{message} (installed={installed})"

            suggestions = []
            suggestion = item.get("suggested_fix")
            if suggestion:
                suggestions.append(f"Run: {suggestion}")
            suggestions.append("Use MCP tool audit_tool_versions(path, auto_fix=true) to repair and verify")

            yield LintIssue(
                linter="version_checker",
                kind=f"version-{status}",
                message=message,
                file=source_path,
                severity=severity,
                confidence=1.0,
                evidence={
                    "tool": tool,
                    "required_specifier": required,
                    "installed_version": installed,
                    "sources": item.get("requirement_sources", []),
                },
                suggestions=suggestions,
            )


def _source_file_path(sources: list[str] | Any, project_root: str) -> str | None:
    """Resolve the first source entry to a project path, if available."""
    if not isinstance(sources, list):
        return None
    for source in sources:
        if not isinstance(source, str):
            continue
        file_part = source.split(":", 1)[0]
        if file_part.startswith(".claude/") or file_part.endswith(".txt") or file_part.endswith(".toml"):
            return os.path.normpath(os.path.join(project_root, file_part))
    return None
