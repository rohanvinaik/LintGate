"""Test channel — supervision for test coverage and test health.

Checks:
1. Missing tests: source files without corresponding test files
2. Impact detection: find which test files are affected by the changed source
3. Test execution: run impacted tests and report failures
4. Skeleton proposals: suggest test stubs via archetype matching

This channel is ADVISORY by default (blocking_capable=False).
Test failures produce warnings, not blocking errors. The agent
should address them but can continue.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lintgate.controlplane.channel import Channel
from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    RepairAction,
    SupervisionEvent,
)
from lintgate.types import LintIssue


# ── Test runner result ───────────────────────────────────────────────────


@dataclass
class TestRunResult:
    """Result from running pytest on impacted test files."""

    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    failures: list[TestFailure] = field(default_factory=list)
    stdout: str = ""
    timed_out: bool = False


@dataclass
class TestFailure:
    """A single test failure from pytest output."""

    test_name: str = ""
    file: str | None = None
    line: int | None = None
    message: str = ""


# ── Test Channel ─────────────────────────────────────────────────────────


class TestChannel:
    """Supervision channel for test coverage and test health.

    Advisory by default — test failures produce warnings, not blocking errors.
    """

    name = "tests"
    timeout_ms = 10000  # Tests can be slow
    blocking_capable = False  # Advisory by default

    def should_run(self, event: SupervisionEvent, config: ControlPlaneConfig) -> bool:
        """Run on logic, structural, and test changes to Python files."""
        if event.surface == "mcp":
            return True
        classification = event.change_classification
        if classification is None:
            return False
        return classification.change_kind in ("logic", "structural", "test")

    def execute(self, event: SupervisionEvent, config: ControlPlaneConfig) -> ChannelResult:
        """Execute test supervision checks."""
        start = time.perf_counter()
        findings: list[LintIssue] = []
        repairs: list[RepairAction] = []

        project_root = event.project_root
        changed_files = event.files_changed

        # Step 1: Find impacted test files
        impacted_tests = find_impacted_tests(changed_files, project_root)

        # Step 2: Check for missing tests
        for src_file in changed_files:
            if _is_source_file(src_file, project_root) and not _has_test(src_file, project_root):
                findings.append(LintIssue(
                    linter="test_channel",
                    kind="missing_test",
                    message=f"No test file found for {os.path.basename(src_file)}",
                    file=src_file,
                    severity="informational",
                ))
                # Propose skeleton repair
                try:
                    from lintgate.controlplane.test_archetype_selector import select_archetypes
                    archetypes = select_archetypes(src_file, project_root)
                    if archetypes:
                        repairs.append(RepairAction(
                            channel="tests",
                            kind="create_test_skeleton",
                            summary=(
                                f"Create test skeleton for {os.path.basename(src_file)} "
                                f"({archetypes[0].name})"
                            ),
                            payload={
                                "source_file": src_file,
                                "archetypes": [a.name for a in archetypes],
                            },
                            safe=True,
                        ))
                except Exception:
                    pass  # Archetype selection failure is non-fatal

        # Step 3: Run impacted tests (if any exist)
        if impacted_tests:
            remaining_ms = self.timeout_ms - int((time.perf_counter() - start) * 1000)
            remaining_ms = max(remaining_ms, 2000)

            test_result = run_tests(impacted_tests, project_root, timeout_ms=remaining_ms)

            if test_result.timed_out:
                findings.append(LintIssue(
                    linter="test_channel",
                    kind="test_timeout",
                    message=f"Test execution timed out ({remaining_ms}ms budget)",
                    severity="warning",
                ))

            for failure in test_result.failures:
                findings.append(LintIssue(
                    linter="test_channel",
                    kind="test_failure",
                    message=failure.message,
                    file=failure.file,
                    line=failure.line,
                    severity="warning",  # Advisory by default
                ))

        elapsed_ms = (time.perf_counter() - start) * 1000
        status = "fail" if findings else "pass"
        severity = "warning" if any(f.severity == "warning" for f in findings) else (
            "informational" if findings else "none"
        )

        return ChannelResult(
            channel=self.name,
            status=status,
            severity=severity,
            findings=findings,
            repairs=repairs,
            metrics={
                "impacted_tests_found": len(impacted_tests),
                "missing_test_count": sum(1 for f in findings if f.kind == "missing_test"),
                "test_failure_count": sum(1 for f in findings if f.kind == "test_failure"),
            },
            duration_ms=elapsed_ms,
        )


# ── Impact Detection ─────────────────────────────────────────────────────


def find_impacted_tests(changed_files: list[str], project_root: str) -> list[str]:
    """Find test files impacted by the changed source files.

    For each changed source file `src/foo/bar.py`, looks for:
    - tests/test_bar.py
    - tests/foo/test_bar.py
    - tests/test_foo_bar.py
    - test_bar.py (in same directory)
    """
    root = Path(project_root)
    impacted: list[str] = []
    seen: set[str] = set()

    for src_file in changed_files:
        src_path = Path(src_file)
        basename = src_path.stem  # e.g., "bar" from "bar.py"

        # Skip test files themselves and non-Python files
        if not src_path.suffix == ".py":
            continue
        if basename.startswith("test_") or src_path.name == "conftest.py":
            # Changed file IS a test file — include it directly
            if src_path.exists() and str(src_path) not in seen:
                impacted.append(str(src_path))
                seen.add(str(src_path))
            continue

        # Search patterns for corresponding test files
        test_name = f"test_{basename}.py"

        # Search in common test directories
        search_dirs = [
            root / "tests",
            root / "test",
            src_path.parent,  # Same directory
            src_path.parent / "tests",
        ]

        # Also try to mirror the package structure
        # e.g., src/foo/bar.py → tests/foo/test_bar.py
        try:
            rel = src_path.relative_to(root)
            if len(rel.parts) > 1:
                # Build mirrored path: tests/<package>/test_<module>.py
                package_parts = rel.parts[:-1]  # Everything except filename
                # Skip common src directories
                if package_parts[0] in ("src", "lib", "lintgate"):
                    package_parts = package_parts[1:]
                if package_parts:
                    search_dirs.append(root / "tests" / Path(*package_parts))
        except ValueError:
            pass

        for search_dir in search_dirs:
            candidate = search_dir / test_name
            if candidate.exists() and str(candidate) not in seen:
                impacted.append(str(candidate))
                seen.add(str(candidate))

        # Also check for underscore-joined names: test_foo_bar.py
        try:
            rel = src_path.relative_to(root)
            joined_name = "test_" + "_".join(
                p for p in rel.with_suffix("").parts
                if p not in ("src", "lib", "__init__")
            ) + ".py"
            for test_dir in [root / "tests", root / "test"]:
                candidate = test_dir / joined_name
                if candidate.exists() and str(candidate) not in seen:
                    impacted.append(str(candidate))
                    seen.add(str(candidate))
        except ValueError:
            pass

    return sorted(impacted)


# ── Test Runner ──────────────────────────────────────────────────────────


def run_tests(
    test_files: list[str],
    project_root: str,
    timeout_ms: int = 10000,
) -> TestRunResult:
    """Run pytest on specified test files and parse results.

    Uses `python -m pytest <files> -q --tb=line --no-header` with
    subprocess timeout protection.
    """
    if not test_files:
        return TestRunResult()

    cmd = [
        "python", "-m", "pytest",
        *test_files,
        "-q", "--tb=line", "--no-header",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_ms / 1000.0,
            cwd=project_root,
        )
        return _parse_pytest_output(result.stdout, result.stderr, result.returncode)
    except subprocess.TimeoutExpired:
        return TestRunResult(timed_out=True)
    except (OSError, subprocess.SubprocessError):
        return TestRunResult()


def _parse_pytest_output(stdout: str, stderr: str, returncode: int) -> TestRunResult:
    """Parse pytest -q --tb=line output into structured result."""
    result = TestRunResult(stdout=stdout)

    # Parse the summary line: "X passed, Y failed, Z error"
    summary_match = re.search(
        r"(\d+) passed",
        stdout + stderr,
    )
    if summary_match:
        result.passed = int(summary_match.group(1))

    failed_match = re.search(r"(\d+) failed", stdout + stderr)
    if failed_match:
        result.failed = int(failed_match.group(1))

    error_match = re.search(r"(\d+) error", stdout + stderr)
    if error_match:
        result.errors = int(error_match.group(1))

    skipped_match = re.search(r"(\d+) skipped", stdout + stderr)
    if skipped_match:
        result.skipped = int(skipped_match.group(1))

    # Parse failure lines (--tb=line format):
    # FAILED tests/test_foo.py::test_bar - AssertionError: ...
    for line in stdout.splitlines():
        fail_match = re.match(
            r"FAILED\s+([\w/\\.]+)::(\w+)\s*-?\s*(.*)",
            line.strip(),
        )
        if fail_match:
            file_path = fail_match.group(1)
            test_name = fail_match.group(2)
            message = fail_match.group(3).strip() or f"Test {test_name} failed"
            result.failures.append(TestFailure(
                test_name=test_name,
                file=file_path,
                message=message,
            ))

    return result


# ── Helpers ──────────────────────────────────────────────────────────────


def _is_source_file(filepath: str, project_root: str) -> bool:
    """Check if a file is a Python source file (not test, not config)."""
    p = Path(filepath)
    if p.suffix != ".py":
        return False
    if p.stem.startswith("test_") or p.name == "conftest.py":
        return False
    if p.stem.startswith("__"):
        return False
    # Exclude setup.py, conftest.py, etc.
    if p.stem in ("setup", "conftest"):
        return False
    return True


def _has_test(source_file: str, project_root: str) -> bool:
    """Check if a source file has a corresponding test file."""
    tests = find_impacted_tests([source_file], project_root)
    return len(tests) > 0
