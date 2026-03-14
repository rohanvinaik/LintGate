"""Execution and coverage helpers for the test channel."""

from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING, Any

from lintgate.controlplane.types import ChannelConfig, ControlPlaneConfig
from lintgate.types import LintIssue

from ._test_channel_drift import _collect_test_findings
from ._test_channel_models import CoverageEvaluation

if TYPE_CHECKING:
    from lintgate.channels._test_types import TestRunResult


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
        source_packages = [str(pkg).strip() for pkg in raw_pkgs if str(pkg).strip()]
    elif isinstance(raw_pkgs, str) and raw_pkgs.strip():
        source_packages = [raw_pkgs.strip()]
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


def _check_coverage_threshold(
    test_result: TestRunResult | None,
    measure_coverage: bool,
    coverage_threshold: float | None,
    findings: list[LintIssue],
) -> None:
    """Emit a finding if coverage is below threshold."""
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
