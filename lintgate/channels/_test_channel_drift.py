"""Drift, failure classification, and contract checks for the test channel."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING, Any

from lintgate.types import LintIssue

if TYPE_CHECKING:
    from lintgate.channels._test_types import TestFailure, TestRunResult


def _classify_failure(
    failure: TestFailure,
    drift_context: dict[str, set[str]] | None,
    project_root: str,
) -> str:
    """Classify a single test failure as drift, regression, or unknown."""
    if not drift_context or not failure.file:
        return "unknown"
    return _classify_test_failure(
        failure.file,
        drift_context["modified"],
        drift_context["untracked"],
        project_root,
    )


def _emit_drift_summary(
    drift_count: int,
    regression_count: int,
    findings: list[LintIssue],
) -> None:
    """Emit a summary finding when drift and/or regression failures exist."""
    if drift_count + regression_count == 0:
        return
    parts: list[str] = []
    if drift_count:
        parts.append(
            f"{drift_count} in uncommitted test files (likely test drift — update assertions)"
        )
    if regression_count:
        parts.append(f"{regression_count} in committed test files (likely regression — fix code)")
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


def _collect_test_findings(
    test_result: TestRunResult,
    remaining_ms: int,
    findings: list[LintIssue],
    project_root: str | None = None,
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

    drift_context = _build_drift_context(project_root) if project_root else None
    drift_count = 0
    regression_count = 0

    for failure in test_result.failures:
        classification = _classify_failure(failure, drift_context, project_root or "")
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

    if project_root and test_result.failures:
        _check_stale_test_symbols(test_result.failures, project_root, findings)

    _emit_drift_summary(drift_count, regression_count, findings)


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
    """Classify a test failure as test_drift or regression."""
    if os.path.isabs(test_file):
        try:
            rel = os.path.relpath(test_file, project_root)
        except ValueError:
            return "unknown"
    else:
        rel = test_file

    rel = rel.replace(os.sep, "/")
    if rel in untracked_files or rel in modified_files:
        return "test_drift"
    return "regression"


def _check_stale_test_symbols(
    failures: list[TestFailure],
    project_root: str,
    findings: list[LintIssue],
) -> None:
    """TEFF009 -- Detect failing tests that reference deleted symbols."""
    try:
        from lintgate.channels.test_symbol_resolver import build_stale_test_findings
    except ImportError:
        return

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
                        f"Test references deleted symbol '{ref['module']}.{ref['symbol']}'. "
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
                evidence={"stale_count": stale_count, "verdict": "stale_tests_detected"},
            )
        )


def _check_contract_drift(
    changed_files: list[str],
    project_root: str,
    findings: list[LintIssue],
) -> None:
    """TEFF010 -- Detect function signature changes that will break tests."""
    try:
        from lintgate.channels.contract_drift_detector import analyze_contract_drift
    except ImportError:
        return

    source_files = [
        path
        for path in changed_files
        if path.endswith(".py") and not os.path.basename(path).startswith("test_")
    ]
    if not source_files:
        return

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
        return

    try:
        with open(abs_path, encoding="utf-8") as handle:
            new_source = handle.read()
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
                        {"file": site.test_file, "line": site.line}
                        for site in drift.affected_sites[:10]
                    ],
                },
                suggestions=[
                    "Update test call sites to match the new function contract.",
                    "For return arity changes: update tuple unpacking to match new return count.",
                    "For parameter changes: add/remove arguments at call sites.",
                ],
            )
        )
