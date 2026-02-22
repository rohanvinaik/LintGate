"""Tests for test channel coverage — targeting uncovered symbols.

Covers:
- TestRunResult and TestFailure dataclass defaults and construction
- TestChannel.should_run with various event types
- find_impacted_tests with various file layouts
- _parse_pytest_output with various pytest output formats
- _parse_coverage with XML primary and terminal fallback
- _is_source_file with test files, __init__.py, setup.py, conftest.py, normal source
- _has_test delegate to find_impacted_tests
- _check_missing_tests with files that have/don't have tests
- _parse_coverage_settings with various channel settings
- _collect_test_findings with timeouts and failures
- _check_coverage_threshold with below/above threshold and None values
- _filter_to_source_packages filtering behavior
- _compute_severity with blocking, warning, informational, and no findings
- _discover_fallback_test_targets with tests/ dir, test/ dir, and root-level test files
- _select_tests_to_run with impacted tests, no impacted with symbol gate, no impacted without gate
- _emit_symbol_findings converting symbol gate results to findings
- _run_symbol_gate conditional execution
- _build_channel_result assembly
- run_tests with mocked subprocess
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.channels.test_channel import (
    TestChannel,
    TestFailure,
    TestRunResult,
    _build_channel_result,
    _check_coverage_threshold,
    _check_missing_tests,
    _collect_test_findings,
    _compute_severity,
    _discover_fallback_test_targets,
    _emit_symbol_findings,
    _filter_to_source_packages,
    _has_test,
    _is_source_file,
    _parse_coverage,
    _parse_coverage_settings,
    _parse_pytest_output,
    _run_symbol_gate,
    _select_tests_to_run,
    find_impacted_tests,
    run_tests,
)
from lintgate.controlplane.types import (
    ControlPlaneConfig,
    RepairAction,
    SupervisionEvent,
)
from lintgate.types import ChangeClassification, LintIssue

# ── TestRunResult and TestFailure dataclass defaults ────────────────────


class TestTestRunResultDefaults:
    """Verify TestRunResult dataclass default field values."""

    def test_default_construction(self) -> None:
        r = TestRunResult()
        assert r.passed == 0
        assert r.failed == 0
        assert r.errors == 0
        assert r.skipped == 0
        assert r.failures == []
        assert r.stdout == ""
        assert r.timed_out is False
        assert r.coverage_pct is None
        assert r.coverage_json_path is None
        assert r.coverage_json_ephemeral is False

    def test_custom_values(self) -> None:
        failures = [TestFailure(test_name="test_a", message="boom")]
        r = TestRunResult(
            passed=5,
            failed=2,
            errors=1,
            skipped=3,
            failures=failures,
            stdout="output",
            timed_out=True,
            coverage_pct=85.0,
            coverage_json_path="/tmp/cov.json",
            coverage_json_ephemeral=True,
        )
        assert r.passed == 5
        assert r.failed == 2
        assert r.errors == 1
        assert r.skipped == 3
        assert r.failures == failures
        assert r.stdout == "output"
        assert r.timed_out is True
        assert r.coverage_pct == 85.0
        assert r.coverage_json_path == "/tmp/cov.json"
        assert r.coverage_json_ephemeral is True


class TestTestFailureDefaults:
    """Verify TestFailure dataclass default field values."""

    def test_default_construction(self) -> None:
        f = TestFailure()
        assert f.test_name == ""
        assert f.file is None
        assert f.line is None
        assert f.message == ""

    def test_custom_values(self) -> None:
        f = TestFailure(
            test_name="test_foo",
            file="tests/test_foo.py",
            line=42,
            message="assertion failed",
        )
        assert f.test_name == "test_foo"
        assert f.file == "tests/test_foo.py"
        assert f.line == 42
        assert f.message == "assertion failed"


# ── TestChannel.should_run ──────────────────────────────────────────────


class TestShouldRun:
    """Verify TestChannel.should_run logic for different event types."""

    def test_mcp_surface_always_runs(self) -> None:
        event = SupervisionEvent(surface="mcp", change_classification=None)
        assert TestChannel().should_run(event, ControlPlaneConfig()) is True

    def test_no_classification_returns_false(self) -> None:
        event = SupervisionEvent(surface="hook", change_classification=None)
        assert TestChannel().should_run(event, ControlPlaneConfig()) is False

    def test_logic_change_runs(self) -> None:
        cc = ChangeClassification(change_kind="logic")
        event = SupervisionEvent(surface="hook", change_classification=cc)
        assert TestChannel().should_run(event, ControlPlaneConfig()) is True

    def test_structural_change_runs(self) -> None:
        cc = ChangeClassification(change_kind="structural")
        event = SupervisionEvent(surface="hook", change_classification=cc)
        assert TestChannel().should_run(event, ControlPlaneConfig()) is True

    def test_test_change_runs(self) -> None:
        cc = ChangeClassification(change_kind="test")
        event = SupervisionEvent(surface="hook", change_classification=cc)
        assert TestChannel().should_run(event, ControlPlaneConfig()) is True

    def test_config_change_does_not_run(self) -> None:
        cc = ChangeClassification(change_kind="config")
        event = SupervisionEvent(surface="hook", change_classification=cc)
        assert TestChannel().should_run(event, ControlPlaneConfig()) is False

    def test_docs_change_does_not_run(self) -> None:
        cc = ChangeClassification(change_kind="docs")
        event = SupervisionEvent(surface="hook", change_classification=cc)
        assert TestChannel().should_run(event, ControlPlaneConfig()) is False

    def test_import_change_does_not_run(self) -> None:
        cc = ChangeClassification(change_kind="import")
        event = SupervisionEvent(surface="hook", change_classification=cc)
        assert TestChannel().should_run(event, ControlPlaneConfig()) is False


# ── _is_source_file ─────────────────────────────────────────────────────


class TestIsSourceFile:
    """Verify _is_source_file with various file types."""

    def test_normal_source_file(self, tmp_path: Path) -> None:
        assert _is_source_file(str(tmp_path / "module.py"), str(tmp_path)) is True

    def test_test_file_excluded(self, tmp_path: Path) -> None:
        assert _is_source_file(str(tmp_path / "test_module.py"), str(tmp_path)) is False

    def test_conftest_excluded(self, tmp_path: Path) -> None:
        assert _is_source_file(str(tmp_path / "conftest.py"), str(tmp_path)) is False

    def test_setup_excluded(self, tmp_path: Path) -> None:
        assert _is_source_file(str(tmp_path / "setup.py"), str(tmp_path)) is False

    def test_dunder_init_excluded(self, tmp_path: Path) -> None:
        assert _is_source_file(str(tmp_path / "__init__.py"), str(tmp_path)) is False

    def test_non_python_excluded(self, tmp_path: Path) -> None:
        assert _is_source_file(str(tmp_path / "readme.md"), str(tmp_path)) is False

    def test_yaml_excluded(self, tmp_path: Path) -> None:
        assert _is_source_file(str(tmp_path / "config.yaml"), str(tmp_path)) is False

    def test_nested_source_file(self, tmp_path: Path) -> None:
        assert _is_source_file(str(tmp_path / "pkg" / "core.py"), str(tmp_path)) is True


# ── _has_test ───────────────────────────────────────────────────────────


class TestHasTest:
    """Verify _has_test delegates to find_impacted_tests."""

    def test_source_with_test(self, tmp_path: Path) -> None:
        (tmp_path / "module.py").write_text("x = 1")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_module.py").write_text("def test_x(): pass")
        assert _has_test(str(tmp_path / "module.py"), str(tmp_path)) is True

    def test_source_without_test(self, tmp_path: Path) -> None:
        (tmp_path / "orphan.py").write_text("x = 1")
        assert _has_test(str(tmp_path / "orphan.py"), str(tmp_path)) is False


# ── find_impacted_tests ─────────────────────────────────────────────────


class TestFindImpactedTests:
    """Verify find_impacted_tests with various file layouts."""

    def test_conftest_included_directly(self, tmp_path: Path) -> None:
        conftest = tmp_path / "conftest.py"
        conftest.write_text("import pytest")
        result = find_impacted_tests([str(conftest)], str(tmp_path))
        assert str(conftest) in result

    def test_non_python_skipped(self, tmp_path: Path) -> None:
        txt = tmp_path / "notes.txt"
        txt.write_text("hello")
        result = find_impacted_tests([str(txt)], str(tmp_path))
        assert result == []

    def test_underscore_joined_name(self, tmp_path: Path) -> None:
        """Source pkg/module.py finds tests/test_pkg_module.py."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        src = pkg / "module.py"
        src.write_text("x = 1")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        joined = tests_dir / "test_pkg_module.py"
        joined.write_text("def test_x(): pass")
        result = find_impacted_tests([str(src)], str(tmp_path))
        assert str(joined) in result

    def test_mirrored_package_structure(self, tmp_path: Path) -> None:
        """Source lintgate/channels/foo.py finds tests/channels/test_foo.py."""
        src_dir = tmp_path / "lintgate" / "channels"
        src_dir.mkdir(parents=True)
        src = src_dir / "foo.py"
        src.write_text("x = 1")
        test_dir = tmp_path / "tests" / "channels"
        test_dir.mkdir(parents=True)
        test_file = test_dir / "test_foo.py"
        test_file.write_text("def test_x(): pass")
        result = find_impacted_tests([str(src)], str(tmp_path))
        assert str(test_file) in result

    def test_deduplication_across_patterns(self, tmp_path: Path) -> None:
        """Same test file found via multiple patterns appears only once."""
        (tmp_path / "app.py").write_text("x = 1")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_app.py"
        test_file.write_text("def test_x(): pass")
        result = find_impacted_tests(
            [str(tmp_path / "app.py"), str(tmp_path / "app.py")],
            str(tmp_path),
        )
        assert result.count(str(test_file)) == 1

    def test_empty_changed_files(self, tmp_path: Path) -> None:
        result = find_impacted_tests([], str(tmp_path))
        assert result == []

    def test_test_in_same_dir(self, tmp_path: Path) -> None:
        (tmp_path / "helper.py").write_text("x = 1")
        test_file = tmp_path / "test_helper.py"
        test_file.write_text("def test_x(): pass")
        result = find_impacted_tests([str(tmp_path / "helper.py")], str(tmp_path))
        assert str(test_file) in result

    def test_test_in_test_directory(self, tmp_path: Path) -> None:
        """Source file finds test in test/ (not tests/) directory."""
        (tmp_path / "core.py").write_text("x = 1")
        test_dir = tmp_path / "test"
        test_dir.mkdir()
        test_file = test_dir / "test_core.py"
        test_file.write_text("def test_x(): pass")
        result = find_impacted_tests([str(tmp_path / "core.py")], str(tmp_path))
        assert str(test_file) in result


