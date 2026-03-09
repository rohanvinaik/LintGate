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

        # Step 2b: Bootstrap trigger — signal when project has zero test files
        bootstrap_needed = False
        if not impacted_tests and _no_test_files_exist(project_root):
            bootstrap_needed = True
            findings.append(
                LintIssue(
                    linter="test_channel",
                    kind="BOOTSTRAP_TRIGGERED",
                    message="No test files detected. Test bootstrap pipeline available.",
                    severity="informational",
                    confidence=1.0,
                )
            )

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
                base_timeout_ms = config.channels.get("tests", ChannelConfig()).timeout_ms
                if not base_timeout_ms:
                    base_timeout_ms = self.timeout_ms
                remaining_ms = base_timeout_ms - int((time.perf_counter() - start) * 1000)
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
                _collect_test_findings(test_result, remaining_ms, findings, project_root)

            # Step 5: Check coverage threshold
            _check_coverage_threshold(
                test_result,
                cov_cfg["measure"],
                cov_cfg["threshold"],
                findings,
            )

            # Determine partial run context
            targets_mode = "unknown"
            if tests_to_run:
                if impacted_tests and tests_to_run == impacted_tests:
                    targets_mode = "impacted"
                else:
                    targets_mode = "fallback"

            is_partial_run = False
            if targets_mode == "impacted":
                is_partial_run = True

            coverage_pct: float | None = None
            if test_result and test_result.coverage_pct is not None:
                coverage_pct = test_result.coverage_pct

            coverage_ok = True
            if (
                cov_cfg["measure"]
                and coverage_pct is not None
                and cov_cfg.get("threshold") is not None
            ):
                coverage_ok = coverage_pct >= float(cov_cfg["threshold"])

            # Step 5b: Contract drift detection (#184)
            _check_contract_drift(changed_files, project_root, findings)

            # Step 6: Symbol coverage gate
            gate_result = _run_symbol_gate_if_enabled(
                cov_cfg,
                test_result,
                changed_files,
                project_root,
                event.surface,
                findings,
                is_partial_run=is_partial_run,
                coverage_ok=coverage_ok,
                targets_mode=targets_mode,
                coverage_pct=coverage_pct,
            )

            return _build_channel_result(
                TestChannelContext(
                    channel_name=self.name,
                    start=start,
                    findings=findings,
                    repairs=repairs,
                    impacted_tests=impacted_tests,
                    test_result=test_result,
                    cov_cfg=cov_cfg,
                    gate_result=gate_result,
                    targets_mode=targets_mode,
                    coverage_pct=coverage_pct,
                    is_partial_run=is_partial_run,
                    coverage_ok=coverage_ok,
                    bootstrap_needed=bootstrap_needed,
                )
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
    test_result: TestRunResult,
    remaining_ms: int,
    findings: list[LintIssue],
    project_root: str | None = None,
) -> None:
    """Convert test execution results into findings.

    When project_root is provided, classifies each failure as
    ``test_drift`` (test file has uncommitted changes — likely needs
    assertion update) or ``regression`` (test file is committed —
    likely a code bug).  See issue #183.
    """
    if test_result.timed_out:
        findings.append(
            LintIssue(
                linter="test_channel",
                kind="test_timeout",
                message=f"Test execution timed out ({remaining_ms}ms budget)",
                severity="warning",
            )
        )

    # Collect git context for drift classification
    drift_context = _build_drift_context(project_root) if project_root else None

    drift_count = 0
    regression_count = 0

    for failure in test_result.failures:
        classification = "unknown"
        if drift_context and failure.file:
            classification = _classify_test_failure(
                failure.file,
                drift_context["modified"],
                drift_context["untracked"],
                project_root or "",
            )
            if classification == "test_drift":
                drift_count += 1
            elif classification == "regression":
                regression_count += 1

        findings.append(
            LintIssue(
                linter="test_channel",
                kind="test_failure",
                message=failure.message,
                file=failure.file,
                line=failure.line,
                severity="warning",
                evidence={"failure_class": classification} if classification != "unknown" else {},
            )
        )

    # TEFF009: Check for stale test references to deleted symbols
    if project_root and test_result.failures:
        _check_stale_test_symbols(test_result.failures, project_root, findings)

    # Emit summary finding when both drift and regression are present
    if drift_count + regression_count > 0 and (drift_count > 0 or regression_count > 0):
        parts: list[str] = []
        if drift_count:
            parts.append(
                f"{drift_count} in uncommitted test files (likely test drift — update assertions)"
            )
        if regression_count:
            parts.append(
                f"{regression_count} in committed test files (likely regression — fix code)"
            )
        findings.append(
            LintIssue(
                linter="test_channel",
                kind="test_drift_summary",
                message=f"Test failure classification: {'; '.join(parts)}.",
                severity="informational",
                evidence={
                    "drift_count": drift_count,
                    "regression_count": regression_count,
                },
            )
        )


