"""Tests for lintgate.specification.scheduler."""

from __future__ import annotations

import json
from unittest.mock import patch

from lintgate.specification.scheduler import (
    MutationScheduler,
    ScheduledItem,
    SchedulerConfig,
    SchedulerStatus,
    _compute_priority,
    _item_from_dict,
)

# ── _compute_priority ────────────────────────────────────────────────


class TestComputePriority:
    def test_defaults_include_coverage_none_boost(self):
        # Default coverage_depth="none" adds 20
        assert _compute_priority() == 20.0

    def test_full_coverage_no_coverage_boost(self):
        assert _compute_priority(coverage_depth="full") == 0.0

    def test_sigma_contributes_up_to_40(self):
        base = _compute_priority(coverage_depth="full")  # isolate sigma
        # sigma=30 → min(30/30, 1.0) * 40 = 40
        assert _compute_priority(sigma=30, coverage_depth="full") == base + 40.0
        # sigma=60 → capped at 1.0 * 40 = 40
        assert _compute_priority(sigma=60, coverage_depth="full") == base + 40.0
        # sigma=15 → min(15/30, 1.0) * 40 = 20
        assert _compute_priority(sigma=15, coverage_depth="full") == base + 20.0

    def test_risk_score_contributes_up_to_30(self):
        assert _compute_priority(risk_score=1.0, coverage_depth="full") == 30.0
        assert _compute_priority(risk_score=0.5, coverage_depth="full") == 15.0

    def test_pure_with_hints_adds_10(self):
        assert _compute_priority(is_pure=True, has_hints=True, coverage_depth="full") == 10.0

    def test_pure_without_hints_adds_nothing(self):
        assert _compute_priority(is_pure=True, has_hints=False, coverage_depth="full") == 0.0

    def test_hints_without_pure_adds_nothing(self):
        assert _compute_priority(is_pure=False, has_hints=True, coverage_depth="full") == 0.0

    def test_contradicts_with_high_confidence_adds_15(self):
        score = _compute_priority(
            overlay_status="CONTRADICTS", overlay_confidence=0.7, coverage_depth="full"
        )
        assert score == 15.0

    def test_contradicts_with_low_confidence_no_boost(self):
        score = _compute_priority(
            overlay_status="CONTRADICTS", overlay_confidence=0.5, coverage_depth="full"
        )
        assert score == 0.0

    def test_no_empirical_data_adds_8(self):
        score = _compute_priority(overlay_status="NO_EMPIRICAL_DATA", coverage_depth="full")
        assert score == 8.0

    def test_agrees_no_boost(self):
        score = _compute_priority(
            overlay_status="AGREES", overlay_confidence=0.9, coverage_depth="full"
        )
        assert score == 0.0

    def test_combined_max_score(self):
        score = _compute_priority(
            sigma=30,
            risk_score=1.0,
            coverage_depth="none",
            is_pure=True,
            has_hints=True,
            overlay_status="CONTRADICTS",
            overlay_confidence=0.9,
        )
        # 40 + 30 + 20 + 10 + 15 = 115
        assert score == 115.0

    def test_sigma_linear_scaling(self):
        # Use coverage_depth="full" to isolate sigma contribution
        s10 = _compute_priority(sigma=10, coverage_depth="full")
        s20 = _compute_priority(sigma=20, coverage_depth="full")
        assert abs(s20 - 2 * s10) < 1e-9


# ── ScheduledItem ────────────────────────────────────────────────────


