"""Test channel data types — pure value containers.

Separated from test_channel.py to stay under the class-per-module limit.
Re-exported from test_channel for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TestFailure:
    """A single test failure from pytest output."""

    test_name: str = ""
    file: str | None = None
    line: int | None = None
    message: str = ""


@dataclass
class TestRunResult:
    """Result from running pytest on impacted test files."""

    __test__ = False  # Not a pytest test class

    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    failures: list[TestFailure] = field(default_factory=list)
    stdout: str = ""
    timed_out: bool = False
    coverage_pct: float | None = None  # Set when measure_coverage=True
    coverage_json_path: str | None = None  # Path to coverage.json when measured
    coverage_json_ephemeral: bool = False  # True when path should be cleaned up by caller
