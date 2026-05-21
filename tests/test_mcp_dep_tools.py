"""MCP wrapper tests for mcp_tools/dep_tools.py.

The tools are now thin subprocess wrappers — these tests verify that
the correct script subcommand and flags are assembled and that stdout
is relayed verbatim. Behavioral tests for the underlying compute live
in tests/test_scripts_dep_check.py.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

from mcp_tools.dep_tools import register


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


class TestRegister:
    def test_register_returns_all_tool_names(self) -> None:
        tools = _register_tools()
        assert set(tools.keys()) == {
            "dep_health_check",
            "dep_sync",
            "toolchain_health_check",
        }


class TestDepHealthCheck:
    def test_invokes_health_subcommand(self, tmp_path: Path) -> None:
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["dep_health_check"](path=str(tmp_path))
        argv = run.call_args[0][0]
        assert argv[0] == sys.executable
        assert argv[1].endswith("dep_check.py")
        assert argv[2] == str(tmp_path)
        assert argv[3] == "health"

    def test_relays_stdout_verbatim(self, tmp_path: Path) -> None:
        tools = _register_tools()
        envelope = '{"analysis_id":"abc","summary":"ok","file":"/tmp/x.json"}'
        with patch("subprocess.run", return_value=_mock_proc(stdout=envelope)):
            result = tools["dep_health_check"](path=str(tmp_path))
        assert result == envelope


class TestDepSync:
    def test_defaults_to_status_only(self, tmp_path: Path) -> None:
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["dep_sync"](path=str(tmp_path))
        argv = run.call_args[0][0]
        assert "sync" in argv
        assert "--create-venv" not in argv
        assert "--lock" not in argv

    def test_create_venv_flag(self, tmp_path: Path) -> None:
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["dep_sync"](path=str(tmp_path), create_venv=True)
        assert "--create-venv" in run.call_args[0][0]

    def test_lock_flag(self, tmp_path: Path) -> None:
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["dep_sync"](path=str(tmp_path), lock=True)
        assert "--lock" in run.call_args[0][0]

    def test_both_flags(self, tmp_path: Path) -> None:
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["dep_sync"](path=str(tmp_path), create_venv=True, lock=True)
        argv = run.call_args[0][0]
        assert "--create-venv" in argv
        assert "--lock" in argv


class TestToolchainHealthCheck:
    def test_defaults(self, tmp_path: Path) -> None:
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["toolchain_health_check"](path=str(tmp_path))
        argv = run.call_args[0][0]
        assert "toolchain" in argv
        assert "--install-missing" not in argv

    def test_install_missing_flag(self, tmp_path: Path) -> None:
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["toolchain_health_check"](path=str(tmp_path), install_missing=True)
        assert "--install-missing" in run.call_args[0][0]


class TestErrorHandling:
    def test_timeout_returns_error_envelope(self, tmp_path: Path) -> None:
        import subprocess as sp
        tools = _register_tools()
        with patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="x", timeout=1)):
            result = tools["dep_health_check"](path=str(tmp_path))
        parsed = json.loads(result)
        assert "error" in parsed
        assert "timed out" in parsed["error"]

    def test_nonzero_exit_with_no_stdout_returns_error(self, tmp_path: Path) -> None:
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc(stdout="", returncode=2, stderr="boom")):
            result = tools["dep_health_check"](path=str(tmp_path))
        parsed = json.loads(result)
        assert "error" in parsed
        assert "boom" in parsed["stderr"]

    def test_nonzero_exit_with_stdout_relays(self, tmp_path: Path) -> None:
        """Even on nonzero exit, if the script emitted an envelope, relay it."""
        tools = _register_tools()
        envelope = '{"analysis_id":"x","summary":"partial","file":"/tmp/x.json"}'
        with patch("subprocess.run", return_value=_mock_proc(stdout=envelope, returncode=1)):
            result = tools["dep_health_check"](path=str(tmp_path))
        assert result == envelope