class TestScheduledItem:
    def test_defaults(self):
        item = ScheduledItem(function_key="m::f", file_path="src/m.py")
        assert item.priority == 0.0
        assert item.tier == "sampling"
        assert item.enqueued_at == 0.0

    def test_to_dict_keys(self):
        item = ScheduledItem(
            function_key="m::f",
            file_path="src/m.py",
            priority=12.345,
            tier="profiling",
            enqueued_at=1000.0,
        )
        d = item.to_dict()
        assert set(d.keys()) == {"function_key", "file_path", "priority", "tier", "enqueued_at"}

    def test_to_dict_rounds_priority(self):
        item = ScheduledItem(function_key="m::f", file_path="m.py", priority=12.3456789)
        d = item.to_dict()
        assert d["priority"] == 12.35

    def test_to_dict_values(self):
        item = ScheduledItem(
            function_key="mod::func",
            file_path="src/mod.py",
            priority=50.0,
            tier="sampling",
            enqueued_at=999.0,
        )
        d = item.to_dict()
        assert d["function_key"] == "mod::func"
        assert d["file_path"] == "src/mod.py"
        assert d["tier"] == "sampling"
        assert d["enqueued_at"] == 999.0


# ── SchedulerStatus ──────────────────────────────────────────────────


class TestSchedulerStatus:
    def test_defaults(self):
        s = SchedulerStatus()
        assert s.queue_depth == 0
        assert s.completed_count == 0
        assert s.estimated_remaining_s == 0.0

    def test_to_dict_keys(self):
        s = SchedulerStatus(queue_depth=5, sampling_pending=3, profiling_pending=2)
        d = s.to_dict()
        assert set(d.keys()) == {
            "queue_depth",
            "completed_count",
            "sampling_pending",
            "profiling_pending",
            "estimated_remaining_s",
        }

    def test_to_dict_rounds_remaining(self):
        s = SchedulerStatus(estimated_remaining_s=12.3456)
        assert s.to_dict()["estimated_remaining_s"] == 12.3

    def test_last_batch_at_not_in_to_dict(self):
        s = SchedulerStatus(last_batch_at=1000.0)
        assert "last_batch_at" not in s.to_dict()


# ── _item_from_dict (round-trip) ─────────────────────────────────────


class TestItemFromDict:
    def test_full_round_trip(self):
        original = ScheduledItem(
            function_key="mod::func",
            file_path="src/mod.py",
            priority=42.5,
            tier="profiling",
            enqueued_at=1000.0,
        )
        reconstructed = _item_from_dict(original.to_dict())
        assert reconstructed.function_key == original.function_key
        assert reconstructed.file_path == original.file_path
        assert reconstructed.priority == original.priority
        assert reconstructed.tier == original.tier
        assert reconstructed.enqueued_at == original.enqueued_at

    def test_missing_keys_use_defaults(self):
        item = _item_from_dict({})
        assert item.function_key == ""
        assert item.file_path == ""
        assert item.priority == 0.0
        assert item.tier == "sampling"
        assert item.enqueued_at == 0.0

    def test_partial_dict(self):
        item = _item_from_dict({"function_key": "m::f", "tier": "profiling"})
        assert item.function_key == "m::f"
        assert item.tier == "profiling"
        assert item.file_path == ""


# ── MutationScheduler ────────────────────────────────────────────────


class TestMutationSchedulerInit:
    def test_default_config(self):
        s = MutationScheduler()
        assert s.config.batch_size == 10
        assert s.config.auto_promote is True

    def test_custom_config(self):
        cfg = SchedulerConfig(batch_size=5, auto_promote=False)
        s = MutationScheduler(config=cfg)
        assert s.config.batch_size == 5
        assert s.config.auto_promote is False

    def test_empty_queue(self):
        s = MutationScheduler()
        assert s.status().queue_depth == 0


