"""Tests for mcp_tools/_onboarding_venv_impl.py helper functions."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from mcp_tools._onboarding_venv_impl import (
    _auto_install_optional_tools,
    _collect_external_tool_gaps,
    _ensure_project_venv,
    _format_cmd,
    _install_command_for_package,
    _install_commands_for_package,
    _linter_available,
    _project_venv_python,
    _tool_package_name,
    _venv_create_command,
)

# ---------------------------------------------------------------------------
# _tool_package_name
# ---------------------------------------------------------------------------


class TestToolPackageName:
    def test_pip_audit_maps_correctly(self):
        assert _tool_package_name("pip-audit") == "pip-audit"

    def test_ty_maps_correctly(self):
        assert _tool_package_name("ty") == "ty"

    def test_unknown_tool_passes_through(self):
        assert _tool_package_name("mypy") == "mypy"


# ---------------------------------------------------------------------------
# _project_venv_python
# ---------------------------------------------------------------------------


class TestProjectVenvPython:
    def test_finds_dotenv_python(self, tmp_path):
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        py = venv_bin / "python"
        py.write_text("#!/usr/bin/env python3")
        assert _project_venv_python(str(tmp_path)) == str(py)

    def test_returns_none_when_absent(self, tmp_path):
        assert _project_venv_python(str(tmp_path)) is None


# ---------------------------------------------------------------------------
# _format_cmd
# ---------------------------------------------------------------------------


class TestFormatCmd:
    def test_simple_args(self):
        assert _format_cmd(["pip", "install", "ty"]) == "pip install ty"

    def test_quotes_special_chars(self):
        result = _format_cmd(["python", "-c", "import sys; print(sys.path)"])
        assert "'" in result  # shlex.quote wraps in quotes


# ---------------------------------------------------------------------------
# _linter_available
# ---------------------------------------------------------------------------


class TestLinterAvailable:
    def test_calls_with_project_root(self):
        linter = MagicMock()
        linter.available.return_value = True
        assert _linter_available(linter, "/proj") is True
        linter.available.assert_called_with(project_root="/proj")

    def test_fallback_to_no_args(self):
        linter = MagicMock()
        linter.available.side_effect = [TypeError, True]
        assert _linter_available(linter, "/proj") is True


# ---------------------------------------------------------------------------
# _venv_create_command
# ---------------------------------------------------------------------------


class TestVenvCreateCommand:
    def test_prefers_uv_when_available(self):
        mock_ot = MagicMock()
        mock_ot.shutil.which.return_value = "/usr/local/bin/uv"
        with patch("mcp_tools._onboarding_venv_impl._ot", return_value=mock_ot):
            cmd, manager = _venv_create_command()
            assert cmd == ["/usr/local/bin/uv", "venv", ".venv"]
            assert manager == "uv"

    def test_falls_back_to_python_venv(self):
        mock_ot = MagicMock()
        mock_ot.shutil.which.return_value = None
        mock_ot.sys.executable = "/usr/bin/python3"
        with patch("mcp_tools._onboarding_venv_impl._ot", return_value=mock_ot):
            cmd, manager = _venv_create_command()
            assert cmd == ["/usr/bin/python3", "-m", "venv", ".venv"]
            assert manager == "python_venv"


# ---------------------------------------------------------------------------
# _ensure_project_venv
# ---------------------------------------------------------------------------


class TestEnsureProjectVenv:
    def test_returns_present_when_venv_exists(self):
        mock_ot = MagicMock()
        mock_ot._project_venv_python.return_value = "/proj/.venv/bin/python"
        with patch("mcp_tools._onboarding_venv_impl._ot", return_value=mock_ot):
            result = _ensure_project_venv("/proj")
            assert result["status"] == "present"
            assert result["venv_python"] == "/proj/.venv/bin/python"

    def test_creates_venv_successfully(self):
        mock_ot = MagicMock()
        mock_ot._project_venv_python.side_effect = [None, "/proj/.venv/bin/python"]
        mock_ot._venv_create_command.return_value = (["uv", "venv", ".venv"], "uv")
        create_result = MagicMock()
        create_result.returncode = 0
        pip_result = MagicMock()
        pip_result.returncode = 0
        mock_ot.subprocess.run.side_effect = [create_result, pip_result]
        mock_ot.subprocess.TimeoutExpired = subprocess.TimeoutExpired

        with patch("mcp_tools._onboarding_venv_impl._ot", return_value=mock_ot):
            result = _ensure_project_venv("/proj")
            assert result["status"] == "created"
            assert result["manager"] == "uv"
            assert result["pip_ready"] is True

    def test_returns_error_on_create_failure(self):
        mock_ot = MagicMock()
        mock_ot._project_venv_python.return_value = None
        mock_ot._venv_create_command.return_value = (
            ["python3", "-m", "venv", ".venv"],
            "python_venv",
        )
        create_result = MagicMock()
        create_result.returncode = 1
        create_result.stderr = "Permission denied"
        mock_ot.subprocess.run.return_value = create_result
        mock_ot.subprocess.TimeoutExpired = subprocess.TimeoutExpired

        with patch("mcp_tools._onboarding_venv_impl._ot", return_value=mock_ot):
            result = _ensure_project_venv("/proj")
            assert result["status"] == "error"
            assert result["reason"] == "venv_create_failed"


# ---------------------------------------------------------------------------
# _install_commands_for_package
# ---------------------------------------------------------------------------


class TestInstallCommandsForPackage:
    def test_returns_empty_without_venv(self):
        mock_ot = MagicMock()
        mock_ot._project_venv_python.return_value = None
        with patch("mcp_tools._onboarding_venv_impl._ot", return_value=mock_ot):
            assert _install_commands_for_package("/proj", "ty") == []

    def test_returns_uv_and_pip_commands(self):
        mock_ot = MagicMock()
        mock_ot._project_venv_python.return_value = "/proj/.venv/bin/python"
        mock_ot.shutil.which.return_value = "/usr/local/bin/uv"
        with patch("mcp_tools._onboarding_venv_impl._ot", return_value=mock_ot):
            cmds = _install_commands_for_package("/proj", "ty")
            assert len(cmds) == 2
            assert cmds[0] == [
                "/usr/local/bin/uv",
                "pip",
                "install",
                "--python",
                "/proj/.venv/bin/python",
                "ty",
            ]
            assert cmds[1] == ["/proj/.venv/bin/python", "-m", "pip", "install", "ty"]

    def test_returns_only_pip_when_no_uv(self):
        mock_ot = MagicMock()
        mock_ot._project_venv_python.return_value = "/proj/.venv/bin/python"
        mock_ot.shutil.which.return_value = None
        with patch("mcp_tools._onboarding_venv_impl._ot", return_value=mock_ot):
            cmds = _install_commands_for_package("/proj", "ruff")
            assert len(cmds) == 1
            assert cmds[0] == ["/proj/.venv/bin/python", "-m", "pip", "install", "ruff"]


# ---------------------------------------------------------------------------
# _install_command_for_package
# ---------------------------------------------------------------------------


class TestInstallCommandForPackage:
    def test_returns_first_command(self):
        with patch(
            "mcp_tools._onboarding_venv_impl._install_commands_for_package",
            return_value=[["uv", "pip", "install", "ty"], ["pip", "install", "ty"]],
        ):
            result = _install_command_for_package("/proj", "ty")
            assert result == ["uv", "pip", "install", "ty"]

    def test_returns_none_when_no_commands(self):
        with patch(
            "mcp_tools._onboarding_venv_impl._install_commands_for_package",
            return_value=[],
        ):
            assert _install_command_for_package("/proj", "ty") is None


# ---------------------------------------------------------------------------
# _collect_external_tool_gaps
# ---------------------------------------------------------------------------


class TestCollectExternalToolGaps:
    def test_identifies_missing_tools(self):
        linter = MagicMock()
        linter.required_tool = "ty"
        linter.available.return_value = False

        registry = {"ty_check": linter}
        mock_config = MagicMock()

        with (
            patch("lintgate.config.load_config", return_value=mock_config),
            patch("lintgate.registry.build_registry", return_value=registry),
            patch(
                "mcp_tools._onboarding_venv_impl._install_command_for_package",
                return_value=None,
            ),
        ):
            result = _collect_external_tool_gaps("/proj")
            assert len(result["missing_tools"]) == 1
            assert result["missing_tools"][0]["tool"] == "ty"

    def test_no_gaps_when_all_available(self):
        linter = MagicMock()
        linter.required_tool = "ruff"
        linter.available.return_value = True

        registry = {"ruff_check": linter}
        mock_config = MagicMock()

        with (
            patch("lintgate.config.load_config", return_value=mock_config),
            patch("lintgate.registry.build_registry", return_value=registry),
        ):
            result = _collect_external_tool_gaps("/proj")
            assert result["missing_tools"] == []


# ---------------------------------------------------------------------------
# _auto_install_optional_tools
# ---------------------------------------------------------------------------


class TestAutoInstallOptionalTools:
    def test_skips_non_optional_tools(self):
        mock_ot = MagicMock()
        with patch("mcp_tools._onboarding_venv_impl._ot", return_value=mock_ot):
            result = _auto_install_optional_tools(
                "/proj",
                [{"tool": "ruff", "package": "ruff"}],
            )
            assert result == []

    def test_installs_optional_tool_successfully(self):
        mock_ot = MagicMock()
        mock_ot._install_commands_for_package.return_value = [
            ["/venv/bin/python", "-m", "pip", "install", "ty"]
        ]
        install_result = MagicMock()
        install_result.returncode = 0
        install_result.stderr = ""
        mock_ot.subprocess.run.return_value = install_result
        mock_ot.subprocess.TimeoutExpired = subprocess.TimeoutExpired

        with patch("mcp_tools._onboarding_venv_impl._ot", return_value=mock_ot):
            result = _auto_install_optional_tools(
                "/proj",
                [{"tool": "ty", "package": "ty"}],
            )
            assert len(result) == 1
            assert result[0]["status"] == "installed"
            assert result[0]["tool"] == "ty"

    def test_reports_failure_on_install_error(self):
        mock_ot = MagicMock()
        mock_ot._install_commands_for_package.return_value = [["pip", "install", "pip-audit"]]
        install_result = MagicMock()
        install_result.returncode = 1
        install_result.stderr = "some error"
        mock_ot.subprocess.run.return_value = install_result
        mock_ot.subprocess.TimeoutExpired = subprocess.TimeoutExpired

        with patch("mcp_tools._onboarding_venv_impl._ot", return_value=mock_ot):
            result = _auto_install_optional_tools(
                "/proj",
                [{"tool": "pip-audit", "package": "pip-audit"}],
            )
            assert len(result) == 1
            assert result[0]["status"] == "error"
            assert result[0]["reason"] == "all_install_commands_failed"
