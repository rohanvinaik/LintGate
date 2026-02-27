"""Deterministic cycle-detection state and heuristics.

Part of the orchestration module for identifying repetitive edit/failure loops.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# Reason codes for detected cycles
CYCLE_SAME_FILE = "CYCLE_SAME_FILE"
CYCLE_SAME_FINDING = "CYCLE_SAME_FINDING"
CYCLE_REPLACE_FAIL = "CYCLE_REPLACE_FAIL"

# Heuristic Thresholds
THRESHOLD_SAME_FILE_EDITS = 4
THRESHOLD_SAME_FINDING = 3
THRESHOLD_REPLACE_FAIL = 3


@dataclass
class CycleDetectionResult:
    """Result of a cycle detection evaluation."""

    cycle_detected: bool
    reason: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    escalation_level: Literal["advisory", "enforced"] = "advisory"


@dataclass
class EditCycleState:
    """State tracker for edit cycles and failures over time."""

    # file path -> contiguous edit count without a successful controlplane_run
    file_edit_counts: dict[str, int] = field(default_factory=dict)

    # finding fingerprint -> number of times seen across runs
    finding_persistence: dict[str, int] = field(default_factory=dict)

    # Number of consecutive replace_file_content or multi_replace calls
    # that did not clear a specific error or failed due to syntax/indent
    consecutive_replace_failures: int = 0

    # Total number of cycles detected in this session so far
    total_detections: int = 0


def track_event(state: EditCycleState, event: dict[str, Any]) -> EditCycleState:
    """Track an event, immutably returning an updated state.

    If an event is malformed or uses an unknown tool, it is ignored and the
    original state is returned unmodified.
    """
    if not isinstance(event, dict):
        return state

    tool_name = event.get("tool_name")
    if not tool_name:
        return state

    new_state = EditCycleState(
        file_edit_counts=state.file_edit_counts.copy(),
        finding_persistence=state.finding_persistence.copy(),
        consecutive_replace_failures=state.consecutive_replace_failures,
        total_detections=state.total_detections,
    )

    if tool_name in {
        "replace_file_content",
        "multi_replace_file_content",
        "Write",
        "Edit",
        "MultiEdit",
    }:
        target_file = event.get("target_file")
        if target_file and isinstance(target_file, str):
            new_state.file_edit_counts[target_file] = (
                new_state.file_edit_counts.get(target_file, 0) + 1
            )
        status = event.get("status")
        if status == "error":
            new_state.consecutive_replace_failures += 1
        elif status == "success":
            # Just a successful edit. It resets the replacement failure counter,
            # but NOT the file edit counter (which needs a successful CP run).
            new_state.consecutive_replace_failures = 0

    elif tool_name == "controlplane_run":
        status = event.get("status")
        if status == "success":
            # Successful CP run clears isolated file edit counts
            new_state.file_edit_counts.clear()
            # It also clears replacement failures
            new_state.consecutive_replace_failures = 0

            # Track persistent findings if provided in the event args
            findings = event.get("findings", [])
            seen_fingerprints = set()
            for finding in findings:
                if not isinstance(finding, dict):
                    continue
                fp = finding.get("fingerprint")
                if fp and isinstance(fp, str):
                    seen_fingerprints.add(fp)

            for fp in seen_fingerprints:
                new_state.finding_persistence[fp] = new_state.finding_persistence.get(fp, 0) + 1

            # Prune findings that were resolved (not in this run)
            for old_fp in list(new_state.finding_persistence.keys()):
                if old_fp not in seen_fingerprints:
                    del new_state.finding_persistence[old_fp]

    return new_state


def detect_cycles(state: EditCycleState) -> list[CycleDetectionResult]:
    """Evaluate pure detection heuristics against the current cycle state."""
    results: list[CycleDetectionResult] = []

    # 1. Same-file edit count cycle
    for file_path, count in state.file_edit_counts.items():
        if count >= THRESHOLD_SAME_FILE_EDITS:
            results.append(
                CycleDetectionResult(
                    cycle_detected=True,
                    reason=CYCLE_SAME_FILE,
                    diagnostics={"file": file_path, "edit_count": count},
                )
            )

    # 2. Same-finding persistence cycle
    for fp, count in state.finding_persistence.items():
        if count >= THRESHOLD_SAME_FINDING:
            results.append(
                CycleDetectionResult(
                    cycle_detected=True,
                    reason=CYCLE_SAME_FINDING,
                    diagnostics={"fingerprint": fp, "persistence_count": count},
                )
            )

    # 3. Repeated replacement failures
    if state.consecutive_replace_failures >= THRESHOLD_REPLACE_FAIL:
        results.append(
            CycleDetectionResult(
                cycle_detected=True,
                reason=CYCLE_REPLACE_FAIL,
                diagnostics={"consecutive_failures": state.consecutive_replace_failures},
            )
        )

    # Calculate deterministic escalation level
    # If the user has hit detections multiple times in the same session, we escalate.
    escalation_level: Literal["advisory", "enforced"] = (
        "enforced" if state.total_detections >= 3 else "advisory"
    )

    for r in results:
        r.escalation_level = escalation_level

    if not results:
        return [CycleDetectionResult(cycle_detected=False)]

    return results
