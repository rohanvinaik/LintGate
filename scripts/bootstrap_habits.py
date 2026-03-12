"""Bootstrap habit state from historical Claude Code session data.

Usage:
    python scripts/bootstrap_habits.py [--sessions-root ~/.claude/projects] [--project-filter REGEX]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap habit state from historical session data."
    )
    parser.add_argument(
        "--sessions-root",
        default=str(Path.home() / ".claude" / "projects"),
        help="Root directory for Claude Code sessions",
    )
    parser.add_argument(
        "--project-filter",
        default=None,
        help="Regex to filter project paths",
    )
    args = parser.parse_args()

    # Ensure mneme is importable
    try:
        from mneme.ingest.session_parser import iter_sessions
    except ImportError:
        print("Error: mneme package not found. Install it or add to PYTHONPATH.", file=sys.stderr)
        sys.exit(1)

    from collections import defaultdict

    from lintgate._habit_bootstrap import HabitBootstrapper

    sessions_root = Path(args.sessions_root)
    project_filter = re.compile(args.project_filter) if args.project_filter else None

    # Group sessions by project
    projects: dict[str, list] = defaultdict(list)
    for session in iter_sessions(sessions_root):
        key = session.project_path or session.cwd or "unknown"
        if project_filter and not project_filter.search(key):
            continue
        projects[key].append(session)

    print(f"Found {len(projects)} projects with {sum(len(v) for v in projects.values())} sessions")

    bootstrapper = HabitBootstrapper()
    results = []

    for project_path, sessions in sorted(projects.items()):
        summary = bootstrapper.bootstrap_project(sessions)
        results.append(summary)
        print(
            f"  {project_path}: "
            f"{summary.get('sessions_count', 0)} sessions, "
            f"{summary.get('total_actions', 0)} actions, "
            f"score={summary.get('habit_score', 0):.3f}, "
            f"errors={summary.get('error_signatures', 0)}"
        )

    print(f"\nBootstrapped {len(results)} projects.")


if __name__ == "__main__":
    main()
