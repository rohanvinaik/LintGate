"""Tests for lintgate/hooks/arbitration.py — message arbitration layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from lintgate.hooks.arbitration import (
    _DISPOSITION_TRIGGERS,
    arbitrate_output,
    build_pulse_delta,
    extract_habit_signals,
    inject_dispositions,
    resolve_verbosity,
    should_emit,
    should_force_emit,
)

# ── Lightweight stubs ────────────────────────────────────────────────


@dataclass
class StubConfig:
    """Minimal stand-in for ControlPlane config objects."""

    hook_verbosity: str = "full"
    hook_pulse_interval: int = 5
    hook_dispositions_enabled: bool = True
    habit_mode_enter_score: float = 0.70


class BareObject:
    """Object with no attributes — used to test getattr fallbacks."""

    pass


# ── resolve_verbosity ────────────────────────────────────────────────


class TestResolveVerbosity:
    def test_returns_full_by_default(self):
        cfg = StubConfig(hook_verbosity="full")
        assert resolve_verbosity(cfg, habit_active=False) == "full"

    def test_returns_silent_when_configured(self):
        cfg = StubConfig(hook_verbosity="silent")
        assert resolve_verbosity(cfg, habit_active=True) == "silent"

    def test_returns_pulse_when_configured(self):
        cfg = StubConfig(hook_verbosity="pulse")
        assert resolve_verbosity(cfg, habit_active=False) == "pulse"

    def test_auto_resolves_to_pulse_when_habit_active(self):
        cfg = StubConfig(hook_verbosity="auto")
        assert resolve_verbosity(cfg, habit_active=True) == "pulse"

    def test_auto_resolves_to_full_when_habit_inactive(self):
        cfg = StubConfig(hook_verbosity="auto")
        assert resolve_verbosity(cfg, habit_active=False) == "full"

    def test_unknown_verbosity_falls_back_to_full(self):
        cfg = StubConfig(hook_verbosity="unknown_value")
        assert resolve_verbosity(cfg, habit_active=False) == "full"

    def test_empty_string_falls_back_to_full(self):
        cfg = StubConfig(hook_verbosity="")
        assert resolve_verbosity(cfg, habit_active=False) == "full"

    def test_missing_attribute_falls_back_to_full(self):
        cfg = BareObject()
        assert resolve_verbosity(cfg, habit_active=False) == "full"

    def test_missing_attribute_with_habit_active(self):
        # getattr default is "full", so habit_active should not affect outcome
        cfg = BareObject()
        assert resolve_verbosity(cfg, habit_active=True) == "full"


# ── should_force_emit ────────────────────────────────────────────────


class TestShouldForceEmit:
    def test_empty_report_returns_false(self):
        assert should_force_emit({}, None) is False

    def test_none_like_empty_report_returns_false(self):
        # Falsy report
        assert should_force_emit({}, None) is False

    def test_priority_3_disposition_forces_emit(self):
        report = {
            "hookSpecificOutput": {"dispositions": [{"priority": 3, "disposition": "warning"}]}
        }
        assert should_force_emit(report, None) is True

    def test_priority_4_disposition_forces_emit(self):
        report = {
            "hookSpecificOutput": {"dispositions": [{"priority": 4, "disposition": "critical"}]}
        }
        assert should_force_emit(report, None) is True

    def test_priority_2_disposition_does_not_force_emit(self):
        report = {"hookSpecificOutput": {"dispositions": [{"priority": 2, "disposition": "info"}]}}
        # No prev_report, no BLOCKING difference → False
        assert should_force_emit(report, None) is False

    def test_priority_1_disposition_does_not_force_emit(self):
        report = {
            "hookSpecificOutput": {"dispositions": [{"priority": 1, "disposition": "suggestion"}]}
        }
        assert should_force_emit(report, None) is False

    def test_non_dict_disposition_is_skipped(self):
        report = {"hookSpecificOutput": {"dispositions": ["not_a_dict", 42]}}
        assert should_force_emit(report, None) is False

    def test_disposition_without_priority_defaults_to_zero(self):
        report = {"hookSpecificOutput": {"dispositions": [{"disposition": "no priority key"}]}}
        assert should_force_emit(report, None) is False

    def test_new_blocking_forces_emit(self):
        report = {"systemMessage": "BLOCKING issue found"}
        prev_report = {"systemMessage": "All clear"}
        assert should_force_emit(report, prev_report) is True

    def test_existing_blocking_does_not_force_emit(self):
        report = {"systemMessage": "BLOCKING issue found"}
        prev_report = {"systemMessage": "BLOCKING issue remains"}
        assert should_force_emit(report, prev_report) is False

    def test_blocking_removed_does_not_force_emit(self):
        report = {"systemMessage": "All clear now"}
        prev_report = {"systemMessage": "BLOCKING issue found"}
        assert should_force_emit(report, prev_report) is False

    def test_no_system_message_does_not_force_emit(self):
        report = {"someOtherKey": "value"}
        prev_report = {"someOtherKey": "other"}
        assert should_force_emit(report, prev_report) is False

    def test_no_hook_specific_output_key(self):
        report = {"systemMessage": "clean"}
        assert should_force_emit(report, None) is False

    def test_empty_dispositions_list(self):
        report: dict[str, Any] = {"hookSpecificOutput": {"dispositions": []}}
        assert should_force_emit(report, None) is False

    def test_multiple_dispositions_one_high_priority(self):
        report = {
            "hookSpecificOutput": {
                "dispositions": [
                    {"priority": 1, "disposition": "low"},
                    {"priority": 3, "disposition": "high"},
                ]
            }
        }
        assert should_force_emit(report, None) is True


# ── should_emit ──────────────────────────────────────────────────────


class TestShouldEmit:
    def test_full_verbosity_always_emits(self):
        cfg = StubConfig(hook_verbosity="full")
        assert should_emit(cfg, {}, False, {"systemMessage": ""}, None) is True

    def test_silent_verbosity_suppresses_normal(self):
        cfg = StubConfig(hook_verbosity="silent")
        report = {"systemMessage": "normal output"}
        assert should_emit(cfg, {}, False, report, None) is False

    def test_silent_verbosity_force_emits_on_high_priority(self):
        cfg = StubConfig(hook_verbosity="silent")
        report = {"hookSpecificOutput": {"dispositions": [{"priority": 3}]}}
        assert should_emit(cfg, {}, False, report, None) is True

    def test_pulse_mode_emits_at_interval(self):
        cfg = StubConfig(hook_verbosity="pulse", hook_pulse_interval=5)
        session = {"event_counter": 10, "_last_pulse_event": 5}
        report = {"systemMessage": "periodic"}
        assert should_emit(cfg, session, False, report, None) is True
        # After emit, _last_pulse_event is updated
        assert session["_last_pulse_event"] == 10

    def test_pulse_mode_suppresses_between_intervals(self):
        cfg = StubConfig(hook_verbosity="pulse", hook_pulse_interval=5)
        session = {"event_counter": 7, "_last_pulse_event": 5}
        report = {"systemMessage": "too soon"}
        assert should_emit(cfg, session, False, report, None) is False

    def test_pulse_mode_force_emits_on_new_blocking(self):
        cfg = StubConfig(hook_verbosity="pulse", hook_pulse_interval=5)
        session = {"event_counter": 1, "_last_pulse_event": 0}
        report = {"systemMessage": "BLOCKING regression"}
        prev = {"systemMessage": "all good"}
        assert should_emit(cfg, session, False, report, prev) is True

    def test_auto_verbosity_with_habit_uses_pulse_logic(self):
        cfg = StubConfig(hook_verbosity="auto", hook_pulse_interval=3)
        session = {"event_counter": 6, "_last_pulse_event": 3}
        report = {"systemMessage": "auto pulse"}
        assert should_emit(cfg, session, True, report, None) is True

    def test_pulse_first_event_emits_when_counter_equals_interval(self):
        cfg = StubConfig(hook_verbosity="pulse", hook_pulse_interval=5)
        session = {"event_counter": 5, "_last_pulse_event": 0}
        report = {"systemMessage": "first pulse"}
        assert should_emit(cfg, session, False, report, None) is True

    def test_pulse_missing_last_pulse_defaults_to_zero(self):
        cfg = StubConfig(hook_verbosity="pulse", hook_pulse_interval=5)
        session = {"event_counter": 5}
        report = {"systemMessage": "should pulse"}
        assert should_emit(cfg, session, False, report, None) is True


# ── inject_dispositions ──────────────────────────────────────────────


class TestInjectDispositions:
    def test_no_triggers_fire_when_conditions_not_met(self):
        session: dict[str, Any] = {"event_counter": 10}
        result = inject_dispositions(
            session,
            habit_active=False,
            habit_score=0.0,
            context_pressure=0.0,
            consecutive_failures=0,
            enter_score=0.70,
        )
        assert result == []

    def test_compact_pressure_fires_when_conditions_met(self):
        session: dict[str, Any] = {"event_counter": 10}
        result = inject_dispositions(
            session,
            habit_active=True,
            habit_score=0.5,
            context_pressure=0.55,
            consecutive_failures=0,
            enter_score=0.70,
        )
        assert len(result) == 1
        assert result[0]["tool_hint"] == "habit_compact"
        assert result[0]["priority"] == 3
        assert "55%" in result[0]["disposition"]

    def test_compact_pressure_does_not_fire_below_threshold(self):
        session: dict[str, Any] = {"event_counter": 10}
        result = inject_dispositions(
            session,
            habit_active=True,
            habit_score=0.5,
            context_pressure=0.49,
            consecutive_failures=0,
            enter_score=0.70,
        )
        assert result == []

    def test_compact_pressure_does_not_fire_without_habit(self):
        session: dict[str, Any] = {"event_counter": 10}
        result = inject_dispositions(
            session,
            habit_active=False,
            habit_score=0.5,
            context_pressure=0.80,
            consecutive_failures=0,
            enter_score=0.70,
        )
        # compact_pressure requires habit_active — no dispositions should fire at all
        compact_hints = [d for d in result if d["tool_hint"] == "habit_compact"]
        assert compact_hints == []

    def test_compact_pressure_boundary_at_50_percent(self):
        session: dict[str, Any] = {"event_counter": 10}
        result = inject_dispositions(
            session,
            habit_active=True,
            habit_score=0.5,
            context_pressure=0.50,
            consecutive_failures=0,
            enter_score=0.70,
        )
        assert len(result) == 1
        assert result[0]["tool_hint"] == "habit_compact"

    def test_habit_enter_fires_when_score_meets_threshold(self):
        session: dict[str, Any] = {"event_counter": 25}
        result = inject_dispositions(
            session,
            habit_active=False,
            habit_score=0.75,
            context_pressure=0.0,
            consecutive_failures=0,
            enter_score=0.70,
        )
        assert len(result) == 1
        assert result[0]["tool_hint"] == "declare_mode"
        assert result[0]["priority"] == 1

    def test_habit_enter_does_not_fire_when_already_active(self):
        session: dict[str, Any] = {"event_counter": 25}
        result = inject_dispositions(
            session,
            habit_active=True,
            habit_score=0.90,
            context_pressure=0.0,
            consecutive_failures=0,
            enter_score=0.70,
        )
        declare_hints = [d for d in result if d["tool_hint"] == "declare_mode"]
        assert declare_hints == []

    def test_habit_enter_does_not_fire_below_enter_score(self):
        session: dict[str, Any] = {"event_counter": 25}
        result = inject_dispositions(
            session,
            habit_active=False,
            habit_score=0.65,
            context_pressure=0.0,
            consecutive_failures=0,
            enter_score=0.70,
        )
        assert result == []

    def test_habit_enter_fires_at_exact_enter_score(self):
        session: dict[str, Any] = {"event_counter": 25}
        result = inject_dispositions(
            session,
            habit_active=False,
            habit_score=0.70,
            context_pressure=0.0,
            consecutive_failures=0,
            enter_score=0.70,
        )
        assert len(result) == 1
        assert result[0]["tool_hint"] == "declare_mode"

    def test_constraint_reorient_fires_on_3_failures(self):
        session: dict[str, Any] = {"event_counter": 5}
        result = inject_dispositions(
            session,
            habit_active=False,
            habit_score=0.0,
            context_pressure=0.0,
            consecutive_failures=3,
            enter_score=0.70,
        )
        assert len(result) == 1
        assert result[0]["tool_hint"] == "constraint_check"
        assert result[0]["priority"] == 2
        assert "3 consecutive failures" in result[0]["disposition"]

    def test_constraint_reorient_fires_on_more_than_3_failures(self):
        session: dict[str, Any] = {"event_counter": 5}
        result = inject_dispositions(
            session,
            habit_active=False,
            habit_score=0.0,
            context_pressure=0.0,
            consecutive_failures=7,
            enter_score=0.70,
        )
        assert len(result) == 1
        assert "7 consecutive failures" in result[0]["disposition"]

    def test_constraint_reorient_does_not_fire_below_3(self):
        session: dict[str, Any] = {"event_counter": 5}
        result = inject_dispositions(
            session,
            habit_active=False,
            habit_score=0.0,
            context_pressure=0.0,
            consecutive_failures=2,
            enter_score=0.70,
        )
        assert result == []

    def test_cooldown_prevents_rapid_refiring(self):
        session: dict[str, Any] = {"event_counter": 5}
        # First fire
        result1 = inject_dispositions(
            session,
            habit_active=False,
            habit_score=0.0,
            context_pressure=0.0,
            consecutive_failures=3,
            enter_score=0.70,
        )
        assert len(result1) == 1

        # Same event counter — cooldown blocks refire (cooldown is 1 event)
        result2 = inject_dispositions(
            session,
            habit_active=False,
            habit_score=0.0,
            context_pressure=0.0,
            consecutive_failures=3,
            enter_score=0.70,
        )
        assert result2 == []

    def test_cooldown_allows_fire_after_cooldown_period(self):
        session: dict[str, Any] = {"event_counter": 5}
        # First fire
        inject_dispositions(
            session,
            habit_active=False,
            habit_score=0.0,
            context_pressure=0.0,
            consecutive_failures=3,
            enter_score=0.70,
        )
        # Advance past cooldown (constraint_reorient cooldown = 1 event)
        session["event_counter"] = 6
        result = inject_dispositions(
            session,
            habit_active=False,
            habit_score=0.0,
            context_pressure=0.0,
            consecutive_failures=3,
            enter_score=0.70,
        )
        assert len(result) == 1

    def test_compact_pressure_cooldown_is_5_events(self):
        session: dict[str, Any] = {"event_counter": 10}
        # First fire
        inject_dispositions(
            session,
            habit_active=True,
            habit_score=0.0,
            context_pressure=0.60,
            consecutive_failures=0,
            enter_score=0.70,
        )
        # 4 events later — still in cooldown
        session["event_counter"] = 14
        result = inject_dispositions(
            session,
            habit_active=True,
            habit_score=0.0,
            context_pressure=0.60,
            consecutive_failures=0,
            enter_score=0.70,
        )
        assert result == []

        # 5 events later — cooldown expired
        session["event_counter"] = 15
        result = inject_dispositions(
            session,
            habit_active=True,
            habit_score=0.0,
            context_pressure=0.60,
            consecutive_failures=0,
            enter_score=0.70,
        )
        assert len(result) == 1

    def test_habit_enter_cooldown_is_20_events(self):
        session: dict[str, Any] = {"event_counter": 25}
        # First fire
        inject_dispositions(
            session,
            habit_active=False,
            habit_score=0.80,
            context_pressure=0.0,
            consecutive_failures=0,
            enter_score=0.70,
        )
        # 19 events later — still in cooldown
        session["event_counter"] = 44
        result = inject_dispositions(
            session,
            habit_active=False,
            habit_score=0.80,
            context_pressure=0.0,
            consecutive_failures=0,
            enter_score=0.70,
        )
        assert result == []

        # 20 events later — cooldown expired
        session["event_counter"] = 45
        result = inject_dispositions(
            session,
            habit_active=False,
            habit_score=0.80,
            context_pressure=0.0,
            consecutive_failures=0,
            enter_score=0.70,
        )
        assert len(result) == 1

    def test_multiple_triggers_fire_simultaneously(self):
        session: dict[str, Any] = {"event_counter": 25}
        result = inject_dispositions(
            session,
            habit_active=True,
            habit_score=0.0,
            context_pressure=0.70,
            consecutive_failures=5,
            enter_score=0.70,
        )
        hints = {d["tool_hint"] for d in result}
        # compact_pressure (habit_active + pressure) + constraint_reorient (failures >= 3)
        # NOT habit_enter (habit_active is True, so trigger 2 skipped)
        assert "habit_compact" in hints
        assert "constraint_check" in hints
        assert "declare_mode" not in hints
        assert len(result) == 2

    def test_cooldowns_stored_in_session_data(self):
        session: dict[str, Any] = {"event_counter": 5}
        inject_dispositions(
            session,
            habit_active=False,
            habit_score=0.0,
            context_pressure=0.0,
            consecutive_failures=3,
            enter_score=0.70,
        )
        assert "_disposition_cooldowns" in session
        assert "constraint_reorient" in session["_disposition_cooldowns"]
        assert session["_disposition_cooldowns"]["constraint_reorient"] == 5

    def test_context_pressure_rounding(self):
        session: dict[str, Any] = {"event_counter": 10}
        result = inject_dispositions(
            session,
            habit_active=True,
            habit_score=0.0,
            context_pressure=0.678,
            consecutive_failures=0,
            enter_score=0.70,
        )
        assert len(result) == 1
        # 0.678 * 100 = 67.8, round = 68
        assert "68%" in result[0]["disposition"]


# ── build_pulse_delta ────────────────────────────────────────────────


class TestBuildPulseDelta:
    def test_minimal_delta(self):
        delta = build_pulse_delta({}, None, 5, [])
        assert delta["pulse"] is True
        assert delta["events_since_last"] == 5
        assert "changes" not in delta
        assert "dispositions" not in delta

    def test_blocking_detected(self):
        report = {"systemMessage": "BLOCKING issue found in lint"}
        delta = build_pulse_delta(report, None, 3, [])
        assert delta["changes"]["has_blockers"] is True
        assert delta["changes"] == {"has_blockers": True}

    def test_coherence_detected(self):
        report = {"systemMessage": "Low coherence score detected"}
        delta = build_pulse_delta(report, None, 2, [])
        assert delta["changes"]["coherence_mentioned"] is True

    def test_coherence_case_insensitive(self):
        report = {"systemMessage": "COHERENCE is fine"}
        delta = build_pulse_delta(report, None, 1, [])
        assert delta["changes"]["coherence_mentioned"] is True

    def test_both_blocking_and_coherence(self):
        report = {"systemMessage": "BLOCKING with low coherence"}
        delta = build_pulse_delta(report, None, 4, [])
        assert delta["changes"]["has_blockers"] is True
        assert delta["changes"]["coherence_mentioned"] is True

    def test_dispositions_included(self):
        disps = [{"disposition": "test", "priority": 1}]
        delta = build_pulse_delta({}, None, 1, disps)
        assert delta["dispositions"] == disps

    def test_empty_dispositions_not_included(self):
        delta = build_pulse_delta({}, None, 1, [])
        assert "dispositions" not in delta

    def test_no_system_message_no_changes(self):
        report = {"otherKey": "value"}
        delta = build_pulse_delta(report, None, 1, [])
        assert "changes" not in delta

    def test_events_since_zero(self):
        delta = build_pulse_delta({}, None, 0, [])
        assert delta["events_since_last"] == 0


# ── extract_habit_signals ────────────────────────────────────────────


class TestExtractHabitSignals:
    def test_empty_session(self):
        active, score, pressure, failures = extract_habit_signals({})
        assert active is False
        assert score == 0.0
        assert pressure == 0.0
        assert failures == 0

    def test_habit_active_true(self):
        session = {"habit_state": {"active": True, "habit_score": 0.85}}
        active, score, pressure, failures = extract_habit_signals(session)
        assert active is True
        assert score == 0.85

    def test_habit_active_false(self):
        session = {"habit_state": {"active": False, "habit_score": 0.3}}
        active, score, _, _ = extract_habit_signals(session)
        assert active is False
        assert score == 0.3

    def test_habit_state_not_dict(self):
        session = {"habit_state": "invalid"}
        active, score, _, _ = extract_habit_signals(session)
        assert active is False
        assert score == 0.0

    def test_context_pressure_calculation(self):
        session = {
            "token_tracker": {
                "estimated_tokens_used": 100000,
                "context_window_size": 200000,
            }
        }
        _, _, pressure, _ = extract_habit_signals(session)
        assert pressure == pytest.approx(0.5)

    def test_context_pressure_zero_window_safeguard(self):
        session = {
            "token_tracker": {
                "estimated_tokens_used": 50000,
                "context_window_size": 0,
            }
        }
        _, _, pressure, _ = extract_habit_signals(session)
        # max(0, 1) = 1, so 50000 / 1 = 50000
        assert pressure == 50000.0

    def test_context_pressure_missing_tracker(self):
        session: dict[str, Any] = {}
        _, _, pressure, _ = extract_habit_signals(session)
        assert pressure == 0.0

    def test_context_pressure_tracker_not_dict(self):
        session = {"token_tracker": "invalid"}
        _, _, pressure, _ = extract_habit_signals(session)
        assert pressure == 0.0

    def test_consecutive_failures_from_action_ring(self):
        session = {
            "action_history": [
                {"exit_code": 0},
                {"exit_code": 1},
                {"exit_code": 2},
                {"exit_code": 1},
            ]
        }
        _, _, _, failures = extract_habit_signals(session)
        assert failures == 3

    def test_consecutive_failures_all_success(self):
        session = {
            "action_history": [
                {"exit_code": 0},
                {"exit_code": 0},
            ]
        }
        _, _, _, failures = extract_habit_signals(session)
        assert failures == 0

    def test_consecutive_failures_break_on_success(self):
        session = {
            "action_history": [
                {"exit_code": 1},
                {"exit_code": 0},
                {"exit_code": 1},
            ]
        }
        _, _, _, failures = extract_habit_signals(session)
        # Only the trailing exit_code=1 counts
        assert failures == 1

    def test_consecutive_failures_empty_action_ring(self):
        session: dict[str, list[Any]] = {"action_history": []}
        _, _, _, failures = extract_habit_signals(session)
        assert failures == 0

    def test_consecutive_failures_non_dict_entries(self):
        session = {
            "action_history": [
                {"exit_code": 1},
                "not_a_dict",
            ]
        }
        _, _, _, failures = extract_habit_signals(session)
        # "not_a_dict" is not a dict, so isinstance check fails → break
        assert failures == 0

    def test_action_ring_not_list(self):
        session = {"action_history": "invalid"}
        _, _, _, failures = extract_habit_signals(session)
        assert failures == 0

    def test_non_dict_session_data(self):
        active, score, pressure, failures = extract_habit_signals("not_a_dict")  # type: ignore[arg-type]
        assert active is False
        assert score == 0.0
        assert pressure == 0.0
        assert failures == 0

    def test_default_context_window_size(self):
        # Missing context_window_size defaults to 200000
        session = {
            "token_tracker": {
                "estimated_tokens_used": 100000,
            }
        }
        _, _, pressure, _ = extract_habit_signals(session)
        assert pressure == pytest.approx(0.5)

    def test_missing_estimated_tokens_defaults_to_zero(self):
        session = {
            "token_tracker": {
                "context_window_size": 200000,
            }
        }
        _, _, pressure, _ = extract_habit_signals(session)
        assert pressure == 0.0


# ── arbitrate_output ─────────────────────────────────────────────────


class TestArbitrateOutput:
    def test_full_verbosity_returns_report_unchanged(self):
        cfg = StubConfig(hook_verbosity="full")
        report = {"systemMessage": "hello"}
        result = arbitrate_output(report, cfg, {})
        assert result == report

    def test_silent_verbosity_suppresses_output(self):
        cfg = StubConfig(hook_verbosity="silent")
        session: dict[str, Any] = {"event_counter": 1}
        report = {"systemMessage": "hello"}
        result = arbitrate_output(report, cfg, session)
        assert result == {}

    def test_dispositions_injected_before_gating(self):
        cfg = StubConfig(hook_verbosity="full")
        session: dict[str, Any] = {
            "event_counter": 10,
            "action_history": [
                {"exit_code": 1},
                {"exit_code": 1},
                {"exit_code": 1},
            ],
        }
        report: dict[str, Any] = {"systemMessage": "test"}
        result = arbitrate_output(report, cfg, session)
        # constraint_reorient should have fired
        disps = result.get("hookSpecificOutput", {}).get("dispositions", [])
        assert len(disps) >= 1
        assert any(d["tool_hint"] == "constraint_check" for d in disps)

    def test_dispositions_disabled_by_config(self):
        cfg = StubConfig(hook_verbosity="full", hook_dispositions_enabled=False)
        session: dict[str, Any] = {
            "event_counter": 10,
            "action_history": [
                {"exit_code": 1},
                {"exit_code": 1},
                {"exit_code": 1},
            ],
        }
        report: dict[str, Any] = {"systemMessage": "test"}
        result = arbitrate_output(report, cfg, session)
        disps = result.get("hookSpecificOutput", {}).get("dispositions", [])
        assert disps == [] or "dispositions" not in result.get("hookSpecificOutput", {})

    def test_silent_with_high_priority_disposition_force_emits(self):
        cfg = StubConfig(hook_verbosity="silent")
        # Set up conditions that trigger compact_pressure (priority 3)
        session: dict[str, Any] = {
            "event_counter": 10,
            "habit_state": {"active": True, "habit_score": 0.5},
            "token_tracker": {
                "estimated_tokens_used": 120000,
                "context_window_size": 200000,
            },
        }
        report: dict[str, Any] = {"systemMessage": "test"}
        result = arbitrate_output(report, cfg, session)
        # compact_pressure is priority 3 → should_force_emit → emitted
        assert result != {}
        disps = result.get("hookSpecificOutput", {}).get("dispositions", [])
        assert any(d["tool_hint"] == "habit_compact" for d in disps)

    def test_prev_report_passed_to_should_emit(self):
        cfg = StubConfig(hook_verbosity="silent")
        session: dict[str, Any] = {"event_counter": 1}
        report = {"systemMessage": "BLOCKING new issue"}
        prev = {"systemMessage": "all clear"}
        result = arbitrate_output(report, cfg, session, prev_report=prev)
        # New BLOCKING → force emit even in silent mode
        assert result != {}

    def test_empty_report_in_full_mode(self):
        cfg = StubConfig(hook_verbosity="full")
        result = arbitrate_output({}, cfg, {})
        # Empty report with full verbosity still returns (should_emit returns True)
        # but dispositions list is empty and empty report is truthy {}
        assert (
            result == {}
        )  # should_emit returns True, but report is {} which is falsy? No, {} is falsy in bool but report is returned as-is
        # Actually let's trace: report={}, dispositions=[] (no conditions met),
        # dispositions is empty so the "if dispositions and report" check skips.
        # Then should_emit with full → True. So return report which is {}.
        assert isinstance(result, dict)

    def test_pulse_mode_integration(self):
        cfg = StubConfig(hook_verbosity="pulse", hook_pulse_interval=3)
        session: dict[str, Any] = {"event_counter": 6, "_last_pulse_event": 3}
        report: dict[str, Any] = {"systemMessage": "pulse output"}
        result = arbitrate_output(report, cfg, session)
        assert result == report

    def test_pulse_mode_suppressed(self):
        cfg = StubConfig(hook_verbosity="pulse", hook_pulse_interval=5)
        session: dict[str, Any] = {"event_counter": 3, "_last_pulse_event": 1}
        report: dict[str, Any] = {"systemMessage": "suppressed"}
        result = arbitrate_output(report, cfg, session)
        assert result == {}

    def test_habit_mode_enter_score_from_config(self):
        cfg = StubConfig(
            hook_verbosity="full",
            habit_mode_enter_score=0.50,
        )
        session: dict[str, Any] = {
            "event_counter": 25,
            "habit_state": {"active": False, "habit_score": 0.55},
        }
        report: dict[str, Any] = {"systemMessage": "test"}
        result = arbitrate_output(report, cfg, session)
        disps = result.get("hookSpecificOutput", {}).get("dispositions", [])
        assert any(d["tool_hint"] == "declare_mode" for d in disps)

    def test_missing_config_attribute_defaults(self):
        cfg = BareObject()
        session: dict[str, Any] = {"event_counter": 1}
        report: dict[str, Any] = {"systemMessage": "test"}
        # hook_verbosity defaults to "full", hook_dispositions_enabled defaults to True
        result = arbitrate_output(report, cfg, session)
        assert result == report


# ── _DISPOSITION_TRIGGERS constant ───────────────────────────────────


class TestDispositionTriggersConstant:
    def test_three_triggers_defined(self):
        assert len(_DISPOSITION_TRIGGERS) == 3

    def test_trigger_names(self):
        names = [t["name"] for t in _DISPOSITION_TRIGGERS]
        assert names == ["compact_pressure", "habit_enter_suggested", "constraint_reorient"]

    def test_all_triggers_have_required_keys(self):
        for trigger in _DISPOSITION_TRIGGERS:
            assert "name" in trigger
            assert "cooldown_events" in trigger
            assert "priority" in trigger
            assert "tool_hint" in trigger

    def test_cooldown_values(self):
        cooldowns = {t["name"]: t["cooldown_events"] for t in _DISPOSITION_TRIGGERS}
        assert cooldowns["compact_pressure"] == 5
        assert cooldowns["habit_enter_suggested"] == 20
        assert cooldowns["constraint_reorient"] == 1

    def test_priority_values(self):
        priorities = {t["name"]: t["priority"] for t in _DISPOSITION_TRIGGERS}
        assert priorities["compact_pressure"] == 3
        assert priorities["habit_enter_suggested"] == 1
        assert priorities["constraint_reorient"] == 2