def _build_drift_context(project_root: str) -> dict[str, set[str]] | None:
    """Collect modified/untracked file sets for drift classification."""
    try:
        from lintgate.channels.git_channel import collect_working_tree_context

        ctx = collect_working_tree_context(project_root)
        return {
            "modified": set(ctx.get("modified_files", [])),
            "untracked": set(ctx.get("untracked_files", [])),
        }
    except Exception:
        return None


def _classify_test_failure(
    test_file: str,
    modified_files: set[str],
    untracked_files: set[str],
    project_root: str,
) -> str:
    """Classify a test failure as test_drift or regression.

    - ``test_drift``: test file has uncommitted changes or is new —
      likely needs assertion updates to match refactored code.
    - ``regression``: test file is committed and unmodified —
      likely the code under test broke something.
    """
    import os

    if os.path.isabs(test_file):
        try:
            rel = os.path.relpath(test_file, project_root)
        except ValueError:
            return "unknown"
    else:
        rel = test_file

    rel = rel.replace(os.sep, "/")

    if rel in untracked_files:
        return "test_drift"
    if rel in modified_files:
        return "test_drift"
    return "regression"


def _check_stale_test_symbols(
    failures: list[TestFailure],
    project_root: str,
    findings: list[LintIssue],
) -> None:
    """TEFF009 — Detect failing tests that reference deleted symbols.

    When a test imports or patches a symbol that no longer exists in the
    source tree, the test is stale — the agent should rewrite or remove it,
    NOT re-add the deleted code.
    """
    try:
        from lintgate.channels.test_symbol_resolver import build_stale_test_findings
    except ImportError:
        return

    # Deduplicate by test file to avoid redundant AST parses
    seen_files: set[str] = set()
    stale_count = 0

    for failure in failures:
        if not failure.file or failure.file in seen_files:
            continue
        seen_files.add(failure.file)

        stale_refs = build_stale_test_findings(failure.file, project_root, failure.test_name)
        for ref in stale_refs:
            stale_count += 1
            findings.append(
                LintIssue(
                    linter="test_effectiveness",
                    kind="TEFF009",
                    message=(
                        f"Test references deleted symbol "
                        f"'{ref['module']}.{ref['symbol']}'. "
                        f"The test is stale — rewrite or remove it. "
                        f"DO NOT re-add the deleted code to satisfy the test."
                    ),
                    file=failure.file,
                    line=ref.get("line"),
                    severity="warning",
                    confidence=ref.get("confidence", 0.95),
                    evidence={
                        "code": "TEFF009",
                        "deleted_symbol": f"{ref['module']}.{ref['symbol']}",
                        "test_file": ref["test_file"],
                        "resolution": "stale_test",
                        "verdict": "remove_or_rewrite_test",
                        "source": ref.get("source", "import"),
                    },
                    suggestions=[
                        "Check git log for the commit that deleted the symbol — it likely explains why.",
                        "If the function was replaced by a new interface, rewrite the test for the new interface.",
                        "If the function was removed entirely, remove the test.",
                    ],
                )
            )

    if stale_count > 0:
        findings.append(
            LintIssue(
                linter="test_effectiveness",
                kind="TEFF009_summary",
                message=(
                    f"{stale_count} test failure{'s' if stale_count != 1 else ''} "
                    f"reference{'s' if stale_count == 1 else ''} deleted symbols. "
                    f"These tests are stale — update or remove them."
                ),
                severity="informational",
                evidence={
                    "stale_count": stale_count,
                    "verdict": "stale_tests_detected",
                },
            )
        )


