"""Tests for the pip-audit supply-chain integrity linter."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from lintgate.linters.pip_audit_linter import PipAuditLinter, _classify_severity


class TestPipAuditMetadata:
    def test_name(self):
        linter = PipAuditLinter()
        assert linter.name == "pip_audit"

    def test_tier(self):
        linter = PipAuditLinter()
        assert linter.tier == 2

    def test_required_tool(self):
        linter = PipAuditLinter()
        assert linter.required_tool == "pip-audit"

    def test_timeout(self):
        linter = PipAuditLinter()
        assert linter.timeout_ms == 15000


class TestPipAuditParsing:
    def _make_ctx(self, tmp_path):
        from lintgate.types import LinterContext

        f = tmp_path / "dummy.py"
        f.write_text("pass\n")
        return LinterContext(
            files=[str(f)],
            project_root=str(tmp_path),
            config={},
        )

    def test_parses_dependencies_format(self, tmp_path):
        """Test parsing the dependencies/vulns nested format."""
        linter = PipAuditLinter()
        ctx = self._make_ctx(tmp_path)

        data = {
            "dependencies": [
                {
                    "name": "requests",
                    "version": "2.25.0",
                    "vulns": [
                        {
                            "id": "CVE-2023-32681",
                            "fix_versions": ["2.31.0"],
                            "description": "Unintended leak of Proxy-Authorization header",
                            "aliases": ["GHSA-j8r2-6x86-q33e"],
                        }
                    ],
                },
                {
                    "name": "flask",
                    "version": "2.3.0",
                    "vulns": [],
                },
            ]
        }

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(data)

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 1
        issue = issues[0]
        assert issue.linter == "pip_audit"
        assert issue.kind == "CVE-2023-32681"
        assert "requests" in issue.message
        assert "2.25.0" in issue.message
        assert issue.confidence == 1.0
        assert issue.evidence["package"] == "requests"
        assert issue.evidence["fix_versions"] == ["2.31.0"]

    def test_no_vulnerabilities(self, tmp_path):
        linter = PipAuditLinter()
        ctx = self._make_ctx(tmp_path)

        data = {
            "dependencies": [
                {"name": "requests", "version": "2.31.0", "vulns": []},
            ]
        }

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(data)

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 0

    def test_empty_output(self, tmp_path):
        linter = PipAuditLinter()
        ctx = self._make_ctx(tmp_path)

        mock_result = MagicMock()
        mock_result.stdout = ""

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 0

    def test_invalid_json(self, tmp_path):
        linter = PipAuditLinter()
        ctx = self._make_ctx(tmp_path)

        mock_result = MagicMock()
        mock_result.stdout = "not json"

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 0

    def test_multiple_vulns_per_package(self, tmp_path):
        linter = PipAuditLinter()
        ctx = self._make_ctx(tmp_path)

        data = {
            "dependencies": [
                {
                    "name": "urllib3",
                    "version": "1.25.0",
                    "vulns": [
                        {
                            "id": "CVE-2021-33503",
                            "fix_versions": ["1.26.5"],
                            "description": "DoS",
                            "aliases": [],
                        },
                        {
                            "id": "CVE-2023-43804",
                            "fix_versions": ["2.0.6"],
                            "description": "Cookie leak",
                            "aliases": [],
                        },
                    ],
                }
            ]
        }

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(data)

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 2

    def test_suggestions_include_fix_version(self, tmp_path):
        linter = PipAuditLinter()
        ctx = self._make_ctx(tmp_path)

        data = {
            "dependencies": [
                {
                    "name": "requests",
                    "version": "2.25.0",
                    "vulns": [
                        {
                            "id": "CVE-2023-32681",
                            "fix_versions": ["2.31.0"],
                            "description": "Test",
                            "aliases": [],
                        },
                    ],
                }
            ]
        }

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(data)

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 1
        assert any("2.31.0" in s for s in issues[0].suggestions)

    def test_description_truncation(self, tmp_path):
        linter = PipAuditLinter()
        ctx = self._make_ctx(tmp_path)

        long_desc = "A" * 200
        data = {
            "dependencies": [
                {
                    "name": "pkg",
                    "version": "1.0",
                    "vulns": [
                        {
                            "id": "CVE-2024-00000",
                            "fix_versions": [],
                            "description": long_desc,
                            "aliases": [],
                        },
                    ],
                }
            ]
        }

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(data)

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 1
        # Message should be truncated, not the full 200 chars
        assert "..." in issues[0].message

    def test_prefers_requirements_file_when_present(self, tmp_path):
        linter = PipAuditLinter()
        ctx = self._make_ctx(tmp_path)
        req = tmp_path / "requirements.txt"
        req.write_text("requests==2.31.0\n")

        data = {"dependencies": []}
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(data)

        with patch.object(linter, "run_command", return_value=mock_result) as mock_cmd:
            list(linter.run(ctx))

        cmd = mock_cmd.call_args[0][0]
        assert "-r" in cmd
        assert str(req) in cmd


class TestPipAuditSeverityClassification:
    def test_cve_is_warning(self):
        assert _classify_severity("CVE-2023-32681", []) == "warning"

    def test_ghsa_is_warning(self):
        assert (
            _classify_severity("PYSEC-2023-001", ["GHSA-j8r2-6x86-q33e"]) == "warning"
        )

    def test_unknown_id_is_informational(self):
        assert _classify_severity("PYSEC-2023-001", []) == "informational"

    def test_empty_id_is_informational(self):
        assert _classify_severity("", []) == "informational"
