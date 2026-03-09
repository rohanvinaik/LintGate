"""Test channel check functions — missing tests, drift, coverage, findings.

Extracted from test_channel.py to keep the main channel file under 400 lines.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from lintgate.controlplane.types import (
    ChannelConfig,
    ChannelResult,
    ControlPlaneConfig,
    RepairAction,
)
from lintgate.types import LintIssue

if TYPE_CHECKING:
    from lintgate.channels._test_types import TestFailure, TestRunResult


# ── Data Types ──────────────────────────────────────────────────────────


class CoverageEvaluation(NamedTuple):
    """Result of evaluating coverage context for downstream gates."""

    targets_mode: str = "unknown"
    is_partial_run: bool = False
    coverage_pct: float | None = None
    coverage_ok: bool = True


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
    cov_eval: CoverageEvaluation = field(default_factory=CoverageEvaluation)
    bootstrap_needed: bool = False

    # Backward-compatible accessors for downstream consumers
    @property
    def targets_mode(self) -> str:
        return self.cov_eval.targets_mode

    @property
    def coverage_pct(self) -> float | None:
        return self.cov_eval.coverage_pct

    @property
    def is_partial_run(self) -> bool:
        return self.cov_eval.is_partial_run

    @property
    def coverage_ok(self) -> bool:
        return self.cov_eval.coverage_ok


# ── Execute Helpers ─────────────────────────────────────────────────────


def _run_selected_tests(
    tests_to_run: list[str],
    project_root: str,
    cov_cfg: dict[str, Any],
    surface: str,
    config: ControlPlaneConfig,
    default_timeout_ms: int,
    start: float,
    findings: list[LintIssue],
) -> tuple[TestRunResult | None, int]:
    """Run selected tests and collect findings. Returns (result, remaining_ms)."""
    from lintgate.channels._test_channel_runner import run_tests

    if not tests_to_run:
        return None, 0
    base_timeout_ms = config.channels.get("tests", ChannelConfig()).timeout_ms
    if not base_timeout_ms:
        base_timeout_ms = default_timeout_ms
    remaining_ms = base_timeout_ms - int((time.perf_counter() - start) * 1000)
    timeout_floor_ms = 2000
    if cov_cfg["symbol_enabled"] and surface in ("mcp", "ci"):
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
    return test_result, remaining_ms


def _evaluate_coverage_context(
    tests_to_run: list[str],
    impacted_tests: list[str],
    test_result: TestRunResult | None,
    cov_cfg: dict[str, Any],
) -> CoverageEvaluation:
    """Determine partial-run context and coverage health for downstream gates."""
    targets_mode = "unknown"
    if tests_to_run:
        if impacted_tests and tests_to_run == impacted_tests:
            targets_mode = "impacted"
        else:
            targets_mode = "fallback"

    is_partial_run = targets_mode == "impacted"

    coverage_pct: float | None = None
    if test_result and test_result.coverage_pct is not None:
        coverage_pct = test_result.coverage_pct

    coverage_ok = True
    if cov_cfg["measure"] and coverage_pct is not None and cov_cfg.get("threshold") is not None:
        coverage_ok = coverage_pct >= float(cov_cfg["threshold"])

    return CoverageEvaluation(
        targets_mode=targets_mode,
        is_partial_run=is_partial_run,
        coverage_pct=coverage_pct,
        coverage_ok=coverage_ok,
    )


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
    ``test_drift`` (test file has uncommitted changes -- likely needs
    assertion update) or ``regression`` (test file is committed --
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

    - ``test_drift``: test file has uncommitted changes or is new --
      likely needs assertion updates to match refactored code.
    - ``regression``: test file is committed and unmodified --
      likely the code under test broke something.
    """
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
    """TEFF009 -- Detect failing tests that reference deleted symbols.

    When a test imports or patches a symbol that no longer exists in the
    source tree, the test is stale -- the agent should rewrite or remove it,
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
    """TEFF010 -- Detect function signature changes that will break tests.

    For each changed source file, compare old (git HEAD) and new versions
    to detect return arity and parameter changes, then find test call sites
    that will break.
    """

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
        _check_single_file_contract_drift(
            source_file,
            project_root,
            test_files,
            analyze_contract_drift,
            findings,
        )


def _check_single_file_contract_drift(
    source_file: str,
    project_root: str,
    test_files: list[str],
    analyze_fn: Any,
    findings: list[LintIssue],
) -> None:
    """Check a single source file for contract drift against test call sites."""
    abs_path = (
        source_file if os.path.isabs(source_file) else os.path.join(project_root, source_file)
    )
    if not os.path.isfile(abs_path):
        return

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
        return

    if not old_source:
        return  # New file, no old version to compare

    try:
        with open(abs_path, encoding="utf-8") as f:
            new_source = f.read()
    except OSError:
        return

    results = analyze_fn(abs_path, old_source, new_source, test_files)

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


# ── Result Builder ──────────────────────────────────────────────────────


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


# ── Test Selection ──────────────────────────────────────────────────────


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


# ── Simple Helpers ──────────────────────────────────────────────────────


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
    from lintgate.channels._test_channel_impact import find_impacted_tests

    tests = find_impacted_tests([source_file], project_root)
    return len(tests) > 0
