"""Tests for cycle detection orchestration core."""

from __future__ import annotations

from lintgate.orchestration.cycle_detector import (
    CYCLE_REPLACE_FAIL,
    CYCLE_SAME_FILE,
    CYCLE_SAME_FINDING,
    CycleDetectionResult,
    EditCycleState,
    detect_cycles,
    track_event,
)


def test_dataclass_fields():
    assert hasattr(CycleDetectionResult, "__dataclass_fields__")
    assert hasattr(EditCycleState, "__dataclass_fields__")


def test_empty_state_no_cycle():
    state = EditCycleState()
    results = detect_cycles(state)
    assert len(results) == 1
    assert results[0].cycle_detected is False


def test_malformed_event_ignored():
    state = EditCycleState(file_edit_counts={"target.py": 1})
    new_state = track_event(state, ["not", "a", "dict"])  # type: ignore
    assert new_state.file_edit_counts == {"target.py": 1}

    # Missing tool_name
    new_state2 = track_event(state, {"other_key": "val"})
    assert new_state2.file_edit_counts == {"target.py": 1}

    # Unknown tool
    new_state3 = track_event(state, {"tool_name": "unknown_tool"})
    assert new_state3.file_edit_counts == {"target.py": 1}


def test_same_file_edit_tracking_and_detection():
    state = EditCycleState()
    # 4 edits -> reaches THRESHOLD_SAME_FILE_EDITS
    for _ in range(4):
        state = track_event(
            state,
            {"tool_name": "replace_file_content", "target_file": "main.py", "status": "success"},
        )

    assert state.file_edit_counts["main.py"] == 4

    results = detect_cycles(state)
    assert len(results) == 1
    res = results[0]
    assert res.cycle_detected is True
    assert res.reason == CYCLE_SAME_FILE
    assert res.diagnostics == {"file": "main.py", "edit_count": 4}
    assert res.escalation_level == "advisory"


def test_same_finding_persistence():
    state = EditCycleState()

    # 3 controlplane runs with the same fingerprint
    for _ in range(3):
        state = track_event(
            state,
            {
                "tool_name": "controlplane_run",
                "status": "success",
                "findings": [{"fingerprint": "hash_xyz"}, {"fingerprint": "hash_abc"}],
            },
        )

    results = detect_cycles(state)
    assert len(results) == 2
    reasons = {r.reason for r in results}
    assert reasons == {CYCLE_SAME_FINDING}

    for r in results:
        assert r.cycle_detected is True
        assert r.diagnostics["persistence_count"] == 3


def test_replace_failure_loop():
    state = EditCycleState()

    for _ in range(3):
        state = track_event(
            state,
            {"tool_name": "multi_replace_file_content", "target_file": "foo.py", "status": "error"},
        )

    results = detect_cycles(state)
    assert len(results) == 1
    assert results[0].cycle_detected is True
    assert results[0].reason == CYCLE_REPLACE_FAIL
    assert results[0].diagnostics == {"consecutive_failures": 3}


def test_successful_cp_run_clears_file_counts():
    state = EditCycleState(file_edit_counts={"app.py": 2})
    state = track_event(
        state, {"tool_name": "controlplane_run", "status": "success", "findings": []}
    )
    assert state.file_edit_counts == {}


def test_escalation_level():
    state = EditCycleState(total_detections=3, consecutive_replace_failures=3)
    results = detect_cycles(state)
    assert len(results) == 1
    assert results[0].escalation_level == "enforced"


def test_resolved_findings_pruned():
    state = EditCycleState(finding_persistence={"old_hash": 2})
    # Run with new findings only
    state = track_event(
        state,
        {
            "tool_name": "controlplane_run",
            "status": "success",
            "findings": [{"fingerprint": "new_hash"}],
        },
    )
    assert "old_hash" not in state.finding_persistence
    assert state.finding_persistence["new_hash"] == 1
