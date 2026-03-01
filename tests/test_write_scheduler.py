"""Tests for lintgate.write_scheduler — cadence control for dynamic rule files."""

from __future__ import annotations

import pytest

from lintgate.write_scheduler import (
    WriteScheduler,
    mark_dirty,
    record_tool_call,
    record_write,
    should_write,
)

# ── Serialization ────────────────────────────────────────────────────


class TestWriteSchedulerDataclass:
    def test_defaults(self):
        ws = WriteScheduler()
        assert ws.last_write_time == 0.0
        assert ws.dirty is False
        assert ws.min_interval_s == 30.0
        assert ws.tool_call_interval == 10

    def test_round_trip(self):
        ws = WriteScheduler(
            last_write_time=100.0,
            last_write_generation=5,
            writes_this_session=3,
            dirty=True,
            tool_calls_since_write=7,
        )
        d = ws.to_dict()
        restored = WriteScheduler.from_dict(d)
        assert restored.last_write_time == 100.0
        assert restored.last_write_generation == 5
        assert restored.writes_this_session == 3
        assert restored.dirty is True
        assert restored.tool_calls_since_write == 7

    def test_from_dict_tolerates_extra_keys(self):
        ws = WriteScheduler.from_dict({"dirty": True, "unknown": 42})
        assert ws.dirty is True

    def test_from_dict_tolerates_missing_keys(self):
        ws = WriteScheduler.from_dict({})
        assert ws.dirty is False
        assert ws.min_interval_s == 30.0


# ── Immediate triggers ───────────────────────────────────────────────


class TestImmediateTriggers:
    """Immediate triggers bypass cooldown and always write when generation changed."""

    @pytest.mark.parametrize(
        "trigger",
        [
            "mode_transition",
            "compass_violation",
            "compaction",
            "session_start",
        ],
    )
    def test_immediate_trigger_writes(self, trigger):
        ws = WriteScheduler(last_write_generation=1, last_write_time=100.0)
        assert (
            should_write(ws, current_generation=2, trigger=trigger, now=100.1) is True
        )

    @pytest.mark.parametrize(
        "trigger",
        [
            "mode_transition",
            "compass_violation",
            "compaction",
            "session_start",
        ],
    )
    def test_immediate_trigger_skips_when_same_generation(self, trigger):
        ws = WriteScheduler(last_write_generation=5)
        assert should_write(ws, current_generation=5, trigger=trigger) is False

    def test_immediate_ignores_cooldown(self):
        ws = WriteScheduler(last_write_generation=1, last_write_time=100.0)
        # Only 0.1s elapsed — well under min_interval_s
        assert (
            should_write(ws, current_generation=2, trigger="mode_transition", now=100.1)
            is True
        )


# ── Cadenced triggers ────────────────────────────────────────────────


class TestCadencedTriggers:
    """Cadenced triggers respect cooldown and dirty flag."""

    def test_tool_call_not_dirty_skips(self):
        ws = WriteScheduler(
            last_write_generation=1,
            dirty=False,
            tool_calls_since_write=20,
            last_write_time=0.0,
        )
        assert (
            should_write(ws, current_generation=2, trigger="tool_call", now=1000.0)
            is False
        )

    def test_tool_call_dirty_under_cooldown_skips(self):
        ws = WriteScheduler(
            last_write_generation=1,
            dirty=True,
            tool_calls_since_write=20,
            last_write_time=100.0,
            min_interval_s=30.0,
        )
        # Only 5s elapsed
        assert (
            should_write(ws, current_generation=2, trigger="tool_call", now=105.0)
            is False
        )

    def test_tool_call_dirty_past_cooldown_interval_met(self):
        ws = WriteScheduler(
            last_write_generation=1,
            dirty=True,
            tool_calls_since_write=10,
            last_write_time=100.0,
            min_interval_s=30.0,
            tool_call_interval=10,
        )
        assert (
            should_write(ws, current_generation=2, trigger="tool_call", now=135.0)
            is True
        )

    def test_tool_call_dirty_past_cooldown_interval_not_met(self):
        ws = WriteScheduler(
            last_write_generation=1,
            dirty=True,
            tool_calls_since_write=5,
            last_write_time=100.0,
            min_interval_s=30.0,
            tool_call_interval=10,
        )
        assert (
            should_write(ws, current_generation=2, trigger="tool_call", now=135.0)
            is False
        )

    def test_lint_complete_dirty_past_cooldown(self):
        ws = WriteScheduler(
            last_write_generation=1,
            dirty=True,
            last_write_time=100.0,
            min_interval_s=30.0,
        )
        assert (
            should_write(ws, current_generation=2, trigger="lint_complete", now=135.0)
            is True
        )

    def test_lint_complete_not_dirty_skips(self):
        ws = WriteScheduler(
            last_write_generation=1,
            dirty=False,
            last_write_time=100.0,
        )
        assert (
            should_write(ws, current_generation=2, trigger="lint_complete", now=200.0)
            is False
        )

    def test_timer_dirty_past_cooldown(self):
        ws = WriteScheduler(
            last_write_generation=1,
            dirty=True,
            last_write_time=100.0,
            min_interval_s=30.0,
        )
        assert (
            should_write(ws, current_generation=2, trigger="timer", now=135.0) is True
        )

    def test_max_interval_forces_write(self):
        ws = WriteScheduler(
            last_write_generation=1,
            dirty=True,
            last_write_time=100.0,
            max_interval_s=300.0,
        )
        # 300s elapsed — max interval hit
        assert (
            should_write(ws, current_generation=2, trigger="tool_call", now=400.0)
            is True
        )

    def test_max_interval_not_reached(self):
        ws = WriteScheduler(
            last_write_generation=1,
            dirty=True,
            tool_calls_since_write=3,
            last_write_time=100.0,
            min_interval_s=30.0,
            max_interval_s=300.0,
            tool_call_interval=10,
        )
        # 200s elapsed — past cooldown but tool call interval not met, not at max
        assert (
            should_write(ws, current_generation=2, trigger="tool_call", now=300.0)
            is False
        )


