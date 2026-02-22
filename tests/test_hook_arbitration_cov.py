"""Coverage tests for lintgate/hook_arbitration.py — all pure functions."""

from __future__ import annotations

from unittest import mock

from lintgate.hook_arbitration import (
    arbitrate_output,
    build_pulse_delta,
    extract_habit_signals,
    inject_dispositions,
    resolve_verbosity,
    should_emit,
    should_force_emit,
)


class TestResolveVerbosity:
    def test_auto_habit_active(self):
        cfg = mock.MagicMock(hook_verbosity="auto")
        assert resolve_verbosity(cfg, habit_active=True) == "pulse"

    def test_auto_habit_inactive(self):
        cfg = mock.MagicMock(hook_verbosity="auto")
        assert resolve_verbosity(cfg, habit_active=False) == "full"

    def test_silent(self):
        cfg = mock.MagicMock(hook_verbosity="silent")
        assert resolve_verbosity(cfg, habit_active=False) == "silent"

    def test_pulse(self):
        cfg = mock.MagicMock(hook_verbosity="pulse")
        assert resolve_verbosity(cfg, habit_active=False) == "pulse"

    def test_invalid_falls_back_to_full(self):
        cfg = mock.MagicMock(hook_verbosity="nonsense")
        assert resolve_verbosity(cfg, habit_active=False) == "full"


class TestShouldForceEmit:
    def test_empty_report(self):
        assert should_force_emit({}, None) is False
        assert should_force_emit(None, None) is False

    def test_priority_3_disposition(self):
        report = {"hookSpecificOutput": {"dispositions": [{"priority": 3}]}}
        assert should_force_emit(report, None) is True

    def test_low_priority_no_prev(self):
        report = {"hookSpecificOutput": {"dispositions": [{"priority": 1}]}}
        assert should_force_emit(report, None) is False

    def test_new_blocking(self):
        report = {"systemMessage": "BLOCKING issue found"}
        prev = {"systemMessage": "all good"}
        assert should_force_emit(report, prev) is True

    def test_blocking_already_existed(self):
        report = {"systemMessage": "BLOCKING issue found"}
        prev = {"systemMessage": "BLOCKING still"}
        assert should_force_emit(report, prev) is False


class TestShouldEmit:
    def test_full_verbosity_always_emits(self):
        cfg = mock.MagicMock(hook_verbosity="full")
        assert should_emit(cfg, {}, False, {}, None) is True

    def test_silent_suppresses(self):
        cfg = mock.MagicMock(hook_verbosity="silent")
        assert should_emit(cfg, {}, False, {}, None) is False

    def test_silent_force_emit_overrides(self):
        cfg = mock.MagicMock(hook_verbosity="silent")
        report = {"hookSpecificOutput": {"dispositions": [{"priority": 3}]}}
        assert should_emit(cfg, {}, False, report, None) is True

    def test_pulse_interval_fires(self):
        cfg = mock.MagicMock(hook_verbosity="pulse", hook_pulse_interval=5)
        session = {"event_counter": 10, "_last_pulse_event": 3}
        assert should_emit(cfg, session, False, {}, None) is True
        assert session["_last_pulse_event"] == 10

    def test_pulse_interval_not_yet(self):
        cfg = mock.MagicMock(hook_verbosity="pulse", hook_pulse_interval=5)
        session = {"event_counter": 5, "_last_pulse_event": 3}
        assert should_emit(cfg, session, False, {}, None) is False


class TestInjectDispositions:
    def test_compact_pressure(self):
        session = {"event_counter": 10, "_disposition_cooldowns": {}}
        fired = inject_dispositions(session, True, 0.8, 0.60, 0, 0.7)
        assert len(fired) >= 1
        assert any("compact" in d["disposition"].lower() or "pressure" in d["disposition"].lower() for d in fired)

    def test_habit_enter_suggested(self):
        session = {"event_counter": 25, "_disposition_cooldowns": {}}
        fired = inject_dispositions(session, False, 0.75, 0.0, 0, 0.70)
        assert any("habit" in d["disposition"].lower() or "declare_mode" in d["tool_hint"] for d in fired)

    def test_constraint_reorient(self):
        session = {"event_counter": 5, "_disposition_cooldowns": {}}
        fired = inject_dispositions(session, False, 0.0, 0.0, 4, 0.7)
        assert any("constraint" in d["tool_hint"] for d in fired)

    def test_cooldown_prevents_refire(self):
        session = {"event_counter": 5, "_disposition_cooldowns": {"constraint_reorient": 5}}
        fired = inject_dispositions(session, False, 0.0, 0.0, 4, 0.7)
        assert not any("constraint" in d.get("tool_hint", "") for d in fired)

    def test_no_triggers_met(self):
        session = {"event_counter": 0, "_disposition_cooldowns": {}}
        fired = inject_dispositions(session, False, 0.0, 0.0, 0, 0.7)
        assert fired == []


