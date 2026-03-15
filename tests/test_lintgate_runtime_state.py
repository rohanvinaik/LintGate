"""Tests for runtime_state."""

from __future__ import annotations

from lintgate.runtime_state import (
    RuntimeState,
)


def test_runtime_state_to_dict_round_trip():
    """ROUND_TRIP: mutation property"""
    # Precondition: RuntimeState.to_dict ↔ RuntimeState.from_dict
    original = RuntimeState(generation=0, session_id="test", timestamp=0.0, mode="test", habit_score=0.0, true_north="test", toward=[], away=[], forbidden=[], compass_hash="test", active_files=[], last_test_status="ok", focus_intent="test", blocking_issues=0, warning_issues=0, symbol_coverage_blockers=0, coherence_state="test", prediction_accuracy=0.0, estimated_tokens_pct=0.0, compaction_count=0, tool_calls_total=0, top_constraint="test", approach_failures=0, prescriptive_spec_count=0, prescriptive_coverage_ratio=0.0)
    serialized = original.to_dict()
    reconstructed = RuntimeState.from_dict(serialized)
    assert reconstructed.generation == original.generation, "generation mismatch"
    assert reconstructed.session_id == original.session_id, "session_id mismatch"
    assert reconstructed.timestamp == original.timestamp, "timestamp mismatch"
    assert reconstructed.mode == original.mode, "mode mismatch"
    assert reconstructed.habit_score == original.habit_score, "habit_score mismatch"
    assert reconstructed.true_north == original.true_north, "true_north mismatch"
    assert reconstructed.toward == original.toward, "toward mismatch"
    assert reconstructed.away == original.away, "away mismatch"
    assert reconstructed.forbidden == original.forbidden, "forbidden mismatch"
    assert reconstructed.compass_hash == original.compass_hash, "compass_hash mismatch"
    assert reconstructed.active_files == original.active_files, "active_files mismatch"
    assert reconstructed.last_test_status == original.last_test_status, "last_test_status mismatch"
    assert reconstructed.focus_intent == original.focus_intent, "focus_intent mismatch"
    assert reconstructed.blocking_issues == original.blocking_issues, "blocking_issues mismatch"
    assert reconstructed.warning_issues == original.warning_issues, "warning_issues mismatch"
    assert reconstructed.symbol_coverage_blockers == original.symbol_coverage_blockers, "symbol_coverage_blockers mismatch"
    assert reconstructed.coherence_state == original.coherence_state, "coherence_state mismatch"
    assert reconstructed.prediction_accuracy == original.prediction_accuracy, "prediction_accuracy mismatch"
    assert reconstructed.estimated_tokens_pct == original.estimated_tokens_pct, "estimated_tokens_pct mismatch"
    assert reconstructed.compaction_count == original.compaction_count, "compaction_count mismatch"
    assert reconstructed.tool_calls_total == original.tool_calls_total, "tool_calls_total mismatch"
    assert reconstructed.top_constraint == original.top_constraint, "top_constraint mismatch"
    assert reconstructed.approach_failures == original.approach_failures, "approach_failures mismatch"
    assert reconstructed.prescriptive_spec_count == original.prescriptive_spec_count, "prescriptive_spec_count mismatch"
    assert reconstructed.prescriptive_coverage_ratio == original.prescriptive_coverage_ratio, "prescriptive_coverage_ratio mismatch"

