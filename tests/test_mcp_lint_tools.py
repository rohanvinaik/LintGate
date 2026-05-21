"""MCP wrapper tests for mcp_tools/lint_tools.py.

The tools are now thin subprocess wrappers. Helper functions
(_tool_package_name, _project_venv_python, _format_cmd, _missing_tool_hints,
_linter_available) are re-exported from mcp_tools.lint_tools for backward
compatibility — their behavioral tests live in tests/test_lint_tools.py.

Behavioral tests for the underlying compute live in tests/test_scripts_lint_run.py.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from mcp_tools.lint_tools import (
    _format_cmd,
    _linter_available,
    _missing_tool_hints,
    _project_venv_python,
    _tool_package_name,
    register,
)

if TYPE_CHECKING:
    from pathlib import Path


def _register_tools() -> dict:
    mcp = MagicMock()
    mcp.tool.return_value = lambda fn: fn
    return register(mcp, helpers={})


def _mock_proc(stdout: str = '{"analysis_id":"x","summary":"s","file":"/tmp/x.json"}', returncode: int = 0, stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


# ── Helper re-exports (smoke tests — full coverage in test_lint_tools.py) ─


class TestHelperReexports:
    def test_tool_package_name_importable(self):
        assert _tool_package_name("ruff") == "ruff"
        assert _tool_package_name("pip-audit") == "pip-audit"

    def test_format_cmd_importable(self):
        assert _format_cmd(["ls", "-la"]) == "ls -la"

    def test_linter_available_importable(self):
        linter = SimpleNamespace(available=lambda project_root="": True)
        assert _linter_available(linter, "/tmp") is True

    def test_project_venv_python_importable(self, tmp_path: Path):
        assert _project_venv_python(str(tmp_path)) is None

    def test_missing_tool_hints_importable(self, tmp_path: Path):
        assert _missing_tool_hints(str(tmp_path), {}) == []


# ── register() returns all tools ─────────────────────────────────────────


class TestRegister:
    def test_register_returns_all_tool_names(self):
        tools = _register_tools()
        assert set(tools.keys()) == {
            "lint_files",
            "lint_project",
            "lint_get_details",
            "lint_status",
            "audit_tool_versions",
            "lint_fix",
        }


# ── lint_files subprocess argv ──────────────────────────────────────────


class TestLintFilesArgv:
    def test_basic_invocation(self, tmp_path: Path):
        tools = _register_tools()
        f = tmp_path / "a.py"
        f.touch()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["lint_files"](files=[str(f)])
        argv = run.call_args[0][0]
        assert argv[0] == sys.executable
        assert argv[1].endswith("lint_run.py")
        assert "files" in argv
        assert "--files" in argv
        assert str(f) in argv

    def test_tier_passed(self, tmp_path: Path):
        tools = _register_tools()
        f = tmp_path / "a.py"
        f.touch()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["lint_files"](files=[str(f)], tier=3)
        argv = run.call_args[0][0]
        assert "--tier" in argv
        assert "3" in argv

    def test_surgical_scope(self, tmp_path: Path):
        tools = _register_tools()
        f = tmp_path / "a.py"
        f.touch()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["lint_files"](files=[str(f)], scope="surgical")
        argv = run.call_args[0][0]
        assert "--scope" in argv
        assert "surgical" in argv

    def test_raises_on_invalid_tier(self):
        tools = _register_tools()
        with pytest.raises(ValueError, match="Invalid tier"):
            tools["lint_files"](files=["/a.py"], tier=99)

    def test_raises_on_empty_files(self):
        tools = _register_tools()
        with pytest.raises(ValueError, match="No files specified"):
            tools["lint_files"](files=[])

    def test_subprocess_error_raises_value_error(self, tmp_path: Path):
        """Script-reported 'No specified files exist' bubbles up as ValueError."""
        tools = _register_tools()
        err_envelope = json.dumps({"error": "No specified files exist. Missing: ['/x.py']"})
        with patch("subprocess.run", return_value=_mock_proc(stdout=err_envelope)):
            with pytest.raises(ValueError, match="No specified files exist"):
                tools["lint_files"](files=["/x.py"])


# ── lint_project subprocess argv ────────────────────────────────────────


class TestLintProjectArgv:
    def test_basic_invocation(self, tmp_path: Path):
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["lint_project"](path=str(tmp_path))
        argv = run.call_args[0][0]
        assert "project" in argv
        assert str(tmp_path) in argv

    def test_raises_on_invalid_tier(self, tmp_path: Path):
        tools = _register_tools()
        with pytest.raises(ValueError, match="Invalid tier"):
            tools["lint_project"](path=str(tmp_path), tier=99)


# ── lint_get_details subprocess argv ────────────────────────────────────


class TestLintGetDetailsArgv:
    def test_basic_invocation(self):
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["lint_get_details"](run_id="r1")
        argv = run.call_args[0][0]
        assert "details" in argv
        assert "--run-id" in argv
        assert "r1" in argv

    def test_severity_passed(self):
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["lint_get_details"](run_id="r1", severity="blocking")
        argv = run.call_args[0][0]
        assert "--severity" in argv
        assert "blocking" in argv

    def test_invalid_severity_raises(self):
        tools = _register_tools()
        with pytest.raises(ValueError, match="Invalid severity"):
            tools["lint_get_details"](run_id="r1", severity="critical")

    def test_run_id_not_found_raises(self):
        tools = _register_tools()
        err_envelope = json.dumps({"error": "No lint run found with run_id: xyz"})
        with patch("subprocess.run", return_value=_mock_proc(stdout=err_envelope)):
            with pytest.raises(ValueError, match="No lint run found"):
                tools["lint_get_details"](run_id="xyz")


# ── lint_status subprocess argv ─────────────────────────────────────────


class TestLintStatusArgv:
    def test_basic_invocation(self, tmp_path: Path):
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["lint_status"](path=str(tmp_path))
        argv = run.call_args[0][0]
        assert "status" in argv


# ── audit_tool_versions subprocess argv ─────────────────────────────────


class TestAuditToolVersionsArgv:
    def test_basic_invocation(self, tmp_path: Path):
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["audit_tool_versions"](path=str(tmp_path))
        argv = run.call_args[0][0]
        assert "audit" in argv
        assert "--auto-fix" not in argv

    def test_auto_fix_flag(self, tmp_path: Path):
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["audit_tool_versions"](path=str(tmp_path), auto_fix=True)
        argv = run.call_args[0][0]
        assert "--auto-fix" in argv

    def test_no_verify_after_fix_flag(self, tmp_path: Path):
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["audit_tool_versions"](path=str(tmp_path), verify_after_fix=False)
        argv = run.call_args[0][0]
        assert "--no-verify-after-fix" in argv


# ── lint_fix subprocess argv ────────────────────────────────────────────


class TestLintFixArgv:
    def test_basic_invocation(self, tmp_path: Path):
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["lint_fix"](path=str(tmp_path))
        argv = run.call_args[0][0]
        assert "fix" in argv
        # dry_run=True is default, so no --no-dry-run
        assert "--no-dry-run" not in argv

    def test_no_dry_run_flag(self, tmp_path: Path):
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["lint_fix"](path=str(tmp_path), dry_run=False)
        assert "--no-dry-run" in run.call_args[0][0]

    def test_files_flag(self, tmp_path: Path):
        tools = _register_tools()
        f = tmp_path / "a.py"
        f.touch()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["lint_fix"](files=[str(f)])
        argv = run.call_args[0][0]
        assert "--files" in argv
        assert str(f) in argv

    def test_raises_on_no_args(self):
        tools = _register_tools()
        with pytest.raises(ValueError, match="Either files or path"):
            tools["lint_fix"]()


# ── Error handling ─────────────────────────────────────────────────────


class TestErrorHandling:
    def test_timeout_returns_error_envelope(self, tmp_path: Path):
        import subprocess as sp
        tools = _register_tools()
        with patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="x", timeout=1)):
            result = tools["lint_status"](path=str(tmp_path))
        parsed = json.loads(result)
        assert "error" in parsed
        assert "timed out" in parsed["error"]

    def test_nonzero_exit_no_stdout(self, tmp_path: Path):
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc(stdout="", returncode=1, stderr="boom")):
            result = tools["lint_status"](path=str(tmp_path))
        parsed = json.loads(result)
        assert "error" in parsed

    def test_envelope_with_error_key_not_raised(self, tmp_path: Path):
        """If an envelope has both analysis_id and error, relay — don't raise."""
        tools = _register_tools()
        # analysis_id present = treated as data, not a fatal error
        envelope = json.dumps({
            "analysis_id": "x",
            "summary": "partial",
            "file": "/tmp/x.json",
            "error": "partial failure",
        })
        with patch("subprocess.run", return_value=_mock_proc(stdout=envelope)):
            result = tools["lint_status"](path=str(tmp_path))
        # no raise, just relay
        assert "partial" in result
