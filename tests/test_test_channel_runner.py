"""Tests for lintgate.channels._test_channel_runner."""

from __future__ import annotations

from lintgate.channels._test_channel_runner import (
    _parse_coverage,
    _parse_pytest_output,
    run_tests,
)
from lintgate.channels._test_types import TestRunResult


# ── _parse_pytest_output ─────────────────────────────────────────────


class TestParsePytestOutput:
    def test_all_passed(self):
        stdout = "10 passed in 1.23s\n"
        result = _parse_pytest_output(stdout, "", 0)
        assert result.passed == 10
        assert result.failed == 0
        assert result.errors == 0

    def test_mixed_results(self):
        stdout = "5 passed, 2 failed, 1 error in 3.45s\n"
        result = _parse_pytest_output(stdout, "", 1)
        assert result.passed == 5
        assert result.failed == 2
        assert result.errors == 1

    def test_with_skipped(self):
        stdout = "8 passed, 3 skipped in 2.00s\n"
        result = _parse_pytest_output(stdout, "", 0)
        assert result.passed == 8
        assert result.skipped == 3

    def test_failure_line_parsed(self):
        stdout = (
            "FAILED tests/test_foo.py::test_bar - AssertionError: expected 1, got 2\n"
            "1 failed in 0.50s\n"
        )
        result = _parse_pytest_output(stdout, "", 1)
        assert result.failed == 1
        assert len(result.failures) == 1
        assert result.failures[0].test_name == "test_bar"
        assert result.failures[0].file == "tests/test_foo.py"
        assert "expected 1" in result.failures[0].message

    def test_failure_line_no_message(self):
        stdout = "FAILED tests/test_x.py::test_y\n1 failed\n"
        result = _parse_pytest_output(stdout, "", 1)
        assert len(result.failures) == 1
        assert result.failures[0].test_name == "test_y"
        assert "test_y failed" in result.failures[0].message

    def test_empty_output(self):
        result = _parse_pytest_output("", "", 0)
        assert result.passed == 0
        assert result.failed == 0
        assert result.failures == []


# ── _parse_coverage ──────────────────────────────────────────────────


class TestParseCoverage:
    def test_xml_line_rate(self, tmp_path):
        xml = tmp_path / "coverage.xml"
        xml.write_text('<coverage line-rate="0.795">\n</coverage>')
        result = _parse_coverage(str(xml), "")
        assert result == 79.5

    def test_terminal_fallback(self, tmp_path):
        xml = tmp_path / "coverage.xml"
        # Write invalid XML so primary parse fails
        xml.write_text("not xml")
        terminal = "TOTAL    500    100    80%"
        result = _parse_coverage(str(xml), terminal)
        assert result == 80.0

    def test_returns_none_on_missing_file(self):
        result = _parse_coverage("/nonexistent/coverage.xml", "no match here")
        assert result is None


# ── run_tests ────────────────────────────────────────────────────────


class TestRunTests:
    def test_empty_file_list_returns_default(self):
        result = run_tests([], "/tmp")
        assert isinstance(result, TestRunResult)
        assert result.passed == 0
        assert result.failed == 0
        assert result.timed_out is False

    def test_nonexistent_test_file_returns_result(self, tmp_path):
        result = run_tests([str(tmp_path / "nonexistent_test.py")], str(tmp_path))
        # pytest will error on missing file but should not crash
        assert isinstance(result, TestRunResult)
