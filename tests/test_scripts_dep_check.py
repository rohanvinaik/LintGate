"""Behavioral tests for scripts/dep_check.py.

These tests exercise cmd_health / cmd_sync / cmd_toolchain directly by
passing argparse.Namespace objects and capturing stdout. The MCP layer
in mcp_tools/dep_tools.py just shells out to this script, so its
subprocess-argv tests live in tests/test_mcp_dep_tools.py.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

from scripts.dep_check import cmd_health, cmd_sync, cmd_toolchain


def _load_emitted(capsys) -> dict:
    """Read the slim envelope printed to stdout, then load the full file."""
    captured = capsys.readouterr()
    envelope = json.loads(captured.out.strip().splitlines()[-1])
    if "file" in envelope:
        with open(envelope["file"]) as f:
            return json.loads(f.read())
    return envelope


def _ns(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


# ── cmd_health ───────────────────────────────────────────────────────────


class TestCmdHealth:
    def test_returns_valid_json(self, tmp_path: Path, capsys) -> None:
        mock_health = {"summary": {"score": 90, "issues": 0}, "checks": []}
        with patch(
            "lintgate.dependency_health.full_dependency_health",
            return_value=mock_health,
        ):
            cmd_health(_ns(path=str(tmp_path)))
        result = _load_emitted(capsys)
        assert result["summary"]["score"] == 90

    def test_passes_project_root(self, tmp_path: Path, capsys) -> None:
        mock_health: dict[str, Any] = {"summary": {}, "checks": []}
        with patch(
            "lintgate.dependency_health.full_dependency_health",
            return_value=mock_health,
        ) as mock_fn:
            cmd_health(_ns(path=str(tmp_path)))
        _load_emitted(capsys)
        mock_fn.assert_called_once_with(str(tmp_path))

    def test_next_actions_included_when_issues(self, tmp_path: Path, capsys) -> None:
        mock_health = {
            "summary": {"score": 50},
            "issues": [{"type": "outdated"}, {"type": "vulnerable"}],
        }
        with patch(
            "lintgate.dependency_health.full_dependency_health",
            return_value=mock_health,
        ):
            cmd_health(_ns(path=str(tmp_path)))
        captured = capsys.readouterr()
        envelope = json.loads(captured.out.strip().splitlines()[-1])
        assert "next_actions" in envelope


# ── cmd_sync ─────────────────────────────────────────────────────────────


class TestCmdSync:
    def test_status_only_no_actions(self, tmp_path: Path, capsys) -> None:
        mock_health = {"summary": {"score": 80}}
        with patch(
            "lintgate.dependency_health.full_dependency_health",
            return_value=mock_health,
        ), patch("shutil.which", return_value="/usr/bin/uv"):
            cmd_sync(_ns(path=str(tmp_path), create_venv=False, lock=False))
        result = _load_emitted(capsys)
        assert result["project"] == str(tmp_path)
        assert result["actions"] == []
        assert "health_before" in result
        assert "health_after" not in result

    def test_uv_not_found(self, tmp_path: Path, capsys) -> None:
        mock_health = {"summary": {"score": 50}}
        with patch(
            "lintgate.dependency_health.full_dependency_health",
            return_value=mock_health,
        ), patch("shutil.which", return_value=None):
            cmd_sync(_ns(path=str(tmp_path), create_venv=True, lock=False))
        result = _load_emitted(capsys)
        assert "error" in result
        assert "uv not found" in result["error"]

    def test_create_venv_skips_existing(self, tmp_path: Path, capsys) -> None:
        (tmp_path / ".venv").mkdir()
        mock_health = {"summary": {"score": 80}}
        with patch(
            "lintgate.dependency_health.full_dependency_health",
            return_value=mock_health,
        ), patch("shutil.which", return_value="/usr/bin/uv"):
            cmd_sync(_ns(path=str(tmp_path), create_venv=True, lock=False))
        result = _load_emitted(capsys)
        venv_action = [a for a in result["actions"] if a["action"] == "create_venv"]
        assert len(venv_action) == 1
        assert venv_action[0]["status"] == "skipped"
        assert "already exists" in venv_action[0]["reason"]

    def test_create_venv_success(self, tmp_path: Path, capsys) -> None:
        mock_health = {"summary": {"score": 80}}
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""
        with patch(
            "lintgate.dependency_health.full_dependency_health",
            return_value=mock_health,
        ), patch("shutil.which", return_value="/usr/bin/uv"), patch(
            "subprocess.run", return_value=mock_proc,
        ):
            cmd_sync(_ns(path=str(tmp_path), create_venv=True, lock=False))
        result = _load_emitted(capsys)
        venv_action = [a for a in result["actions"] if a["action"] == "create_venv"]
        assert venv_action[0]["status"] == "ok"
        assert "health_after" in result

    def test_create_venv_error(self, tmp_path: Path, capsys) -> None:
        mock_health = {"summary": {"score": 80}}
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "some error occurred"
        with patch(
            "lintgate.dependency_health.full_dependency_health",
            return_value=mock_health,
        ), patch("shutil.which", return_value="/usr/bin/uv"), patch(
            "subprocess.run", return_value=mock_proc,
        ):
            cmd_sync(_ns(path=str(tmp_path), create_venv=True, lock=False))
        result = _load_emitted(capsys)
        venv_action = [a for a in result["actions"] if a["action"] == "create_venv"]
        assert venv_action[0]["status"] == "error"
        assert venv_action[0]["returncode"] == 1

    def test_create_venv_timeout(self, tmp_path: Path, capsys) -> None:
        mock_health = {"summary": {"score": 80}}
        with patch(
            "lintgate.dependency_health.full_dependency_health",
            return_value=mock_health,
        ), patch("shutil.which", return_value="/usr/bin/uv"), patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="uv venv", timeout=60),
        ):
            cmd_sync(_ns(path=str(tmp_path), create_venv=True, lock=False))
        result = _load_emitted(capsys)
        venv_action = [a for a in result["actions"] if a["action"] == "create_venv"]
        assert venv_action[0]["status"] == "timeout"

    def test_lock_success(self, tmp_path: Path, capsys) -> None:
        mock_health = {"summary": {"score": 80}}
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""
        with patch(
            "lintgate.dependency_health.full_dependency_health",
            return_value=mock_health,
        ), patch("shutil.which", return_value="/usr/bin/uv"), patch(
            "subprocess.run", return_value=mock_proc,
        ):
            cmd_sync(_ns(path=str(tmp_path), create_venv=False, lock=True))
        result = _load_emitted(capsys)
        lock_action = [a for a in result["actions"] if a["action"] == "lock"]
        assert len(lock_action) == 1
        assert lock_action[0]["status"] == "ok"
        assert "health_after" in result

    def test_lock_error(self, tmp_path: Path, capsys) -> None:
        mock_health = {"summary": {"score": 80}}
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "lock failed"
        with patch(
            "lintgate.dependency_health.full_dependency_health",
            return_value=mock_health,
        ), patch("shutil.which", return_value="/usr/bin/uv"), patch(
            "subprocess.run", return_value=mock_proc,
        ):
            cmd_sync(_ns(path=str(tmp_path), create_venv=False, lock=True))
        result = _load_emitted(capsys)
        lock_action = [a for a in result["actions"] if a["action"] == "lock"]
        assert lock_action[0]["status"] == "error"

    def test_lock_timeout(self, tmp_path: Path, capsys) -> None:
        mock_health = {"summary": {"score": 80}}
        with patch(
            "lintgate.dependency_health.full_dependency_health",
            return_value=mock_health,
        ), patch("shutil.which", return_value="/usr/bin/uv"), patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="uv lock", timeout=120),
        ):
            cmd_sync(_ns(path=str(tmp_path), create_venv=False, lock=True))
        result = _load_emitted(capsys)
        lock_action = [a for a in result["actions"] if a["action"] == "lock"]
        assert lock_action[0]["status"] == "timeout"

    def test_both_create_venv_and_lock(self, tmp_path: Path, capsys) -> None:
        mock_health = {"summary": {"score": 80}}
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""
        with patch(
            "lintgate.dependency_health.full_dependency_health",
            return_value=mock_health,
        ), patch("shutil.which", return_value="/usr/bin/uv"), patch(
            "subprocess.run", return_value=mock_proc,
        ):
            cmd_sync(_ns(path=str(tmp_path), create_venv=True, lock=True))
        result = _load_emitted(capsys)
        action_types = [a["action"] for a in result["actions"]]
        assert "create_venv" in action_types
        assert "lock" in action_types
        assert "health_after" in result

    def test_stderr_truncated_to_500(self, tmp_path: Path, capsys) -> None:
        mock_health = {"summary": {"score": 80}}
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "x" * 1000
        with patch(
            "lintgate.dependency_health.full_dependency_health",
            return_value=mock_health,
        ), patch("shutil.which", return_value="/usr/bin/uv"), patch(
            "subprocess.run", return_value=mock_proc,
        ):
            cmd_sync(_ns(path=str(tmp_path), create_venv=False, lock=True))
        result = _load_emitted(capsys)
        lock_action = [a for a in result["actions"] if a["action"] == "lock"]
        assert len(lock_action[0]["stderr"]) <= 500


# ── cmd_toolchain ────────────────────────────────────────────────────────


class TestCmdToolchain:
    def _mock_report(self, all_met: bool = True, tool_count: int = 2):
        report = MagicMock()
        report.summary = "2 tools checked"
        report.all_required_met = all_met
        report.drift_warnings = []
        tools = []
        for i in range(tool_count):
            t = MagicMock()
            t.id = f"tool{i}"
            t.installed = True
            t.version = "1.0"
            t.location = f"/usr/bin/tool{i}"
            t.requirement.required = True
            t.requirement.kind = "cli"
            t.install_hint = None
            tools.append(t)
        report.tools = tools
        return report

    def test_returns_tool_list(self, tmp_path: Path, capsys) -> None:
        with patch(
            "lintgate.tool_manifest.full_toolchain_report",
            return_value=self._mock_report(),
        ):
            cmd_toolchain(_ns(path=str(tmp_path), install_missing=False))
        result = _load_emitted(capsys)
        assert result["all_required_met"] is True
        assert len(result["tools"]) == 2
        assert result["tools"][0]["kind"] == "cli"
        assert result["tools"][0]["location"] == "/usr/bin/tool0"
