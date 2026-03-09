"""Refactor checkpointing tools — session-aware file-level progress tracking (#199).

3 MCP tools:
- refactor_checkpoint: Record progress on a file during a refactoring session
- refactor_resume: Load state and provide structured summary for session resumption
- refactor_thesis: Record or update the agent's structural thesis
"""

from __future__ import annotations

import json


def register(mcp, helpers):
    """Register refactor checkpointing tools on the shared MCP instance."""

    @mcp.tool()
    def refactor_checkpoint(
        path: str,
        file: str,
        status: str,
        patterns_applied: list[str] | None = None,
        notes: str = "",
        initial_findings: int | None = None,
        remaining_findings: int | None = None,
    ) -> str:
        """Record progress on a file during a refactoring session.

        Creates a new refactor session if none exists. Tracks which files
        have been processed, what patterns were applied, and remaining
        finding counts for cross-session continuity.

        When all tracked files are completed or skipped, the state is
        automatically archived.

        Args:
            path: Project root path.
            file: Relative path to the file being refactored.
            status: One of "completed", "in_progress", "skipped", "pending".
            patterns_applied: List of refactoring pattern names applied
                (e.g., "guard-clause-inversion", "helper-extraction").
            notes: Free-text notes about the refactoring progress.
            initial_findings: Initial finding count before refactoring
                (set once, not overwritten on subsequent calls).
            remaining_findings: Current remaining finding count.
        """
        from lintgate.refactor_state import archive_if_complete, checkpoint

        project_root = helpers["_validate_project_root"](path)

        try:
            state = checkpoint(
                project_root,
                file,
                status,
                patterns_applied=patterns_applied or [],
                notes=notes,
                initial_findings=initial_findings,
                remaining_findings=remaining_findings,
            )
        except ValueError as e:
            return json.dumps({"error": str(e)})

        fp = state.files[file]
        result = {
            "file": file,
            "status": fp.status,
            "initial_findings": fp.initial_findings,
            "remaining_findings": fp.remaining_findings,
            "patterns_applied": fp.patterns_applied,
            "total_files_tracked": len(state.files),
        }

        # Check if refactoring is complete
        archived = archive_if_complete(project_root)
        if archived:
            result["archived"] = True
            result["message"] = (
                "All tracked files are completed or skipped. Refactor state has been archived."
            )

        result["next_actions"] = _build_next_actions(state, archived)
        return str(helpers["_json_dumps"](result, output_mode="compact"))

    @mcp.tool()
    def refactor_resume(path: str) -> str:
        """Load refactor state and provide a structured summary for session resumption.

        Returns:
        - Files completed, in-progress, and pending
        - Patterns applied and their frequency
        - Current finding counts vs. initial
        - Recommended next file (from work queue if available)

        Args:
            path: Project root path.
        """
        from lintgate.refactor_state import resume

        project_root = helpers["_validate_project_root"](path)
        summary = resume(project_root)

        if not summary.get("active"):
            summary["next_actions"] = [
                {
                    "tool": "refactor_thesis",
                    "args": {"path": path, "thesis": "<your codebase thesis>"},
                    "reason": "Start a new refactoring session by recording your structural thesis.",
                    "priority": 1,
                },
                {
                    "tool": "controlplane_run",
                    "args": {"path": path},
                    "reason": "Run diagnostics to identify files needing refactoring.",
                    "priority": 2,
                },
            ]
        else:
            from lintgate.refactor_state import load_state

            state = load_state(project_root)
            if state:
                summary["next_actions"] = _build_next_actions(state, archived=False)

        return str(helpers["_json_dumps"](summary, output_mode="compact"))

    @mcp.tool()
    def refactor_thesis(path: str, thesis: str) -> str:
        """Record or update the agent's structural thesis about the codebase.

        The thesis captures the agent's understanding of the codebase's
        architectural state and refactoring goals. This persists across
        context-window compactions and session handoffs.

        Example theses:
        - "Organic growth — CLI dispatch mixed with business logic. Needs module separation."
        - "Test coverage gaps in auth module. Core logic well-structured."
        - "Performance bottleneck in data pipeline. Needs batch processing extraction."

        Args:
            path: Project root path.
            thesis: The agent's structural thesis about the codebase.
        """
        from lintgate.refactor_state import set_thesis

        project_root = helpers["_validate_project_root"](path)
        state = set_thesis(project_root, thesis)

        result = {
            "session_id": state.session_id,
            "thesis": state.thesis,
            "total_files_tracked": len(state.files),
            "next_actions": [
                {
                    "tool": "controlplane_run",
                    "args": {"path": path},
                    "reason": "Run diagnostics to identify files needing refactoring.",
                    "priority": 1,
                },
                {
                    "tool": "refactor_checkpoint",
                    "args": {
                        "path": path,
                        "file": "<target_file>",
                        "status": "in_progress",
                    },
                    "reason": "Start tracking progress on specific files.",
                    "priority": 2,
                },
            ],
        }
        return str(helpers["_json_dumps"](result, output_mode="compact"))

    return {
        "refactor_checkpoint": refactor_checkpoint,
        "refactor_resume": refactor_resume,
        "refactor_thesis": refactor_thesis,
    }


def _build_next_actions(state, archived: bool) -> list[dict]:
    """Build next_actions based on current refactor state."""
    if archived:
        return [
            {
                "tool": "controlplane_run",
                "args": {"path": "."},
                "reason": "Refactoring complete. Run final diagnostics to verify.",
                "priority": 1,
            }
        ]

    actions = []
    from lintgate.refactor_state import _recommend_next_file

    next_file = _recommend_next_file(state)
    if next_file:
        fp = state.files.get(next_file)
        if fp and fp.status == "in_progress":
            actions.append(
                {
                    "tool": "lint_files",
                    "args": {"path": ".", "files": next_file},
                    "reason": f"Continue refactoring {next_file} ({fp.remaining_findings} findings remaining).",
                    "priority": 1,
                }
            )
        else:
            actions.append(
                {
                    "tool": "refactor_checkpoint",
                    "args": {"path": ".", "file": next_file, "status": "in_progress"},
                    "reason": f"Start refactoring {next_file}.",
                    "priority": 1,
                }
            )

    pending_count = sum(1 for fp in state.files.values() if fp.status == "pending")
    if pending_count > 0:
        actions.append(
            {
                "tool": "controlplane_run",
                "args": {"path": "."},
                "reason": f"{pending_count} files pending. Run diagnostics to update finding counts.",
                "priority": 2,
            }
        )

    return actions
