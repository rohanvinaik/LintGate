"""NSIL Projection — Maps ControlPlane state to NSIL Context Models."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Literal

from lintgate.renderers.nsil.types import (
    AgentState,
    InferenceSnapshot,
    SafetyBounds,
    SystemContext,
    UserIntent,
)

if TYPE_CHECKING:
    from lintgate.controlplane.session_memory import SessionMemory
    from lintgate.controlplane.types import ControlPlaneConfig


def project_snapshot(
    session: SessionMemory, config: ControlPlaneConfig, current_task: str = ""
) -> InferenceSnapshot:
    """Project current session and config into an NSIL InferenceSnapshot."""
    has_uncommitted = len(session.pending_patches) > 0  # Simplified logic
    ecosystem = _infer_ecosystem(session.project_root)

    context = SystemContext(
        project_root=session.project_root,
        active_branch="main",  # Should ideally be grabbed from git
        has_uncommitted_changes=has_uncommitted,
        language_ecosystem=ecosystem,
    )

    # Heuristic for intent and mode
    primary_goal = (
        "Resolve active findings" if session.active_finding_history else "Analyze codebase"
    )
    mode: Literal["planning", "execution"] = "execution" if session.action_history else "planning"

    intent = UserIntent(
        primary_goal=primary_goal,
        constraints=[
            c.get("description", "")
            for c in session.proposed_constraints
            if c.get("status") == "accepted"
        ],
    )

    agent_state = AgentState(
        current_mode=mode,
        active_task=current_task,
        blocked_on=None,
        memory_pressure=0.5 if len(session.snapshots) > 10 else 0.1,
    )

    safety = SafetyBounds(
        max_files_modified=config.disposition_enforcement.max_ignores_before_blocking * 5,
        forbidden_paths=[".git", "venv", ".env"],
        allowed_commands=["pytest", "ruff", "mypy"],
        require_approval_for=["git push", "rm -rf"],
    )

    return InferenceSnapshot(
        snapshot_id=f"snap_{session.session_id}_{int(time.time())}",
        context=context,
        intent=intent,
        agent_state=agent_state,
        safety_bounds=safety,
    )


def _infer_ecosystem(project_root: str) -> list[str]:
    # Very crude heuristic for projection
    # In reality, might inspect pyproject.toml, package.json etc.
    return ["python"]
