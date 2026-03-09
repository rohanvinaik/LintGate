"""Symbol coverage gate — per-symbol coverage checking and finding emission.

Extracted from test_channel.py to keep the main channel file under 400 lines.
"""

from __future__ import annotations

import os
from typing import Any, NamedTuple

from lintgate.types import LintIssue


class SymbolGateContext(NamedTuple):
    """Runtime context for symbol coverage gate execution."""

    surface: str
    findings: list[LintIssue]
    is_partial_run: bool = False
    coverage_ok: bool = True
    targets_mode: str = "unknown"
    coverage_pct: float | None = None


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
    test_result: Any,
    changed_files: list[str],
    project_root: str,
    ctx: SymbolGateContext,
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
        ctx,
    )


def _run_symbol_gate(
    coverage_json_path: str | None,
    changed_files: list[str],
    project_root: str,
    settings: dict[str, Any],
    ctx: SymbolGateContext,
) -> Any:
    """Run symbol coverage gate and append findings. Returns gate result or None."""
    if not coverage_json_path:
        if ctx.surface == "ci":
            ctx.findings.append(
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
        surface=ctx.surface,
    )
    _emit_symbol_findings(gate_result, ctx)
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


def _build_symbol_uncovered_message(sr: Any) -> str:
    """Build the uncovered-symbol message without string concat in a loop."""
    parts = [f"Symbol {sr.symbol.name} is not fully covered"]
    if sr.missing_lines and sr.missing_branches:
        lines_str = ", ".join(str(ln) for ln in sr.missing_lines[:10])
        parts.append(
            f" (missing lines: {lines_str},"
            f" and {len(sr.missing_branches)} branches)"
        )
    elif sr.missing_lines:
        lines_str = ", ".join(str(ln) for ln in sr.missing_lines[:10])
        parts.append(f" (missing lines: {lines_str})")
    elif sr.missing_branches:
        parts.append(f" (missing {len(sr.missing_branches)} branches)")
    return "".join(parts)


def _emit_symbol_findings(
    gate_result: Any,
    ctx: SymbolGateContext,
) -> None:
    """Convert symbol coverage gate results into LintIssue findings."""
    for sr in gate_result.symbol_results:
        if sr.covered:
            continue

        msg = _build_symbol_uncovered_message(sr)

        confidence = 1.0
        downgrade_reason = ""
        severity = "blocking"

        if ctx.is_partial_run:
            if ctx.coverage_ok:
                confidence = 0.6
                severity = "warning"
                downgrade_reason = " (downgraded: partial test run with healthy line coverage)"
            else:
                confidence = 0.7
                severity = "blocking"

        msg += downgrade_reason

        ctx.findings.append(
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
                    "is_partial_run": ctx.is_partial_run,
                    "coverage_ok": ctx.coverage_ok,
                    "coverage_pct": ctx.coverage_pct,
                    "targets_mode": ctx.targets_mode,
                },
                suggestions=_build_symbol_suggestions(sr),
            )
        )

    for unresolved in gate_result.unresolved_required:
        ctx.findings.append(
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
        ctx.findings.append(
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
        ctx.findings.append(
            LintIssue(
                linter="test_channel",
                kind="symbol_gate_skipped",
                message=f"Symbol coverage gate: {reason}",
                severity="warning",
            )
        )
