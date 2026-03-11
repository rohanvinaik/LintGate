"""PR4: Trajectory analysis tests.

Validates teaching-set upper bounds, tail onset detection,
phase classification, and trajectory summaries.
"""

from __future__ import annotations

from lintgate.specification.greedy_convergence import ConvergenceResult, ConvergenceStep
from lintgate.specification.trajectory_analysis import (
    TrajectoryResult,
    analyze_trajectory,
)


def _make_convergence(
    function_key: str = "test.py::f",
    steps: list[ConvergenceStep] | None = None,
    redundant_tests: list[str] | None = None,
    convergence_efficiency: float = 0.0,
    is_fully_specified: bool = False,
) -> ConvergenceResult:
    return ConvergenceResult(
        function_key=function_key,
        sigma=10,
        steps=steps or [],
        redundant_tests=redundant_tests or [],
        convergence_efficiency=convergence_efficiency,
        is_fully_specified=is_fully_specified,
    )


# ── Phase classification ─────────────────────────────────────────


class TestPhaseClassification:
    def test_complete_when_fully_specified(self):
        conv = _make_convergence(is_fully_specified=True)
        traj = analyze_trajectory(conv, total_mutants=10, survival_rate=0.0)
        assert traj.phase == "complete"

    def test_complete_when_zero_survival(self):
        conv = _make_convergence(is_fully_specified=False)
        traj = analyze_trajectory(conv, total_mutants=10, survival_rate=0.005)
        assert traj.phase == "complete"

    def test_bulk_when_high_survival(self):
        conv = _make_convergence()
        traj = analyze_trajectory(conv, total_mutants=10, survival_rate=0.8)
        assert traj.phase == "bulk"

    def test_transition_when_moderate_survival(self):
        conv = _make_convergence()
        traj = analyze_trajectory(conv, total_mutants=10, survival_rate=0.2)
        assert traj.phase == "transition"


# ── Teaching-set upper bound ─────────────────────────────────────


class TestTeachingSetUpperBound:
    def test_counts_non_redundant_steps(self):
        steps = [
            ConvergenceStep("t1", new_kills=3, delta_spec=0.3, cumulative_spec=0.3, meets_bound=True),
            ConvergenceStep("t2", new_kills=2, delta_spec=0.2, cumulative_spec=0.5, meets_bound=True),
            ConvergenceStep("t3", new_kills=0, delta_spec=0.0, cumulative_spec=0.5, meets_bound=True),
        ]
        conv = _make_convergence(steps=steps, redundant_tests=["t3"])
        traj = analyze_trajectory(conv, total_mutants=10, survival_rate=0.5)
        assert traj.teaching_set_upper_bound == 2

    def test_zero_when_no_steps(self):
        conv = _make_convergence()
        traj = analyze_trajectory(conv, total_mutants=10, survival_rate=1.0)
        assert traj.teaching_set_upper_bound == 0


# ── Tail onset detection ─────────────────────────────────────────


class TestTailOnset:
    def test_detects_tail_when_delta_drops(self):
        # sigma_ub = 10, threshold = 0.1
        steps = [
            ConvergenceStep("t1", new_kills=5, delta_spec=0.5, cumulative_spec=0.5, meets_bound=True),
            ConvergenceStep("t2", new_kills=1, delta_spec=0.05, cumulative_spec=0.55, meets_bound=False),
        ]
        conv = _make_convergence(steps=steps)
        traj = analyze_trajectory(conv, total_mutants=10, survival_rate=0.45)
        assert traj.tail_onset_step == 1

    def test_no_tail_when_all_above_threshold(self):
        steps = [
            ConvergenceStep("t1", new_kills=5, delta_spec=0.5, cumulative_spec=0.5, meets_bound=True),
            ConvergenceStep("t2", new_kills=3, delta_spec=0.3, cumulative_spec=0.8, meets_bound=True),
        ]
        conv = _make_convergence(steps=steps)
        traj = analyze_trajectory(conv, total_mutants=10, survival_rate=0.2)
        assert traj.tail_onset_step is None


# ── Trajectory summary ───────────────────────────────────────────


class TestTrajectorySummary:
    def test_complete_summary(self):
        conv = _make_convergence(is_fully_specified=True)
        traj = analyze_trajectory(conv, total_mutants=10, survival_rate=0.0)
        assert "complete" in traj.trajectory_summary.lower()
        assert "all mutants killed" in traj.trajectory_summary.lower()

    def test_bulk_summary_mentions_gaps(self):
        conv = _make_convergence()
        traj = analyze_trajectory(conv, total_mutants=10, survival_rate=0.8)
        assert "80%" in traj.trajectory_summary
        assert "gaps" in traj.trajectory_summary.lower()

    def test_redundant_count_in_summary(self):
        conv = _make_convergence(redundant_tests=["t3", "t4"])
        traj = analyze_trajectory(conv, total_mutants=10, survival_rate=0.5)
        assert "2 redundant" in traj.trajectory_summary


# ── to_dict ──────────────────────────────────────────────────────


class TestTrajectoryToDict:
    def test_basic_fields(self):
        result = TrajectoryResult(
            function_key="f", sigma_upper_bound=10,
            teaching_set_upper_bound=3, phase="bulk",
        )
        d = result.to_dict()
        assert d["sigma_upper_bound"] == 10
        assert d["teaching_set_upper_bound"] == 3
        assert d["phase"] == "bulk"

    def test_tail_onset_only_when_set(self):
        result = TrajectoryResult()
        d = result.to_dict()
        assert "tail_onset_step" not in d

        result.tail_onset_step = 5
        d = result.to_dict()
        assert d["tail_onset_step"] == 5


# ── Integration with post-profiling analysis ─────────────────────


class TestPostProfilingTrajectory:
    def test_trajectory_in_analysis_output(self):
        from mcp_tools._mutation_impl import run_post_profiling_analysis

        results = [{
            "function_key": "test.py::f",
            "coverage_depth": "profiled",
            "categories_tested": 1,
            "total_mutants": 1,
            "total_killed": 1,
            "total_survived": 0,
            "survival_rate": 0.0,
            "per_category": [{
                "category": "VALUE", "total": 1, "killed": 1,
                "survived": 0, "killed_by_assertion": 1, "killed_by_crash": 0,
            }],
            "kill_matrix": {"VALUE_0: replace constant": ["test_f"]},
            "is_pure": False,
            "parameter_count": 1,
        }]
        analysis = run_post_profiling_analysis(results, {})
        assert "trajectory" in analysis
        assert len(analysis["trajectory"]) == 1
        traj = analysis["trajectory"][0]
        assert "phase" in traj
        assert "sigma_upper_bound" in traj
