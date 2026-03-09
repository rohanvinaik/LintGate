"""Test runner — pytest execution and output parsing.

Extracted from test_channel.py to keep the main channel file under 400 lines.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from lintgate.channels._test_types import TestFailure, TestRunResult


def run_tests(
    test_files: list[str],
    project_root: str,
    timeout_ms: int = 10000,
    measure_coverage: bool = False,
    source_packages: list[str] | None = None,
) -> TestRunResult:
    """Run pytest on specified test files and parse results.

    Uses `python -m pytest <files> -q --tb=line --no-header` with
    subprocess timeout protection.

    Args:
        test_files: Test files to run.
        project_root: Project root directory.
        timeout_ms: Timeout in milliseconds.
        measure_coverage: If True, add --cov flags and parse coverage.
            Only used in MCP/CI mode, never on hook path.
        source_packages: Packages to measure coverage for (e.g. ["lintgate", "mcp_tools"]).
    """
    if not test_files:
        return TestRunResult()

    cmd = [
        "python",
        "-m",
        "pytest",
        *test_files,
        "-q",
        "--tb=line",
        "--no-header",
    ]

    coverage_xml_path: str | None = None
    coverage_json_path: str | None = None
    coverage_tmpdir: Any | None = None
    if measure_coverage:
        import tempfile

        coverage_tmpdir = tempfile.TemporaryDirectory(prefix="lintgate_cov_")
        coverage_xml_path = os.path.join(coverage_tmpdir.name, "coverage.xml")
        coverage_json_path = os.path.join(coverage_tmpdir.name, "coverage.json")
        pkgs = source_packages or ["lintgate", "mcp_tools"]
        for pkg in pkgs:
            cmd.extend([f"--cov={pkg}"])
        cmd.extend(
            [
                f"--cov-report=xml:{coverage_xml_path}",
                f"--cov-report=json:{coverage_json_path}",
                "--cov-report=term:skip-covered",
                "--cov-branch",
            ]
        )

    # Isolate subprocess coverage data from any parent pytest-cov process.
    # Without this, --cov-branch writes .coverage with branch data into the
    # project root, which collides with a parent's statement-only .coverage
    # during the combine step (coverage.exceptions.DataError).
    sub_env: dict[str, str] | None = None
    if measure_coverage and coverage_tmpdir is not None:
        sub_env = os.environ.copy()
        sub_env["COVERAGE_FILE"] = os.path.join(coverage_tmpdir.name, ".coverage")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_ms / 1000.0,
            cwd=project_root,
            env=sub_env,
        )
        parsed = _parse_pytest_output(result.stdout, result.stderr, result.returncode)

        # Parse coverage if measured
        if measure_coverage and coverage_xml_path:
            parsed.coverage_pct = _parse_coverage(
                coverage_xml_path,
                f"{result.stdout}\n{result.stderr}",
            )
        if measure_coverage and coverage_json_path and os.path.isfile(coverage_json_path):
            # Persist JSON beyond TemporaryDirectory lifetime so symbol gate can read it.
            import shutil
            import tempfile

            fd, copied_path = tempfile.mkstemp(
                prefix="lintgate_cov_json_",
                suffix=".json",
            )
            os.close(fd)
            shutil.copyfile(coverage_json_path, copied_path)
            parsed.coverage_json_path = copied_path
            parsed.coverage_json_ephemeral = True

        return parsed
    except subprocess.TimeoutExpired:
        return TestRunResult(timed_out=True)
    except (OSError, subprocess.SubprocessError):
        return TestRunResult()
    finally:
        if coverage_tmpdir is not None:
            with contextlib.suppress(Exception):
                coverage_tmpdir.cleanup()


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
            result.failures.append(
                TestFailure(
                    test_name=test_name,
                    file=file_path,
                    message=message,
                )
            )

    return result


def _parse_coverage(coverage_xml_path: str, terminal_output: str) -> float | None:
    """Parse coverage percentage from XML (primary) or terminal output (fallback).

    Primary: Parse the ``line-rate`` attribute from coverage.xml text.
    Example: ``<coverage line-rate="0.795">`` -> 79.5%.
    Fallback: Regex ``TOTAL\\s+\\d+\\s+\\d+\\s+(\\d+)%`` from terminal output.

    Returns None if coverage could not be determined.
    """
    # Primary: read coverage XML as text and extract line-rate.
    # This avoids XML parser attack-surface warnings for CI security scans.
    try:
        xml_text = Path(coverage_xml_path).read_text(encoding="utf-8", errors="ignore")
        line_rate_match = re.search(
            r"""line-rate\s*=\s*["']([0-9]*\.?[0-9]+)["']""",
            xml_text,
        )
        if line_rate_match:
            return round(float(line_rate_match.group(1)) * 100, 1)
    except (OSError, ValueError):
        pass

    # Fallback: terminal regex
    match = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", terminal_output)
    if match:
        return float(match.group(1))

    return None
