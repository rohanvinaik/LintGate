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
CYCLE_SAME_TOOL = "CYCLE_SAME_TOOL"

# Heuristic Thresholds
THRESHOLD_SAME_FILE_EDITS = 4
THRESHOLD_SAME_FINDING = 3
THRESHOLD_REPLACE_FAIL = 3
THRESHOLD_SAME_TOOL_FAIL = 2  # After 2 consecutive failures of the same tool


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

    # Per-tool consecutive failure tracking: tool_name -> consecutive error count
    # Reset when the tool succeeds or a different tool is called
    tool_failure_counts: dict[str, int] = field(default_factory=dict)

    # Last tool that was called (for consecutive failure tracking)
    last_tool_name: str = ""

    # Total number of cycles detected in this session so far
    total_detections: int = 0


_EDIT_TOOLS = frozenset(
    {
        "replace_file_content",
        "multi_replace_file_content",
        "Write",
        "Edit",
        "MultiEdit",
    }
)


def _track_edit_event(new_state: EditCycleState, event: dict[str, Any]) -> None:
    """Update state for an edit tool event."""
    target_file = event.get("target_file")
    if target_file and isinstance(target_file, str):
        new_state.file_edit_counts[target_file] = new_state.file_edit_counts.get(target_file, 0) + 1
    status = event.get("status")
    if status == "error":
        new_state.consecutive_replace_failures += 1
    elif status == "success":
        new_state.consecutive_replace_failures = 0


def _track_controlplane_event(new_state: EditCycleState, event: dict[str, Any]) -> None:
    """Update state for a successful controlplane_run event."""
    if event.get("status") != "success":
        return

    new_state.file_edit_counts.clear()
    new_state.consecutive_replace_failures = 0

    findings = event.get("findings", [])
    seen_fingerprints = {
        finding["fingerprint"]
        for finding in findings
        if isinstance(finding, dict) and isinstance(finding.get("fingerprint"), str)
    }

    for fp in seen_fingerprints:
        new_state.finding_persistence[fp] = new_state.finding_persistence.get(fp, 0) + 1

    for old_fp in list(new_state.finding_persistence.keys()):
        if old_fp not in seen_fingerprints:
            del new_state.finding_persistence[old_fp]


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
        tool_failure_counts=state.tool_failure_counts.copy(),
        last_tool_name=state.last_tool_name,
        total_detections=state.total_detections,
    )

    if tool_name in _EDIT_TOOLS:
        _track_edit_event(new_state, event)
    elif tool_name == "controlplane_run":
        _track_controlplane_event(new_state, event)

    # Per-tool consecutive failure tracking
    _track_tool_failure(new_state, tool_name, event)

    return new_state


def _track_tool_failure(state: EditCycleState, tool_name: str, event: dict[str, Any]) -> None:
    """Track consecutive failures of the same tool for loop-breaker signal."""
    status = event.get("status", "")
    if tool_name == state.last_tool_name and status == "error":
        state.tool_failure_counts[tool_name] = state.tool_failure_counts.get(tool_name, 0) + 1
    elif tool_name == state.last_tool_name and status == "success":
        state.tool_failure_counts.pop(tool_name, None)
    elif tool_name != state.last_tool_name:
        # Different tool called — reset the previous tool's streak
        state.tool_failure_counts.pop(state.last_tool_name, None)
        if status == "error":
            state.tool_failure_counts[tool_name] = 1
    state.last_tool_name = tool_name


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

    # 4. Same-tool consecutive failures (loop breaker)
    for tool_name, count in state.tool_failure_counts.items():
        if count >= THRESHOLD_SAME_TOOL_FAIL:
            _TOOL_ALTERNATIVES = {
                "query_analysis": "Use Read on the analysis file directly, or try a different path/section",
                "refactor_extract_method": "Use manual Edit to perform the extraction, or adjust the line range",
            }
            alt = _TOOL_ALTERNATIVES.get(tool_name, f"Try a different tool or approach instead of {tool_name}")
            results.append(
                CycleDetectionResult(
                    cycle_detected=True,
                    reason=CYCLE_SAME_TOOL,
                    diagnostics={
                        "tool": tool_name,
                        "consecutive_failures": count,
                        "suggestion": alt,
                    },
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
