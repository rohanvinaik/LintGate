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
    coverage_json_ephemeral: bool = False  # True when path should be cleaned up by caller


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

        # Step 2: Check for missing tests + propose skeletons
        _check_missing_tests(changed_files, project_root, findings, repairs)

        # Step 3: Parse coverage settings
        channel_settings = config.channels.get("tests", ChannelConfig()).settings
        cov_cfg = _parse_coverage_settings(channel_settings, event.surface)
        tests_to_run = _select_tests_to_run(
            impacted_tests,
            project_root,
            cov_cfg=cov_cfg,
            surface=event.surface,
            findings=findings,
        )

        test_result: TestRunResult | None = None
        try:
            # Step 4: Run selected tests (impacted or fallback)
            if tests_to_run:
                remaining_ms = self.timeout_ms - int((time.perf_counter() - start) * 1000)
                timeout_floor_ms = 2000
                if cov_cfg["symbol_enabled"] and event.surface in ("mcp", "ci"):
                    # Symbol gate needs a meaningful coverage sample; avoid 10s truncation.
                    timeout_floor_ms = 25000
                test_result = run_tests(
                    tests_to_run,
                    project_root,
                    timeout_ms=max(remaining_ms, timeout_floor_ms),
                    measure_coverage=cov_cfg["measure"],
                    source_packages=cov_cfg["source_packages"],
                )
                _collect_test_findings(test_result, remaining_ms, findings)

            # Step 5: Check coverage threshold
            _check_coverage_threshold(
                test_result,
                cov_cfg["measure"],
                cov_cfg["threshold"],
                findings,
            )

            # Step 6: Symbol coverage gate
            gate_result = _run_symbol_gate_if_enabled(
                cov_cfg,
                test_result,
                changed_files,
                project_root,
                event.surface,
                findings,
            )

            return _build_channel_result(
                self.name,
                start,
                findings,
                repairs,
                impacted_tests,
                test_result,
                cov_cfg,
                gate_result,
            )
        finally:
            if (
                test_result
                and test_result.coverage_json_ephemeral
                and test_result.coverage_json_path
            ):
                with contextlib.suppress(OSError):
                    os.unlink(test_result.coverage_json_path)


# ── Execute Helpers ──────────────────────────────────────────────────────


def _check_missing_tests(
    changed_files: list[str],
    project_root: str,
    findings: list[LintIssue],
    repairs: list[RepairAction],
) -> None:
    """Check for source files without corresponding tests and propose skeletons."""
    for src_file in changed_files:
        if not (_is_source_file(src_file, project_root) and not _has_test(src_file, project_root)):
            continue
        findings.append(
            LintIssue(
                linter="test_channel",
                kind="missing_test",
                message=f"No test file found for {os.path.basename(src_file)}",
                file=src_file,
                severity="informational",
            )
        )
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


def _parse_coverage_settings(channel_settings: dict[str, Any], surface: str) -> dict[str, Any]:
    """Parse coverage-related settings into a flat dict."""
    raw_threshold = channel_settings.get("coverage_threshold")
    threshold: float | None = None
    if raw_threshold is not None:
        with contextlib.suppress(TypeError, ValueError):
            threshold = float(raw_threshold)

    raw_pkgs = channel_settings.get("source_packages")
    source_packages: list[str] | None = None
    if isinstance(raw_pkgs, list):
        source_packages = [str(p).strip() for p in raw_pkgs if str(p).strip()]
    elif isinstance(raw_pkgs, str) and raw_pkgs.strip():
        source_packages = [raw_pkgs.strip()]
    # Fallback matches run_tests() default — keeps --cov and symbol filter consistent
    if not source_packages:
        source_packages = ["lintgate", "mcp_tools"]

    sym_settings = channel_settings.get("symbol_coverage", {})
    sym_enabled = isinstance(sym_settings, dict) and sym_settings.get("enabled", False)

    measure = surface in ("mcp", "ci") and (threshold is not None or sym_enabled)

    return {
        "threshold": threshold,
        "source_packages": source_packages,
        "symbol_coverage": sym_settings,
        "symbol_enabled": sym_enabled,
        "measure": measure,
    }


def _collect_test_findings(
    test_result: TestRunResult, remaining_ms: int, findings: list[LintIssue]
) -> None:
    """Convert test execution results into findings."""
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
                severity="warning",
            )
        )


def _check_coverage_threshold(
    test_result: TestRunResult | None,
    measure_coverage: bool,
    coverage_threshold: float | None,
    findings: list[LintIssue],
) -> None:
    """Emit finding if coverage is below threshold."""
    if not (
        measure_coverage
        and test_result is not None
        and test_result.coverage_pct is not None
        and coverage_threshold is not None
        and test_result.coverage_pct < coverage_threshold
    ):
        return
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


def _filter_to_source_packages(
    changed_files: list[str],
    source_packages: list[str],
    project_root: str,
) -> list[str]:
    """Filter changed files to only those within source packages.

    The symbol coverage gate should only target files that are covered by
    --cov (source packages), not test files or other non-source files.
    """
    if not source_packages:
        return changed_files
    result = []
    for filepath in changed_files:
        try:
            rel = os.path.relpath(filepath, project_root)
        except ValueError:
            continue
        for pkg in source_packages:
            if rel == pkg or rel.startswith(pkg + os.sep) or rel.startswith(pkg + "/"):
                result.append(filepath)
                break
    return result


