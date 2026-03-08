"""Tests for lintgate.specification.scheduler — background orchestration."""

from __future__ import annotations

from lintgate.specification.scheduler import (
    MutationScheduler,
    SchedulerConfig,
    _compute_priority,
)


class TestComputePriority:
    def test_pure_with_hints_highest(self):
        score = _compute_priority(sigma=20, risk_score=0.8, is_pure=True, has_hints=True)
        base = _compute_priority(sigma=20, risk_score=0.8, is_pure=False, has_hints=False)
        assert score > base

    def test_no_data_premium(self):
        none_score = _compute_priority(sigma=10, coverage_depth="none")
        sampled_score = _compute_priority(sigma=10, coverage_depth="sampled")
        assert none_score > sampled_score

    def test_higher_sigma_higher_priority(self):
        low = _compute_priority(sigma=5)
        high = _compute_priority(sigma=25)
        assert high > low

    def test_higher_risk_higher_priority(self):
        low = _compute_priority(risk_score=0.1)
        high = _compute_priority(risk_score=0.9)
        assert high > low


class TestMutationScheduler:
    def test_enqueue_and_next_batch(self):
        sched = MutationScheduler(SchedulerConfig(batch_size=2, cooldown_s=0))
        sched.enqueue("f1", "a.py", sigma=10)
        sched.enqueue("f2", "a.py", sigma=20)
        sched.enqueue("f3", "a.py", sigma=5)

        batch = sched.next_batch()
        assert len(batch) == 2
        # Highest priority first (sigma=20)
        assert batch[0].function_key == "f2"

    def test_cooldown_enforced(self):
        sched = MutationScheduler(SchedulerConfig(batch_size=2, cooldown_s=9999))
        sched.enqueue("f1", "a.py", sigma=10)
        sched.enqueue("f2", "a.py", sigma=10)
        sched.enqueue("f3", "a.py", sigma=10)

        batch1 = sched.next_batch()
        assert len(batch1) == 2
        # Second batch blocked by cooldown
        batch2 = sched.next_batch()
        assert len(batch2) == 0

    def test_auto_promote_high_survival(self):
        sched = MutationScheduler(SchedulerConfig(auto_promote=True, cooldown_s=0))
        sched.enqueue("f1", "a.py", sigma=10)
        batch = sched.next_batch()
        assert len(batch) == 1

        # Report high survival → should promote to profiling
        sched.report_result(batch[0], survival_rate=0.5)
        status = sched.status()
        assert status.profiling_pending == 1

    def test_auto_promote_skips_fully_killed(self):
        sched = MutationScheduler(SchedulerConfig(auto_promote=True, cooldown_s=0))
        sched.enqueue("f1", "a.py", sigma=10)
        batch = sched.next_batch()

        # Report 0% survival, no budget exhaust → skip profiling
        sched.report_result(batch[0], survival_rate=0.0)
        status = sched.status()
        assert status.profiling_pending == 0

    def test_status(self):
        sched = MutationScheduler()
        sched.enqueue("f1", "a.py", sigma=10)
        sched.enqueue("f2", "a.py", sigma=10, coverage_depth="sampled")

        status = sched.status()
        assert status.queue_depth == 2
        assert status.sampling_pending >= 1

    def test_persistence_round_trip(self, tmp_path):
        sched = MutationScheduler(SchedulerConfig(cooldown_s=0))
        sched.enqueue("f1", "a.py", sigma=10)
        sched.enqueue("f2", "b.py", sigma=20)

        sched.save_state(tmp_path)

        sched2 = MutationScheduler()
        loaded = sched2.load_state(tmp_path)
        assert loaded
        assert sched2.status().queue_depth == 2

    def test_load_nonexistent(self, tmp_path):
        sched = MutationScheduler()
        assert not sched.load_state(tmp_path)

    def test_empty_queue(self):
        sched = MutationScheduler(SchedulerConfig(cooldown_s=0))
        batch = sched.next_batch()
        assert batch == []
        status = sched.status()
        assert status.queue_depth == 0
