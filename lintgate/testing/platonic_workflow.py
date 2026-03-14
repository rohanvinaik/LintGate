"""Platonic workflow persistence and envelope helpers."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lintgate.next_action import NextAction, serialize_next_actions

TERMINAL_STATES = frozenset(
    {
        "CONVERGED",
        "READY_TO_APPLY",
        "READY_TO_APPLY_WITH_REVIEW",
        "BLOCKED_DISCOVERY",
        "BLOCKED_TOPOLOGY",
        "BLOCKED_NO_ELIGIBLE_TARGETS",
        "NEEDS_DECOMPOSITION",
        "FAILED",
        "EXISTING_TESTS_SUFFICIENT",
        "PLATEAU_NO_GENERATION",
    }
)


@dataclass
class PlatonicWorkflowRecord:
    """Persisted state for the platonic workflow golden path."""

    workflow_id: str
    scope: str
    target: str
    state: str
    step: str
    config: dict[str, Any] = field(default_factory=dict)
    primary_target: str = ""
    primary_next_action: str = ""
    primary_next_args: dict[str, Any] = field(default_factory=dict)
    autopilot_safe: bool = False
    blocking_reason: str = ""
    reason_code: str = ""
    human_review_required: bool = False
    evidence_summary: dict[str, Any] = field(default_factory=dict)
    proposed_artifacts: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    rel_file: str = ""
    manifest_path: str = ""
    iterations_completed: int = 0
    staged_artifacts: list[dict[str, Any]] = field(default_factory=list)
    orch_state_snapshot: dict[str, Any] = field(default_factory=dict)
    validation_artifact_path: str = ""
    validation_reentry_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "scope": self.scope,
            "target": self.target,
            "state": self.state,
            "step": self.step,
            "config": self.config,
            "primary_target": self.primary_target,
            "primary_next_action": self.primary_next_action,
            "primary_next_args": self.primary_next_args,
            "autopilot_safe": self.autopilot_safe,
            "blocking_reason": self.blocking_reason,
            "reason_code": self.reason_code,
            "human_review_required": self.human_review_required,
            "evidence_summary": self.evidence_summary,
            "proposed_artifacts": self.proposed_artifacts,
            "history": self.history,
            "rel_file": self.rel_file,
            "manifest_path": self.manifest_path,
            "iterations_completed": self.iterations_completed,
            "staged_artifacts": self.staged_artifacts,
            "orch_state_snapshot": self.orch_state_snapshot,
            "validation_artifact_path": self.validation_artifact_path,
            "validation_reentry_count": self.validation_reentry_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlatonicWorkflowRecord:
        return cls(
            workflow_id=str(data.get("workflow_id", "")),
            scope=str(data.get("scope", "")),
            target=str(data.get("target", "")),
            state=str(data.get("state", "FAILED")),
            step=str(data.get("step", "")),
            config=dict(data.get("config", {})),
            primary_target=str(data.get("primary_target", "")),
            primary_next_action=str(data.get("primary_next_action", "")),
            primary_next_args=dict(data.get("primary_next_args", {})),
            autopilot_safe=bool(data.get("autopilot_safe", False)),
            blocking_reason=str(data.get("blocking_reason", "")),
            reason_code=str(data.get("reason_code", "")),
            human_review_required=bool(data.get("human_review_required", False)),
            evidence_summary=dict(data.get("evidence_summary", {})),
            proposed_artifacts=list(data.get("proposed_artifacts", [])),
            history=list(data.get("history", [])),
            rel_file=str(data.get("rel_file", "")),
            manifest_path=str(data.get("manifest_path", "")),
            iterations_completed=int(data.get("iterations_completed", 0)),
            staged_artifacts=list(data.get("staged_artifacts", [])),
            orch_state_snapshot=dict(data.get("orch_state_snapshot", {})),
            validation_artifact_path=str(data.get("validation_artifact_path", "")),
            validation_reentry_count=int(data.get("validation_reentry_count", 0)),
        )

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


def create_workflow_id() -> str:
    """Create a new opaque workflow identifier."""
    return uuid.uuid4().hex[:12]


def workflow_path(project_root: str, workflow_dir: str, workflow_id: str) -> Path:
    """Return the filesystem path for one workflow record."""
    return Path(project_root) / workflow_dir / f"{workflow_id}.json"


def save_workflow(project_root: str, workflow_dir: str, record: PlatonicWorkflowRecord) -> str:
    """Persist a workflow record to disk."""
    out_path = workflow_path(project_root, workflow_dir, record.workflow_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(record.to_dict(), f, indent=2)
    return str(out_path)


def load_workflow(
    project_root: str,
    workflow_dir: str,
    workflow_id: str,
) -> PlatonicWorkflowRecord | None:
    """Load a workflow record from disk."""
    in_path = workflow_path(project_root, workflow_dir, workflow_id)
    if not in_path.exists():
        return None
    try:
        with open(in_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return PlatonicWorkflowRecord.from_dict(data)


def append_history(
    record: PlatonicWorkflowRecord,
    *,
    state: str,
    step: str,
    reason_code: str = "",
    summary: dict[str, Any] | None = None,
) -> None:
    """Append one workflow history entry in-place."""
    entry: dict[str, Any] = {"state": state, "step": step}
    if reason_code:
        entry["reason_code"] = reason_code
    if summary:
        entry["summary"] = summary
    record.history.append(entry)


def staging_dir(project_root: str, workflow_dir: str, workflow_id: str) -> Path:
    """Return the staging directory for one workflow's generated artifacts."""
    return Path(project_root) / workflow_dir / workflow_id / "staged"


def workflow_envelope(
    record: PlatonicWorkflowRecord,
    *,
    next_actions: list[NextAction] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the machine-oriented MCP response envelope."""
    payload = record.to_dict()
    payload["next_actions"] = serialize_next_actions(next_actions or [])
    if extra:
        payload.update(extra)
    return payload
