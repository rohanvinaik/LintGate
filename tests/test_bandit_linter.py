"""Tests for lintgate/linters/bandit_linter.py — BanditLinter and _is_test_or_docs_context."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from lintgate.linters.bandit_linter import (
    BanditLinter,
    _is_test_or_docs_context,
)
from lintgate.types import LinterContext

# ── _is_test_or_docs_context ────────────────────────────────────────


class TestIsTestOrDocsContext:
    def test_file_in_tests_dir(self):
        assert _is_test_or_docs_context("/project/tests/test_foo.py", "/project") is True

    def test_file_in_docs_dir(self):
        assert _is_test_or_docs_context("/project/docs/example.py", "/project") is True

    def test_file_in_examples_dir(self):
        assert _is_test_or_docs_context("/project/examples/demo.py", "/project") is True

    def test_file_in_fixtures_dir(self):
        assert _is_test_or_docs_context("/project/fixtures/data.py", "/project") is True

    def test_file_in_src_not_test(self):
        assert _is_test_or_docs_context("/project/src/core.py", "/project") is False

    def test_file_at_root(self):
        assert _is_test_or_docs_context("/project/setup.py", "/project") is False

    def test_nested_test_dir(self):
        assert _is_test_or_docs_context("/project/pkg/tests/util.py", "/project") is True

    def test_case_insensitive(self):
        assert _is_test_or_docs_context("/project/TESTS/foo.py", "/project") is True


# ── BanditLinter metadata ───────────────────────────────────────────


class TestBanditLinterMetadata:
    def test_name(self):
        linter = BanditLinter()
        assert linter.name == "bandit"

    def test_tier(self):
        linter = BanditLinter()
        assert linter.tier == 3

    def test_required_tool(self):
        linter = BanditLinter()
        assert linter.required_tool == "bandit"

    def test_timeout(self):
        linter = BanditLinter()
        assert linter.timeout_ms == 10000


# ── BanditLinter.run ─────────────────────────────────────────────────


class TestBanditLinterRun:
    def _make_ctx(self, tmp_path, files=None):
        f = tmp_path / "test.py"
        f.write_text("import os\n")
        return LinterContext(
            files=files or [str(f)],
            project_root=str(tmp_path),
            config={},
        )

    def test_parses_json_output(self, tmp_path):
        linter = BanditLinter()
        ctx = self._make_ctx(tmp_path)
        data = {
            "results": [
                {
                    "test_id": "B602",
                    "test_name": "subprocess_popen_with_shell_equals_true",
                    "issue_text": "subprocess call with shell=True",
                    "issue_severity": "HIGH",
                    "issue_confidence": "HIGH",
                    "filename": "test.py",
                    "line_number": 10,
                    "issue_cwe": {"id": 78},
                    "more_info": "https://bandit.readthedocs.io/en/latest/plugins/b602.html",
                }
            ]
        }
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(data)

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 1
        issue = issues[0]
        assert issue.linter == "bandit"
        assert issue.kind == "B602/subprocess_popen_with_shell_equals_true"
        assert issue.severity == "blocking"
        assert issue.confidence == 1.0
        assert issue.line == 10
        assert issue.message == "subprocess call with shell=True"

    def test_severity_mapping_medium(self, tmp_path):
        linter = BanditLinter()
        ctx = self._make_ctx(tmp_path)
        data = {
            "results": [
                {
                    "test_id": "B301",
                    "test_name": "pickle",
                    "issue_text": "Pickle usage",
                    "issue_severity": "MEDIUM",
                    "issue_confidence": "MEDIUM",
                    "filename": "test.py",
                    "line_number": 1,
                }
            ]
        }
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(data)

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert issues[0].severity == "warning"
        assert issues[0].confidence == 0.8

    def test_severity_mapping_low(self, tmp_path):
        linter = BanditLinter()
        ctx = self._make_ctx(tmp_path)
        data = {
            "results": [
                {
                    "test_id": "B105",
                    "test_name": "hardcoded_password_string",
                    "issue_text": "Hardcoded password",
                    "issue_severity": "LOW",
                    "issue_confidence": "LOW",
                    "filename": str(tmp_path / "src" / "main.py"),
                    "line_number": 1,
                }
            ]
        }
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(data)

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert issues[0].severity == "informational"
        assert issues[0].confidence == 0.6

    def test_b105_suppressed_in_test_dir(self, tmp_path):
        linter = BanditLinter()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        test_file = test_dir / "test_foo.py"
        test_file.write_text("pass\n")
        ctx = LinterContext(
            files=[str(test_file)],
            project_root=str(tmp_path),
            config={},
        )
        data = {
            "results": [
                {
                    "test_id": "B105",
                    "test_name": "hardcoded_password_string",
                    "issue_text": "Hardcoded password",
                    "issue_severity": "LOW",
                    "issue_confidence": "MEDIUM",
                    "filename": str(test_file),
                    "line_number": 1,
                }
            ]
        }
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(data)

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 0

    def test_b105_not_suppressed_in_src(self, tmp_path):
        linter = BanditLinter()
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        src_file = src_dir / "main.py"
        src_file.write_text("pass\n")
        ctx = LinterContext(
            files=[str(src_file)],
            project_root=str(tmp_path),
            config={},
        )
        data = {
            "results": [
                {
                    "test_id": "B105",
                    "test_name": "hardcoded_password_string",
                    "issue_text": "Hardcoded password",
                    "issue_severity": "LOW",
                    "issue_confidence": "LOW",
                    "filename": str(src_file),
                    "line_number": 1,
                }
            ]
        }
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(data)

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 1

    def test_empty_output(self, tmp_path):
        linter = BanditLinter()
        ctx = self._make_ctx(tmp_path)
        mock_result = MagicMock()
        mock_result.stdout = ""

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 0

    def test_invalid_json(self, tmp_path):
        linter = BanditLinter()
        ctx = self._make_ctx(tmp_path)
        mock_result = MagicMock()
        mock_result.stdout = "not json at all"

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 0

    def test_empty_results_list(self, tmp_path):
        linter = BanditLinter()
        ctx = self._make_ctx(tmp_path)
        mock_result = MagicMock()
        mock_result.stdout = json.dumps({"results": []})

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 0

    def test_extra_args_passed_to_command(self, tmp_path):
        linter = BanditLinter()
        ctx = LinterContext(
            files=[str(tmp_path / "f.py")],
            project_root=str(tmp_path),
            config={"extra_args": ["--configfile", "bandit.yaml"]},
        )
        mock_result = MagicMock()
        mock_result.stdout = json.dumps({"results": []})

        with patch.object(linter, "run_command", return_value=mock_result) as mock_cmd:
            list(linter.run(ctx))

        cmd = mock_cmd.call_args[0][0]
        assert "--configfile" in cmd
        assert "bandit.yaml" in cmd

    def test_kind_without_test_id(self, tmp_path):
        linter = BanditLinter()
        ctx = self._make_ctx(tmp_path)
        data = {
            "results": [
                {
                    "test_id": "",
                    "test_name": "some_check",
                    "issue_text": "Issue found",
                    "issue_severity": "LOW",
                    "issue_confidence": "LOW",
                    "filename": "f.py",
                    "line_number": 1,
                }
            ]
        }
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(data)

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert issues[0].kind == "some_check"

    def test_suggestion_with_more_info(self, tmp_path):
        linter = BanditLinter()
        ctx = self._make_ctx(tmp_path)
        data = {
            "results": [
                {
                    "test_id": "B602",
                    "test_name": "shell",
                    "issue_text": "shell=True",
                    "issue_severity": "HIGH",
                    "issue_confidence": "HIGH",
                    "filename": "f.py",
                    "line_number": 1,
                    "more_info": "https://example.com",
                }
            ]
        }
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(data)

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert "See: https://example.com" in issues[0].suggestions

    def test_suggestion_without_more_info(self, tmp_path):
        linter = BanditLinter()
        ctx = self._make_ctx(tmp_path)
        data = {
            "results": [
                {
                    "test_id": "B602",
                    "test_name": "shell",
                    "issue_text": "shell=True",
                    "issue_severity": "HIGH",
                    "issue_confidence": "HIGH",
                    "filename": "f.py",
                    "line_number": 1,
                }
            ]
        }
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(data)

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert "Review shell finding" in issues[0].suggestions[0]
