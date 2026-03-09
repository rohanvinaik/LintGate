"""Test hygiene channel — stub/duplicate/weak-only test detection for ControlPlane.

Co-equal mesh participant that detects test suite waste:
- THYGIENE001: Stub test body (pass/ellipsis/NotImplementedError)
- THYGIENE002: Weak-only assertions (callable, is_not_none, isinstance only)
- THYGIENE003: Duplicate test function (byte-identical or AST-normalized equivalent)

Safe deletion proposals for byte-identical duplicates and fully subsumed files.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    RepairAction,
    SupervisionEvent,
)
from lintgate.types import LintIssue

# ── Sub-module imports ───────────────────────────────────────────────
# Helpers are split into focused sub-modules; re-exported here for
# backward compatibility.

from ._test_hygiene_ast import (  # noqa: F401
    _extract_class_test_methods,
    _extract_test_functions,
    _function_body_ast_hash,
    _function_body_source,
    _function_context_hash,
    _parse_file,
    _read_source,
)
from ._test_hygiene_duplicates import (  # noqa: F401
    _add_subsumption_findings,
    _build_test_fingerprints,
    _find_cross_file_duplicates,
    _thygiene003_duplicates,
)
from ._test_hygiene_finders import (  # noqa: F401
    _is_stub_body,
    _thygiene001_stub_tests,
    _thygiene002_weak_only,
)

_TOP_N_FINDINGS = 5


# ── Test file discovery ──────────────────────────────────────────────


def _discover_test_files(project_root: str) -> list[str]:
    """Discover test files."""
    from lintgate.linters.test_effectiveness.test_analyzer import (
        _discover_test_files as discover,
    )

    return discover(project_root)


# ── Channel ──────────────────────────────────────────────────────────


class TestHygieneChannel:
    """Supervision channel for test suite hygiene.

    Detects stub tests, weak-only assertions, and duplicate tests.
    Advisory only — findings are warning or informational severity.
    """

    name = "test_hygiene"
    timeout_ms = 10000
    blocking_capable = False

    def should_run(self, event: SupervisionEvent, config: ControlPlaneConfig) -> bool:
        """Run when the project has a root path."""
        return bool(event.project_root)

    def execute(self, event: SupervisionEvent, config: ControlPlaneConfig) -> ChannelResult:
        """Execute test hygiene analysis."""
        start = time.perf_counter()
        project_root = event.project_root

        test_files = _discover_test_files(project_root)
        if not test_files:
            return ChannelResult(
                channel=self.name,
                status="skip",
                severity="none",
                metrics={"reason": "no_test_files"},
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        # Optional file filter from settings
        file_filter = config.channels.get(self.name, None) and config.channels[
            self.name
        ].settings.get("file_filter")
        if file_filter:
            test_files = [f for f in test_files if file_filter in f]

        findings: list[LintIssue] = []
        repairs: list[RepairAction] = []

        # THYGIENE001: Stub tests
        findings.extend(_thygiene001_stub_tests(test_files))

        # THYGIENE002: Weak-only assertions
        findings.extend(_thygiene002_weak_only(test_files))

        # THYGIENE003 + THYGIENE005: Duplicates and subsumption
        dup_findings, dup_repairs = _thygiene003_duplicates(test_files, project_root)
        findings.extend(dup_findings)
        repairs.extend(dup_repairs)

        elapsed_ms = (time.perf_counter() - start) * 1000

        # Metrics
        stub_count = sum(1 for f in findings if f.kind == "THYGIENE001")
        weak_count = sum(1 for f in findings if f.kind == "THYGIENE002")
        dup_count = sum(1 for f in findings if f.kind == "THYGIENE003")
        subsumed_count = sum(1 for f in findings if f.kind == "THYGIENE005")

        metrics: dict[str, Any] = {
            "test_files_scanned": len(test_files),
            "stub_tests": stub_count,
            "weak_only_tests": weak_count,
            "duplicate_tests": dup_count,
            "subsumed_files": subsumed_count,
            "total_findings": len(findings),
            "safe_delete_proposals": len(repairs),
        }

        status: Literal["pass", "fail"] = "fail" if findings else "pass"
        severity: Literal["blocking", "warning", "informational", "none"] = "none"
        if findings:
            severity = "informational"
            if any(f.severity == "warning" for f in findings):
                severity = "warning"

        return ChannelResult(
            channel=self.name,
            status=status,
            severity=severity,
            findings=findings,
            repairs=repairs,
            metrics=metrics,
            duration_ms=elapsed_ms,
        )