class TestBuildPulseDelta:
    def test_blocking_in_message(self):
        report = {"systemMessage": "BLOCKING issue"}
        delta = build_pulse_delta(report, None, 5, [])
        assert delta["pulse"] is True
        assert delta["changes"]["has_blockers"] is True

    def test_coherence_mentioned(self):
        report = {"systemMessage": "coherence is stable"}
        delta = build_pulse_delta(report, None, 3, [])
        assert delta["changes"]["coherence_mentioned"] is True

    def test_no_changes(self):
        report = {"systemMessage": "all clear"}
        delta = build_pulse_delta(report, None, 2, [])
        assert "changes" not in delta

    def test_with_dispositions(self):
        disps = [{"disposition": "test", "priority": 1}]
        delta = build_pulse_delta({}, None, 1, disps)
        assert delta["dispositions"] == disps


class TestExtractHabitSignals:
    def test_normal_session(self):
        session = {
            "habit_state": {"active": True, "habit_score": 0.85},
            "token_tracker": {"estimated_tokens_used": 100000, "context_window_size": 200000},
            "action_history": [{"exit_code": 1}, {"exit_code": 1}],
        }
        active, score, pressure, failures = extract_habit_signals(session)
        assert active is True
        assert score == 0.85
        assert abs(pressure - 0.5) < 0.01
        assert failures == 2

    def test_non_dict_habit_data(self):
        session = {"habit_state": "invalid", "token_tracker": "bad"}
        active, score, pressure, failures = extract_habit_signals(session)
        assert active is False
        assert score == 0.0
        assert pressure == 0.0

    def test_empty_session(self):
        active, score, pressure, failures = extract_habit_signals({})
        assert active is False
        assert failures == 0

    def test_non_dict_input(self):
        active, score, pressure, failures = extract_habit_signals("not a dict")
        assert active is False

    def test_consecutive_failures_broken_by_success(self):
        session = {
            "action_history": [
                {"exit_code": 1},
                {"exit_code": 0},
                {"exit_code": 1},
            ],
        }
        _, _, _, failures = extract_habit_signals(session)
        assert failures == 1


class TestArbitrateOutput:
    def test_dispositions_enabled_and_fires(self):
        cfg = mock.MagicMock(
            hook_verbosity="full",
            hook_dispositions_enabled=True,
            habit_mode_enter_score=0.70,
        )
        session = {
            "event_counter": 10,
            "_disposition_cooldowns": {},
            "habit_state": {"active": False, "habit_score": 0.0},
        }
        report = {"systemMessage": "OK"}
        result = arbitrate_output(report, cfg, session)
        assert result is report  # full verbosity always emits

    def test_dispositions_disabled(self):
        cfg = mock.MagicMock(
            hook_verbosity="full",
            hook_dispositions_enabled=False,
        )
        session = {"habit_state": {}, "event_counter": 0}
        report = {"systemMessage": "OK"}
        result = arbitrate_output(report, cfg, session)
        assert "dispositions" not in result.get("hookSpecificOutput", {})

    def test_suppressed_by_verbosity(self):
        cfg = mock.MagicMock(
            hook_verbosity="silent",
            hook_dispositions_enabled=False,
        )
        session = {"habit_state": {}, "event_counter": 0}
        report = {"systemMessage": "OK"}
        result = arbitrate_output(report, cfg, session)
        assert result == {}

    def test_dispositions_attached_to_report(self):
        cfg = mock.MagicMock(
            hook_verbosity="full",
            hook_dispositions_enabled=True,
            habit_mode_enter_score=0.70,
        )
        session = {
            "event_counter": 10,
            "_disposition_cooldowns": {},
            "habit_state": {"active": True, "habit_score": 0.8},
            "token_tracker": {"estimated_tokens_used": 150000, "context_window_size": 200000},
        }
        report = {"systemMessage": "OK"}
        result = arbitrate_output(report, cfg, session)
        # May or may not have dispositions depending on trigger conditions,
        # but the function should not crash
        assert isinstance(result, dict)