# ── _parse_pytest_output ────────────────────────────────────────────────


class TestParsePytestOutput:
    """Verify _parse_pytest_output with various pytest output formats."""

    def test_passed_only(self) -> None:
        result = _parse_pytest_output("10 passed in 0.5s", "", 0)
        assert result.passed == 10
        assert result.failed == 0
        assert result.errors == 0
        assert result.skipped == 0

    def test_failed_only(self) -> None:
        result = _parse_pytest_output("3 failed in 0.1s", "", 1)
        assert result.failed == 3
        assert result.passed == 0

    def test_errors_only(self) -> None:
        result = _parse_pytest_output("2 error in 0.1s", "", 1)
        assert result.errors == 2

    def test_skipped_only(self) -> None:
        result = _parse_pytest_output("4 skipped in 0.1s", "", 0)
        assert result.skipped == 4

    def test_mixed_results(self) -> None:
        stdout = "5 passed, 2 failed, 1 error, 3 skipped in 0.5s"
        result = _parse_pytest_output(stdout, "", 1)
        assert result.passed == 5
        assert result.failed == 2
        assert result.errors == 1
        assert result.skipped == 3

    def test_failure_lines_parsed(self) -> None:
        stdout = (
            "FAILED tests/test_foo.py::test_bar - AssertionError: expected 1\n1 failed in 0.05s"
        )
        result = _parse_pytest_output(stdout, "", 1)
        assert len(result.failures) == 1
        assert result.failures[0].test_name == "test_bar"
        assert result.failures[0].file == "tests/test_foo.py"
        assert "AssertionError" in result.failures[0].message

    def test_failure_line_without_message(self) -> None:
        stdout = "FAILED tests/test_foo.py::test_bar\n1 failed in 0.05s"
        result = _parse_pytest_output(stdout, "", 1)
        assert len(result.failures) == 1
        assert result.failures[0].test_name == "test_bar"
        assert "test_bar" in result.failures[0].message

    def test_empty_output(self) -> None:
        result = _parse_pytest_output("", "", 0)
        assert result.passed == 0
        assert result.failed == 0
        assert result.failures == []

    def test_counts_from_stderr(self) -> None:
        result = _parse_pytest_output("", "3 passed in 0.1s", 0)
        assert result.passed == 3

    def test_stdout_preserved(self) -> None:
        stdout = "some output\n5 passed in 0.1s"
        result = _parse_pytest_output(stdout, "", 0)
        assert result.stdout == stdout


