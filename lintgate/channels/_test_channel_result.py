"""Result assembly helpers for the test channel."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Literal

from lintgate.controlplane.types import ChannelResult

if TYPE_CHECKING:
    from lintgate.types import LintIssue

    from ._test_channel_models import TestChannelContext


def _build_channel_result(ctx: TestChannelContext) -> ChannelResult:
    """Assemble the final ChannelResult from collected findings and metrics."""
    elapsed_ms = (time.perf_counter() - ctx.start) * 1000
    status: Literal["pass", "fail"] = "fail" if ctx.findings else "pass"
    severity = _compute_severity(ctx.findings)

    metrics: dict[str, Any] = {
        "impacted_tests_found": len(ctx.impacted_tests),
        "missing_test_count": sum(1 for finding in ctx.findings if finding.kind == "missing_test"),
        "test_failure_count": sum(1 for finding in ctx.findings if finding.kind == "test_failure"),
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
        sym_uncovered = sum(1 for result in ctx.gate_result.symbol_results if not result.covered)
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


def _compute_severity(
    findings: list[LintIssue],
) -> Literal["blocking", "warning", "informational", "none"]:
    """Determine the highest severity across all findings."""
    if any(finding.severity == "blocking" for finding in findings):
        return "blocking"
    if any(finding.severity == "warning" for finding in findings):
        return "warning"
    if findings:
        return "informational"
    return "none"