def _run_symbol_gate_if_enabled(
    cov_cfg: dict[str, Any],
    test_result: TestRunResult | None,
    changed_files: list[str],
    project_root: str,
    surface: str,
    findings: list[LintIssue],
) -> Any:
    """Run symbol coverage gate if enabled. Returns gate result or None."""
    if not cov_cfg["symbol_enabled"]:
        return None
    cov_json_path = test_result.coverage_json_path if test_result else None
    source_files = _filter_to_source_packages(
        changed_files,
        cov_cfg["source_packages"],
        project_root,
    )
    return _run_symbol_gate(
        cov_json_path,
        source_files,
        project_root,
        cov_cfg["symbol_coverage"],
        surface,
        findings,
    )


def _build_channel_result(
    channel_name: str,
    start: float,
    findings: list[LintIssue],
    repairs: list[RepairAction],
    impacted_tests: list[str],
    test_result: TestRunResult | None,
    cov_cfg: dict[str, Any],
    gate_result: Any,
) -> ChannelResult:
    """Assemble the final ChannelResult from collected findings and metrics."""
    elapsed_ms = (time.perf_counter() - start) * 1000
    status: Literal["pass", "fail"] = "fail" if findings else "pass"
    severity = _compute_severity(findings)

    metrics: dict[str, Any] = {
        "impacted_tests_found": len(impacted_tests),
        "missing_test_count": sum(1 for f in findings if f.kind == "missing_test"),
        "test_failure_count": sum(1 for f in findings if f.kind == "test_failure"),
    }
    if cov_cfg["measure"] and test_result is not None:
        cov = test_result.coverage_pct
        if cov is not None:
            metrics["coverage_pct"] = cov
        if cov_cfg["threshold"] is not None:
            metrics["coverage_threshold"] = float(cov_cfg["threshold"])
    if gate_result is not None:
        sym_uncovered = sum(1 for r in gate_result.symbol_results if not r.covered)
        metrics["symbol_coverage_targets"] = len(gate_result.symbol_results)
        metrics["symbol_coverage_passed"] = len(gate_result.symbol_results) - sym_uncovered
        metrics["symbol_coverage_failed"] = sym_uncovered
        metrics["symbol_coverage_waivers"] = len(gate_result.waivers_applied)

    return ChannelResult(
        channel=channel_name,
        status=status,
        severity=severity,
        findings=findings,
        repairs=repairs,
        metrics=metrics,
        duration_ms=elapsed_ms,
    )


def _discover_fallback_test_targets(project_root: str) -> list[str]:
    """Discover broad test targets when impacted-test mapping finds none."""
    root = Path(project_root)
    targets: list[str] = []
    for dirname in ("tests", "test"):
        candidate = root / dirname
        if candidate.is_dir():
            targets.append(str(candidate))
    if targets:
        return targets
    # Fallback: root-level test files
    for candidate in sorted(root.glob("test_*.py")):
        if candidate.is_file():
            targets.append(str(candidate))
    return targets


def _select_tests_to_run(
    impacted_tests: list[str],
    project_root: str,
    cov_cfg: dict[str, Any] | None,
    surface: str,
    findings: list[LintIssue],
) -> list[str]:
    """Choose test targets. Symbol gate in MCP/CI falls back to broad suite."""
    if impacted_tests:
        return impacted_tests
    if not isinstance(cov_cfg, dict):
        return []
    if not (cov_cfg.get("symbol_enabled") and surface in ("mcp", "ci")):
        return []
    fallback_targets = _discover_fallback_test_targets(project_root)
    if fallback_targets:
        findings.append(
            LintIssue(
                linter="test_channel",
                kind="symbol_gate_fallback",
                message="No impacted tests detected; running fallback test targets for symbol gate.",
                severity="informational",
                evidence={"targets": fallback_targets[:4], "surface": surface},
            )
        )
    return fallback_targets


def _compute_severity(
    findings: list[LintIssue],
) -> Literal["blocking", "warning", "informational", "none"]:
    """Determine the highest severity across all findings."""
    if any(f.severity == "blocking" for f in findings):
        return "blocking"
    if any(f.severity == "warning" for f in findings):
        return "warning"
    if findings:
        return "informational"
    return "none"


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


# ── Symbol Coverage Gate ─────────────────────────────────────────────────


def _run_symbol_gate(
    coverage_json_path: str | None,
    changed_files: list[str],
    project_root: str,
    settings: dict[str, Any],
    surface: str,
    findings: list[LintIssue],
) -> Any:
    """Run symbol coverage gate and append findings. Returns gate result or None."""
    if not coverage_json_path:
        if surface == "ci":
            findings.append(
                LintIssue(
                    linter="test_channel",
                    kind="symbol_gate_skipped",
                    message="Symbol coverage gate skipped: no coverage data collected",
                    severity="warning",
                )
            )
        return None

    from lintgate.channels.symbol_coverage import run_symbol_coverage_gate

    gate_result = run_symbol_coverage_gate(
        coverage_json_path=coverage_json_path,
        changed_files=changed_files,
        project_root=project_root,
        settings=settings,
        surface=surface,
    )
    _emit_symbol_findings(gate_result, findings)
    return gate_result


def _emit_symbol_findings(gate_result: Any, findings: list[LintIssue]) -> None:
    """Convert symbol coverage gate results into LintIssue findings."""
    for sr in gate_result.symbol_results:
        if sr.covered:
            continue
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

    for waiver in gate_result.waivers_expired:
        findings.append(
            LintIssue(
                linter="test_channel",
                kind="waiver_expired",
                message=(
                    f"Symbol coverage waiver expired: {waiver.symbol} (expired {waiver.expires})"
                ),
                severity="informational",
                evidence={"symbol": waiver.symbol, "expires": waiver.expires},
            )
        )

    for reason in gate_result.skipped_reasons:
        findings.append(
            LintIssue(
                linter="test_channel",
                kind="symbol_gate_skipped",
                message=f"Symbol coverage gate: {reason}",
                severity="warning",
            )
        )


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
