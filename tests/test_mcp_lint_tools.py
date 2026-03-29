"""Coverage tests for mcp_tools/lint_tools.py.

Covers helper functions (_tool_package_name, _project_venv_python, _format_cmd,
_missing_tool_hints, _linter_available) and the MCP tool functions registered via
register() (lint_files, lint_project, lint_get_details, lint_status,
audit_tool_versions, lint_fix).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

import pytest

from mcp_tools.lint_tools import (
    _format_cmd,
    _linter_available,
    _missing_tool_hints,
    _project_venv_python,
    _tool_package_name,
    register,
)


def _load_tool_result(json_str):
    import json
    import os
    r = json.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and os.path.isfile(r.get("file","")):
        with open(r["file"]) as f: return json.loads(f.read())
    return r


# ── _FakeMCP for registering tools ──────────────────────────────────


class _FakeMCP:
    """Minimal MCP stub that captures decorated functions."""

    def tool(self):
        def _decorator(fn):
            return fn

        return _decorator


def _stub_helpers(**overrides):
    """Build a helpers dict with reasonable defaults; override per-test."""
    defaults = {
        "_validate_project_root": lambda p, **kw: p or "/tmp/test",
        "_resolve_files": lambda files, root: (files, []),
        "_run_lint": lambda files, root, tier, strict, output_mode="compact": {
            "run_id": "r1",
            "blocking_count": 0,
        },
        "_collect_python_files": lambda root: [f"{root}/a.py"],
        "_json_dumps": lambda obj, mode="compact": json.dumps(obj),
        "_build_onboarding_status": lambda root: {"config_state": "config_enabled"},
    }
    defaults.update(overrides)
    return defaults


def _register(**helper_overrides):
    tools = register(_FakeMCP(), _stub_helpers(**helper_overrides))
    return tools


# ── _tool_package_name ───────────────────────────────────────────────


class TestToolPackageName:
    def test_pip_audit_maps_correctly(self):
        assert _tool_package_name("pip-audit") == "pip-audit"

    def test_other_tool_passes_through(self):
        assert _tool_package_name("ruff") == "ruff"

    def test_arbitrary_name(self):
        assert _tool_package_name("my-tool") == "my-tool"


# ── _project_venv_python ────────────────────────────────────────────


class TestProjectVenvPython:
    def test_finds_venv(self, tmp_path):
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        python = venv_bin / "python"
        python.touch()
        result = _project_venv_python(str(tmp_path))
        assert result == str(python)

    def test_finds_venv_second_candidate(self, tmp_path):
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        python = venv_bin / "python"
        python.touch()
        result = _project_venv_python(str(tmp_path))
        assert result == str(python)

    def test_finds_env_third_candidate(self, tmp_path):
        env_bin = tmp_path / "env" / "bin"
        env_bin.mkdir(parents=True)
        python = env_bin / "python"
        python.touch()
        result = _project_venv_python(str(tmp_path))
        assert result == str(python)

    def test_returns_none_when_no_venv(self, tmp_path):
        assert _project_venv_python(str(tmp_path)) is None

    def test_returns_none_when_python_not_file(self, tmp_path):
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        # python exists as a directory, not a file
        (venv_bin / "python").mkdir()
        assert _project_venv_python(str(tmp_path)) is None


# ── _format_cmd ──────────────────────────────────────────────────────


class TestFormatCmd:
    def test_simple_command(self):
        assert _format_cmd(["ls", "-la"]) == "ls -la"

    def test_quoting_spaces(self):
        result = _format_cmd(["echo", "hello world"])
        assert result == "echo 'hello world'"

    def test_empty_list(self):
        assert _format_cmd([]) == ""


# ── _linter_available ────────────────────────────────────────────────


class TestLinterAvailable:
    def test_available_with_kwarg(self):
        linter = SimpleNamespace(available=lambda project_root="": True)
        assert _linter_available(linter, "/some/path") is True

    def test_unavailable_with_kwarg(self):
        linter = SimpleNamespace(available=lambda project_root="": False)
        assert _linter_available(linter, "/some/path") is False

    def test_falls_back_to_no_arg(self):
        """Old linters that do not accept project_root keyword."""

        def avail():
            return True

        linter = SimpleNamespace(available=avail)
        assert _linter_available(linter, "/some/path") is True

    def test_falls_back_to_no_arg_false(self):
        def avail():
            return False

        linter = SimpleNamespace(available=avail)
        assert _linter_available(linter, "/some/path") is False


# ── _missing_tool_hints ──────────────────────────────────────────────


class TestMissingToolHints:
    def test_no_missing_tools(self, tmp_path):
        linter = SimpleNamespace(
            required_tool="ruff",
            available=lambda project_root="": True,
        )
        hints = _missing_tool_hints(str(tmp_path), {"ruff": linter})
        assert hints == []

    def test_missing_tool_without_venv(self, tmp_path):
        linter = SimpleNamespace(
            required_tool="ruff",
            available=lambda project_root="": False,
        )
        hints = _missing_tool_hints(str(tmp_path), {"ruff_checker": linter})
        assert len(hints) == 1
        hint = hints[0]
        assert hint["tool"] == "ruff"
        assert hint["package"] == "ruff"
        assert "ruff_checker" in hint["required_by"]
        assert hint["auto_installable"] is False
        assert hint["install_command"] == "pip install ruff"

    def test_missing_tool_with_venv(self, tmp_path):
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").touch()

        linter = SimpleNamespace(
            required_tool="ty",
            available=lambda project_root="": False,
        )
        hints = _missing_tool_hints(str(tmp_path), {"ty_check": linter})
        assert len(hints) == 1
        hint = hints[0]
        assert hint["auto_installable"] is True
        assert ".venv/bin/python" in hint["install_command"]

    def test_pip_audit_auto_installable_with_venv(self, tmp_path):
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").touch()

        linter = SimpleNamespace(
            required_tool="pip-audit",
            available=lambda project_root="": False,
        )
        hints = _missing_tool_hints(str(tmp_path), {"audit": linter})
        assert hints[0]["auto_installable"] is True
        assert hints[0]["package"] == "pip-audit"

    def test_linter_without_required_tool_skipped(self, tmp_path):
        linter = SimpleNamespace(required_tool=None)
        hints = _missing_tool_hints(str(tmp_path), {"plain": linter})
        assert hints == []

    def test_multiple_linters_same_tool_dedup(self, tmp_path):
        linter_a = SimpleNamespace(
            required_tool="ruff",
            available=lambda project_root="": False,
        )
        linter_b = SimpleNamespace(
            required_tool="ruff",
            available=lambda project_root="": False,
        )
        hints = _missing_tool_hints(str(tmp_path), {"a": linter_a, "b": linter_b})
        assert len(hints) == 1
        assert set(hints[0]["required_by"]) == {"a", "b"}


# ── lint_files tool ──────────────────────────────────────────────────


class TestLintFiles:
    def test_basic_invocation(self, tmp_path):
        f = tmp_path / "a.py"
        f.touch()
        tools = _register()
        result = _load_tool_result(tools["lint_files"](files=[str(f)]))
        assert result["run_id"] == "r1"

    def test_raises_when_no_files(self):
        tools = _register()
        with pytest.raises(ValueError, match="No files specified"):
            tools["lint_files"](files=[])

    def test_raises_when_no_existing_files(self, tmp_path):
        tools = _register(
            _resolve_files=lambda files, root: ([], ["/missing.py"]),
        )
        with pytest.raises(ValueError, match="No specified files exist"):
            tools["lint_files"](files=["/missing.py"])

    def test_missing_files_reported(self):
        tools = _register(
            _resolve_files=lambda files, root: (["/a.py"], ["/b.py"]),
        )
        result = _load_tool_result(tools["lint_files"](files=["/a.py", "/b.py"]))
        assert result["missing_files"] == ["/b.py"]

    def test_explicit_project_root(self, tmp_path):
        captured = {}

        def mock_validate(p, **kw):
            captured["root"] = p
            return p

        tools = _register(_validate_project_root=mock_validate)
        tools["lint_files"](files=["/a.py"], project_root="/my/project")
        assert captured["root"] == "/my/project"


# ── lint_project tool ───────────────────────────────────────────────


class TestLintProject:
    def test_basic_invocation(self, tmp_path):
        tools = _register()
        result = _load_tool_result(tools["lint_project"](path=str(tmp_path)))
        assert "run_id" in result
        assert result["total_python_files"] == 1

    def test_raises_when_no_python_files(self, tmp_path):
        tools = _register(_collect_python_files=lambda root: [])
        with pytest.raises(ValueError, match="No Python files found"):
            tools["lint_project"](path=str(tmp_path))

    def test_raises_for_invalid_tier(self, tmp_path):
        tools = _register()
        with pytest.raises(ValueError, match="Invalid tier"):
            tools["lint_project"](path=str(tmp_path), tier=99)


# ── lint_get_details tool ───────────────────────────────────────────


class TestLintGetDetails:
    def _make_tools(self, run_data=None):
        """Register tools with a mocked load_run_details."""
        details = run_data or {
            "tier": 2,
            "project": "/proj",
            "duration_ms": 150,
            "blocking_issues": [{"code": "E1", "severity": "blocking"}],
            "warning_issues": [{"code": "W1", "severity": "warning"}],
            "info_issues": [{"code": "I1", "severity": "informational"}],
            "recurrence": {"E1": 3},
            "linter_diagnostics": {"ruff": "ok"},
        }
        patcher = mock.patch(
            "lintgate.state.load_run_details",
            return_value=details,
        )
        patcher.start()
        tools = _register()
        return tools, patcher

    def test_all_severities(self):
        tools, p = self._make_tools()
        try:
            result = _load_tool_result(tools["lint_get_details"](run_id="r1"))
            assert result["total_matching"] == 3
            assert len(result["issues"]) == 3
        finally:
            p.stop()

    def test_filter_blocking(self):
        tools, p = self._make_tools()
        try:
            result = _load_tool_result(tools["lint_get_details"](run_id="r1", severity="blocking"))
            assert result["total_matching"] == 1
            assert result["issues"][0]["severity"] == "blocking"
        finally:
            p.stop()

    def test_filter_warning(self):
        tools, p = self._make_tools()
        try:
            result = _load_tool_result(tools["lint_get_details"](run_id="r1", severity="warning"))
            assert result["total_matching"] == 1
        finally:
            p.stop()

    def test_filter_informational(self):
        tools, p = self._make_tools()
        try:
            result = _load_tool_result(tools["lint_get_details"](run_id="r1", severity="informational"))
            assert result["total_matching"] == 1
        finally:
            p.stop()

    def test_invalid_severity(self):
        tools, p = self._make_tools()
        try:
            with pytest.raises(ValueError, match="Invalid severity"):
                tools["lint_get_details"](run_id="r1", severity="critical")
        finally:
            p.stop()

    def test_run_id_not_found(self):
        with mock.patch("lintgate.state.load_run_details", return_value=None):
            tools = _register()
            with pytest.raises(ValueError, match="No lint run found"):
                tools["lint_get_details"](run_id="nonexistent")

    def test_truncation(self):
        many_issues = [{"code": f"E{i}", "severity": "blocking"} for i in range(20)]
        tools, p = self._make_tools(
            {
                "tier": 2,
                "project": "/proj",
                "duration_ms": 100,
                "blocking_issues": many_issues,
                "warning_issues": [],
                "info_issues": [],
            }
        )
        try:
            result = _load_tool_result(tools["lint_get_details"](run_id="r1", max_issues=5))
            assert result["total_matching"] == 20
            assert len(result["issues"]) == 5
            assert result["truncated"] == 15
        finally:
            p.stop()

    def test_include_recurrence(self):
        tools, p = self._make_tools()
        try:
            result = _load_tool_result(tools["lint_get_details"](run_id="r1", include_recurrence=True))
            assert result["recurrence"] == {"E1": 3}
        finally:
            p.stop()

    def test_linter_diagnostics_included(self):
        tools, p = self._make_tools()
        try:
            result = _load_tool_result(tools["lint_get_details"](run_id="r1"))
            assert result["linter_diagnostics"] == {"ruff": "ok"}
        finally:
            p.stop()


# ── lint_status tool ─────────────────────────────────────────────────


class TestLintStatus:
    def test_basic_status(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        fake_config = SimpleNamespace(
            languages=["python"],
            pipeline_critical_paths=[],
            severity_overrides={},
            enabled_linters=["ruff"],
            tool_version_requirements={},
        )
        fake_linter = SimpleNamespace(
            tier=1,
            required_tool="ruff",
            available=lambda project_root="": True,
        )

        with (
            mock.patch("lintgate.config.load_config", return_value=fake_config),
            mock.patch(
                "lintgate.registry.build_registry",
                return_value={"ruff": fake_linter},
            ),
            mock.patch("lintgate.state.load_last_run", return_value=None),
            mock.patch("lintgate.state.load_last_version_audit", return_value=None),
            mock.patch(
                "lintgate.context_guidance.build_context_guidance",
                return_value={},
            ),
            mock.patch(
                "lintgate.context_guidance.summarize_context_guidance",
                return_value={"summary": "ok"},
            ),
        ):
            tools = _register()
            result = _load_tool_result(tools["lint_status"](path=str(tmp_path)))

        assert result["version"] == "0.2.0"
        assert "ruff" in result["linters"]
        assert result["linter_count"] == 1
        assert result["last_run"] is None
        assert result["last_version_audit"] is None


# ── audit_tool_versions tool ────────────────────────────────────────


class TestAuditToolVersions:
    def test_basic_audit(self, tmp_path):
        fake_config = SimpleNamespace(tool_version_requirements={"ruff": ">=0.4"})
        fake_audit = {
            "project": str(tmp_path),
            "timestamp": 1000.0,
            "tools": [],
            "issues": [],
            "issue_count": 0,
            "auto_fix_applied": False,
        }

        with (
            mock.patch("lintgate.config.load_config", return_value=fake_config),
            mock.patch("lintgate.versioning.run_version_audit", return_value=fake_audit),
            mock.patch(
                "lintgate.versioning.format_version_audit_summary",
                return_value={"issue_count": 0},
            ),
            mock.patch("lintgate.state.save_version_audit"),
            mock.patch("lintgate.state.log_version_event"),
        ):
            tools = _register()
            result = _load_tool_result(tools["audit_tool_versions"](path=str(tmp_path)))

        assert result["summary"]["issue_count"] == 0
        assert result["issue_count"] == 0


# ── lint_fix tool ────────────────────────────────────────────────────


class TestLintFix:
    def test_fix_with_path(self, tmp_path):
        fix_result = SimpleNamespace(
            to_dict=lambda: {
                "files_modified": 0,
                "changes": [],
                "dry_run": True,
            }
        )
        with mock.patch("lintgate.lint_fixer.run_safe_fixes", return_value=fix_result):
            tools = _register()
            result = _load_tool_result(tools["lint_fix"](path=str(tmp_path)))
        assert result["dry_run"] is True

    def test_fix_with_files(self, tmp_path):
        f = tmp_path / "a.py"
        f.touch()
        fix_result = SimpleNamespace(
            to_dict=lambda: {"files_modified": 1, "changes": [], "dry_run": False}
        )
        with mock.patch("lintgate.lint_fixer.run_safe_fixes", return_value=fix_result):
            tools = _register()
            result = _load_tool_result(tools["lint_fix"](files=[str(f)], dry_run=False))
        assert result["dry_run"] is False

    def test_fix_no_files_no_path_raises(self):
        tools = _register()
        with pytest.raises(ValueError, match="Either files or path"):
            tools["lint_fix"]()

    def test_fix_no_python_files_found(self, tmp_path):
        tools = _register(_collect_python_files=lambda root: [])
        result = _load_tool_result(tools["lint_fix"](path=str(tmp_path)))
        assert result["error"] == "No Python files found"