# ── _parse_coverage ─────────────────────────────────────────────────────


class TestParseCoverage:
    """Verify _parse_coverage XML parsing and terminal fallback."""

    def test_xml_primary_path(self, tmp_path: Path) -> None:
        xml_path = tmp_path / "coverage.xml"
        xml_path.write_text('<coverage line-rate="0.795">\n</coverage>')
        result = _parse_coverage(str(xml_path), "")
        assert result == 79.5

    def test_xml_100_percent(self, tmp_path: Path) -> None:
        xml_path = tmp_path / "coverage.xml"
        xml_path.write_text('<coverage line-rate="1.0">\n</coverage>')
        result = _parse_coverage(str(xml_path), "")
        assert result == 100.0

    def test_xml_zero_percent(self, tmp_path: Path) -> None:
        xml_path = tmp_path / "coverage.xml"
        xml_path.write_text('<coverage line-rate="0.0">\n</coverage>')
        result = _parse_coverage(str(xml_path), "")
        assert result == 0.0

    def test_terminal_fallback(self) -> None:
        terminal = "TOTAL                  500    100    80%\n"
        result = _parse_coverage("/nonexistent/coverage.xml", terminal)
        assert result == 80.0

    def test_no_coverage_data_returns_none(self) -> None:
        result = _parse_coverage("/nonexistent/coverage.xml", "no data")
        assert result is None

    def test_malformed_xml_falls_back(self, tmp_path: Path) -> None:
        xml_path = tmp_path / "coverage.xml"
        xml_path.write_text("not xml at all")
        terminal = "TOTAL                  500    100    80%\n"
        result = _parse_coverage(str(xml_path), terminal)
        assert result == 80.0

    def test_xml_single_quotes(self, tmp_path: Path) -> None:
        xml_path = tmp_path / "coverage.xml"
        xml_path.write_text("<coverage line-rate='0.55'>\n</coverage>")
        result = _parse_coverage(str(xml_path), "")
        assert result == 55.0


