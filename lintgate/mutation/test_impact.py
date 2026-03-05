"""Test-impact mapping from coverage.py SQLite databases.

Parses .coverage SQLite DBs (CoverageDB v7+) to build a mapping from
source files to the test node IDs that exercise them. This enables
signal-first mutation testing: instead of running all tests against
every mutant, run only the tests that actually cover the mutated code.

Usage:
    mapping = load_test_impact_mapping("/path/to/project")
    if mapping is not None:
        tests = get_tests_for_file(mapping, "lintgate/foo.py")
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def load_test_impact_mapping(project_root: str) -> dict[str, list[str]] | None:
    """Load file -> covering tests from .coverage SQLite DB.

    Returns None if no coverage data exists (graceful degradation).
    Coverage data is produced by: coverage run -m pytest

    Returns:
        Dict mapping file paths (relative to project_root) to lists of
        test node IDs that cover code in that file. Returns None if
        no .coverage file exists or if it can't be parsed.
    """
    coverage_path = Path(project_root) / ".coverage"
    if not coverage_path.is_file():
        return None

    try:
        return _parse_coverage_db(coverage_path, project_root)
    except (sqlite3.DatabaseError, sqlite3.OperationalError):
        return None


def get_tests_for_file(
    mapping: dict[str, list[str]],
    file_path: str,
) -> list[str]:
    """Get covering tests for a specific file.

    Args:
        mapping: Output from load_test_impact_mapping()
        file_path: File path to look up (relative to project root)

    Returns:
        List of test node IDs, or empty list if no coverage data.
    """
    return mapping.get(file_path, [])


def _parse_coverage_db(
    coverage_path: Path,
    project_root: str,
) -> dict[str, list[str]] | None:
    """Parse coverage.py SQLite DB and return file-to-tests mapping."""
    root = Path(project_root).resolve()

    conn = sqlite3.connect(str(coverage_path))
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT f.path, c.context
            FROM line_bits lb
            JOIN file f ON lb.file_id = f.id
            JOIN context c ON lb.context_id = c.id
            WHERE c.context != ''
            """
        ).fetchall()
    except sqlite3.OperationalError:
        # Missing tables — old schema or not a coverage DB
        conn.close()
        return None
    finally:
        conn.close()

    if not rows:
        return None

    mapping: dict[str, list[str]] = {}
    for file_path, context in rows:
        rel = _to_relative(file_path, root)
        if rel is None:
            continue
        # Skip test files as keys — we only want source files
        if _is_test_file(rel):
            continue
        test_id = _strip_context_suffix(context)
        if not test_id:
            continue
        mapping.setdefault(rel, []).append(test_id)

    # Deduplicate test lists
    for key in mapping:
        mapping[key] = sorted(set(mapping[key]))

    return mapping if mapping else None


def _to_relative(file_path: str, root: Path) -> str | None:
    """Convert a file path to a path relative to root, or None if outside."""
    try:
        p = Path(file_path).resolve()
        return str(p.relative_to(root))
    except (ValueError, OSError):
        return None


def _is_test_file(rel_path: str) -> bool:
    """Check if a relative path looks like a test file."""
    basename = os.path.basename(rel_path)
    return basename.startswith("test_") or basename.endswith("_test.py")


def _strip_context_suffix(context: str) -> str:
    """Strip |run or |setup suffix from coverage context strings."""
    for suffix in ("|run", "|setup"):
        if context.endswith(suffix):
            return context[: -len(suffix)]
    return context
