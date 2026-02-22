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

import contextlib
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from lintgate.controlplane.types import (
    ChannelConfig,
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
    coverage_pct: float | None = None  # Set when measure_coverage=True
    coverage_json_path: str | None = None  # Path to coverage.json when measured


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
                findings.append(
                    LintIssue(
                        linter="test_channel",
                        kind="missing_test",
                        message=f"No test file found for {os.path.basename(src_file)}",
                        file=src_file,
                        severity="informational",
                    )
                )
                # Propose skeleton repair
                try:
                    from lintgate.controlplane.test_archetype_selector import select_archetypes

                    archetypes = select_archetypes(src_file, project_root)
                    if archetypes:
                        repairs.append(
                            RepairAction(
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
                            )
                        )
                except Exception:
                    pass  # Archetype selection failure is non-fatal

        # Step 3: Determine coverage measurement mode
        # Coverage collection is MCP/CI mode ONLY — never on the hook path
        channel_settings = config.channels.get("tests", ChannelConfig()).settings
        raw_coverage_threshold = channel_settings.get("coverage_threshold")
        coverage_threshold: float | None = None
        if raw_coverage_threshold is not None:
            with contextlib.suppress(TypeError, ValueError):
                coverage_threshold = float(raw_coverage_threshold)
        raw_source_packages = channel_settings.get("source_packages")
        source_packages: list[str] | None = None
        if isinstance(raw_source_packages, list):
            source_packages = [str(p).strip() for p in raw_source_packages if str(p).strip()]
        elif isinstance(raw_source_packages, str) and raw_source_packages.strip():
            source_packages = [raw_source_packages.strip()]
        symbol_cov_settings = channel_settings.get("symbol_coverage", {})
        symbol_coverage_enabled = (
            isinstance(symbol_cov_settings, dict)
            and symbol_cov_settings.get("enabled", False)
        )
        measure_coverage = event.surface in ("mcp", "ci") and (
            coverage_threshold is not None or symbol_coverage_enabled
        )

        # Step 4: Run impacted tests (if any exist)
        test_result: TestRunResult | None = None
        if impacted_tests:
            remaining_ms = self.timeout_ms - int((time.perf_counter() - start) * 1000)
            remaining_ms = max(remaining_ms, 2000)

            test_result = run_tests(
                impacted_tests,
                project_root,
                timeout_ms=remaining_ms,
                measure_coverage=measure_coverage,
                source_packages=source_packages,
            )

            if test_result.timed_out:
                findings.append(
                    LintIssue(
                        linter="test_channel",
                        kind="test_timeout",
                        message=f"Test execution timed out ({remaining_ms}ms budget)",
                        severity="warning",
                    )
                )

            for failure in test_result.failures:
                findings.append(
                    LintIssue(
                        linter="test_channel",
                        kind="test_failure",
                        message=failure.message,
                        file=failure.file,
                        line=failure.line,
                        severity="warning",  # Advisory by default
                    )
                )

            # Step 5: Check coverage threshold
            if (
                measure_coverage
                and test_result.coverage_pct is not None
                and coverage_threshold is not None
                and test_result.coverage_pct < coverage_threshold
            ):
                findings.append(
                    LintIssue(
                        linter="test_channel",
                        kind="coverage_below_threshold",
                        message=(
                            f"Code coverage {test_result.coverage_pct:.1f}% "
                            f"is below threshold {coverage_threshold}%"
                        ),
                        severity="warning",
                        evidence={
                            "coverage_pct": test_result.coverage_pct,
                            "threshold": coverage_threshold,
                        },
                    )
                )

        # Step 6: Symbol coverage gate
        gate_result = None
        cov_json_path = None
        if symbol_coverage_enabled:
            cov_json_path = test_result.coverage_json_path if test_result else None
            if cov_json_path:
                from lintgate.channels.symbol_coverage import run_symbol_coverage_gate

                gate_result = run_symbol_coverage_gate(
                    coverage_json_path=cov_json_path,
                    changed_files=changed_files,
                    project_root=project_root,
                    settings=symbol_cov_settings,
                    surface=event.surface,
                )
                # Uncovered symbols → blocking findings
                for sr in gate_result.symbol_results:
                    if not sr.covered:
                        missing_str = ", ".join(str(ln) for ln in sr.missing_lines[:10])
                        msg = f"Symbol {sr.symbol.name} is not fully covered"
                        if sr.missing_lines:
                            msg += f" (missing lines: {missing_str})"
                        findings.append(
                            LintIssue(
                                linter="test_channel",
                                kind="symbol_uncovered",
                                message=msg,
                                file=sr.symbol.file,
                                line=sr.symbol.start_line,
                                severity="blocking",
                                confidence=1.0,
                                evidence={
                                    "symbol_key": sr.symbol.symbol_key,
                                    "symbol": sr.symbol.name,
                                    "missing_lines": sr.missing_lines,
                                    "missing_branches": sr.missing_branches,
                                    "total_lines": sr.total_lines_in_span,
                                    "executed_lines": sr.executed_lines_in_span,
                                },
                                suggestions=[
                                    f"Add tests that execute lines {missing_str} in {sr.symbol.name}",
                                    "Or add a waiver with reason in symbol_coverage.waivers",
                                ],
                            )
                        )
                # Unresolved required symbols → blocking findings
                for unresolved in gate_result.unresolved_required:
                    findings.append(
                        LintIssue(
                            linter="test_channel",
                            kind="unresolved_required_symbol",
                            message=f"Required symbol not found: {unresolved}",
                            severity="blocking",
                            confidence=1.0,
                            evidence={"symbol": unresolved},
                            suggestions=[
                                "Check that the file and symbol exist",
                                "Update required_symbols in symbol_coverage config",
                            ],
                        )
                    )
                # Expired waivers → informational
                for waiver in gate_result.waivers_expired:
                    findings.append(
                        LintIssue(
                            linter="test_channel",
                            kind="waiver_expired",
                            message=f"Symbol coverage waiver expired: {waiver.symbol} (expired {waiver.expires})",
                            severity="informational",
                            evidence={"symbol": waiver.symbol, "expires": waiver.expires},
                        )
                    )
                # Skipped reasons → warning
                for reason in gate_result.skipped_reasons:
                    findings.append(
                        LintIssue(
                            linter="test_channel",
                            kind="symbol_gate_skipped",
                            message=f"Symbol coverage gate: {reason}",
                            severity="warning",
                        )
                    )
            elif event.surface == "ci":
                findings.append(
                    LintIssue(
                        linter="test_channel",
                        kind="symbol_gate_skipped",
                        message="Symbol coverage gate skipped: no coverage data collected",
                        severity="warning",
                    )
                )

        elapsed_ms = (time.perf_counter() - start) * 1000
        status: Literal["pass", "fail"] = "fail" if findings else "pass"
        severity: Literal["blocking", "warning", "informational", "none"]
        if any(f.severity == "blocking" for f in findings):
            severity = "blocking"
        elif any(f.severity == "warning" for f in findings):
            severity = "warning"
        elif findings:
            severity = "informational"
        else:
            severity = "none"

        metrics: dict[str, Any] = {
            "impacted_tests_found": len(impacted_tests),
            "missing_test_count": sum(1 for f in findings if f.kind == "missing_test"),
            "test_failure_count": sum(1 for f in findings if f.kind == "test_failure"),
        }
        if measure_coverage and test_result is not None:
            cov = test_result.coverage_pct
            if cov is not None:
                metrics["coverage_pct"] = cov
            if coverage_threshold is not None:
                metrics["coverage_threshold"] = float(coverage_threshold)
        if symbol_coverage_enabled and cov_json_path and gate_result is not None:
            sym_uncovered = sum(1 for r in gate_result.symbol_results if not r.covered)
            metrics["symbol_coverage_targets"] = len(gate_result.symbol_results)
            metrics["symbol_coverage_passed"] = len(gate_result.symbol_results) - sym_uncovered
            metrics["symbol_coverage_failed"] = sym_uncovered
            metrics["symbol_coverage_waivers"] = len(gate_result.waivers_applied)

        return ChannelResult(
            channel=self.name,
            status=status,
            severity=severity,
            findings=findings,
            repairs=repairs,
            metrics=metrics,
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
        if src_path.suffix != ".py":
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
            joined_name = (
                "test_"
                + "_".join(
                    p for p in rel.with_suffix("").parts if p not in ("src", "lib", "__init__")
                )
                + ".py"
            )
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

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_ms / 1000.0,
            cwd=project_root,
        )
        parsed = _parse_pytest_output(result.stdout, result.stderr, result.returncode)

        # Parse coverage if measured
        if measure_coverage and coverage_xml_path:
            parsed.coverage_pct = _parse_coverage(
                coverage_xml_path,
                f"{result.stdout}\n{result.stderr}",
            )
        if measure_coverage and coverage_json_path and os.path.isfile(coverage_json_path):
            parsed.coverage_json_path = coverage_json_path

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
    Example: ``<coverage line-rate="0.795">`` → 79.5%.
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
    return p.stem not in ("setup", "conftest")


def _has_test(source_file: str, project_root: str) -> bool:
    """Check if a source file has a corresponding test file."""
    tests = find_impacted_tests([source_file], project_root)
    return len(tests) > 0
