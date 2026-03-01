"""NSIL (Neural-Symbolic Interface Language) Core Types."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class SystemContext:
    """Environmental context for the agent."""

    project_root: str
    active_branch: str = "main"
    has_uncommitted_changes: bool = False
    language_ecosystem: list[str] = field(default_factory=list)


@dataclass
class UserIntent:
    """The inferred or explicit goal of the current session."""

    primary_goal: str
    constraints: list[str] = field(default_factory=list)
    confidence_level: float = 1.0


@dataclass
class AgentState:
    """The current cognitive/execution state of the agent."""

    current_mode: Literal["planning", "execution", "verification"] = "planning"
    active_task: str = ""
    blocked_on: str | None = None
    memory_pressure: float = 0.0


@dataclass
class SafetyBounds:
    """Operational constraints to prevent destructive behavior."""

    max_files_modified: int = 10
    forbidden_paths: list[str] = field(default_factory=list)
    allowed_commands: list[str] = field(default_factory=list)
    require_approval_for: list[str] = field(default_factory=list)


@dataclass
class InferenceSnapshot:
    """A point-in-time snapshot of the agent's inference context."""

    snapshot_id: str = field(default_factory=lambda: "snap_" + str(int(time.time())))
    context: SystemContext = field(
        default_factory=lambda: SystemContext(project_root="")
    )
    intent: UserIntent = field(default_factory=lambda: UserIntent(primary_goal=""))
    agent_state: AgentState = field(default_factory=AgentState)
    safety_bounds: SafetyBounds = field(default_factory=SafetyBounds)
    timestamp: float = field(default_factory=time.time)
