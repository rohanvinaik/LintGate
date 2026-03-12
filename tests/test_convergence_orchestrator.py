"""Tests for convergence orchestrator decision engine."""

from __future__ import annotations

from lintgate.testing.convergence_orchestrator import (
    ConvergenceConfig,
    ConvergenceDecision,
    IterationRecord,
    TargetState,
    _check_halt,
    _compute_priority,
    decide,
    init_from_targets,
    summarize,
    update_target,
)


# ── Initialization ────────────────────────────────────────────────


class TestInitFromTargets:
    def test_basic_init(self):
        targets = [
            {"function_key": "mod::foo", "source_file": "mod.py"},
            {"function_key": "mod::bar"},
        ]
        state = init_from_targets(targets)
        assert len(state.targets) == 2
        assert "mod::foo" in state.targets
        assert state.targets["mod::foo"].source_file == "mod.py"
        assert state.targets["mod::bar"].source_file == ""

    def test_empty_targets(self):
        state = init_from_targets([])
        assert len(state.targets) == 0
        assert state.iteration_count == 0

    def test_initial_status_eligible(self):
        state = init_from_targets([{"function_key": "f"}])
        assert state.targets["f"].status == "eligible"
        assert state.targets["f"].phase == "bulk"

    def test_start_ms_set(self):
        state = init_from_targets([{"function_key": "f"}])
        assert state.start_ms > 0

    def test_target_test_file_preserved(self):
        state = init_from_targets([
            {"function_key": "f", "target_test_file": "tests/test_f.py"},
        ])
        assert state.targets["f"].target_test_file == "tests/test_f.py"


# ── State updates ─────────────────────────────────────────────────


class TestUpdateTarget:
    def test_records_iteration(self):
        state = init_from_targets([{"function_key": "f"}])
        update_target(state, "f", spec_level=0.5, kill_rate=0.3)
        t = state.targets["f"]
        assert t.spec_level == 0.5
        assert t.kill_rate == 0.3
        assert len(t.trajectory) == 1
        assert t.trajectory[0].iteration == 1

    def test_delta_computed(self):
        state = init_from_targets([{"function_key": "f"}])
        update_target(state, "f", spec_level=0.3, kill_rate=0.2)
        update_target(state, "f", spec_level=0.5, kill_rate=0.4)
        last = state.targets["f"].trajectory[-1]
        assert abs(last.delta_spec - 0.2) < 1e-9
        assert abs(last.delta_kill - 0.2) < 1e-9

    def test_phase_updated(self):
        state = init_from_targets([{"function_key": "f"}])
        update_target(state, "f", spec_level=0.5, kill_rate=0.3, phase="transition")
        assert state.targets["f"].phase == "transition"

    def test_convergence_rate_stored(self):
        state = init_from_targets([{"function_key": "f"}])
        update_target(state, "f", spec_level=0.5, kill_rate=0.3, convergence_rate=0.8)
        assert state.targets["f"].convergence_rate == 0.8

    def test_unknown_target_ignored(self):
        state = init_from_targets([{"function_key": "f"}])
        update_target(state, "nonexistent", spec_level=0.5, kill_rate=0.3)
        assert state.iteration_count == 0

    def test_iteration_count_increments(self):
        state = init_from_targets([{"function_key": "a"}, {"function_key": "b"}])
        update_target(state, "a", spec_level=0.5, kill_rate=0.3)
        update_target(state, "b", spec_level=0.6, kill_rate=0.4)
        assert state.iteration_count == 2

    def test_elapsed_ms_tracked(self):
        state = init_from_targets([{"function_key": "f"}])
        update_target(state, "f", spec_level=0.5, kill_rate=0.3)
        assert state.elapsed_ms >= 0


# ── Decision engine ───────────────────────────────────────────────