class TestMutationSchedulerEnqueue:
    def test_enqueue_adds_to_queue(self):
        s = MutationScheduler()
        s.enqueue("m::f", "m.py", sigma=10)
        assert s.status().queue_depth == 1

    def test_enqueue_sorts_by_priority_desc(self):
        s = MutationScheduler()
        s.enqueue("m::low", "m.py", sigma=5)
        s.enqueue("m::high", "m.py", sigma=30)
        s.enqueue("m::mid", "m.py", sigma=15)
        batch = s._queue
        assert batch[0].function_key == "m::high"
        assert batch[1].function_key == "m::mid"
        assert batch[2].function_key == "m::low"

    def test_enqueue_assigns_sampling_tier_by_default(self):
        s = MutationScheduler()
        s.enqueue("m::f", "m.py")
        assert s._queue[0].tier == "sampling"

    def test_enqueue_promotes_to_profiling_when_sampled(self):
        s = MutationScheduler()
        s.enqueue("m::f", "m.py", coverage_depth="sampled")
        assert s._queue[0].tier == "profiling"

    def test_enqueue_computes_priority(self):
        s = MutationScheduler()
        s.enqueue("m::f", "m.py", sigma=30, risk_score=1.0, coverage_depth="none")
        # 40 + 30 + 20 = 90
        assert s._queue[0].priority == 90.0


class TestMutationSchedulerNextBatch:
    def test_returns_up_to_batch_size(self):
        s = MutationScheduler(config=SchedulerConfig(batch_size=2, cooldown_s=0))
        for i in range(5):
            s.enqueue(f"m::f{i}", "m.py")
        batch = s.next_batch()
        assert len(batch) == 2
        assert s.status().queue_depth == 3

    def test_respects_cooldown(self):
        s = MutationScheduler(config=SchedulerConfig(cooldown_s=60))
        s.enqueue("m::f", "m.py")
        batch1 = s.next_batch()
        assert len(batch1) == 1
        batch2 = s.next_batch()
        assert len(batch2) == 0  # cooldown not elapsed

    def test_empty_queue_returns_empty(self):
        s = MutationScheduler()
        assert s.next_batch() == []

    @patch("time.time", return_value=1000.0)
    def test_cooldown_bypass_after_elapsed(self, _mock_time):
        s = MutationScheduler(config=SchedulerConfig(cooldown_s=10))
        s._last_batch_at = 989.0  # 11s ago
        s.enqueue("m::f", "m.py")
        batch = s.next_batch()
        assert len(batch) == 1


class TestMutationSchedulerReportResult:
    def test_completed_count_increments(self):
        s = MutationScheduler()
        item = ScheduledItem(function_key="m::f", file_path="m.py", tier="sampling")
        s.report_result(item, survival_rate=0.0)
        assert s.status().completed_count == 1

    def test_auto_promote_high_survival(self):
        s = MutationScheduler(config=SchedulerConfig(auto_promote=True))
        item = ScheduledItem(function_key="m::f", file_path="m.py", tier="sampling", priority=10.0)
        s.report_result(item, survival_rate=0.5)
        assert s.status().queue_depth == 1
        assert s._queue[0].tier == "profiling"
        assert s._queue[0].priority == 15.0  # original + 5 boost

    def test_no_promote_zero_survival(self):
        s = MutationScheduler(config=SchedulerConfig(auto_promote=True))
        item = ScheduledItem(function_key="m::f", file_path="m.py", tier="sampling")
        s.report_result(item, survival_rate=0.0, budget_exhausted=False)
        assert s.status().queue_depth == 0

    def test_promote_on_budget_exhausted(self):
        s = MutationScheduler(config=SchedulerConfig(auto_promote=True))
        item = ScheduledItem(function_key="m::f", file_path="m.py", tier="sampling", priority=5.0)
        s.report_result(item, survival_rate=0.0, budget_exhausted=True)
        assert s.status().queue_depth == 1
        assert s._queue[0].tier == "profiling"

    def test_no_promote_when_disabled(self):
        s = MutationScheduler(config=SchedulerConfig(auto_promote=False))
        item = ScheduledItem(function_key="m::f", file_path="m.py", tier="sampling")
        s.report_result(item, survival_rate=0.9)
        assert s.status().queue_depth == 0

    def test_no_promote_profiling_tier(self):
        s = MutationScheduler(config=SchedulerConfig(auto_promote=True))
        item = ScheduledItem(function_key="m::f", file_path="m.py", tier="profiling")
        s.report_result(item, survival_rate=0.9)
        assert s.status().queue_depth == 0

    def test_no_promote_low_survival(self):
        s = MutationScheduler(config=SchedulerConfig(auto_promote=True))
        item = ScheduledItem(function_key="m::f", file_path="m.py", tier="sampling")
        s.report_result(item, survival_rate=0.1)
        assert s.status().queue_depth == 0

    def test_boundary_survival_0_2_not_promoted(self):
        s = MutationScheduler(config=SchedulerConfig(auto_promote=True))
        item = ScheduledItem(function_key="m::f", file_path="m.py", tier="sampling")
        s.report_result(item, survival_rate=0.2)
        assert s.status().queue_depth == 0

    def test_boundary_survival_above_0_2_promoted(self):
        s = MutationScheduler(config=SchedulerConfig(auto_promote=True))
        item = ScheduledItem(function_key="m::f", file_path="m.py", tier="sampling", priority=1.0)
        s.report_result(item, survival_rate=0.21)
        assert s.status().queue_depth == 1


