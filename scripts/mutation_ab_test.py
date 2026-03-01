#!/usr/bin/env python3
"""A/B Test execution script for Mutation Telemetry filtering.

Runs a baseline (unfiltered) mutation sweep against a set of files, then
runs a filtered (Monty Hall) sweep, comparing runtime reduction and score
degradation against theoretical targets defined in `lintgate.mutation.telemetry`.

Usage:
  .venv/bin/python scripts/mutation_ab_test.py <file1.py> <file2.py> ...
"""

import argparse
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

from lintgate.mutation.engine import MutationEngine
from lintgate.mutation.policy import MutationTelemetry, RuntimeBudget
from lintgate.mutation.state import MutationStateManager
from lintgate.mutation.telemetry import (
    TelemetryTargets,
    evaluate_telemetry_against_targets,
)


def compute_metrics(states):
    """Compute score and other high-level stats from a list of FunctionMutationState."""
    total_mutants = sum(s.total for s in states)
    killed = sum(s.killed for s in states)
    score = (killed / total_mutants * 100.0) if total_mutants > 0 else 100.0
    return {"score": score, "total_killed": killed, "total_mutants": total_mutants}


def main():
    parser = argparse.ArgumentParser(
        description="Run A/B Test for Monty Hall filtering"
    )
    parser.add_argument("files", nargs="+", help="Python files to mutate")
    args = parser.parse_args()

    # We use a temporary state file so we don't pollute the real cache
    state_path = Path(".mutation_ab_state.json")
    if state_path.exists():
        state_path.unlink()

    budget = RuntimeBudget(max_workers=4)  # Use reasonable workers
    files = [os.path.abspath(f) for f in args.files]

    print(f"=== Running Baseline (Unfiltered) on {len(files)} files ===")
    state_manager = MutationStateManager(state_path)
    engine = MutationEngine(state_manager, budget)
    baseline_telemetry = MutationTelemetry("baseline_run")

    baseline_start = time.perf_counter()
    # Baseline: Force relevant_categories=None to disable pre-execution filtering
    original_execute = engine._execute_mutmut

    def _baseline_exec(
        paths, depth, test_filter, relevant_categories=None, telemetry=None
    ):
        return original_execute(
            paths, depth, test_filter, relevant_categories=None, telemetry=telemetry
        )

    with patch.object(engine, "_execute_mutmut", side_effect=_baseline_exec):
        baseline_states = engine.run_inline_sampling(files, baseline_telemetry)
    baseline_end = time.perf_counter()
    baseline_telemetry.finish()

    baseline_stats = compute_metrics(baseline_states)
    baseline_stats["total_time_s"] = baseline_end - baseline_start

    print(
        f"Baseline: {baseline_stats['total_time_s']:.2f}s, Score: {baseline_stats['score']:.1f}% "
        f"({baseline_stats['total_killed']}/{baseline_stats['total_mutants']})"
    )

    # Clear state for the second run
    state_path.unlink()

    print(f"\n=== Running Filtered (Monty Hall) on {len(files)} files ===")
    state_manager = MutationStateManager(state_path)
    engine = MutationEngine(state_manager, budget)
    filtered_telemetry = MutationTelemetry("filtered_run")

    # Filtered: Run normally, using the `_compute_relevant_categories` and LibCST filtering
    filtered_start = time.perf_counter()
    filtered_states = engine.run_inline_sampling(files, filtered_telemetry)
    filtered_end = time.perf_counter()
    filtered_telemetry.finish()

    filtered_stats = compute_metrics(filtered_states)
    filtered_stats["total_time_s"] = filtered_end - filtered_start

    print(
        f"Filtered: {filtered_stats['total_time_s']:.2f}s, Score: {filtered_stats['score']:.1f}% "
        f"({filtered_stats['total_killed']}/{filtered_stats['total_mutants']})"
    )
    print(
        f"Mutants execution skipped fully by policy: {filtered_telemetry.mutants_skipped_policy}"
    )

    print("\n=== Evaluating Against Targets ===")
    targets = TelemetryTargets()
    evaluation = evaluate_telemetry_against_targets(
        baseline_stats, filtered_telemetry, filtered_stats, targets
    )

    print(json.dumps(evaluation, indent=2))

    report_path = Path("mutation_ab_report.json")
    report_path.write_text(
        json.dumps(
            {
                "baseline": baseline_stats,
                "filtered": filtered_stats,
                "telemetry": {
                    "mutants_executed": filtered_telemetry.mutants_executed,
                    "mutants_skipped_policy": filtered_telemetry.mutants_skipped_policy,
                },
                "evaluation": evaluation,
            },
            indent=2,
        )
    )

    print(f"\nSaved report artifact to {report_path}")

    if state_path.exists():
        state_path.unlink()


if __name__ == "__main__":
    main()
