#!/usr/bin/env python3
"""CLI wrapper for mutation stats parsing.

Thin entry point used by .github/workflows/mutation.yml.
All logic lives in lintgate.mutation.ci_stats.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter

from lintgate.mutation.ci_stats import load_mutation_hotspots, parse_stats_for_ci


def main():
    stats_path = os.environ.get("STATS_PATH", "mutants/mutmut-cicd-stats.json")
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    rc = parse_stats_for_ci(stats_path, github_output)

    # Only generate triage summaries if the stats run was valid.
    if rc == 0:
        survivors_txt = os.environ.get("SURVIVORS_TXT", "mutants/mutmut-survivors.txt")
        survivors_json = os.environ.get(
            "SURVIVORS_JSON", "mutants/mutmut-survivors.json"
        )

        # Parse text logic in load_mutation_hotspots
        hotspots = load_mutation_hotspots(survivors_txt)

        # Write canonical JSON layout artifact
        with open(survivors_json, "w", encoding="utf-8") as f:
            json.dump(hotspots, f, indent=2)

        # Output survivor triage summary for GH Actions step summary
        if hotspots:
            counts = Counter(h.get("file", "") for h in hotspots if h.get("file"))
            summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null")
            try:
                with open(summary_path, "a", encoding="utf-8") as f:
                    f.write("### 🦠 Top Mutation Survivor Files\n\n")
                    for file_path, count in counts.most_common(10):
                        f.write(f"- **`{file_path}`**: {count} surviving mutants\n")
                    f.write(
                        "\n_Run `lintgate mcp` or check artifact `mutmut-survivors.json` for details._\n"
                    )
            except OSError:
                pass  # Local runs outside actions might not have writable paths

    sys.exit(rc)


if __name__ == "__main__":
    main()