class TestCheckHalt:
    def _target(self, **kwargs) -> TargetState:
        t = TargetState(function_key="f", **kwargs)
        return t

    def test_threshold_reached(self):
        t = self._target(spec_level=0.85, kill_rate=0.75)
        action, reason = _check_halt(t, ConvergenceConfig(), budget_remaining_ms=10000)
        assert action == "converged"
        assert reason == "threshold_reached"

    def test_phase_complete(self):
        t = self._target(phase="complete")
        action, reason = _check_halt(t, ConvergenceConfig(), budget_remaining_ms=10000)
        assert action == "converged"
        assert reason == "phase_complete"

    def test_max_iterations(self):
        t = self._target()
        t.trajectory = [IterationRecord(i, 0.3, 0.2) for i in range(5)]
        action, reason = _check_halt(t, ConvergenceConfig(max_iterations=5),
                                     budget_remaining_ms=10000)
        assert action == "halt"
        assert reason == "max_iterations"

    def test_budget_exhausted(self):
        t = self._target()
        action, reason = _check_halt(t, ConvergenceConfig(), budget_remaining_ms=-1)
        assert action == "halt"
        assert reason == "budget_exhausted"

    def test_stall_detected(self):
        t = self._target(phase="bulk")
        t.trajectory = [
            IterationRecord(1, 0.5, 0.3, delta_spec=0.005, delta_kill=0.005),
            IterationRecord(2, 0.505, 0.305, delta_spec=0.005, delta_kill=0.005),
        ]
        action, reason = _check_halt(t, ConvergenceConfig(stall_limit=2, min_delta=0.01),
                                     budget_remaining_ms=10000)
        assert action == "halt"
        assert reason == "stall_detected"

    def test_stall_in_tail_recommends_decompose(self):
        t = self._target(phase="tail")
        t.trajectory = [
            IterationRecord(1, 0.5, 0.3, delta_spec=0.005, delta_kill=0.005),
            IterationRecord(2, 0.505, 0.305, delta_spec=0.005, delta_kill=0.005),
        ]
        action, reason = _check_halt(t, ConvergenceConfig(stall_limit=2, min_delta=0.01),
                                     budget_remaining_ms=10000)
        assert action == "decompose"
        assert reason == "stall_in_tail"

    def test_iterate_when_no_halt(self):
        t = self._target(spec_level=0.3, kill_rate=0.2, phase="bulk")
        action, reason = _check_halt(t, ConvergenceConfig(), budget_remaining_ms=10000)
        assert action == "iterate"
        assert reason == ""

    def test_threshold_checked_before_max_iterations(self):
        """Converged takes priority over max_iterations."""
        t = self._target(spec_level=0.9, kill_rate=0.8)
        t.trajectory = [IterationRecord(i, 0.9, 0.8) for i in range(5)]
        action, _ = _check_halt(t, ConvergenceConfig(max_iterations=5),
                                budget_remaining_ms=10000)
        assert action == "converged"


class TestComputePriority:
    def test_bulk_highest_priority(self):
        t = TargetState(function_key="f", phase="bulk", spec_level=0.0)
        assert _compute_priority(t) == 100

    def test_tail_lower_priority(self):
        t = TargetState(function_key="f", phase="tail", spec_level=0.0)
        assert _compute_priority(t) == 300

    def test_lower_spec_higher_priority_within_phase(self):
        low = TargetState(function_key="a", phase="bulk", spec_level=0.2)
        high = TargetState(function_key="b", phase="bulk", spec_level=0.8)
        assert _compute_priority(low) < _compute_priority(high)

    def test_bulk_always_before_transition(self):
        bulk_high = TargetState(function_key="a", phase="bulk", spec_level=0.99)
        trans_low = TargetState(function_key="b", phase="transition", spec_level=0.0)
        assert _compute_priority(bulk_high) < _compute_priority(trans_low)


class TestDecide:
    def test_eligible_targets_get_decisions(self):
        state = init_from_targets([
            {"function_key": "a"},
            {"function_key": "b"},
        ])
        decisions = decide(state)
        assert len(decisions) == 2
        assert all(isinstance(d, ConvergenceDecision) for d in decisions)

    def test_converged_targets_excluded(self):
        state = init_from_targets([{"function_key": "a"}, {"function_key": "b"}])
        state.targets["a"].status = "converged"
        decisions = decide(state)
        assert len(decisions) == 1
        assert decisions[0].function_key == "b"

    def test_halted_targets_excluded(self):
        state = init_from_targets([{"function_key": "a"}])
        state.targets["a"].status = "halted"
        decisions = decide(state)
        assert len(decisions) == 0

    def test_converged_sets_status(self):
        state = init_from_targets([{"function_key": "f"}])
        state.targets["f"].spec_level = 0.9
        state.targets["f"].kill_rate = 0.8
        decide(state)
        assert state.targets["f"].status == "converged"

    def test_halt_sets_status(self):
        state = init_from_targets([{"function_key": "f"}])
        state.targets["f"].trajectory = [IterationRecord(i, 0.3, 0.2) for i in range(5)]
        decide(state, ConvergenceConfig(max_iterations=5))
        assert state.targets["f"].status == "halted"

    def test_decompose_sets_status(self):
        state = init_from_targets([{"function_key": "f"}])
        t = state.targets["f"]
        t.phase = "tail"
        t.trajectory = [
            IterationRecord(1, 0.5, 0.3, delta_spec=0.005, delta_kill=0.005),
            IterationRecord(2, 0.505, 0.305, delta_spec=0.005, delta_kill=0.005),
        ]
        decide(state, ConvergenceConfig(stall_limit=2, min_delta=0.01))
        assert t.status == "decompose"

    def test_decisions_sorted_by_priority(self):
        state = init_from_targets([
            {"function_key": "tail_fn"},
            {"function_key": "bulk_fn"},
        ])
        state.targets["tail_fn"].phase = "tail"
        state.targets["bulk_fn"].phase = "bulk"
        decisions = decide(state)
        assert decisions[0].function_key == "bulk_fn"
        assert decisions[1].function_key == "tail_fn"


