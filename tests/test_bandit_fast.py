"""Tests for the bandit fast path Tier 2 security linter."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from lintgate.linters.bandit_fast_linter import _FAST_TESTS, BanditFastLinter


class TestBanditFastMetadata:
    def test_name(self):
        linter = BanditFastLinter()
        assert linter.name == "bandit_fast"

    def test_tier_is_2(self):
        linter = BanditFastLinter()
        assert linter.tier == 2

    def test_required_tool(self):
        linter = BanditFastLinter()
        assert linter.required_tool == "bandit"

    def test_timeout(self):
        linter = BanditFastLinter()
        assert linter.timeout_ms == 8000


class TestBanditFastTestSet:
    def test_fast_tests_include_critical_checks(self):
        tests = _FAST_TESTS.split(",")
        # Hardcoded passwords
        assert "B105" in tests
        assert "B106" in tests
        assert "B107" in tests
        # Shell injection
        assert "B602" in tests
        assert "B603" in tests
        # SQL injection
        assert "B608" in tests
        # Unsafe SSL
        assert "B501" in tests


class TestBanditFastParsing:
    def _make_ctx(self, tmp_path, files=None):
        from lintgate.types import LinterContext

        f = tmp_path / "test.py"
        f.write_text("import os\n")
        return LinterContext(
            files=files or [str(f)],
            project_root=str(tmp_path),
            config={},
        )

    def test_parses_json_output(self, tmp_path):
        linter = BanditFastLinter()
        ctx = self._make_ctx(tmp_path)

        data = {
            "results": [
                {
                    "test_id": "B105",
                    "test_name": "hardcoded_password_string",
                    "issue_text": "Possible hardcoded password: 'secret123'",
                    "issue_severity": "LOW",
                    "issue_confidence": "MEDIUM",
                    "filename": "test.py",
                    "line_number": 5,
                    "issue_cwe": {
                        "id": 259,
                        "link": "https://cwe.mitre.org/data/definitions/259.html",
                    },
                    "more_info": "https://bandit.readthedocs.io/en/latest/plugins/b105.html",
                }
            ]
        }

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(data)

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 1
        issue = issues[0]
        assert issue.linter == "bandit_fast"
        assert issue.kind == "B105/hardcoded_password_string"
        assert issue.line == 5
        assert issue.evidence["test_set"] == "fast"

    def test_severity_mapping(self, tmp_path):
        linter = BanditFastLinter()
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
                    "line_number": 1,
                },
            ]
        }

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(data)

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert issues[0].severity == "blocking"  # HIGH → blocking
        assert issues[0].confidence == 1.0  # HIGH → 1.0

    def test_empty_results(self, tmp_path):
        linter = BanditFastLinter()
        ctx = self._make_ctx(tmp_path)

        mock_result = MagicMock()
        mock_result.stdout = json.dumps({"results": []})

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 0

    def test_empty_output(self, tmp_path):
        linter = BanditFastLinter()
        ctx = self._make_ctx(tmp_path)

        mock_result = MagicMock()
        mock_result.stdout = ""

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 0

    def test_invalid_json(self, tmp_path):
        linter = BanditFastLinter()
        ctx = self._make_ctx(tmp_path)

        mock_result = MagicMock()
        mock_result.stdout = "not json"

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 0

    def test_focused_test_set_in_command(self, tmp_path):
        linter = BanditFastLinter()
        ctx = self._make_ctx(tmp_path)

        mock_result = MagicMock()
        mock_result.stdout = json.dumps({"results": []})

        with patch.object(linter, "run_command", return_value=mock_result) as mock_cmd:
            list(linter.run(ctx))

        cmd = mock_cmd.call_args[0][0]
        assert "-t" in cmd
        assert _FAST_TESTS in cmd