# ── _compute_severity ───────────────────────────────────────────────────


class TestComputeSeverity:
    """Verify _compute_severity across all severity levels."""

    def test_no_findings_returns_none(self) -> None:
        assert _compute_severity([]) == "none"

    def test_informational_only(self) -> None:
        findings = [
            LintIssue(linter="x", kind="y", message="z", severity="informational"),
        ]
        assert _compute_severity(findings) == "informational"

    def test_warning_trumps_informational(self) -> None:
        findings = [
            LintIssue(linter="x", kind="y", message="z", severity="informational"),
            LintIssue(linter="x", kind="y", message="z", severity="warning"),
        ]
        assert _compute_severity(findings) == "warning"

    def test_blocking_trumps_all(self) -> None:
        findings = [
            LintIssue(linter="x", kind="y", message="z", severity="informational"),
            LintIssue(linter="x", kind="y", message="z", severity="warning"),
            LintIssue(linter="x", kind="y", message="z", severity="blocking"),
        ]
        assert _compute_severity(findings) == "blocking"


# ── _parse_coverage_settings ────────────────────────────────────────────


class TestParseCoverageSettings:
    """Verify _parse_coverage_settings with various inputs."""

    def test_empty_settings_hook(self) -> None:
        cfg = _parse_coverage_settings({}, "hook")
        assert cfg["threshold"] is None
        assert cfg["measure"] is False
        assert cfg["source_packages"] == ["lintgate", "mcp_tools"]
        assert cfg["symbol_enabled"] is False

    def test_threshold_set_mcp(self) -> None:
        cfg = _parse_coverage_settings({"coverage_threshold": 80}, "mcp")
        assert cfg["threshold"] == 80.0
        assert cfg["measure"] is True

    def test_threshold_set_ci(self) -> None:
        cfg = _parse_coverage_settings({"coverage_threshold": 75}, "ci")
        assert cfg["threshold"] == 75.0
        assert cfg["measure"] is True

    def test_threshold_set_hook_no_measure(self) -> None:
        cfg = _parse_coverage_settings({"coverage_threshold": 80}, "hook")
        assert cfg["threshold"] == 80.0
        assert cfg["measure"] is False

    def test_invalid_threshold_ignored(self) -> None:
        cfg = _parse_coverage_settings({"coverage_threshold": "not-a-number"}, "mcp")
        assert cfg["threshold"] is None
        assert cfg["measure"] is False

    def test_source_packages_list(self) -> None:
        cfg = _parse_coverage_settings(
            {"source_packages": ["mylib", "utils"]},
            "hook",
        )
        assert cfg["source_packages"] == ["mylib", "utils"]

    def test_source_packages_string(self) -> None:
        cfg = _parse_coverage_settings({"source_packages": "mylib"}, "hook")
        assert cfg["source_packages"] == ["mylib"]

    def test_source_packages_empty_string_uses_default(self) -> None:
        cfg = _parse_coverage_settings({"source_packages": "  "}, "hook")
        assert cfg["source_packages"] == ["lintgate", "mcp_tools"]

    def test_source_packages_empty_list_uses_default(self) -> None:
        cfg = _parse_coverage_settings({"source_packages": []}, "hook")
        assert cfg["source_packages"] == ["lintgate", "mcp_tools"]

    def test_symbol_coverage_enabled(self) -> None:
        cfg = _parse_coverage_settings(
            {"symbol_coverage": {"enabled": True}},
            "mcp",
        )
        assert cfg["symbol_enabled"] is True
        assert cfg["measure"] is True

    def test_symbol_coverage_disabled(self) -> None:
        cfg = _parse_coverage_settings(
            {"symbol_coverage": {"enabled": False}},
            "mcp",
        )
        assert cfg["symbol_enabled"] is False

    def test_symbol_coverage_not_dict(self) -> None:
        cfg = _parse_coverage_settings({"symbol_coverage": "yes"}, "mcp")
        assert cfg["symbol_enabled"] is False


# ── _filter_to_source_packages ──────────────────────────────────────────


