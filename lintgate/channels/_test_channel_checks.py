"""Compatibility re-exports for test channel checks.

The test channel implementation is split by concern:
- `_test_channel_models.py` for value objects
- `_test_channel_execution.py` for pytest execution and coverage context
- `_test_channel_drift.py` for failure classification and contract drift
- `_test_channel_selection.py` for target selection and filesystem helpers
- `_test_channel_result.py` for channel result assembly
"""

from __future__ import annotations

from ._test_channel_drift import (
    _build_drift_context,
    _check_contract_drift,
    _check_single_file_contract_drift,
    _check_stale_test_symbols,
    _classify_failure,
    _classify_test_failure,
    _collect_test_findings,
    _emit_drift_summary,
)
from ._test_channel_execution import (
    _check_coverage_threshold,
    _evaluate_coverage_context,
    _parse_coverage_settings,
    _run_selected_tests,
)
from ._test_channel_models import CoverageEvaluation, TestChannelContext
from ._test_channel_result import _build_channel_result, _compute_severity
from ._test_channel_selection import (
    _check_missing_tests,
    _discover_fallback_test_targets,
    _has_test,
    _is_source_file,
    _no_test_files_exist,
    _select_tests_to_run,
)

__all__ = [
    "CoverageEvaluation",
    "TestChannelContext",
    "_build_channel_result",
    "_build_drift_context",
    "_check_contract_drift",
    "_check_coverage_threshold",
    "_check_missing_tests",
    "_check_single_file_contract_drift",
    "_check_stale_test_symbols",
    "_classify_failure",
    "_classify_test_failure",
    "_collect_test_findings",
    "_compute_severity",
    "_discover_fallback_test_targets",
    "_emit_drift_summary",
    "_evaluate_coverage_context",
    "_has_test",
    "_is_source_file",
    "_no_test_files_exist",
    "_parse_coverage_settings",
    "_run_selected_tests",
    "_select_tests_to_run",
]
