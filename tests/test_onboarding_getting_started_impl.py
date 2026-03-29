"""Tests for mcp_tools/_onboarding_getting_started_impl.py — onboarding orchestration helpers."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, mock_open, patch

from mcp_tools._onboarding_getting_started_impl import (
    _DEFAULT_WORKFLOW,
    _ESSENTIAL_TOOLS,
    _TOOL_APPLICABILITY_GUIDE,
    _build_next_actions,
    _detect_mutation_guard,
    _handle_config_and_venv,
    _handle_quality_bootstrap,
    _handle_tool_installs,
    _impl_scaffold_config,
    _impl_tool_applicability_guide,
)


def _load_tool_result(json_str):
    import json as _j
    import os as _os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and _os.path.isfile(r.get("file","")):
        with open(r["file"]) as f: return _j.loads(f.read())
    return r


# ---------------------------------------------------------------------------
# _detect_mutation_guard
# ---------------------------------------------------------------------------


class TestDetectMutationGuard:
    def test_returns_true_when_hook_present(self, tmp_path):
        settings = {"hooks": {"PreToolUse": [{"hooks": [{"command": "lintgate-pre --check"}]}]}}
        with patch("builtins.open", mock_open(read_data=json.dumps(settings))):
            assert _detect_mutation_guard() is True

    def test_returns_false_when_no_hook(self, tmp_path):
        settings = {"hooks": {"PreToolUse": [{"hooks": [{"command": "other-tool"}]}]}}
        with patch("builtins.open", mock_open(read_data=json.dumps(settings))):
            assert _detect_mutation_guard() is False

    def test_returns_false_when_file_missing(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            assert _detect_mutation_guard() is False

    def test_returns_false_when_empty_hooks(self):
        settings: dict[str, dict[str, list[object]]] = {"hooks": {}}
        with patch("builtins.open", mock_open(read_data=json.dumps(settings))):
            assert _detect_mutation_guard() is False


# ---------------------------------------------------------------------------
# _handle_config_and_venv
# ---------------------------------------------------------------------------


class TestHandleConfigAndVenv:
    @patch("mcp_tools._onboarding_getting_started_impl._ot")
    def test_no_auto_setup_returns_not_requested(self, mock_ot, tmp_path):
        startup_actions: list = []
        result = _handle_config_and_venv(
            str(tmp_path), auto_setup=False, startup_actions=startup_actions, helpers={}
        )
        assert result == {"status": "not_requested"}
        assert startup_actions == []

    @patch("mcp_tools._onboarding_getting_started_impl._ot")
    def test_auto_setup_creates_config_when_missing(self, mock_ot, tmp_path):
        mock_mod = mock_ot.return_value
        mock_mod._scaffold_config_yaml.return_value = "controlplane:\n  enabled: true\n"
        mock_mod._ensure_project_venv.return_value = {"status": "existing"}

        startup_actions: list = []
        _handle_config_and_venv(
            str(tmp_path), auto_setup=True, startup_actions=startup_actions, helpers={}
        )
        config_path = os.path.join(str(tmp_path), ".claude", "lintgate.yaml")
        assert os.path.isfile(config_path)
        assert any(a["action"] == "config_scaffolded" for a in startup_actions)

    @patch("mcp_tools._onboarding_getting_started_impl._ot")
    def test_venv_created_appends_action(self, mock_ot, tmp_path):
        mock_mod = mock_ot.return_value
        mock_mod._scaffold_config_yaml.return_value = "yaml: true"
        mock_mod._ensure_project_venv.return_value = {
            "status": "created",
            "manager": "venv",
            "venv_python": "/tmp/venv/bin/python",
            "pip_ready": True,
        }

        startup_actions: list = []
        result = _handle_config_and_venv(
            str(tmp_path), auto_setup=True, startup_actions=startup_actions, helpers={}
        )
        assert result["status"] == "created"
        assert any(a["action"] == "venv_provisioned" for a in startup_actions)

    @patch("mcp_tools._onboarding_getting_started_impl._ot")
    def test_venv_error_appends_failed_action(self, mock_ot, tmp_path):
        mock_mod = mock_ot.return_value
        mock_mod._scaffold_config_yaml.return_value = "yaml: true"
        mock_mod._ensure_project_venv.return_value = {
            "status": "error",
            "manager": "venv",
            "reason": "python not found",
        }

        startup_actions: list = []
        _handle_config_and_venv(
            str(tmp_path), auto_setup=True, startup_actions=startup_actions, helpers={}
        )
        assert any(a["action"] == "venv_provision_failed" for a in startup_actions)


# ---------------------------------------------------------------------------
# _handle_tool_installs
# ---------------------------------------------------------------------------


class TestHandleToolInstalls:
    @patch("mcp_tools._onboarding_getting_started_impl._ot")
    def test_no_auto_install_returns_empty(self, mock_ot):
        with patch.dict("sys.modules", {"lintgate.tool_manifest": None}):
            mock_mod = mock_ot.return_value
            mock_mod._collect_external_tool_gaps.return_value = {"missing_tools": []}
            startup_actions: list = []
            result = _handle_tool_installs(
                "/proj", auto_install=False, startup_actions=startup_actions
            )
            assert result == []

    @patch("mcp_tools._onboarding_getting_started_impl._ot")
    def test_legacy_fallback_installs_missing(self, mock_ot):
        with patch.dict("sys.modules", {"lintgate.tool_manifest": None}):
            mock_mod = mock_ot.return_value
            mock_mod._collect_external_tool_gaps.return_value = {
                "missing_tools": [{"tool": "ruff", "install_command": "pip install ruff"}]
            }
            mock_mod._auto_install_optional_tools.return_value = [
                {"tool": "ruff", "status": "installed"}
            ]

            startup_actions: list = []
            result = _handle_tool_installs(
                "/proj", auto_install=True, startup_actions=startup_actions
            )
            assert len(result) == 1
            assert any(a["action"] == "optional_tool_install_attempted" for a in startup_actions)

    @patch("lintgate.tool_manifest.reconcile_with_registry", return_value=[])
    @patch("lintgate.tool_manifest.install_missing_tools", return_value=[{"tool": "ruff"}])
    @patch("lintgate.tool_manifest.check_tool_health", return_value=[])
    @patch("lintgate.tool_manifest.load_toolchain_manifest", return_value={})
    def test_manifest_path_installs_and_reports(self, _load, _health, _install, _reconcile):
        startup_actions: list = []
        result = _handle_tool_installs("/proj", auto_install=True, startup_actions=startup_actions)
        assert len(result) == 1
        assert any(a["action"] == "toolchain_install_attempted" for a in startup_actions)

    @patch("lintgate.tool_manifest.reconcile_with_registry", return_value=["drift warning"])
    @patch("lintgate.tool_manifest.install_missing_tools", return_value=[])
    @patch("lintgate.tool_manifest.check_tool_health", return_value=[])
    @patch("lintgate.tool_manifest.load_toolchain_manifest", return_value={})
    def test_drift_warnings_appended(self, _load, _health, _install, _reconcile):
        startup_actions: list = []
        _handle_tool_installs("/proj", auto_install=True, startup_actions=startup_actions)
        assert any(a["action"] == "toolchain_drift_detected" for a in startup_actions)


# ---------------------------------------------------------------------------
# _handle_quality_bootstrap
# ---------------------------------------------------------------------------


class TestHandleQualityBootstrap:
    @patch("mcp_tools._onboarding_getting_started_impl._ot")
    @patch("lintgate.quality_infra.audit_quality_infrastructure")
    @patch("mcp_tools.setup_github_quality.setup_github_quality")
    def test_no_auto_setup_returns_not_requested(self, mock_setup, mock_audit, mock_ot):
        mock_mod = mock_ot.return_value
        mock_mod._detect_github_remote.return_value = {"detected": False}
        mock_audit_result = MagicMock()
        mock_audit_result.complete = True
        mock_audit_result.has_github_remote = False
        mock_audit.return_value = mock_audit_result

        startup_actions: list = []
        result = _handle_quality_bootstrap(
            "/proj", auto_setup=False, startup_actions=startup_actions
        )
        assert result == {"status": "not_requested"}

    @patch("mcp_tools._onboarding_getting_started_impl._ot")
    @patch("lintgate.quality_infra.audit_quality_infrastructure")
    @patch("mcp_tools.setup_github_quality.setup_github_quality")
    def test_auto_setup_with_github_bootstraps(self, mock_setup, mock_audit, mock_ot):
        mock_mod = mock_ot.return_value
        mock_mod._detect_github_remote.return_value = {"detected": True}
        mock_audit_result = MagicMock()
        mock_audit_result.complete = False
        mock_audit_result.has_github_remote = False
        mock_audit.return_value = mock_audit_result
        mock_setup.return_value = json.dumps({"status": "created"})

        startup_actions: list = []
        result = _handle_quality_bootstrap(
            "/proj", auto_setup=True, startup_actions=startup_actions
        )
        assert result["status"] == "created"
        assert any(a["action"] == "github_quality_bootstrapped" for a in startup_actions)


# ---------------------------------------------------------------------------
# _build_next_actions
# ---------------------------------------------------------------------------


class TestBuildNextActions:
    @patch("mcp_tools._onboarding_getting_started_impl._ot")
    def test_config_enabled_no_scaffold_action(self, mock_ot, tmp_path):
        mock_mod = mock_ot.return_value
        mock_mod._venv_create_command.return_value = (["python", "-m", "venv", ".venv"], "venv")
        mock_mod._format_cmd.return_value = "python -m venv .venv"

        actions = _build_next_actions(
            str(tmp_path),
            config_status={"config_state": "config_enabled"},
            venv_python_after="/tmp/venv/bin/python",
            tool_gaps_after={"missing_tools": []},
        )
        tool_names = [a["tool"] for a in actions]
        assert "controlplane_run" in tool_names
        assert "scaffold_config" not in tool_names

    @patch("mcp_tools._onboarding_getting_started_impl._ot")
    def test_no_config_includes_scaffold(self, mock_ot, tmp_path):
        mock_mod = mock_ot.return_value
        mock_mod._venv_create_command.return_value = (["python", "-m", "venv", ".venv"], "venv")
        mock_mod._format_cmd.return_value = "python -m venv .venv"

        actions = _build_next_actions(
            str(tmp_path),
            config_status={"config_state": "no_config"},
            venv_python_after="/tmp/venv/bin/python",
            tool_gaps_after={"missing_tools": []},
        )
        tool_names = [a["tool"] for a in actions]
        assert "scaffold_config" in tool_names

    @patch("mcp_tools._onboarding_getting_started_impl._ot")
    def test_no_venv_includes_bash_create(self, mock_ot, tmp_path):
        mock_mod = mock_ot.return_value
        mock_mod._venv_create_command.return_value = (["python", "-m", "venv", ".venv"], "venv")
        mock_mod._format_cmd.return_value = "python -m venv .venv"

        actions = _build_next_actions(
            str(tmp_path),
            config_status={"config_state": "config_enabled"},
            venv_python_after=None,
            tool_gaps_after={"missing_tools": []},
        )
        bash_actions = [a for a in actions if a["tool"] == "Bash"]
        assert len(bash_actions) >= 1
        assert "venv" in bash_actions[0]["reason"].lower() or "venv" in bash_actions[0]["example"]

    @patch("mcp_tools._onboarding_getting_started_impl._ot")
    def test_missing_tools_get_install_actions(self, mock_ot, tmp_path):
        mock_mod = mock_ot.return_value
        mock_mod._venv_create_command.return_value = (["python", "-m", "venv", ".venv"], "venv")
        mock_mod._format_cmd.return_value = "python -m venv .venv"

        actions = _build_next_actions(
            str(tmp_path),
            config_status={"config_state": "config_enabled"},
            venv_python_after="/bin/python",
            tool_gaps_after={
                "missing_tools": [{"tool": "ruff", "install_command": "pip install ruff"}]
            },
        )
        install_actions = [a for a in actions if "ruff" in a.get("reason", "")]
        assert len(install_actions) == 1


# ---------------------------------------------------------------------------
# _impl_tool_applicability_guide
# ---------------------------------------------------------------------------


class TestImplToolApplicabilityGuide:
    def test_returns_json_string(self):
        result = _impl_tool_applicability_guide(helpers={})
        parsed = _load_tool_result(result)
        assert "controlplane_run" in parsed
        assert "lint_files" in parsed
        assert parsed["getting_started"]["cadence"] == "Onboarding only."

    def test_uses_custom_json_dumps(self):
        custom_output = '{"custom": true}'
        helpers = {"_json_dumps": MagicMock(return_value=custom_output)}
        result = _impl_tool_applicability_guide(helpers)
        assert result == custom_output
        helpers["_json_dumps"].assert_called_once_with(_TOOL_APPLICABILITY_GUIDE)


# ---------------------------------------------------------------------------
# _impl_scaffold_config
# ---------------------------------------------------------------------------


class TestImplScaffoldConfig:
    @patch("mcp_tools._onboarding_getting_started_impl._ot")
    def test_preview_mode(self, mock_ot, tmp_path):
        mock_mod = mock_ot.return_value
        mock_mod._scaffold_config_yaml.return_value = "yaml: content"
        helpers = {"_validate_project_root": lambda p: str(tmp_path)}
        result = _load_tool_result(_impl_scaffold_config(helpers, str(tmp_path), write=False))
        assert result["status"] == "preview"
        assert result["yaml"] == "yaml: content"

    @patch("mcp_tools._onboarding_getting_started_impl._ot")
    def test_write_mode(self, mock_ot, tmp_path):
        mock_mod = mock_ot.return_value
        mock_mod._scaffold_config_yaml.return_value = "yaml: content"
        helpers = {"_validate_project_root": lambda p: str(tmp_path)}
        result = _load_tool_result(_impl_scaffold_config(helpers, str(tmp_path), write=True))
        assert result["status"] == "written"
        config_path = os.path.join(str(tmp_path), ".claude", "lintgate.yaml")
        assert os.path.isfile(config_path)

    @patch("mcp_tools._onboarding_getting_started_impl._ot")
    def test_existing_config_preview(self, mock_ot, tmp_path):
        mock_mod = mock_ot.return_value
        mock_mod._scaffold_config_yaml.return_value = "yaml: new"
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text("yaml: old")
        helpers = {"_validate_project_root": lambda p: str(tmp_path)}
        result = _load_tool_result(_impl_scaffold_config(helpers, str(tmp_path), write=False))
        assert result["status"] == "preview_existing"
        assert "message" in result


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_default_workflow_has_steps(self):
        assert len(_DEFAULT_WORKFLOW) == 5
        assert "getting_started" in _DEFAULT_WORKFLOW[0]

    def test_essential_tools_keys(self):
        expected = {
            "lint_files",
            "lint_project",
            "lint_fix",
            "controlplane_run",
            "controlplane_get_details",
            "bootstrap_context_files",
        }
        assert set(_ESSENTIAL_TOOLS.keys()) == expected

    def test_tool_applicability_guide_has_all_tools(self):
        expected_tools = {
            "controlplane_run",
            "lint_files",
            "lint_project",
            "lint_fix",
            "scaffold_config",
            "getting_started",
        }
        assert set(_TOOL_APPLICABILITY_GUIDE.keys()) == expected_tools
