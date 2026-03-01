"""Tests for the ty type checker linter integration."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from lintgate.linters.ty_linter import TyLinter, _classify_severity


class TestTyLinterMetadata:
    """Verify linter metadata and protocol compliance."""

    def test_name(self):
        linter = TyLinter()
        assert linter.name == "ty"

    def test_tier(self):
        linter = TyLinter()
        assert linter.tier == 2

    def test_required_tool(self):
        linter = TyLinter()
        assert linter.required_tool == "ty"

    def test_timeout(self):
        linter = TyLinter()
        assert linter.timeout_ms == 10000


class TestTyLinterAvailability:
    """Test tool availability detection."""

    def test_available_when_installed(self):
        linter = TyLinter()
        with patch("shutil.which", return_value="/usr/bin/ty"):
            assert linter.available() is True

    def test_not_available_when_missing(self):
        linter = TyLinter()
        with patch("shutil.which", return_value=None):
            assert linter.available() is False


class TestTyLinterParsing:
    """Test GitLab Code Quality JSON output parsing."""

    def _make_ctx(self, tmp_path, files=None, strictness="normal"):
        from lintgate.types import LinterContext

        f = tmp_path / "test.py"
        f.write_text("x = 1\n")
        return LinterContext(
            files=files or [str(f)],
            project_root=str(tmp_path),
            strictness=strictness,
            config={},
        )

    def test_parses_gitlab_json(self, tmp_path):
        linter = TyLinter()
        ctx = self._make_ctx(tmp_path)

        diagnostics = [
            {
                "description": "Name `foo` is not defined",
                "check_name": "unresolved-reference",
                "fingerprint": "abc123",
                "severity": "major",
                "location": {
                    "path": "test.py",
                    "lines": {"begin": 10},
                },
            }
        ]

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(diagnostics)
        mock_result.returncode = 1

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 1
        assert issues[0].linter == "ty"
        assert issues[0].kind == "unresolved-reference"
        assert issues[0].line == 10
        assert issues[0].confidence == 1.0
        assert "foo" in issues[0].message

    def test_parses_positions_format(self, tmp_path):
        """GitLab format also supports positions.begin.line."""
        linter = TyLinter()
        ctx = self._make_ctx(tmp_path)

        diagnostics = [
            {
                "description": "Invalid type annotation",
                "check_name": "invalid-type-form",
                "fingerprint": "def456",
                "severity": "minor",
                "location": {
                    "path": "test.py",
                    "positions": {"begin": {"line": 5, "column": 3}},
                },
            }
        ]

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(diagnostics)

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 1
        assert issues[0].line == 5

    def test_empty_output(self, tmp_path):
        linter = TyLinter()
        ctx = self._make_ctx(tmp_path)

        mock_result = MagicMock()
        mock_result.stdout = ""

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 0

    def test_invalid_json(self, tmp_path):
        linter = TyLinter()
        ctx = self._make_ctx(tmp_path)

        mock_result = MagicMock()
        mock_result.stdout = "not json"

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 0

    def test_non_list_json(self, tmp_path):
        """If output is not a JSON array, skip gracefully."""
        linter = TyLinter()
        ctx = self._make_ctx(tmp_path)

        mock_result = MagicMock()
        mock_result.stdout = '{"error": "something"}'

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 0

    def test_multiple_diagnostics(self, tmp_path):
        linter = TyLinter()
        ctx = self._make_ctx(tmp_path)

        diagnostics = [
            {
                "description": "Error 1",
                "check_name": "invalid-assignment",
                "fingerprint": "a",
                "severity": "major",
                "location": {"path": "test.py", "lines": {"begin": 1}},
            },
            {
                "description": "Error 2",
                "check_name": "invalid-return-type",
                "fingerprint": "b",
                "severity": "major",
                "location": {"path": "test.py", "lines": {"begin": 5}},
            },
        ]

        mock_result = MagicMock()
        mock_result.stdout = json.dumps(diagnostics)

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 2

    def test_unresolved_import_downgraded_when_sys_path_mutated(self, tmp_path):
        linter = TyLinter()
        target = tmp_path / "script.py"
        target.write_text(
            "import sys\nsys.path.insert(0, 'vendor')\nimport missing_pkg\n",
        )
        ctx = self._make_ctx(tmp_path, files=[str(target)])

        diagnostics = [
            {
                "description": "Cannot resolve import `missing_pkg`",
                "check_name": "unresolved-import",
                "severity": "major",
                "location": {"path": str(target), "lines": {"begin": 3}},
            }
        ]
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(diagnostics)
        mock_result.returncode = 1

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 1
        assert issues[0].severity == "informational"
        assert issues[0].evidence["sys_path_dynamic_import"] is True

    def test_unresolved_import_without_sys_path_remains_blocking(self, tmp_path):
        linter = TyLinter()
        target = tmp_path / "module.py"
        target.write_text("import missing_pkg\n")
        ctx = self._make_ctx(tmp_path, files=[str(target)])

        diagnostics = [
            {
                "description": "Cannot resolve import `missing_pkg`",
                "check_name": "unresolved-import",
                "severity": "major",
                "location": {"path": str(target), "lines": {"begin": 1}},
            }
        ]
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(diagnostics)
        mock_result.returncode = 1

        with patch.object(linter, "run_command", return_value=mock_result):
            issues = list(linter.run(ctx))

        assert len(issues) == 1
        assert issues[0].severity == "blocking"
        assert issues[0].evidence["sys_path_dynamic_import"] is False

    def test_extra_args_from_config(self, tmp_path):
        linter = TyLinter()
        from lintgate.types import LinterContext

        f = tmp_path / "test.py"
        f.write_text("x = 1\n")
        ctx = LinterContext(
            files=[str(f)],
            project_root=str(tmp_path),
            config={"extra_args": ["--python-version", "3.12"]},
        )

        mock_result = MagicMock()
        mock_result.stdout = "[]"

        with patch.object(linter, "run_command", return_value=mock_result) as mock_cmd:
            list(linter.run(ctx))

        cmd = mock_cmd.call_args[0][0]
        assert "--python-version" in cmd
        assert "3.12" in cmd


class TestTySeverityClassification:
    """Test severity mapping logic."""

    def test_blocking_checks(self):
        assert _classify_severity("major", "unresolved-import", "normal") == "blocking"
        assert (
            _classify_severity("major", "unresolved-reference", "normal") == "blocking"
        )
        assert _classify_severity("minor", "invalid-syntax", "normal") == "blocking"

    def test_informational_checks(self):
        assert (
            _classify_severity("minor", "unused-ignore-comment", "normal")
            == "informational"
        )
        assert _classify_severity("minor", "deprecated", "normal") == "informational"

    def test_severity_map_defaults(self):
        assert _classify_severity("blocker", "some-check", "normal") == "blocking"
        assert _classify_severity("critical", "some-check", "normal") == "blocking"
        assert _classify_severity("major", "some-check", "normal") == "warning"
        assert _classify_severity("minor", "some-check", "normal") == "informational"
        assert _classify_severity("info", "some-check", "normal") == "informational"

    def test_strict_mode_escalation(self):
        assert _classify_severity("major", "some-check", "strict") == "blocking"
        assert _classify_severity("blocker", "some-check", "strict") == "blocking"
        # minor doesn't escalate in strict mode
        assert _classify_severity("minor", "some-check", "strict") == "informational"

    def test_unknown_severity(self):
        assert _classify_severity("unknown", "some-check", "normal") == "warning"
