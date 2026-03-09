from lintgate.orchestration.cycle_detector import (
    CYCLE_REPLACE_FAIL,
    CYCLE_SAME_FILE,
    CYCLE_SAME_FINDING,
    THRESHOLD_REPLACE_FAIL,
    THRESHOLD_SAME_FILE_EDITS,
    THRESHOLD_SAME_FINDING,
    EditCycleState,
    detect_cycles,
    track_event,
)


def test_track_event_malformed():
    state = EditCycleState()
    new_state = track_event(state, {"no_tool_name": True})
    assert state == new_state


def test_track_event_file_edits():
    state = EditCycleState()
    for _ in range(3):
        state = track_event(
            state,
            {
                "tool_name": "replace_file_content",
                "target_file": "foo.py",
                "status": "success",
            },
        )

    assert state.file_edit_counts.get("foo.py") == 3

    # A successful CP run should clear file edit counts
    state = track_event(state, {"tool_name": "controlplane_run", "status": "success"})
    assert "foo.py" not in state.file_edit_counts


def test_track_event_consecutive_replace_failures():
    state = EditCycleState()
    for _ in range(2):
        state = track_event(
            state,
            {
                "tool_name": "replace_file_content",
                "status": "error",
                "target_file": "foo.py",
            },
        )

    assert state.consecutive_replace_failures == 2

    # A successful edit resets the consecutive failures
    state = track_event(
        state,
        {
            "tool_name": "replace_file_content",
            "status": "success",
            "target_file": "foo.py",
        },
    )
    assert state.consecutive_replace_failures == 0


def test_track_event_finding_persistence():
    state = EditCycleState()
    for _ in range(2):
        state = track_event(
            state,
            {
                "tool_name": "controlplane_run",
                "status": "success",
                "findings": [{"fingerprint": "X"}],
            },
        )

    assert state.finding_persistence.get("X") == 2

    # Prunes resolved findings
    state = track_event(
        state,
        {
            "tool_name": "controlplane_run",
            "status": "success",
            "findings": [{"fingerprint": "Y"}],
        },
    )

    assert "X" not in state.finding_persistence
    assert state.finding_persistence.get("Y") == 1


def test_detect_cycles_thresholds():
    # Same file
    state = EditCycleState(file_edit_counts={"foo.py": THRESHOLD_SAME_FILE_EDITS})
    results = detect_cycles(state)
    assert len(results) == 1
    assert results[0].cycle_detected is True
    assert results[0].reason == CYCLE_SAME_FILE

    # Same finding
    state = EditCycleState(finding_persistence={"X": THRESHOLD_SAME_FINDING})
    results = detect_cycles(state)
    assert len(results) == 1
    assert results[0].reason == CYCLE_SAME_FINDING

    # Replace Fail
    state = EditCycleState(consecutive_replace_failures=THRESHOLD_REPLACE_FAIL)
    results = detect_cycles(state)
    assert len(results) == 1
    assert results[0].reason == CYCLE_REPLACE_FAIL

    # Escalation
    state = EditCycleState(consecutive_replace_failures=THRESHOLD_REPLACE_FAIL, total_detections=3)
    results = detect_cycles(state)
    assert results[0].escalation_level == "enforced"


def test_detect_cycles_none():
    state = EditCycleState()
    results = detect_cycles(state)
    assert len(results) == 1
    assert results[0].cycle_detected is False