# ── OrchestratorState properties ──────────────────────────────────


class TestOrchestratorState:
    def test_converged_count(self):
        state = init_from_targets([{"function_key": "a"}, {"function_key": "b"}])
        state.targets["a"].status = "converged"
        assert state.converged_count == 1

    def test_halted_count(self):
        state = init_from_targets([{"function_key": "a"}, {"function_key": "b"}])
        state.targets["a"].status = "halted"
        assert state.halted_count == 1

    def test_eligible_count(self):
        state = init_from_targets([{"function_key": "a"}, {"function_key": "b"}])
        assert state.eligible_count == 2

    def test_ready_to_apply_when_mixed_resolved(self):
        state = init_from_targets([{"function_key": "a"}, {"function_key": "b"}])
        state.targets["a"].status = "converged"
        state.targets["b"].status = "halted"
        # eligible=0, converged>0 → ready
        assert state.ready_to_apply is True

    def test_not_ready_when_all_halted(self):
        state = init_from_targets([{"function_key": "a"}, {"function_key": "b"}])
        state.targets["a"].status = "halted"
        state.targets["b"].status = "halted"
        # eligible=0 but converged=0 → not ready
        assert state.ready_to_apply is False

    def test_ready_to_apply_with_converged(self):
        state = init_from_targets([{"function_key": "a"}, {"function_key": "b"}])
        state.targets["a"].status = "converged"
        state.targets["b"].status = "converged"
        assert state.ready_to_apply is True

    def test_not_ready_when_eligible_remain(self):
        state = init_from_targets([{"function_key": "a"}, {"function_key": "b"}])
        state.targets["a"].status = "converged"
        assert state.ready_to_apply is False


# ── Reporting ─────────────────────────────────────────────────────


class TestSummarize:
    def test_basic_summary_structure(self):
        state = init_from_targets([{"function_key": "f"}])
        update_target(state, "f", spec_level=0.5, kill_rate=0.3)
        s = summarize(state)
        assert s["total_targets"] == 1
        assert s["eligible"] == 1
        assert s["converged"] == 0
        assert s["halted"] == 0
        assert s["iterations"] == 1
        assert "targets" in s

    def test_target_summary_fields(self):
        state = init_from_targets([{"function_key": "f"}])
        update_target(state, "f", spec_level=0.5, kill_rate=0.3, phase="transition")
        s = summarize(state)
        ts = s["targets"][0]
        assert ts["function_key"] == "f"
        assert ts["spec_level"] == 0.5
        assert ts["kill_rate"] == 0.3
        assert ts["phase"] == "transition"
        assert ts["iterations"] == 1
        assert "last_delta_spec" in ts
        assert "last_delta_kill" in ts

    def test_halt_reason_in_summary(self):
        state = init_from_targets([{"function_key": "f"}])
        state.targets["f"].status = "halted"
        state.targets["f"].halt_reason = "max_iterations"
        s = summarize(state)
        assert s["targets"][0]["halt_reason"] == "max_iterations"

    def test_ready_to_apply_in_summary(self):
        state = init_from_targets([{"function_key": "f"}])
        state.targets["f"].status = "converged"
        s = summarize(state)
        assert s["ready_to_apply"] is True

    def test_empty_state_summary(self):
        state = init_from_targets([])
        s = summarize(state)
        assert s["total_targets"] == 0
        assert s["targets"] == []
        assert s["ready_to_apply"] is False
