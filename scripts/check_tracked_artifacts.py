#!/usr/bin/env python3
"""Check for tracked generated/cache/scratch artifacts.

This script scans git tracked files against a blocked pattern list to prevent
generated/cache/scratch artifacts from being committed. It helps prevent the
ship friction from tracked artifact churn identified in #96.

Usage:
    python scripts/check_tracked_artifacts.py [--enforce] <repo_path>

Exit codes:
    0 - No blocked tracked artifacts found
    1 - Blocked tracked artifacts detected (when --enforce is used)
    2 - Invalid arguments or git error
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Blocked patterns for generated/cache/scratch artifacts.
# These patterns match files that should NOT be tracked in git.
# Based on .gitignore patterns that are commonly accidentally committed.
BLOCKED_PATTERNS = [
    # Hypothesis auto-generated directory
    (r"^\.hypothesis/", "Hypothesis auto-generated directory"),
    # Coverage artifacts
    (r"^coverage\.xml$", "Coverage XML report"),
    (r"^coverage\.json$", "Coverage JSON report"),
    (r"^\.coverage\.", "Coverage cache files"),
    (r"^htmlcov/", "Coverage HTML report directory"),
    (r"^pytest-results\.xml$", "Pytest JUnit XML results"),
    # Quality tool caches
    (r"^\.qlty/logs/", "Qlty logs directory"),
    (r"^\.qlty/out/", "Qlty output directory"),
    (r"^\.qlty/plugin_cachedir/", "Qlty plugin cache"),
    (r"^\.qlty/results/", "Qlty results directory"),
    # Mutmut working directory
    (r"^mutants/", "Mutmut working directory"),
    # Local MCP/ControlPlane scratch outputs
    (r"^cp_.*\.json$", "ControlPlane scratch output"),
    (r"^details_.*\.json$", "Details scratch output"),
    (r"^algebra_.*\.json$", "Algebra scratch output"),
    (r"^getting_started\.json$", "Getting started artifact"),
    (r"^install_report\.json$", "Install report artifact"),
    (r"^issue_.*\.txt$", "Issue scratch file"),
    (r"^issues_dump\.txt$", "Issues dump artifact"),
    (r"^lint_status\.json$", "Lint status artifact"),
    (r"^list\.txt$", "List artifact"),
    (r"^new$", "New files artifact"),
    (r"^structure_details\.json$", "Structure details artifact"),
    (r"^telemetry_strength\.json$", "Telemetry strength artifact"),
    (r"^test_assertions_reporter\.json$", "Test assertions reporter artifact"),
    (r"^test_strength_.*\.json$", "Test strength artifact"),
    (r"^call_mcp\.py$", "MCP call scratch script"),
    (r"^check_findings\.py$", "Check findings scratch script"),
    # Mutation scratch artifacts
    (r"^mutation_ab_report\.json$", "Mutation AB report"),
    (r"^\.mutation_ab_state\.json$", "Mutation AB state"),
    (r"\.py\.meta$", "Python metadata file"),
    # Additional common scratch files
    (r"^radon_.*\.json$", "Radon output artifact"),
    (r"^bulk_test_gen\.py$", "Bulk test generator"),
]


def get_tracked_files(repo_path: Path) -> list[str]:
    """Get list of all tracked files in the repository.

    Returns:
        List of file paths relative to repo root.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip().split("\n")
    except subprocess.CalledProcessError as e:
        print(f"Error running git ls-files: {e}", file=sys.stderr)
        return []
    except FileNotFoundError:
        print("Error: git not found in PATH", file=sys.stderr)
        return []


def check_tracked_artifacts(repo_path: Path) -> list[tuple[str, str]]:
    """Check for tracked generated/cache/scratch artifacts.

    Returns:
        List of (file_path, reason) tuples for violating files.
    """
    tracked_files = get_tracked_files(repo_path)
    violations = []

    for file_path in tracked_files:
        if not file_path:  # Skip empty lines
            continue

        for pattern, reason in BLOCKED_PATTERNS:
            if re.match(pattern, file_path):
                violations.append((file_path, reason))
                break

    return violations


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Check for tracked generated/cache/scratch artifacts"
    )
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="Exit non-zero if blocked artifacts are found",
    )
    parser.add_argument(
        "repo_path",
        nargs="?",
        default=".",
        help="Path to repository (default: current directory)",
    )
    args = parser.parse_args()

    repo_path = Path(args.repo_path).resolve()

    if not repo_path.exists():
        print(f"Error: Path does not exist: {repo_path}", file=sys.stderr)
        return 2

    if not (repo_path / ".git").exists():
        print(f"Error: Not a git repository: {repo_path}", file=sys.stderr)
        return 2

    violations = check_tracked_artifacts(repo_path)

    if not violations:
        print("PASS: No blocked tracked artifacts found")
        return 0

    # Print violations
    print("FAIL: Blocked tracked artifacts detected:")
    print("-" * 60)

    for file_path, reason in violations:
        print(f"  {file_path}")
        print(f"    → {reason}")

    print("-" * 60)
    print("\nTo fix:")
    print("  1. Add the pattern to .gitignore if not already there")
    print("  2. Remove from git tracking: git rm --cached <file>")
    print("  3. Commit the .gitignore change and the file removal")

    if args.enforce:
        return 1

    # Without --enforce, just warn but don't fail
    return 0


if __name__ == "__main__":
    sys.exit(main())
