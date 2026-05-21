"""MCP wrapper tests for mcp_tools/behavior_tools.py.

The tools are now thin subprocess wrappers — these tests verify that
the correct script subcommand and flags are assembled and that stdout
is relayed verbatim. Behavioral tests for the underlying compute live
in tests/test_scripts_behavior_check.py.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

from mcp_tools.behavior_tools import register


def _register_tools() -> dict:
    mcp = MagicMock()
    mcp.tool.return_value = lambda fn: fn
    return register(mcp, helpers={})


def _mock_proc(
    stdout: str = '{"analysis_id":"x","summary":"s","file":"/tmp/x.json"}',
    returncode: int = 0,
    stderr: str = "",
) -> MagicMock:
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


class TestRegister:
    def test_register_returns_all_tool_names(self) -> None:
        tools = _register_tools()
        assert set(tools.keys()) == {
            "hygiene_check",
            "constraint_check",
            "prediction_register",
            "behavior_precheck",
            "global_memory_status",
            "global_memory_reset",
        }


class TestHygieneCheckArgv:
    def test_invokes_hygiene_subcommand(self, tmp_path: Path) -> None:
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["hygiene_check"](path=str(tmp_path), planned_action="pip install foo")
        argv = run.call_args[0][0]
        assert argv[0] == sys.executable
        assert argv[1].endswith("behavior_check.py")
        assert argv[2] == "hygiene"
        assert argv[3] == str(tmp_path)
        assert "--action" in argv
        assert "pip install foo" in argv

    def test_relays_stdout_verbatim(self, tmp_path: Path) -> None:
        tools = _register_tools()
        envelope = '{"analysis_id":"abc","summary":"ok","file":"/tmp/x.json"}'
        with patch("subprocess.run", return_value=_mock_proc(stdout=envelope)):
            result = tools["hygiene_check"](path=str(tmp_path), planned_action="echo hi")
        assert result == envelope


class TestConstraintCheckArgv:
    def test_invokes_constraint_subcommand(self, tmp_path: Path) -> None:
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["constraint_check"](path=str(tmp_path), planned_action="pytest tests/")
        argv = run.call_args[0][0]
        assert argv[2] == "constraint"
        assert argv[3] == str(tmp_path)
        assert "--action" in argv
        assert "pytest tests/" in argv
        assert "--known-constraint" not in argv

    def test_known_constraints_passed_through(self, tmp_path: Path) -> None:
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["constraint_check"](
                path=str(tmp_path),
                planned_action="pytest",
                known_constraints=["needs venv", "needs config"],
            )
        argv = run.call_args[0][0]
        # Each constraint has its own --known-constraint flag
        kc_positions = [i for i, a in enumerate(argv) if a == "--known-constraint"]
        assert len(kc_positions) == 2
        assert argv[kc_positions[0] + 1] == "needs venv"
        assert argv[kc_positions[1] + 1] == "needs config"


class TestPredictionRegisterArgv:
    def test_invokes_predict_subcommand(self, tmp_path: Path) -> None:
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["prediction_register"](
                path=str(tmp_path),
                planned_action="run pytest",
                prediction="tests pass",
                prediction_type="exit_code",
                prediction_value=0,
            )
        argv = run.call_args[0][0]
        assert argv[2] == "predict"
        assert argv[3] == str(tmp_path)
        assert "--prediction" in argv
        assert "tests pass" in argv
        assert "--type" in argv
        assert "exit_code" in argv
        assert "--value" in argv
        assert "0" in argv  # int converted to str

    def test_int_value_stringified(self, tmp_path: Path) -> None:
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["prediction_register"](
                path=str(tmp_path),
                planned_action="run pytest",
                prediction="tests pass",
                prediction_type="exit_code",
                prediction_value=42,
            )
        argv = run.call_args[0][0]
        assert "42" in argv


class TestBehaviorPrecheckArgv:
    def test_minimal_invocation(self, tmp_path: Path) -> None:
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["behavior_precheck"](path=str(tmp_path), planned_action="pytest")
        argv = run.call_args[0][0]
        assert argv[2] == "precheck"
        assert "--prediction" not in argv

    def test_all_optional_flags(self, tmp_path: Path) -> None:
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["behavior_precheck"](
                path=str(tmp_path),
                planned_action="run pytest",
                known_constraints=["needs fixtures"],
                prediction="pass",
                prediction_type="exit_code",
                prediction_value=0,
            )
        argv = run.call_args[0][0]
        assert "--known-constraint" in argv
        assert "needs fixtures" in argv
        assert "--prediction" in argv
        assert "pass" in argv
        assert "--type" in argv
        assert "--value" in argv
        assert "0" in argv


class TestMemoryTools:
    def test_memory_status_invocation(self, tmp_path: Path) -> None:
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["global_memory_status"](path=str(tmp_path))
        argv = run.call_args[0][0]
        assert argv[2] == "memory-status"
        assert argv[3] == str(tmp_path)

    def test_memory_reset_invocation(self, tmp_path: Path) -> None:
        tools = _register_tools()
        with patch("subprocess.run", return_value=_mock_proc()) as run:
            tools["global_memory_reset"](path=str(tmp_path))
        argv = run.call_args[0][0]
        assert argv[2] == "memory-reset"
        assert argv[3] == str(tmp_path)


class TestErrorRelay:
    def test_timeout_error_envelope(self, tmp_path: Path) -> None:
        import json
        import subprocess as _sp

        tools = _register_tools()
        with patch("subprocess.run", side_effect=_sp.TimeoutExpired(cmd="x", timeout=1)):
            result = tools["hygiene_check"](path=str(tmp_path), planned_action="x")
        parsed = json.loads(result)
        assert "timed out" in parsed["error"]

    def test_nonzero_exit_with_no_stdout(self, tmp_path: Path) -> None:
        import json

        tools = _register_tools()
        with patch(
            "subprocess.run",
            return_value=_mock_proc(stdout="", returncode=2, stderr="boom"),
        ):
            result = tools["hygiene_check"](path=str(tmp_path), planned_action="x")
        parsed = json.loads(result)
        assert "behavior_check exit 2" in parsed["error"]
        assert parsed["stderr"] == "boom"
