"""Tests for lintgate/testing/platonic_workflow.py — persistence and envelope helpers."""

from __future__ import annotations

from pathlib import Path

from lintgate.next_action import NextAction
from lintgate.testing.platonic_workflow import (
    TERMINAL_STATES,
    PlatonicWorkflowRecord,
    append_history,
    create_workflow_id,
    load_workflow,
    save_workflow,
    staging_dir,
    workflow_envelope,
    workflow_path,
)

# --- create_workflow_id ---


def test_create_workflow_id_length():
    wid = create_workflow_id()
    assert len(wid) == 12


def test_create_workflow_id_unique():
    ids = {create_workflow_id() for _ in range(50)}
    assert len(ids) == 50


def test_create_workflow_id_hex():
    wid = create_workflow_id()
    int(wid, 16)  # Should not raise — valid hex


# --- workflow_path ---


def test_workflow_path_construction():
    result = workflow_path("/project", ".lintgate/workflows", "abc123def456")
    assert result == Path("/project/.lintgate/workflows/abc123def456.json")


def test_workflow_path_nested_dir():
    result = workflow_path("/root", "sub/dir", "id1")
    assert result == Path("/root/sub/dir/id1.json")


# --- staging_dir ---


def test_staging_dir_construction():
    result = staging_dir("/project", ".lintgate/workflows", "wf123")
    assert result == Path("/project/.lintgate/workflows/wf123/staged")


# --- PlatonicWorkflowRecord ---


def test_record_to_dict_roundtrip():
    record = PlatonicWorkflowRecord(
        workflow_id="test123",
        scope="file",
        target="src/mod.py",
        state="PROFILING",
        step="mutation_sampling",
        config={"max_iterations": 3},
        primary_target="mod.func",
        autopilot_safe=True,
        blocking_reason="",
        reason_code="CONTINUE",
    )
    d = record.to_dict()
    assert d["workflow_id"] == "test123"
    assert d["scope"] == "file"
    assert d["state"] == "PROFILING"
    assert d["autopilot_safe"] is True
    assert d["config"] == {"max_iterations": 3}


def test_record_from_dict():
    data = {
        "workflow_id": "abc",
        "scope": "project",
        "target": "all",
        "state": "CONVERGED",
        "step": "done",
        "autopilot_safe": True,
        "iterations_completed": 5,
    }
    record = PlatonicWorkflowRecord.from_dict(data)
    assert record.workflow_id == "abc"
    assert record.state == "CONVERGED"
    assert record.iterations_completed == 5
    assert record.autopilot_safe is True


def test_record_from_dict_defaults():
    record = PlatonicWorkflowRecord.from_dict({})
    assert record.workflow_id == ""
    assert record.state == "FAILED"  # default when missing
    assert record.autopilot_safe is False
    assert record.iterations_completed == 0
    assert record.history == []


def test_record_is_terminal():
    record = PlatonicWorkflowRecord(
        workflow_id="x", scope="f", target="t", state="CONVERGED", step="done"
    )
    assert record.is_terminal() is True


def test_record_is_not_terminal():
    record = PlatonicWorkflowRecord(
        workflow_id="x", scope="f", target="t", state="PROFILING", step="sampling"
    )
    assert record.is_terminal() is False


# --- TERMINAL_STATES ---


def test_terminal_states_contains_expected():
    assert "CONVERGED" in TERMINAL_STATES
    assert "READY_TO_APPLY" in TERMINAL_STATES
    assert "FAILED" in TERMINAL_STATES
    assert "NEEDS_DECOMPOSITION" in TERMINAL_STATES
    assert "EXISTING_TESTS_SUFFICIENT" in TERMINAL_STATES
    assert "PLATEAU_NO_GENERATION" in TERMINAL_STATES


def test_terminal_states_excludes_non_terminal():
    assert "PROFILING" not in TERMINAL_STATES
    assert "ASSESSING" not in TERMINAL_STATES
    assert "VALIDATING" not in TERMINAL_STATES


# --- save_workflow / load_workflow ---


def test_save_and_load_workflow(tmp_path: Path):
    record = PlatonicWorkflowRecord(
        workflow_id="wf_test",
        scope="file",
        target="src/x.py",
        state="PROFILING",
        step="sampling",
        iterations_completed=2,
    )
    saved_path = save_workflow(str(tmp_path), "workflows", record)
    assert Path(saved_path).exists()

    loaded = load_workflow(str(tmp_path), "workflows", "wf_test")
    assert loaded is not None
    assert loaded.workflow_id == "wf_test"
    assert loaded.state == "PROFILING"
    assert loaded.iterations_completed == 2


def test_load_workflow_missing(tmp_path: Path):
    result = load_workflow(str(tmp_path), "workflows", "nonexistent")
    assert result is None


def test_load_workflow_invalid_json(tmp_path: Path):
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    bad_file = wf_dir / "bad.json"
    bad_file.write_text("not json{{{")
    result = load_workflow(str(tmp_path), "workflows", "bad")
    assert result is None


# --- append_history ---


def test_append_history_basic():
    record = PlatonicWorkflowRecord(
        workflow_id="x", scope="f", target="t", state="PROFILING", step="s"
    )
    append_history(record, state="VALIDATING", step="validate")
    assert len(record.history) == 1
    assert record.history[0] == {"state": "VALIDATING", "step": "validate"}


def test_append_history_with_reason_and_summary():
    record = PlatonicWorkflowRecord(
        workflow_id="x", scope="f", target="t", state="PROFILING", step="s"
    )
    append_history(
        record,
        state="FAILED",
        step="gate",
        reason_code="TIMEOUT",
        summary={"elapsed_ms": 5000},
    )
    assert len(record.history) == 1
    entry = record.history[0]
    assert entry["reason_code"] == "TIMEOUT"
    assert entry["summary"] == {"elapsed_ms": 5000}


def test_append_history_accumulates():
    record = PlatonicWorkflowRecord(
        workflow_id="x", scope="f", target="t", state="PROFILING", step="s"
    )
    append_history(record, state="A", step="1")
    append_history(record, state="B", step="2")
    append_history(record, state="C", step="3")
    assert len(record.history) == 3
    assert [e["state"] for e in record.history] == ["A", "B", "C"]


# --- workflow_envelope ---


def test_workflow_envelope_basic():
    record = PlatonicWorkflowRecord(
        workflow_id="env1", scope="file", target="t", state="PROFILING", step="s"
    )
    env = workflow_envelope(record)
    assert env["workflow_id"] == "env1"
    assert env["next_actions"] == []


def test_workflow_envelope_with_actions():
    record = PlatonicWorkflowRecord(
        workflow_id="env2", scope="file", target="t", state="PROFILING", step="s"
    )
    actions = [NextAction(tool="mutation_run_sampling", args={"file": "x.py"}, reason="profile")]
    env = workflow_envelope(record, next_actions=actions)
    assert len(env["next_actions"]) == 1
    assert env["next_actions"][0]["tool"] == "mutation_run_sampling"


def test_workflow_envelope_with_extra():
    record = PlatonicWorkflowRecord(
        workflow_id="env3", scope="file", target="t", state="PROFILING", step="s"
    )
    env = workflow_envelope(record, extra={"custom_key": 42})
    assert env["custom_key"] == 42
