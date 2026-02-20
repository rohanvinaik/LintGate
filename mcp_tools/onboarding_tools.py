"""Onboarding tools — getting_started entry point for LintGate MCP."""

from __future__ import annotations

import json
import os
from typing import Any


def register(mcp, helpers):
    """Register onboarding tools on the shared MCP instance."""

    @mcp.tool()
    def getting_started(path: str) -> str:
        """Start here. Get oriented with LintGate on any project.

        WHEN TO USE: First time using LintGate on a project, or when unsure
        what to do next. Returns project status, recommended next steps, and
        the essential tool workflow.

        Example: getting_started(path="/my/project")
        """
        project_root = helpers["_validate_project_root"](path)

        config_status = helpers["_build_onboarding_status"](project_root)

        # Build dynamic next_actions based on project state
        next_actions: list[dict[str, str]] = []
        if config_status["config_state"] != "config_enabled":
            next_actions.append({
                "tool": "controlplane_run",
                "reason": "Run a comprehensive health check (works without config)",
                "example": f'controlplane_run(path="{project_root}")',
            })
        else:
            next_actions.append({
                "tool": "controlplane_run",
                "reason": "Run a comprehensive health check",
                "example": f'controlplane_run(path="{project_root}")',
            })

        # Check if bootstrap files exist
        claude_md = os.path.join(project_root, ".claude", "CLAUDE.md")
        if not os.path.exists(claude_md):
            next_actions.append({
                "tool": "bootstrap_context_files",
                "reason": "Generate project-specific CLAUDE.md with documented principles",
                "example": f'bootstrap_context_files(path="{project_root}", write=True)',
            })

        next_actions.append({
            "tool": "lint_project",
            "reason": "Full project lint scan",
            "example": f'lint_project(path="{project_root}")',
        })

        output: dict[str, Any] = {
            "project": project_root,
            "config_status": config_status,
            "essential_tools": {
                "lint_files": "Check specific files after edits — "
                'lint_files(files=["/path/to/file.py"])',
                "lint_project": "Full project scan — "
                'lint_project(path="/my/project")',
                "lint_fix": "Auto-fix safe issues — "
                'lint_fix(path="/my/project", dry_run=False)',
                "controlplane_run": "6-channel health check (lint + tests + deps + git + behavior + structure) — "
                'controlplane_run(path="/my/project")',
                "controlplane_get_details": "Drill into health check findings — "
                'controlplane_get_details(run_id="...")',
                "bootstrap_context_files": "Generate project CLAUDE.md — "
                'bootstrap_context_files(path="/my/project", write=True)',
            },
            "first_session_workflow": [
                "1. Run controlplane_run(path) for a full project health check",
                "2. Run controlplane_get_details(run_id) to review specific findings",
                "3. Run lint_fix(path, dry_run=False) to auto-fix safe issues",
                "4. Run bootstrap_context_files(path, write=true) to generate persistent context files",
            ],
            "all_tools_count": 35,
            "next_actions": next_actions,
        }

        return json.dumps(output, indent=2)

    return {"getting_started": getting_started}