class TestMutationSchedulerStatus:
    def test_counts_tiers(self):
        s = MutationScheduler()
        s.enqueue("m::a", "m.py", coverage_depth="none")  # sampling
        s.enqueue("m::b", "m.py", coverage_depth="sampled")  # profiling
        s.enqueue("m::c", "m.py", coverage_depth="none")  # sampling
        status = s.status()
        assert status.sampling_pending == 2
        assert status.profiling_pending == 1
        assert status.queue_depth == 3

    def test_estimated_remaining(self):
        s = MutationScheduler()
        s.enqueue("m::a", "m.py", coverage_depth="none")  # sampling: 0.5s
        s.enqueue("m::b", "m.py", coverage_depth="sampled")  # profiling: 5s
        status = s.status()
        assert status.estimated_remaining_s == 5.5


class TestMutationSchedulerPersistence:
    def test_save_and_load(self, tmp_path):
        s = MutationScheduler()
        s.enqueue("m::f", "m.py", sigma=10)
        s.save_state(tmp_path)

        s2 = MutationScheduler()
        loaded = s2.load_state(tmp_path)
        assert loaded is True
        assert s2.status().queue_depth == 1
        assert s2._queue[0].function_key == "m::f"

    def test_save_creates_directory(self, tmp_path):
        s = MutationScheduler()
        nested = tmp_path / "a" / "b"
        s.save_state(nested)
        assert (nested / "scheduler_state.json").exists()

    def test_load_missing_file_returns_false(self, tmp_path):
        s = MutationScheduler()
        assert s.load_state(tmp_path) is False

    def test_load_corrupt_json_returns_false(self, tmp_path):
        (tmp_path / "scheduler_state.json").write_text("not json")
        s = MutationScheduler()
        assert s.load_state(tmp_path) is False

    def test_round_trip_preserves_completed(self, tmp_path):
        s = MutationScheduler(config=SchedulerConfig(cooldown_s=0))
        s.enqueue("m::f", "m.py")
        batch = s.next_batch()
        s.report_result(batch[0], survival_rate=0.0)
        s.save_state(tmp_path)

        s2 = MutationScheduler()
        s2.load_state(tmp_path)
        assert s2.status().completed_count == 1
        assert s2.status().queue_depth == 0

    def test_save_state_file_contents(self, tmp_path):
        s = MutationScheduler()
        s.enqueue("m::f", "m.py", sigma=5)
        s.save_state(tmp_path)
        data = json.loads((tmp_path / "scheduler_state.json").read_text())
        assert "queue" in data
        assert "completed" in data
        assert "last_batch_at" in data
        assert len(data["queue"]) == 1
        assert data["queue"][0]["function_key"] == "m::f"
