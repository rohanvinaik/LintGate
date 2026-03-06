#!/usr/bin/env python3
"""Generate a calibration report from current mutation state.

This script reads all persistent mutation state across the repository,
calculates the current calibrated thresholds, and generates a markdown
report in docs/mutation/calibration_report.md.
"""

import os
from pathlib import Path

from lintgate.mutation.policy import CalibratedPolicy
from lintgate.mutation.state import MutationStateManager
from lintgate.state import MUTATION_CACHE_DIR


def main():
    state_path = os.path.join(MUTATION_CACHE_DIR, "state.json")
    if not os.path.exists(state_path):
        print("No mutation state found. Run mutations first.")
        return

    state_manager = MutationStateManager(state_path)
    policy = CalibratedPolicy()

    all_states = state_manager.state
    valid_states = [s for s in all_states.values() if s.total > 0]

    if not valid_states:
        print("No valid mutation runs with mutants generated found.")
        return

    avg_survival = sum(s.survival_rate for s in valid_states) / len(valid_states)

    # Simulate thresholds for an average function to get the current repository baseline
    dummy_state = valid_states[0]  # Just an object to pass to get_thresholds
    warning_thresh, blocking_thresh = policy.get_thresholds(dummy_state, all_states)

    report_lines = [
        "# Mutation Calibration Report",
        "",
        f"**Repository Average Survival Rate:** {avg_survival:.1%}",
        f"**Functions Profiled:** {len(valid_states)}",
        "",
        "## Active Calibrated Thresholds",
        f"- **Warning (MUT001):** {warning_thresh:.1%} (Functions exceeding this rate will trigger warnings)",
        f"- **Blocking (MUT002):** {blocking_thresh:.1%} (Functions exceeding this rate on exhaustive profile will block)",
        "",
        "## Entanglement Statistics",
        "Functions requiring decomposition (MUTCH007 candidate thresholds):",
    ]

    # Check for entanglement
    highly_entangled = 0
    for state in valid_states:
        surviving_cats = [
            c for c, count in state.survived_by_category.items() if count > 0
        ]
        if state.survival_rate >= 0.50 and len(surviving_cats) >= 3:
            highly_entangled += 1

    report_lines.extend(
        [
            f"- **Highly Entangled Functions:** {highly_entangled} (Survival >= 50% across 3+ operators)",
            f"- **Healthy Functions:** {len(valid_states) - highly_entangled}",
            "",
        ]
    )

    report_lines.append("## Calibration Mechanism")
    report_lines.append(
        "Thresholds are dynamically adjusted based on the `avg_survival` rate of the repository, "
        "ensuring that the enforcement floor naturally raises as overall test quality improves."
    )

    out_dir = Path("docs/mutation")
    out_dir.mkdir(parents=True, exist_ok=True)
    report_file = out_dir / "calibration_report.md"

    report_file.write_text("\n".join(report_lines))
    print(f"Calibration report generated at: {report_file}")


if __name__ == "__main__":
    main()
