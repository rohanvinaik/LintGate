"""Test channel — supervision for test coverage and test health.

Checks:
1. Missing tests: source files without corresponding test files
2. Impact detection: find which test files are affected by the changed source
3. Test execution: run impacted tests and report failures
4. Skeleton proposals: suggest test stubs via archetype matching

This channel is ADVISORY by default (blocking_capable=False).
Test failures produce warnings, not blocking errors. The agent
should address them but can continue.

Implementation split across sub-modules:
- _test_types.py: pure value containers (TestFailure, TestRunResult)
- _test_channel_runner.py: pytest execution and output parsing
- _test_channel_impact.py: impact detection (find test files for changed source)
- _test_channel_models.py: shared value objects and channel context
- _test_channel_execution.py: test execution and coverage context
- _test_channel_drift.py: drift classification, stale symbols, contract drift
- _test_channel_selection.py: fallback selection and source/test discovery
- _test_channel_result.py: result assembly and severity computation
- _test_channel_checks.py: compatibility re-exports for the split helpers
- _test_channel_symbol_gate.py: symbol coverage gate and finding emission
"""

from __future__ import annotations

import contextlib
import os
import time

from lintgate.channels._test_types import TestFailure, TestRunResult
from lintgate.controlplane.types import (
    ChannelConfig,
    ChannelResult,
    ControlPlaneConfig,
    RepairAction,
    SupervisionEvent,
)
from lintgate.types import LintIssue

from ._test_channel_checks import (  # noqa: F401 — re-exports
    CoverageEvaluation,
    TestChannelContext,
    _build_channel_result,
    _build_drift_context,
    _check_contract_drift,
    _check_coverage_threshold,
    _check_missing_tests,
    _check_single_file_contract_drift,
    _check_stale_test_symbols,
    _classify_test_failure,
    _collect_test_findings,
    _compute_severity,
    _discover_fallback_test_targets,
    _evaluate_coverage_context,
    _has_test,
    _is_source_file,
    _no_test_files_exist,
    _parse_coverage_settings,
    _run_selected_tests,
    _select_tests_to_run,
)
from ._test_channel_impact import (  # noqa: F401 — re-exports
    _build_search_dirs,
    _find_joined_test,
    find_impacted_tests,
)
from ._test_channel_runner import (  # noqa: F401 — re-exports
    _parse_coverage,
    _parse_pytest_output,
    run_tests,
)
from ._test_channel_symbol_gate import (  # noqa: F401 — re-exports
    SymbolGateContext,
    _build_symbol_suggestions,
    _build_symbol_uncovered_message,
    _emit_symbol_findings,
    _filter_to_source_packages,
    _run_symbol_gate,
    _run_symbol_gate_if_enabled,
)

# Re-export for backward compatibility
__all__ = ["TestChannel", "TestFailure", "TestRunResult"]

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
            test_result, remaining_ms = _run_selected_tests(
                tests_to_run,
                project_root,
                cov_cfg,
                event.surface,
                config,
                self.timeout_ms,
                start,
                findings,
            )

            # Step 5: Check coverage threshold
            _check_coverage_threshold(
                test_result,
                cov_cfg["measure"],
                cov_cfg["threshold"],
                findings,
            )

            # Evaluate coverage context for downstream gates
            cov_eval = _evaluate_coverage_context(
                tests_to_run,
                impacted_tests,
                test_result,
                cov_cfg,
            )

            # Step 5b: Contract drift detection (#184)
            _check_contract_drift(changed_files, project_root, findings)

            # Step 6: Symbol coverage gate
            sym_ctx = SymbolGateContext(
                surface=event.surface,
                findings=findings,
                is_partial_run=cov_eval.is_partial_run,
                coverage_ok=cov_eval.coverage_ok,
                targets_mode=cov_eval.targets_mode,
                coverage_pct=cov_eval.coverage_pct,
            )
            gate_result = _run_symbol_gate_if_enabled(
                cov_cfg,
                test_result,
                changed_files,
                project_root,
                sym_ctx,
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
                    cov_eval=cov_eval,
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