class TestFilterToSourcePackages:
    """Verify _filter_to_source_packages filtering behavior."""

    def test_filters_to_matching_packages(self, tmp_path: Path) -> None:
        root = str(tmp_path)
        files = [
            str(tmp_path / "lintgate" / "foo.py"),
            str(tmp_path / "tests" / "test_foo.py"),
            str(tmp_path / "mcp_tools" / "bar.py"),
        ]
        result = _filter_to_source_packages(files, ["lintgate", "mcp_tools"], root)
        assert str(tmp_path / "lintgate" / "foo.py") in result
        assert str(tmp_path / "mcp_tools" / "bar.py") in result
        assert str(tmp_path / "tests" / "test_foo.py") not in result

    def test_empty_packages_returns_all(self, tmp_path: Path) -> None:
        root = str(tmp_path)
        files = [str(tmp_path / "a.py"), str(tmp_path / "b.py")]
        result = _filter_to_source_packages(files, [], root)
        assert result == files

    def test_no_matching_files(self, tmp_path: Path) -> None:
        root = str(tmp_path)
        files = [str(tmp_path / "tests" / "test_foo.py")]
        result = _filter_to_source_packages(files, ["lintgate"], root)
        assert result == []

    def test_exact_package_name_match(self, tmp_path: Path) -> None:
        """File at exactly the package directory level is included."""
        root = str(tmp_path)
        # A file path that is exactly the package name (edge case for single-file packages)
        files = [str(tmp_path / "lintgate")]
        result = _filter_to_source_packages(files, ["lintgate"], root)
        assert str(tmp_path / "lintgate") in result


# ── _check_coverage_threshold ───────────────────────────────────────────


class TestCheckCoverageThreshold:
    """Verify _check_coverage_threshold emits findings correctly."""

    def test_below_threshold_emits_finding(self) -> None:
        findings: list[LintIssue] = []
        result = TestRunResult(coverage_pct=60.0)
        _check_coverage_threshold(result, True, 80.0, findings)
        assert len(findings) == 1
        assert findings[0].kind == "coverage_below_threshold"
        assert "60.0%" in findings[0].message
        assert "80.0%" in findings[0].message

    def test_above_threshold_no_finding(self) -> None:
        findings: list[LintIssue] = []
        result = TestRunResult(coverage_pct=90.0)
        _check_coverage_threshold(result, True, 80.0, findings)
        assert len(findings) == 0

    def test_equal_threshold_no_finding(self) -> None:
        findings: list[LintIssue] = []
        result = TestRunResult(coverage_pct=80.0)
        _check_coverage_threshold(result, True, 80.0, findings)
        assert len(findings) == 0

    def test_measure_false_no_finding(self) -> None:
        findings: list[LintIssue] = []
        result = TestRunResult(coverage_pct=50.0)
        _check_coverage_threshold(result, False, 80.0, findings)
        assert len(findings) == 0

    def test_none_result_no_finding(self) -> None:
        findings: list[LintIssue] = []
        _check_coverage_threshold(None, True, 80.0, findings)
        assert len(findings) == 0

    def test_none_coverage_pct_no_finding(self) -> None:
        findings: list[LintIssue] = []
        result = TestRunResult(coverage_pct=None)
        _check_coverage_threshold(result, True, 80.0, findings)
        assert len(findings) == 0

    def test_none_threshold_no_finding(self) -> None:
        findings: list[LintIssue] = []
        result = TestRunResult(coverage_pct=50.0)
        _check_coverage_threshold(result, True, None, findings)
        assert len(findings) == 0


# ── _collect_test_findings ──────────────────────────────────────────────


class TestCollectTestFindings:
    """Verify _collect_test_findings converts test results to findings."""

    def test_timeout_emits_finding(self) -> None:
        findings: list[LintIssue] = []
        result = TestRunResult(timed_out=True)
        _collect_test_findings(result, 5000, findings)
        assert len(findings) == 1
        assert findings[0].kind == "test_timeout"
        assert "5000ms" in findings[0].message

    def test_failures_emitted(self) -> None:
        findings: list[LintIssue] = []
        failures = [
            TestFailure(test_name="test_a", file="a.py", line=10, message="boom"),
            TestFailure(test_name="test_b", file="b.py", line=20, message="crash"),
        ]
        result = TestRunResult(failures=failures)
        _collect_test_findings(result, 5000, findings)
        assert len(findings) == 2
        assert findings[0].kind == "test_failure"
        assert findings[0].message == "boom"
        assert findings[0].file == "a.py"
        assert findings[0].line == 10

    def test_no_issues_no_findings(self) -> None:
        findings: list[LintIssue] = []
        result = TestRunResult(passed=5)
        _collect_test_findings(result, 5000, findings)
        assert len(findings) == 0

    def test_timeout_plus_failures(self) -> None:
        findings: list[LintIssue] = []
        failures = [TestFailure(test_name="t", message="fail")]
        result = TestRunResult(timed_out=True, failures=failures)
        _collect_test_findings(result, 3000, findings)
        assert len(findings) == 2
        kinds = {f.kind for f in findings}
        assert "test_timeout" in kinds
        assert "test_failure" in kinds