# ── Skip conditions ──────────────────────────────────────────────────


class TestSkipConditions:
    def test_same_generation_always_skips(self):
        ws = WriteScheduler(last_write_generation=5, dirty=True)
        assert (
            should_write(ws, current_generation=5, trigger="mode_transition") is False
        )
        assert should_write(ws, current_generation=5, trigger="tool_call") is False

    def test_unknown_trigger_skips(self):
        ws = WriteScheduler(last_write_generation=1, dirty=True)
        assert (
            should_write(
                ws, current_generation=2, trigger="unknown_trigger", now=1000.0
            )
            is False
        )


# ── State mutation helpers ───────────────────────────────────────────


class TestStateMutations:
    def test_record_write(self):
        ws = WriteScheduler(
            dirty=True, tool_calls_since_write=15, writes_this_session=2
        )
        record_write(ws, generation=10, now=500.0)
        assert ws.last_write_time == 500.0
        assert ws.last_write_generation == 10
        assert ws.writes_this_session == 3
        assert ws.dirty is False
        assert ws.tool_calls_since_write == 0

    def test_mark_dirty(self):
        ws = WriteScheduler(dirty=False)
        mark_dirty(ws)
        assert ws.dirty is True

    def test_record_tool_call(self):
        ws = WriteScheduler(tool_calls_since_write=4)
        record_tool_call(ws)
        assert ws.tool_calls_since_write == 5


# ── Integration scenario ─────────────────────────────────────────────


class TestIntegrationScenario:
    """Simulate a realistic session: writes on transitions, batches on edits."""

    def test_session_lifecycle(self):
        ws = WriteScheduler()
        gen = 0

        # Session start — immediate write
        gen += 1
        assert should_write(ws, gen, "session_start", now=0.0) is True
        record_write(ws, gen, now=0.0)

        # 10 tool calls, each marks dirty
        for _ in range(10):
            gen += 1
            mark_dirty(ws)
            record_tool_call(ws)

        # Tool call at 15s — under cooldown, skips
        assert should_write(ws, gen, "tool_call", now=15.0) is False

        # Tool call at 35s — past cooldown, interval met, writes
        assert should_write(ws, gen, "tool_call", now=35.0) is True
        record_write(ws, gen, now=35.0)

        # 3 more tool calls, not enough for interval
        for _ in range(3):
            gen += 1
            mark_dirty(ws)
            record_tool_call(ws)

        # Tool call at 70s — past cooldown but interval not met
        assert should_write(ws, gen, "tool_call", now=70.0) is False

        # Mode transition — immediate, writes regardless
        gen += 1
        assert should_write(ws, gen, "mode_transition", now=70.0) is True
        record_write(ws, gen, now=70.0)

        # Compaction — immediate
        gen += 1
        assert should_write(ws, gen, "compaction", now=71.0) is True
        record_write(ws, gen, now=71.0)

        assert ws.writes_this_session == 4