def _check_contract_drift(
    changed_files: list[str],
    project_root: str,
    findings: list[LintIssue],
) -> None:
    """TEFF010 — Detect function signature changes that will break tests.

    For each changed source file, compare old (git HEAD) and new versions
    to detect return arity and parameter changes, then find test call sites
    that will break.
    """
    import subprocess

    try:
        from lintgate.channels.contract_drift_detector import (
            analyze_contract_drift,
        )
    except ImportError:
        return

    # Only check Python source files, not test files
    source_files = [
        f
        for f in changed_files
        if f.endswith(".py") and not os.path.basename(f).startswith("test_")
    ]
    if not source_files:
        return

    # Discover test files for call site scanning
    test_dir = os.path.join(project_root, "tests")
    if not os.path.isdir(test_dir):
        return

    test_files: list[str] = []
    for root, _dirs, files in os.walk(test_dir):
        for fname in files:
            if fname.startswith("test_") and fname.endswith(".py"):
                test_files.append(os.path.join(root, fname))

    if not test_files:
        return

    for source_file in source_files:
        abs_path = (
            source_file if os.path.isabs(source_file) else os.path.join(project_root, source_file)
        )
        if not os.path.isfile(abs_path):
            continue

        # Get old version from git HEAD
        try:
            rel = os.path.relpath(abs_path, project_root)
            old_source = subprocess.run(
                ["git", "show", f"HEAD:{rel}"],
                capture_output=True,
                text=True,
                cwd=project_root,
                timeout=5,
            ).stdout
        except (subprocess.SubprocessError, OSError):
            continue

        if not old_source:
            continue  # New file, no old version to compare

        try:
            with open(abs_path, encoding="utf-8") as f:
                new_source = f.read()
        except OSError:
            continue

        results = analyze_contract_drift(abs_path, old_source, new_source, test_files)

        for drift in results:
            if not drift.affected_sites:
                continue
            findings.append(
                LintIssue(
                    linter="test_effectiveness",
                    kind="TEFF010",
                    message=drift.advisory,
                    file=drift.change.file,
                    line=drift.change.line,
                    severity="warning",
                    confidence=0.90,
                    evidence={
                        "code": "TEFF010",
                        "function": drift.change.function,
                        "change_type": drift.change.change_type,
                        "old_value": drift.change.old_value,
                        "new_value": drift.change.new_value,
                        "affected_count": len(drift.affected_sites),
                        "affected_sites": [
                            {"file": s.test_file, "line": s.line} for s in drift.affected_sites[:10]
                        ],
                    },
                    suggestions=[
                        "Update test call sites to match the new function contract.",
                        "For return arity changes: update tuple unpacking to match new return count.",
                        "For parameter changes: add/remove arguments at call sites.",
                    ],
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
    is_partial_run: bool = False,
    coverage_ok: bool = True,
    targets_mode: str = "unknown",
    coverage_pct: float | None = None,
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
        is_partial_run=is_partial_run,
        coverage_ok=coverage_ok,
        targets_mode=targets_mode,
        coverage_pct=coverage_pct,
    )


@dataclass
class TestChannelContext:
    """Context for building test channel results with many metrics."""

    channel_name: str
    start: float
    findings: list[LintIssue]
    repairs: list[RepairAction]
    impacted_tests: list[str]
    test_result: TestRunResult | None
    cov_cfg: dict[str, Any]
    gate_result: Any
    targets_mode: str = "unknown"
    coverage_pct: float | None = None
    is_partial_run: bool = False
    coverage_ok: bool = True
    bootstrap_needed: bool = False


def _build_channel_result(ctx: TestChannelContext) -> ChannelResult:
    """Assemble the final ChannelResult from collected findings and metrics."""
    elapsed_ms = (time.perf_counter() - ctx.start) * 1000
    status: Literal["pass", "fail"] = "fail" if ctx.findings else "pass"
    severity = _compute_severity(ctx.findings)

    metrics: dict[str, Any] = {
        "impacted_tests_found": len(ctx.impacted_tests),
        "missing_test_count": sum(1 for f in ctx.findings if f.kind == "missing_test"),
        "test_failure_count": sum(1 for f in ctx.findings if f.kind == "test_failure"),
    }
    if ctx.bootstrap_needed:
        metrics["bootstrap_needed"] = True
        metrics["bootstrap_reason"] = "zero_test_files"
    if ctx.cov_cfg["measure"] and ctx.test_result is not None:
        cov = ctx.test_result.coverage_pct
        if cov is not None:
            metrics["coverage_pct"] = cov
        if ctx.cov_cfg["threshold"] is not None:
            metrics["coverage_threshold"] = float(ctx.cov_cfg["threshold"])
    if ctx.gate_result is not None:
        sym_uncovered = sum(1 for r in ctx.gate_result.symbol_results if not r.covered)
        metrics["symbol_coverage_targets"] = len(ctx.gate_result.symbol_results)
        metrics["symbol_coverage_passed"] = len(ctx.gate_result.symbol_results) - sym_uncovered
        metrics["symbol_coverage_failed"] = sym_uncovered
        metrics["symbol_coverage_waivers"] = len(ctx.gate_result.waivers_applied)
        metrics["symbol_gate_context"] = {
            "targets_mode": ctx.targets_mode,
            "is_partial_run": ctx.is_partial_run,
            "coverage_ok": ctx.coverage_ok,
            "coverage_pct": ctx.coverage_pct,
        }

    return ChannelResult(
        channel=ctx.channel_name,
        status=status,
        severity=severity,
        findings=ctx.findings,
        repairs=ctx.repairs,
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
    is_partial_run: bool = False,
    coverage_ok: bool = True,
    targets_mode: str = "unknown",
    coverage_pct: float | None = None,
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
    _emit_symbol_findings(
        gate_result, findings, is_partial_run, coverage_ok, targets_mode, coverage_pct
    )
    return gate_result


def _build_symbol_suggestions(sr: Any) -> list[str]:
    """Generate branch-aware or line-aware remediation text."""
    suggestions = []

    missing_line_str = ", ".join(str(ln) for ln in sr.missing_lines[:10])
    missing_branch_str = ", ".join(f"{b[0]}->{b[1]}" for b in sr.missing_branches[:5])

    if sr.missing_lines and sr.missing_branches:
        suggestions.append(
            f"Add tests that execute lines {missing_line_str} and branches {missing_branch_str} in {sr.symbol.name}"
        )
    elif sr.missing_lines:
        suggestions.append(f"Add tests that execute lines {missing_line_str} in {sr.symbol.name}")
    elif sr.missing_branches:
        suggestions.append(
            f"Add tests that execute branches {missing_branch_str} in {sr.symbol.name}"
        )
    else:
        suggestions.append(f"Add missing tests for {sr.symbol.name}")

    suggestions.append("Or add a waiver with reason in symbol_coverage.waivers")
    return suggestions


def _emit_symbol_findings(
    gate_result: Any,
    findings: list[LintIssue],
    is_partial_run: bool = False,
    coverage_ok: bool = True,
    targets_mode: str = "unknown",
    coverage_pct: float | None = None,
) -> None:
    """Convert symbol coverage gate results into LintIssue findings."""
    for sr in gate_result.symbol_results:
        if sr.covered:
            continue

        msg = f"Symbol {sr.symbol.name} is not fully covered"
        if sr.missing_lines and sr.missing_branches:
            msg += f" (missing lines: {', '.join(str(ln) for ln in sr.missing_lines[:10])}, and {len(sr.missing_branches)} branches)"
        elif sr.missing_lines:
            msg += f" (missing lines: {', '.join(str(ln) for ln in sr.missing_lines[:10])})"
        elif sr.missing_branches:
            msg += f" (missing {len(sr.missing_branches)} branches)"

        confidence = 1.0
        downgrade_reason = ""
        severity = "blocking"

        if is_partial_run:
            if coverage_ok:
                confidence = 0.6
                severity = "warning"
                downgrade_reason = " (downgraded: partial test run with healthy line coverage)"
            else:
                confidence = 0.7
                severity = "blocking"

        msg += downgrade_reason

        findings.append(
            LintIssue(
                linter="test_channel",
                kind="symbol_uncovered",
                message=msg,
                file=sr.symbol.file,
                line=sr.symbol.start_line,
                severity=severity,
                confidence=confidence,
                evidence={
                    "symbol_key": sr.symbol.symbol_key,
                    "symbol": sr.symbol.name,
                    "missing_lines": sr.missing_lines,
                    "missing_branches": sr.missing_branches,
                    "total_lines": sr.total_lines_in_span,
                    "executed_lines": sr.executed_lines_in_span,
                    "is_partial_run": is_partial_run,
                    "coverage_ok": coverage_ok,
                    "coverage_pct": coverage_pct,
                    "targets_mode": targets_mode,
                },
                suggestions=_build_symbol_suggestions(sr),
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


def _no_test_files_exist(project_root: str) -> bool:
    """Check if the project has zero test files anywhere.

    Scans common test locations for any file matching test_*.py or *_test.py.
    Returns True only when absolutely no test files exist (cold-start project).
    """
    root = Path(project_root)
    # Check common test directories first (fast path)
    for dirname in ("tests", "test"):
        test_dir = root / dirname
        if test_dir.is_dir():
            for _ in test_dir.rglob("test_*.py"):
                return False
            for _ in test_dir.rglob("*_test.py"):
                return False
    # Check for root-level test files
    for _ in root.glob("test_*.py"):
        return False
    # Check for test files co-located with source
    for _ in root.rglob("test_*.py"):
        return False
    return True


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