# ── _discover_fallback_test_targets ─────────────────────────────────────


class TestDiscoverFallbackTestTargets:
    """Verify _discover_fallback_test_targets with various directory layouts."""

    def test_tests_directory(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        targets = _discover_fallback_test_targets(str(tmp_path))
        assert str(tmp_path / "tests") in targets

    def test_test_directory(self, tmp_path: Path) -> None:
        (tmp_path / "test").mkdir()
        targets = _discover_fallback_test_targets(str(tmp_path))
        assert str(tmp_path / "test") in targets

    def test_both_tests_and_test(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "test").mkdir()
        targets = _discover_fallback_test_targets(str(tmp_path))
        assert len(targets) == 2

    def test_root_level_test_files(self, tmp_path: Path) -> None:
        (tmp_path / "test_app.py").write_text("def test_x(): pass")
        (tmp_path / "test_utils.py").write_text("def test_y(): pass")
        targets = _discover_fallback_test_targets(str(tmp_path))
        assert len(targets) == 2
        names = [os.path.basename(t) for t in targets]
        assert "test_app.py" in names
        assert "test_utils.py" in names

    def test_no_tests_at_all(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x = 1")
        targets = _discover_fallback_test_targets(str(tmp_path))
        assert targets == []

    def test_tests_dir_preferred_over_root_files(self, tmp_path: Path) -> None:
        """When tests/ exists, root-level test_*.py are NOT included."""
        (tmp_path / "tests").mkdir()
        (tmp_path / "test_root.py").write_text("def test_x(): pass")
        targets = _discover_fallback_test_targets(str(tmp_path))
        assert str(tmp_path / "tests") in targets
        assert str(tmp_path / "test_root.py") not in targets


# ── _select_tests_to_run ───────────────────────────────────────────────


class TestSelectTestsToRun:
    """Verify _select_tests_to_run selection logic."""

    def test_impacted_tests_returned_directly(self, tmp_path: Path) -> None:
        impacted = ["test_a.py", "test_b.py"]
        findings: list[LintIssue] = []
        result = _select_tests_to_run(impacted, str(tmp_path), None, "hook", findings)
        assert result == impacted

    def test_no_impacted_no_symbol_gate_returns_empty(self, tmp_path: Path) -> None:
        findings: list[LintIssue] = []
        cfg = {"symbol_enabled": False}
        result = _select_tests_to_run([], str(tmp_path), cfg, "mcp", findings)
        assert result == []

    def test_no_impacted_symbol_gate_mcp_fallback(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        findings: list[LintIssue] = []
        cfg = {"symbol_enabled": True}
        result = _select_tests_to_run([], str(tmp_path), cfg, "mcp", findings)
        assert str(tmp_path / "tests") in result
        assert any(f.kind == "symbol_gate_fallback" for f in findings)

    def test_no_impacted_symbol_gate_ci_fallback(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        findings: list[LintIssue] = []
        cfg = {"symbol_enabled": True}
        result = _select_tests_to_run([], str(tmp_path), cfg, "ci", findings)
        assert str(tmp_path / "tests") in result

    def test_no_impacted_symbol_gate_hook_returns_empty(self, tmp_path: Path) -> None:
        """Symbol gate fallback only for mcp/ci, not hook."""
        (tmp_path / "tests").mkdir()
        findings: list[LintIssue] = []
        cfg = {"symbol_enabled": True}
        result = _select_tests_to_run([], str(tmp_path), cfg, "hook", findings)
        assert result == []

    def test_none_cov_cfg_returns_empty(self, tmp_path: Path) -> None:
        findings: list[LintIssue] = []
        result = _select_tests_to_run([], str(tmp_path), None, "mcp", findings)
        assert result == []

    def test_no_fallback_targets_no_finding(self, tmp_path: Path) -> None:
        """When no tests dir and no test files, no fallback finding emitted."""
        findings: list[LintIssue] = []
        cfg = {"symbol_enabled": True}
        result = _select_tests_to_run([], str(tmp_path), cfg, "mcp", findings)
        assert result == []
        assert not any(f.kind == "symbol_gate_fallback" for f in findings)


# ── _check_missing_tests ───────────────────────────────────────────────


class TestCheckMissingTests:
    """Verify _check_missing_tests generates findings and repairs."""

    def test_source_without_test_produces_finding(self, tmp_path: Path) -> None:
        src = tmp_path / "module.py"
        src.write_text("x = 1")
        findings: list[LintIssue] = []
        repairs: list[RepairAction] = []
        _check_missing_tests([str(src)], str(tmp_path), findings, repairs)
        assert len(findings) == 1
        assert findings[0].kind == "missing_test"
        assert "module.py" in findings[0].message

    def test_source_with_test_no_finding(self, tmp_path: Path) -> None:
        src = tmp_path / "module.py"
        src.write_text("x = 1")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_module.py").write_text("def test_x(): pass")
        findings: list[LintIssue] = []
        repairs: list[RepairAction] = []
        _check_missing_tests([str(src)], str(tmp_path), findings, repairs)
        assert len(findings) == 0

    def test_test_file_skipped(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test_module.py"
        test_file.write_text("def test_x(): pass")
        findings: list[LintIssue] = []
        repairs: list[RepairAction] = []
        _check_missing_tests([str(test_file)], str(tmp_path), findings, repairs)
        assert len(findings) == 0

    def test_non_python_skipped(self, tmp_path: Path) -> None:
        md_file = tmp_path / "readme.md"
        md_file.write_text("# hello")
        findings: list[LintIssue] = []
        repairs: list[RepairAction] = []
        _check_missing_tests([str(md_file)], str(tmp_path), findings, repairs)
        assert len(findings) == 0


# ── _emit_symbol_findings ───────────────────────────────────────────────


class TestEmitSymbolFindings:
    """Verify _emit_symbol_findings converts gate results to LintIssue list."""

    def _make_symbol_span(
        self,
        name: str = "my_func",
        file: str = "mod.py",
        start_line: int = 1,
        end_line: int = 10,
    ) -> Any:
        """Create a mock SymbolSpan."""
        span = MagicMock()
        span.name = name
        span.file = file
        span.start_line = start_line
        span.end_line = end_line
        span.symbol_key = f"{file}::{name}"
        return span

    def test_uncovered_symbol_emits_blocking_finding(self) -> None:
        span = self._make_symbol_span()
        sr = MagicMock()
        sr.covered = False
        sr.symbol = span
        sr.missing_lines = [3, 5, 7]
        sr.missing_branches = []
        sr.total_lines_in_span = 10
        sr.executed_lines_in_span = 7

        gate = MagicMock()
        gate.symbol_results = [sr]
        gate.unresolved_required = []
        gate.waivers_expired = []
        gate.skipped_reasons = []

        findings: list[LintIssue] = []
        _emit_symbol_findings(gate, findings)
        assert len(findings) == 1
        assert findings[0].kind == "symbol_uncovered"
        assert findings[0].severity == "blocking"
        assert "my_func" in findings[0].message
        assert "3, 5, 7" in findings[0].message

    def test_covered_symbol_no_finding(self) -> None:
        sr = MagicMock()
        sr.covered = True

        gate = MagicMock()
        gate.symbol_results = [sr]
        gate.unresolved_required = []
        gate.waivers_expired = []
        gate.skipped_reasons = []

        findings: list[LintIssue] = []
        _emit_symbol_findings(gate, findings)
        assert len(findings) == 0

    def test_unresolved_required_emits_finding(self) -> None:
        gate = MagicMock()
        gate.symbol_results = []
        gate.unresolved_required = ["mod.py::missing_func"]
        gate.waivers_expired = []
        gate.skipped_reasons = []

        findings: list[LintIssue] = []
        _emit_symbol_findings(gate, findings)
        assert len(findings) == 1
        assert findings[0].kind == "unresolved_required_symbol"
        assert "mod.py::missing_func" in findings[0].message

    def test_expired_waiver_emits_finding(self) -> None:
        waiver = MagicMock()
        waiver.symbol = "mod.py::old_func"
        waiver.expires = "2025-01-01"

        gate = MagicMock()
        gate.symbol_results = []
        gate.unresolved_required = []
        gate.waivers_expired = [waiver]
        gate.skipped_reasons = []

        findings: list[LintIssue] = []
        _emit_symbol_findings(gate, findings)
        assert len(findings) == 1
        assert findings[0].kind == "waiver_expired"
        assert "2025-01-01" in findings[0].message

    def test_skipped_reason_emits_finding(self) -> None:
        gate = MagicMock()
        gate.symbol_results = []
        gate.unresolved_required = []
        gate.waivers_expired = []
        gate.skipped_reasons = ["no coverage data"]

        findings: list[LintIssue] = []
        _emit_symbol_findings(gate, findings)
        assert len(findings) == 1
        assert findings[0].kind == "symbol_gate_skipped"
        assert "no coverage data" in findings[0].message


# ── _run_symbol_gate ────────────────────────────────────────────────────


class TestRunSymbolGate:
    """Verify _run_symbol_gate conditional execution."""

    def test_no_coverage_json_ci_emits_warning(self) -> None:
        findings: list[LintIssue] = []
        result = _run_symbol_gate(None, [], "/tmp", {}, "ci", findings)
        assert result is None
        assert len(findings) == 1
        assert findings[0].kind == "symbol_gate_skipped"

    def test_no_coverage_json_mcp_no_finding(self) -> None:
        findings: list[LintIssue] = []
        result = _run_symbol_gate(None, [], "/tmp", {}, "mcp", findings)
        assert result is None
        assert len(findings) == 0

    @patch("lintgate.channels.symbol_coverage.run_symbol_coverage_gate")
    def test_with_coverage_json_runs_gate(self, mock_gate: MagicMock) -> None:
        mock_result = MagicMock()
        mock_result.symbol_results = []
        mock_result.unresolved_required = []
        mock_result.waivers_expired = []
        mock_result.skipped_reasons = []
        mock_gate.return_value = mock_result

        findings: list[LintIssue] = []
        result = _run_symbol_gate(
            "/tmp/cov.json",
            ["/tmp/mod.py"],
            "/tmp",
            {},
            "mcp",
            findings,
        )
        assert result is mock_result
        mock_gate.assert_called_once()


# ── _build_channel_result ───────────────────────────────────────────────


class TestBuildChannelResult:
    """Verify _build_channel_result assembly."""

    def test_pass_with_no_findings(self) -> None:
        start = time.perf_counter()
        result = _build_channel_result(
            "tests",
            start,
            [],
            [],
            ["test_a.py"],
            None,
            {"measure": False, "threshold": None},
            None,
        )
        assert result.channel == "tests"
        assert result.status == "pass"
        assert result.severity == "none"
        assert result.metrics["impacted_tests_found"] == 1

    def test_fail_with_findings(self) -> None:
        start = time.perf_counter()
        findings = [
            LintIssue(
                linter="test_channel", kind="test_failure", message="boom", severity="warning"
            ),
        ]
        result = _build_channel_result(
            "tests",
            start,
            findings,
            [],
            [],
            None,
            {"measure": False, "threshold": None},
            None,
        )
        assert result.status == "fail"
        assert result.severity == "warning"
        assert result.metrics["test_failure_count"] == 1

    def test_coverage_metrics_included(self) -> None:
        start = time.perf_counter()
        tr = TestRunResult(coverage_pct=85.0)
        result = _build_channel_result(
            "tests",
            start,
            [],
            [],
            [],
            tr,
            {"measure": True, "threshold": 80.0},
            None,
        )
        assert result.metrics["coverage_pct"] == 85.0
        assert result.metrics["coverage_threshold"] == 80.0

    def test_symbol_gate_metrics_included(self) -> None:
        start = time.perf_counter()
        sr_covered = MagicMock()
        sr_covered.covered = True
        sr_uncovered = MagicMock()
        sr_uncovered.covered = False
        gate = MagicMock()
        gate.symbol_results = [sr_covered, sr_uncovered]
        gate.waivers_applied = [("key", MagicMock())]

        result = _build_channel_result(
            "tests",
            start,
            [],
            [],
            [],
            None,
            {"measure": False, "threshold": None},
            gate,
        )
        assert result.metrics["symbol_coverage_targets"] == 2
        assert result.metrics["symbol_coverage_passed"] == 1
        assert result.metrics["symbol_coverage_failed"] == 1
        assert result.metrics["symbol_coverage_waivers"] == 1

    def test_missing_test_count_in_metrics(self) -> None:
        start = time.perf_counter()
        findings = [
            LintIssue(
                linter="test_channel", kind="missing_test", message="m1", severity="informational"
            ),
            LintIssue(
                linter="test_channel", kind="missing_test", message="m2", severity="informational"
            ),
        ]
        result = _build_channel_result(
            "tests",
            start,
            findings,
            [],
            [],
            None,
            {"measure": False, "threshold": None},
            None,
        )
        assert result.metrics["missing_test_count"] == 2


# ── run_tests (mocked subprocess) ──────────────────────────────────────


class TestRunTestsMocked:
    """Verify run_tests with mocked subprocess.run."""

    @patch("lintgate.channels.test_channel.subprocess.run")
    def test_empty_test_files_returns_default(self, mock_run: MagicMock) -> None:
        result = run_tests([], "/tmp")
        assert result.passed == 0
        mock_run.assert_not_called()

    @patch("lintgate.channels.test_channel.subprocess.run")
    def test_timeout_returns_timed_out(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="pytest", timeout=10)
        result = run_tests(["test_a.py"], "/tmp")
        assert result.timed_out is True

    @patch("lintgate.channels.test_channel.subprocess.run")
    def test_os_error_returns_empty(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = OSError("not found")
        result = run_tests(["test_a.py"], "/tmp")
        assert result.passed == 0
        assert result.timed_out is False

    @patch("lintgate.channels.test_channel.subprocess.run")
    def test_success_parses_output(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            stdout="8 passed, 1 skipped in 0.5s",
            stderr="",
            returncode=0,
        )
        result = run_tests(["test_a.py"], "/tmp")
        assert result.passed == 8
        assert result.skipped == 1
