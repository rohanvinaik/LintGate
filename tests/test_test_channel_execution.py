"""Tests for lintgate.channels._test_channel_execution — coverage parsing and thresholds."""

from __future__ import annotations

from lintgate.channels._test_channel_execution import (
    _check_coverage_threshold,
    _evaluate_coverage_context,
    _parse_coverage_settings,
)
from lintgate.channels._test_types import TestRunResult
from lintgate.types import LintIssue  # noqa: TC001

# ── _parse_coverage_settings ─────────────────────────────────────────


class TestParseCoverageSettings:
    """Tests for parsing coverage config into flat dict."""

    def test_defaults_with_empty_settings(self) -> None:
        result = _parse_coverage_settings({}, "hook")
        assert result["threshold"] is None
        assert result["source_packages"] == ["lintgate", "mcp_tools"]
        assert result["symbol_enabled"] is False
        assert result["measure"] is False

    def test_threshold_parsed_as_float(self) -> None:
        result = _parse_coverage_settings({"coverage_threshold": "80"}, "ci")
        assert result["threshold"] == 80.0
        assert result["measure"] is True

    def test_threshold_none_when_invalid(self) -> None:
        result = _parse_coverage_settings({"coverage_threshold": "not-a-number"}, "ci")
        assert result["threshold"] is None

    def test_source_packages_from_list(self) -> None:
        result = _parse_coverage_settings({"source_packages": ["myapp", "mylib"]}, "hook")
        assert result["source_packages"] == ["myapp", "mylib"]

    def test_source_packages_from_string(self) -> None:
        result = _parse_coverage_settings({"source_packages": "myapp"}, "hook")
        assert result["source_packages"] == ["myapp"]

    def test_source_packages_empty_list_gets_defaults(self) -> None:
        result = _parse_coverage_settings({"source_packages": []}, "hook")
        assert result["source_packages"] == ["lintgate", "mcp_tools"]

    def test_symbol_enabled_from_dict(self) -> None:
        result = _parse_coverage_settings({"symbol_coverage": {"enabled": True}}, "mcp")
        assert result["symbol_enabled"] is True
        assert result["measure"] is True

    def test_measure_false_on_hook_surface(self) -> None:
        result = _parse_coverage_settings({"coverage_threshold": "80"}, "hook")
        assert result["measure"] is False


# ── _evaluate_coverage_context ───────────────────────────────────────


class TestEvaluateCoverageContext:
    """Tests for coverage evaluation logic."""

    def test_no_tests_returns_unknown(self) -> None:
        result = _evaluate_coverage_context(
            tests_to_run=[],
            impacted_tests=[],
            test_result=None,
            cov_cfg={"measure": False, "threshold": None},
        )
        assert result.targets_mode == "unknown"
        assert result.is_partial_run is False
        assert result.coverage_pct is None
        assert result.coverage_ok is True

    def test_impacted_tests_matched(self) -> None:
        tests = ["test_a.py", "test_b.py"]
        result = _evaluate_coverage_context(
            tests_to_run=tests,
            impacted_tests=tests,
            test_result=None,
            cov_cfg={"measure": False, "threshold": None},
        )
        assert result.targets_mode == "impacted"
        assert result.is_partial_run is True

    def test_fallback_when_tests_differ(self) -> None:
        result = _evaluate_coverage_context(
            tests_to_run=["test_a.py"],
            impacted_tests=["test_b.py"],
            test_result=None,
            cov_cfg={"measure": False, "threshold": None},
        )
        assert result.targets_mode == "fallback"
        assert result.is_partial_run is False

    def test_coverage_pct_from_result(self) -> None:
        tr = TestRunResult(passed=5, coverage_pct=85.5)
        result = _evaluate_coverage_context(
            tests_to_run=["t.py"],
            impacted_tests=[],
            test_result=tr,
            cov_cfg={"measure": True, "threshold": 80.0},
        )
        assert result.coverage_pct == 85.5
        assert result.coverage_ok is True

    def test_coverage_below_threshold(self) -> None:
        tr = TestRunResult(passed=5, coverage_pct=50.0)
        result = _evaluate_coverage_context(
            tests_to_run=["t.py"],
            impacted_tests=[],
            test_result=tr,
            cov_cfg={"measure": True, "threshold": 80.0},
        )
        assert result.coverage_ok is False


# ── _check_coverage_threshold ────────────────────────────────────────


class TestCheckCoverageThreshold:
    """Tests for threshold finding emission."""

    def test_no_finding_when_coverage_ok(self) -> None:
        tr = TestRunResult(passed=10, coverage_pct=90.0)
        findings: list[LintIssue] = []
        _check_coverage_threshold(tr, True, 80.0, findings)
        assert findings == []

    def test_finding_when_below_threshold(self) -> None:
        tr = TestRunResult(passed=10, coverage_pct=60.0)
        findings: list[LintIssue] = []
        _check_coverage_threshold(tr, True, 80.0, findings)
        assert len(findings) == 1
        assert findings[0].kind == "coverage_below_threshold"
        assert findings[0].severity == "warning"
        assert "60.0%" in findings[0].message
        assert "80.0%" in findings[0].message

    def test_no_finding_when_measure_false(self) -> None:
        tr = TestRunResult(passed=10, coverage_pct=10.0)
        findings: list[LintIssue] = []
        _check_coverage_threshold(tr, False, 80.0, findings)
        assert findings == []

    def test_no_finding_when_threshold_none(self) -> None:
        tr = TestRunResult(passed=10, coverage_pct=10.0)
        findings: list[LintIssue] = []
        _check_coverage_threshold(tr, True, None, findings)
        assert findings == []

    def test_no_finding_when_no_result(self) -> None:
        findings: list[LintIssue] = []
        _check_coverage_threshold(None, True, 80.0, findings)
        assert findings == []
