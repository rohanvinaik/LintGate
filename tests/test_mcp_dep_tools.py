"""Coverage tests for mcp_tools/dep_tools.py.

Exercises the register() function and each MCP tool it registers:
dep_health_check, dep_sync.
"""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

from mcp_tools.dep_tools import register

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_helpers(tmp_path: Path) -> dict:
    """Build a minimal helpers dict that validates against tmp_path."""
    return {
        "_validate_project_root": lambda path, **kw: str(tmp_path),
    }


def _register_tools(tmp_path: Path) -> dict:
    """Register tools on a mock MCP and return the tool function dict."""
    mcp = MagicMock()
    mcp.tool.return_value = lambda fn: fn
    helpers = _make_helpers(tmp_path)
    return register(mcp, helpers)  # type: ignore[no-any-return]


# ── register() ───────────────────────────────────────────────────────────


class TestRegister:
    def test_register_returns_all_tool_names(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        assert set(tools.keys()) == {
            "dep_health_check",
            "dep_sync",
            "toolchain_health_check",
        }


# ── dep_health_check ─────────────────────────────────────────────────────


class TestDepHealthCheck:
    def test_returns_valid_json(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_health = {
            "summary": {"score": 90, "issues": 0},
            "checks": [],
        }
        with patch(
            "lintgate.dependency_health.full_dependency_health",
            return_value=mock_health,
        ):
            result = json.loads(tools["dep_health_check"](path=str(tmp_path)))
        assert result["summary"]["score"] == 90

    def test_passes_project_root(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_health: dict[str, Any] = {"summary": {}, "checks": []}
        with patch(
            "lintgate.dependency_health.full_dependency_health",
            return_value=mock_health,
        ) as mock_fn:
            tools["dep_health_check"](path=str(tmp_path))
        mock_fn.assert_called_once_with(str(tmp_path))


# ── dep_sync ─────────────────────────────────────────────────────────────


class TestDepSync:
    def test_status_only_no_actions(self, tmp_path: Path) -> None:
        """Default call (no flags) returns status without actions."""
        tools = _register_tools(tmp_path)
        mock_health = {"summary": {"score": 80}}
        with patch(
            "lintgate.dependency_health.full_dependency_health",
            return_value=mock_health,
        ):
            result = json.loads(tools["dep_sync"](path=str(tmp_path)))
        assert result["project"] == str(tmp_path)
        assert result["actions"] == []
        assert "health_before" in result
        assert "health_after" not in result

    def test_uv_not_found(self, tmp_path: Path) -> None:
        """When uv is not in PATH, returns error message."""
        tools = _register_tools(tmp_path)
        mock_health = {"summary": {"score": 50}}
        with (
            patch(
                "lintgate.dependency_health.full_dependency_health",
                return_value=mock_health,
            ),
            patch("shutil.which", return_value=None),
        ):
            result = json.loads(tools["dep_sync"](path=str(tmp_path), create_venv=True))
        assert "error" in result
        assert "uv not found" in result["error"]

    def test_create_venv_skips_existing(self, tmp_path: Path) -> None:
        """When .venv already exists, create_venv is skipped."""
        tools = _register_tools(tmp_path)
        (tmp_path / ".venv").mkdir()
        mock_health = {"summary": {"score": 80}}
        with (
            patch(
                "lintgate.dependency_health.full_dependency_health",
                return_value=mock_health,
            ),
            patch("shutil.which", return_value="/usr/bin/uv"),
        ):
            result = json.loads(tools["dep_sync"](path=str(tmp_path), create_venv=True))
        venv_action = [a for a in result["actions"] if a["action"] == "create_venv"]
        assert len(venv_action) == 1
        assert venv_action[0]["status"] == "skipped"
        assert "already exists" in venv_action[0]["reason"]

    def test_create_venv_success(self, tmp_path: Path) -> None:
        """Successful venv creation returns ok status."""
        tools = _register_tools(tmp_path)
        mock_health = {"summary": {"score": 80}}
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""
        with (
            patch(
                "lintgate.dependency_health.full_dependency_health",
                return_value=mock_health,
            ),
            patch("shutil.which", return_value="/usr/bin/uv"),
            patch(
                "subprocess.run",
                return_value=mock_proc,
            ),
        ):
            result = json.loads(tools["dep_sync"](path=str(tmp_path), create_venv=True))
        venv_action = [a for a in result["actions"] if a["action"] == "create_venv"]
        assert venv_action[0]["status"] == "ok"
        assert "health_after" in result

    def test_create_venv_error(self, tmp_path: Path) -> None:
        """Failed venv creation returns error status with stderr."""
        tools = _register_tools(tmp_path)
        mock_health = {"summary": {"score": 80}}
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "some error occurred"
        with (
            patch(
                "lintgate.dependency_health.full_dependency_health",
                return_value=mock_health,
            ),
            patch("shutil.which", return_value="/usr/bin/uv"),
            patch(
                "subprocess.run",
                return_value=mock_proc,
            ),
        ):
            result = json.loads(tools["dep_sync"](path=str(tmp_path), create_venv=True))
        venv_action = [a for a in result["actions"] if a["action"] == "create_venv"]
        assert venv_action[0]["status"] == "error"
        assert venv_action[0]["returncode"] == 1

    def test_create_venv_timeout(self, tmp_path: Path) -> None:
        """Timeout during venv creation returns timeout status."""
        tools = _register_tools(tmp_path)
        mock_health = {"summary": {"score": 80}}
        with (
            patch(
                "lintgate.dependency_health.full_dependency_health",
                return_value=mock_health,
            ),
            patch("shutil.which", return_value="/usr/bin/uv"),
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="uv venv", timeout=60),
            ),
        ):
            result = json.loads(tools["dep_sync"](path=str(tmp_path), create_venv=True))
        venv_action = [a for a in result["actions"] if a["action"] == "create_venv"]
        assert venv_action[0]["status"] == "timeout"

    def test_lock_success(self, tmp_path: Path) -> None:
        """Successful lock returns ok status."""
        tools = _register_tools(tmp_path)
        mock_health = {"summary": {"score": 80}}
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""
        with (
            patch(
                "lintgate.dependency_health.full_dependency_health",
                return_value=mock_health,
            ),
            patch("shutil.which", return_value="/usr/bin/uv"),
            patch(
                "subprocess.run",
                return_value=mock_proc,
            ),
        ):
            result = json.loads(tools["dep_sync"](path=str(tmp_path), lock=True))
        lock_action = [a for a in result["actions"] if a["action"] == "lock"]
        assert len(lock_action) == 1
        assert lock_action[0]["status"] == "ok"
        assert "health_after" in result

    def test_lock_error(self, tmp_path: Path) -> None:
        """Failed lock returns error status."""
        tools = _register_tools(tmp_path)
        mock_health = {"summary": {"score": 80}}
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "lock failed"
        with (
            patch(
                "lintgate.dependency_health.full_dependency_health",
                return_value=mock_health,
            ),
            patch("shutil.which", return_value="/usr/bin/uv"),
            patch(
                "subprocess.run",
                return_value=mock_proc,
            ),
        ):
            result = json.loads(tools["dep_sync"](path=str(tmp_path), lock=True))
        lock_action = [a for a in result["actions"] if a["action"] == "lock"]
        assert lock_action[0]["status"] == "error"

    def test_lock_timeout(self, tmp_path: Path) -> None:
        """Timeout during lock returns timeout status."""
        tools = _register_tools(tmp_path)
        mock_health = {"summary": {"score": 80}}
        with (
            patch(
                "lintgate.dependency_health.full_dependency_health",
                return_value=mock_health,
            ),
            patch("shutil.which", return_value="/usr/bin/uv"),
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="uv lock", timeout=120),
            ),
        ):
            result = json.loads(tools["dep_sync"](path=str(tmp_path), lock=True))
        lock_action = [a for a in result["actions"] if a["action"] == "lock"]
        assert lock_action[0]["status"] == "timeout"

    def test_both_create_venv_and_lock(self, tmp_path: Path) -> None:
        """Both flags produce two actions and a health_after check."""
        tools = _register_tools(tmp_path)
        mock_health = {"summary": {"score": 80}}
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""
        with (
            patch(
                "lintgate.dependency_health.full_dependency_health",
                return_value=mock_health,
            ),
            patch("shutil.which", return_value="/usr/bin/uv"),
            patch(
                "subprocess.run",
                return_value=mock_proc,
            ),
        ):
            result = json.loads(tools["dep_sync"](path=str(tmp_path), create_venv=True, lock=True))
        action_types = [a["action"] for a in result["actions"]]
        assert "create_venv" in action_types
        assert "lock" in action_types
        assert "health_after" in result

    def test_stderr_truncated_to_500(self, tmp_path: Path) -> None:
        """Long stderr is truncated to last 500 chars."""
        tools = _register_tools(tmp_path)
        mock_health = {"summary": {"score": 80}}
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "x" * 1000
        with (
            patch(
                "lintgate.dependency_health.full_dependency_health",
                return_value=mock_health,
            ),
            patch("shutil.which", return_value="/usr/bin/uv"),
            patch(
                "subprocess.run",
                return_value=mock_proc,
            ),
        ):
            result = json.loads(tools["dep_sync"](path=str(tmp_path), lock=True))
        lock_action = [a for a in result["actions"] if a["action"] == "lock"]
        assert len(lock_action[0]["stderr"]) <= 500
