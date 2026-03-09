"""Resolution Repertoire — capture successful problem-solving patterns.

Tracks what actions led to the resolution of specific behavioral findings,
enabling future 'proven resolution' hints.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintgate.controlplane.session_memory import SessionMemory


@dataclass
class ResolutionRecord:
    """A record of a successful resolution to a behavioral finding."""

    finding_kind: str
    finding_message: str
    resolution_steps: list[dict[str, Any]]  # sequence of intents/actions
    context: dict[str, Any] = field(default_factory=dict)
    resolved_at: float = field(default_factory=time.time)


class RepertoireManager:
    """Manages the capture and retrieval of resolution patterns."""

    def __init__(self, session_memory: SessionMemory):
        self.session_memory = session_memory

    def track_findings(
        self,
        current_finding_kinds: set[str],
        event_counter: int,
        action_history_len: int,
    ):
        """Update active finding history and capture resolutions."""
        active_history = self.session_memory.active_finding_history

        # 1. Detect resolutions (findings present in history but not in current run)
        resolved_kinds = set(active_history.keys()) - current_finding_kinds
        for kind in resolved_kinds:
            self._capture_resolution(kind, action_history_len)
            del active_history[kind]

        # 2. Track new findings
        for kind in current_finding_kinds:
            if kind not in active_history:
                active_history[kind] = {
                    "started_at_event": event_counter,
                    "action_start_idx": action_history_len - 1 if action_history_len > 0 else 0,
                }

    def _capture_resolution(self, kind: str, current_action_idx: int):
        """Extract resolution steps from action history and store record."""
        history = self.session_memory.action_history
        active_info = self.session_memory.active_finding_history.get(kind)
        if not active_info or not history:
            return

        start_idx = active_info["action_start_idx"]
        # The resolution happens between start_idx and current_action_idx
        steps = history[start_idx:current_action_idx]

        # Only capture if there's meaningful history
        if not steps:
            return

        record = ResolutionRecord(
            finding_kind=kind,
            finding_message=f"Resolved via {len(steps)} actions",  # Placeholder
            resolution_steps=[{"intent": s.get("intent"), "tool": s.get("tool")} for s in steps],
        )

        self.session_memory.resolution_repertoire.append(
            {
                "finding_kind": record.finding_kind,
                "resolution_steps": record.resolution_steps,
                "resolved_at": record.resolved_at,
            }
        )

        # Keep repertoire bounded manually in case of direct list manipulation
        if len(self.session_memory.resolution_repertoire) > 50:
            self.session_memory.resolution_repertoire = self.session_memory.resolution_repertoire[
                -50:
            ]

    def get_resolution_hint(self, kind: str) -> str | None:
        """Find a proven resolution hint for a given finding kind."""
        for record in reversed(self.session_memory.resolution_repertoire):
            if record.get("finding_kind") == kind:
                steps = record.get("resolution_steps", [])
                if steps:
                    intents = [s.get("intent", "unknown") for s in steps if s.get("intent")]
                    if intents:
                        return f"Previously resolved via: {' -> '.join(intents[-3:])}"
        return None

    def query_repertoire(self, trigger_signature: str) -> str | None:
        """Query the repertoire by signature (alias to get_resolution_hint)."""
        return self.get_resolution_hint(trigger_signature)
