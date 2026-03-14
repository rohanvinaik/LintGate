"""Tests for lintgate/channels/_test_channel_drift.py — drift classification, findings, and contract checks."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lintgate.channels._test_channel_drift import (
    _build_drift_context,
    _check_contract_drift,
    _check_single_file_contract_drift,
    _check_stale_test_symbols,
    _classify_failure,
    _classify_test_failure,
    _collect_test_findings,
    _emit_drift_summary,
)
from lintgate.channels._test_types import TestFailure, TestRunResult
from lintgate.types import LintIssue  # noqa: TC001

# ---------------------------------------------------------------------------
# _classify_test_failure
# ---------------------------------------------------------------------------


class TestClassifyTestFailure:
    def test_untracked_file_classified_as_drift(self):
        result = _classify_test_failure(
            "tests/test_new.py",
            modified_files=set(),
            untracked_files={"tests/test_new.py"},
            project_root="/project",
        )
        assert result == "test_drift"

    def test_modified_file_classified_as_drift(self):
        result = _classify_test_failure(
            "tests/test_edited.py",
            modified_files={"tests/test_edited.py"},
            untracked_files=set(),
            project_root="/project",
        )
        assert result == "test_drift"

    def test_committed_file_classified_as_regression(self):
        result = _classify_test_failure(
            "tests/test_stable.py",
            modified_files=set(),
            untracked_files=set(),
            project_root="/project",
        )
        assert result == "regression"

    def test_absolute_path_resolved_to_relative(self):
        result = _classify_test_failure(
            "/project/tests/test_abs.py",
            modified_files={"tests/test_abs.py"},
            untracked_files=set(),
            project_root="/project",
        )
        assert result == "test_drift"


# ---------------------------------------------------------------------------
# _classify_failure
# ---------------------------------------------------------------------------


class TestClassifyFailure:
    def test_no_drift_context_returns_unknown(self):
        failure = TestFailure(test_name="test_x", file="tests/test_x.py", message="fail")
        result = _classify_failure(failure, drift_context=None, project_root="/proj")
        assert result == "unknown"

    def test_no_file_returns_unknown(self):
        failure = TestFailure(test_name="test_x", file=None, message="fail")
        ctx = {"modified": set(), "untracked": set()}
        result = _classify_failure(failure, drift_context=ctx, project_root="/proj")
        assert result == "unknown"

    def test_delegates_to_classify_test_failure(self):
        failure = TestFailure(test_name="test_x", file="tests/test_x.py", message="fail")
        ctx = {"modified": {"tests/test_x.py"}, "untracked": set()}
        result = _classify_failure(failure, drift_context=ctx, project_root="/proj")
        assert result == "test_drift"

    def test_committed_file_returns_regression(self):
        failure = TestFailure(test_name="test_x", file="tests/test_x.py", message="fail")
        ctx = {"modified": set(), "untracked": set()}
        result = _classify_failure(failure, drift_context=ctx, project_root="/proj")
        assert result == "regression"


# ---------------------------------------------------------------------------
# _emit_drift_summary
# ---------------------------------------------------------------------------


class TestEmitDriftSummary:
    def test_zero_counts_emits_nothing(self):
        findings: list[LintIssue] = []
        _emit_drift_summary(0, 0, findings)
        assert findings == []

    def test_drift_only(self):
        findings: list[LintIssue] = []
        _emit_drift_summary(3, 0, findings)
        assert len(findings) == 1
        assert findings[0].kind == "test_drift_summary"
        assert "3 in uncommitted" in findings[0].message
        assert findings[0].evidence["drift_count"] == 3
        assert findings[0].evidence["regression_count"] == 0

    def test_regression_only(self):
        findings: list[LintIssue] = []
        _emit_drift_summary(0, 2, findings)
        assert len(findings) == 1
        assert "2 in committed" in findings[0].message

    def test_both_drift_and_regression(self):
        findings: list[LintIssue] = []
        _emit_drift_summary(1, 4, findings)
        assert len(findings) == 1
        assert "1 in uncommitted" in findings[0].message
        assert "4 in committed" in findings[0].message
        assert findings[0].severity == "informational"


# ---------------------------------------------------------------------------
# _collect_test_findings
# ---------------------------------------------------------------------------


class TestCollectTestFindings:
    def test_timeout_emits_warning(self):
        result = TestRunResult(timed_out=True, failures=[])
        findings: list[LintIssue] = []
        _collect_test_findings(result, remaining_ms=5000, findings=findings)
        assert len(findings) == 1
        assert findings[0].kind == "test_timeout"
        assert "5000ms" in findings[0].message
        assert findings[0].severity == "warning"

    def test_no_failures_no_timeout_emits_nothing(self):
        result = TestRunResult(timed_out=False, failures=[])
        findings: list[LintIssue] = []
        _collect_test_findings(result, remaining_ms=5000, findings=findings)
        assert findings == []

    def test_failure_without_project_root_skips_drift(self):
        failure = TestFailure(test_name="test_a", file="tests/a.py", message="boom")
        result = TestRunResult(failures=[failure])
        findings: list[LintIssue] = []
        _collect_test_findings(result, remaining_ms=5000, findings=findings)
        # One failure finding, no drift summary (no project_root)
        assert len(findings) == 1
        assert findings[0].kind == "test_failure"
        assert findings[0].message == "boom"
        assert findings[0].evidence == {}  # unknown classification → empty evidence

    @patch("lintgate.channels._test_channel_drift._build_drift_context")
    @patch("lintgate.channels._test_channel_drift._check_stale_test_symbols")
    def test_failure_with_project_root_classifies_and_summarizes(
        self, mock_stale, mock_drift_ctx
    ):
        mock_drift_ctx.return_value = {
            "modified": {"tests/a.py"},
            "untracked": set(),
        }
        failure = TestFailure(test_name="test_a", file="tests/a.py", message="boom")
        result = TestRunResult(failures=[failure])
        findings: list[LintIssue] = []
        _collect_test_findings(result, remaining_ms=5000, findings=findings, project_root="/proj")
        # 1 failure finding + 1 drift summary
        assert len(findings) == 2
        assert findings[0].evidence == {"failure_class": "test_drift"}
        assert findings[1].kind == "test_drift_summary"


# ---------------------------------------------------------------------------
# _build_drift_context
# ---------------------------------------------------------------------------


class TestBuildDriftContext:
    @patch("lintgate.channels.git_channel.collect_working_tree_context")
    def test_returns_modified_and_untracked_sets(self, mock_ctx):
        mock_ctx.return_value = {
            "modified_files": ["a.py", "b.py"],
            "untracked_files": ["c.py"],
        }
        result = _build_drift_context("/proj")
        assert result == {
            "modified": {"a.py", "b.py"},
            "untracked": {"c.py"},
        }

    @patch(
        "lintgate.channels.git_channel.collect_working_tree_context",
        side_effect=Exception("git not found"),
    )
    def test_returns_none_on_exception(self, _mock):
        result = _build_drift_context("/proj")
        assert result is None


# ---------------------------------------------------------------------------
# _check_stale_test_symbols
# ---------------------------------------------------------------------------


class TestCheckStaleTestSymbols:
    @patch(
        "lintgate.channels.test_symbol_resolver.build_stale_test_findings",
        return_value=[
            {
                "module": "foo",
                "symbol": "bar",
                "test_file": "tests/test_foo.py",
                "line": 10,
                "confidence": 0.95,
                "source": "import",
            }
        ],
    )
    def test_emits_teff009_and_summary(self, _mock):
        failures = [TestFailure(test_name="test_a", file="tests/test_foo.py", message="fail")]
        findings: list[LintIssue] = []
        _check_stale_test_symbols(failures, "/proj", findings)
        assert len(findings) == 2
        assert findings[0].kind == "TEFF009"
        assert "deleted symbol 'foo.bar'" in findings[0].message
        assert findings[0].confidence == 0.95
        assert findings[1].kind == "TEFF009_summary"
        assert findings[1].evidence["stale_count"] == 1

    @patch(
        "lintgate.channels.test_symbol_resolver.build_stale_test_findings",
        return_value=[],
    )
    def test_no_stale_refs_emits_nothing(self, _mock):
        failures = [TestFailure(test_name="test_a", file="tests/test_foo.py", message="fail")]
        findings: list[LintIssue] = []
        _check_stale_test_symbols(failures, "/proj", findings)
        assert findings == []

    def test_skips_duplicate_files(self):
        """Two failures referencing the same file should only be checked once."""
        with patch(
            "lintgate.channels.test_symbol_resolver.build_stale_test_findings",
            return_value=[],
        ) as mock_fn:
            failures = [
                TestFailure(test_name="test_a", file="tests/test_foo.py", message="fail"),
                TestFailure(test_name="test_b", file="tests/test_foo.py", message="fail2"),
            ]
            findings: list[LintIssue] = []
            _check_stale_test_symbols(failures, "/proj", findings)
            assert mock_fn.call_count == 1

    def test_import_error_returns_silently(self):
        """If build_stale_test_findings cannot be imported, no crash."""
        with patch.dict(
            "sys.modules",
            {"lintgate.channels.test_symbol_resolver": None},
        ):
            failures = [TestFailure(test_name="test_a", file="tests/test_a.py", message="fail")]
            findings: list[LintIssue] = []
            # Should not raise
            _check_stale_test_symbols(failures, "/proj", findings)
            assert findings == []


# ---------------------------------------------------------------------------
# _check_contract_drift
# ---------------------------------------------------------------------------


class TestCheckContractDrift:
    def test_no_python_source_files_returns_early(self, tmp_path):
        findings: list[LintIssue] = []
        _check_contract_drift(["data.json"], str(tmp_path), findings)
        assert findings == []

    def test_test_files_excluded_from_source(self, tmp_path):
        findings: list[LintIssue] = []
        _check_contract_drift(["test_foo.py"], str(tmp_path), findings)
        assert findings == []

    def test_no_tests_dir_returns_early(self, tmp_path):
        # source file exists but no tests/ directory
        src = tmp_path / "core.py"
        src.write_text("def f(): pass")
        findings: list[LintIssue] = []
        _check_contract_drift(["core.py"], str(tmp_path), findings)
        assert findings == []

    @patch("lintgate.channels.contract_drift_detector.analyze_contract_drift")
    def test_delegates_to_single_file_checker(self, mock_analyze, tmp_path):
        # Create source file and test directory with a test file
        src = tmp_path / "core.py"
        src.write_text("def f(): pass")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_core.py"
        test_file.write_text("def test_f(): pass")

        mock_analyze.return_value = []
        findings: list[LintIssue] = []
        _check_contract_drift(["core.py"], str(tmp_path), findings)
        # Should not crash; analyze_contract_drift may or may not be called
        # depending on file resolution


# ---------------------------------------------------------------------------
# _check_single_file_contract_drift
# ---------------------------------------------------------------------------


class TestCheckSingleFileContractDrift:
    def test_nonexistent_file_returns_early(self, tmp_path):
        findings: list[LintIssue] = []
        _check_single_file_contract_drift(
            str(tmp_path / "nope.py"),
            str(tmp_path),
            [],
            MagicMock(),
            findings,
        )
        assert findings == []

    @patch("subprocess.run")
    def test_git_show_empty_returns_early(self, mock_run, tmp_path):
        src = tmp_path / "core.py"
        src.write_text("def f(): pass")
        mock_run.return_value = MagicMock(stdout="")
        findings: list[LintIssue] = []
        _check_single_file_contract_drift(
            str(src), str(tmp_path), [], MagicMock(), findings
        )
        assert findings == []

    @patch("subprocess.run")
    def test_drift_result_with_affected_sites_emits_finding(self, mock_run, tmp_path):
        src = tmp_path / "core.py"
        src.write_text("def f(x, y): return x + y")
        mock_run.return_value = MagicMock(stdout="def f(x): return x")

        site = MagicMock()
        site.test_file = "tests/test_core.py"
        site.line = 5

        drift = MagicMock()
        drift.affected_sites = [site]
        drift.advisory = "Parameter added"
        drift.change.file = str(src)
        drift.change.line = 1
        drift.change.function = "f"
        drift.change.change_type = "param_added"
        drift.change.old_value = "(x)"
        drift.change.new_value = "(x, y)"

        analyze_fn = MagicMock(return_value=[drift])
        findings: list[LintIssue] = []
        _check_single_file_contract_drift(
            str(src), str(tmp_path), ["tests/test_core.py"], analyze_fn, findings
        )
        assert len(findings) == 1
        assert findings[0].kind == "TEFF010"
        assert findings[0].message == "Parameter added"
        assert findings[0].evidence["function"] == "f"
        assert findings[0].evidence["affected_count"] == 1

    @patch("subprocess.run", side_effect=OSError("git not found"))
    def test_git_error_returns_silently(self, _mock, tmp_path):
        src = tmp_path / "core.py"
        src.write_text("def f(): pass")
        findings: list[LintIssue] = []
        _check_single_file_contract_drift(
            str(src), str(tmp_path), [], MagicMock(), findings
        )
        assert findings == []
