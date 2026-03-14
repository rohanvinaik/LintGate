"""Coverage tests for lintgate/versioning.py.

Covers all public and private functions: ToolSpec, _TRACKED_TOOLS,
run_version_audit, collect_required_version_specs, inspect_tool_versions,
_collect_from_pyproject, _collect_from_requirements_files,
_parse_requirement_entry, _tool_from_package, _canonical_tool_name,
_normalize_name, _find_venv_bin, _installed_version, _which,
_version_satisfies, _suggest_fix_command, _attempt_repairs,
_verify_environment, _tail, format_version_audit_summary.
"""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from lintgate.versioning import (
    _TRACKED_TOOLS,
    ToolSpec,
    _attempt_repairs,
    _canonical_tool_name,
    _collect_from_pyproject,
    _collect_from_requirements_files,
    _find_venv_bin,
    _installed_version,
    _normalize_name,
    _parse_requirement_entry,
    _suggest_fix_command,
    _tail,
    _tool_from_package,
    _verify_environment,
    _version_satisfies,
    _which,
    collect_required_version_specs,
    format_version_audit_summary,
    inspect_tool_versions,
    run_version_audit,
)

# ── ToolSpec and _TRACKED_TOOLS ──────────────────────────────────────


class TestToolSpec:
    def test_frozen_dataclass(self):
        spec = ToolSpec(tool="ruff", package="ruff", executable="ruff")
        assert spec.tool == "ruff"
        with pytest.raises(AttributeError):
            spec.tool = "other"  # type: ignore[misc]

    def test_tracked_tools_contains_expected_entries(self):
        assert "ruff" in _TRACKED_TOOLS
        assert "python" in _TRACKED_TOOLS
        assert "mypy" in _TRACKED_TOOLS
        assert "pip-audit" in _TRACKED_TOOLS
        assert _TRACKED_TOOLS["python"].package is None


# ── _normalize_name ──────────────────────────────────────────────────


class TestNormalizeName:
    def test_lowercases(self):
        assert _normalize_name("Ruff") == "ruff"

    def test_replaces_underscores(self):
        assert _normalize_name("pip_audit") == "pip-audit"

    def test_strips_whitespace(self):
        assert _normalize_name("  ruff  ") == "ruff"


# ── _canonical_tool_name ─────────────────────────────────────────────


class TestCanonicalToolName:
    def test_direct_match(self):
        assert _canonical_tool_name("ruff") == "ruff"

    def test_package_alias_match(self):
        assert _canonical_tool_name("pip-audit") == "pip-audit"

    def test_underscore_variant(self):
        assert _canonical_tool_name("pip_audit") == "pip-audit"

    def test_unknown_returns_normalized(self):
        assert _canonical_tool_name("unknown_tool") == "unknown-tool"


# ── _tool_from_package ───────────────────────────────────────────────


class TestToolFromPackage:
    def test_known_package(self):
        assert _tool_from_package("ruff") == "ruff"

    def test_pip_audit_package(self):
        assert _tool_from_package("pip-audit") == "pip-audit"

    def test_unknown_package(self):
        assert _tool_from_package("flask") is None


# ── _parse_requirement_entry ─────────────────────────────────────────


class TestParseRequirementEntry:
    def test_valid_requirement(self):
        result = _parse_requirement_entry("ruff>=0.4.0")
        assert result is not None
        assert result[0] == "ruff"
        assert ">=0.4.0" in result[1]

    def test_comment_line(self):
        assert _parse_requirement_entry("# this is a comment") is None

    def test_empty_line(self):
        assert _parse_requirement_entry("") is None

    def test_whitespace_only(self):
        assert _parse_requirement_entry("   ") is None

    def test_pip_option(self):
        assert _parse_requirement_entry("-r other.txt") is None

    def test_vcs_url(self):
        assert _parse_requirement_entry("git+https://github.com/repo") is None

    def test_url_requirement(self):
        assert _parse_requirement_entry("https://example.com/pkg.whl") is None

    def test_untracked_package(self):
        assert _parse_requirement_entry("flask>=2.0") is None

    def test_inline_comment_stripped(self):
        result = _parse_requirement_entry("ruff>=0.4.0  # linting")
        assert result is not None
        assert result[0] == "ruff"

    def test_no_specifier(self):
        result = _parse_requirement_entry("ruff")
        assert result is not None
        assert result[0] == "ruff"
        assert result[1] == ""

    def test_invalid_requirement(self):
        assert _parse_requirement_entry("!!!invalid!!!") is None


# ── _find_venv_bin ───────────────────────────────────────────────────


class TestFindVenvBin:
    def test_dot_venv(self, tmp_path):
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        assert _find_venv_bin(str(tmp_path)) == str(tmp_path / ".venv" / "bin")

    def test_venv(self, tmp_path):
        (tmp_path / "venv" / "bin").mkdir(parents=True)
        assert _find_venv_bin(str(tmp_path)) == str(tmp_path / "venv" / "bin")

    def test_env(self, tmp_path):
        (tmp_path / "env" / "bin").mkdir(parents=True)
        assert _find_venv_bin(str(tmp_path)) == str(tmp_path / "env" / "bin")

    def test_no_venv(self, tmp_path):
        assert _find_venv_bin(str(tmp_path)) is None

    def test_none_project_root(self):
        assert _find_venv_bin(None) is None

    def test_priority_dot_venv_first(self, tmp_path):
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / "venv" / "bin").mkdir(parents=True)
        assert _find_venv_bin(str(tmp_path)) == str(tmp_path / ".venv" / "bin")


# ── _version_satisfies ──────────────────────────────────────────────


class TestVersionSatisfies:
    def test_satisfies(self):
        assert _version_satisfies("0.5.0", ">=0.4.0") is True

    def test_does_not_satisfy(self):
        assert _version_satisfies("0.3.0", ">=0.4.0") is False

    def test_exact_match(self):
        assert _version_satisfies("1.0.0", "==1.0.0") is True

    def test_invalid_version(self):
        assert _version_satisfies("not-a-version", ">=1.0") is False

    def test_invalid_specifier(self):
        assert _version_satisfies("1.0.0", "!!!") is False

    def test_compound_specifier(self):
        assert _version_satisfies("1.5.0", ">=1.0,<2.0") is True
        assert _version_satisfies("2.1.0", ">=1.0,<2.0") is False


# ── _suggest_fix_command ─────────────────────────────────────────────


class TestSuggestFixCommand:
    def test_python_returns_none(self):
        spec = ToolSpec(tool="python", package=None, executable=None)
        assert _suggest_fix_command(spec, ">=3.10") is None

    def test_no_package_returns_none(self):
        spec = ToolSpec(tool="custom", package=None, executable="custom")
        assert _suggest_fix_command(spec, ">=1.0") is None

    def test_with_specifier(self):
        spec = ToolSpec(tool="ruff", package="ruff", executable="ruff")
        result = _suggest_fix_command(spec, ">=0.4")
        assert result is not None
        assert "ruff>=0.4" in result
        assert sys.executable in result

    def test_without_specifier(self):
        spec = ToolSpec(tool="ruff", package="ruff", executable="ruff")
        result = _suggest_fix_command(spec, "")
        assert result is not None
        assert "ruff'" in result


# ── _tail ────────────────────────────────────────────────────────────


class TestTail:
    def test_short_text(self):
        assert _tail("hello", 100) == "hello"

    def test_long_text_truncated(self):
        text = "x" * 2000
        result = _tail(text, 1200)
        assert len(result) == 1200
        assert result == text[-1200:]

    def test_default_max_chars(self):
        text = "y" * 1500
        result = _tail(text)
        assert len(result) == 1200

    def test_exact_boundary(self):
        text = "z" * 1200
        assert _tail(text, 1200) == text


# ── _installed_version ───────────────────────────────────────────────


class TestInstalledVersion:
    def test_python_version_no_venv(self):
        spec = ToolSpec(tool="python", package=None, executable=None)
        result = _installed_version(spec, project_root=None)
        expected = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        assert result == expected

    def test_python_version_with_venv(self, tmp_path):
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").touch()

        spec = ToolSpec(tool="python", package=None, executable=None)
        fake_proc = SimpleNamespace(returncode=0, stdout="3.11.5\n")
        with mock.patch("subprocess.run", return_value=fake_proc):
            result = _installed_version(spec, project_root=str(tmp_path))
        assert result == "3.11.5"

    def test_python_venv_subprocess_fails(self, tmp_path):
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").touch()

        spec = ToolSpec(tool="python", package=None, executable=None)
        fake_proc = SimpleNamespace(returncode=1, stdout="")
        with mock.patch("subprocess.run", return_value=fake_proc):
            result = _installed_version(spec, project_root=str(tmp_path))
        expected = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        assert result == expected

    def test_python_venv_subprocess_timeout(self, tmp_path):
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").touch()

        spec = ToolSpec(tool="python", package=None, executable=None)
        with mock.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="python", timeout=5),
        ):
            result = _installed_version(spec, project_root=str(tmp_path))
        expected = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        assert result == expected

    def test_package_no_package_attr(self):
        spec = ToolSpec(tool="custom", package=None, executable="custom")
        assert _installed_version(spec) is None

    def test_package_from_venv(self, tmp_path):
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").touch()

        spec = ToolSpec(tool="ruff", package="ruff", executable="ruff")
        fake_proc = SimpleNamespace(returncode=0, stdout="0.5.1\n")
        with mock.patch("subprocess.run", return_value=fake_proc):
            result = _installed_version(spec, project_root=str(tmp_path))
        assert result == "0.5.1"

    def test_package_venv_not_found_falls_through(self, tmp_path):
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").touch()

        spec = ToolSpec(tool="ruff", package="ruff", executable="ruff")
        fake_proc = SimpleNamespace(returncode=1, stdout="", stderr="")
        with mock.patch("subprocess.run", return_value=fake_proc):
            result = _installed_version(spec, project_root=str(tmp_path))
        assert result is None

    def test_package_host_fallback(self):
        spec = ToolSpec(tool="ruff", package="ruff", executable="ruff")
        with mock.patch("importlib.metadata.version", return_value="0.6.0"):
            result = _installed_version(spec, project_root=None)
        assert result == "0.6.0"

    def test_package_host_not_found(self):
        spec = ToolSpec(tool="ruff", package="ruff", executable="ruff")
        import importlib.metadata

        with mock.patch(
            "importlib.metadata.version",
            side_effect=importlib.metadata.PackageNotFoundError("ruff"),
        ):
            result = _installed_version(spec, project_root=None)
        assert result is None


# ── _which ───────────────────────────────────────────────────────────


class TestWhich:
    def test_none_executable(self):
        assert _which(None) is None

    def test_finds_in_venv(self, tmp_path):
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        exe = venv_bin / "ruff"
        exe.touch()
        result = _which("ruff", project_root=str(tmp_path))
        assert result == str(exe)

    def test_falls_back_to_system(self, tmp_path):
        with mock.patch("shutil.which", return_value="/usr/bin/ruff"):
            result = _which("ruff", project_root=str(tmp_path))
        assert result == "/usr/bin/ruff"

    def test_not_found_anywhere(self, tmp_path):
        with mock.patch("shutil.which", return_value=None):
            result = _which("nonexistent", project_root=str(tmp_path))
        assert result is None


# ── _collect_from_pyproject ──────────────────────────────────────────


class TestCollectFromPyproject:
    def _empty_reqs(self):
        return {
            tool: {"specifiers": [], "sources": [], "is_optional": []} for tool in _TRACKED_TOOLS
        }

    def test_no_pyproject(self, tmp_path):
        reqs = self._empty_reqs()
        _collect_from_pyproject(tmp_path, reqs)
        for tool in reqs:
            assert reqs[tool]["specifiers"] == []

    def test_requires_python(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.10"\n')
        reqs = self._empty_reqs()
        _collect_from_pyproject(tmp_path, reqs)
        assert ">=3.10" in reqs["python"]["specifiers"]
        assert any("pyproject.toml" in s for s in reqs["python"]["sources"])

    def test_project_dependencies(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["ruff>=0.4"]\n')
        reqs = self._empty_reqs()
        _collect_from_pyproject(tmp_path, reqs)
        assert any(">=0.4" in s for s in reqs["ruff"]["specifiers"])

    def test_optional_dependencies(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project.optional-dependencies]\ndev = ["mypy>=1.0"]\n'
        )
        reqs = self._empty_reqs()
        _collect_from_pyproject(tmp_path, reqs)
        assert any(">=1.0" in s for s in reqs["mypy"]["specifiers"])

    def test_invalid_toml(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("this is not valid toml {{{}}")
        reqs = self._empty_reqs()
        _collect_from_pyproject(tmp_path, reqs)
        # Should not raise, just skip
        for tool in reqs:
            assert reqs[tool]["specifiers"] == []


# ── _collect_from_requirements_files ────────────────────────────────


class TestCollectFromRequirementsFiles:
    def _empty_reqs(self):
        return {
            tool: {"specifiers": [], "sources": [], "is_optional": []} for tool in _TRACKED_TOOLS
        }

    def test_requirements_txt(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("ruff>=0.4.0\nflask>=2.0\n")
        reqs = self._empty_reqs()
        _collect_from_requirements_files(tmp_path, reqs)
        assert any(">=0.4.0" in s for s in reqs["ruff"]["specifiers"])

    def test_requirements_dev_txt(self, tmp_path):
        (tmp_path / "requirements-dev.txt").write_text("bandit>=1.7\n")
        reqs = self._empty_reqs()
        _collect_from_requirements_files(tmp_path, reqs)
        assert any(">=1.7" in s for s in reqs["bandit"]["specifiers"])

    def test_no_requirements_files(self, tmp_path):
        reqs = self._empty_reqs()
        _collect_from_requirements_files(tmp_path, reqs)
        for tool in reqs:
            assert reqs[tool]["specifiers"] == []


# ── collect_required_version_specs ───────────────────────────────────


class TestCollectRequiredVersionSpecs:
    def test_with_config_requirements(self, tmp_path):
        result = collect_required_version_specs(
            str(tmp_path),
            config_requirements={"ruff": ">=0.5"},
        )
        assert ">=0.5" in result["ruff"]["specifiers"]
        assert any("lintgate.yaml" in s for s in result["ruff"]["sources"])

    def test_deduplication(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("ruff>=0.5\n")
        result = collect_required_version_specs(
            str(tmp_path),
            config_requirements={"ruff": ">=0.5"},
        )
        assert result["ruff"]["specifiers"].count(">=0.5") == 1

    def test_combined_specifier(self, tmp_path):
        result = collect_required_version_specs(
            str(tmp_path),
            config_requirements={"ruff": ">=0.4", "mypy": ">=1.0"},
        )
        assert result["ruff"]["combined_specifier"] == ">=0.4"
        assert result["mypy"]["combined_specifier"] == ">=1.0"

    def test_empty_specifier_skipped(self, tmp_path):
        result = collect_required_version_specs(
            str(tmp_path),
            config_requirements={"ruff": "  "},
        )
        assert result["ruff"]["specifiers"] == []


# ── inspect_tool_versions ────────────────────────────────────────────


class TestInspectToolVersions:
    def test_all_ok(self):
        reqs = {
            tool: {"specifiers": [], "sources": [], "combined_specifier": ""}
            for tool in _TRACKED_TOOLS
        }
        with (
            mock.patch("lintgate.versioning._installed_version", return_value="1.0.0"),
            mock.patch("lintgate.versioning._which", return_value="/usr/bin/tool"),
        ):
            observations = inspect_tool_versions(reqs)
        assert all(o["status"] == "ok" for o in observations)

    def test_missing_required_tool(self):
        reqs = {
            tool: {"specifiers": [], "sources": [], "combined_specifier": ""}
            for tool in _TRACKED_TOOLS
        }
        reqs["ruff"]["combined_specifier"] = ">=0.4"
        with (
            mock.patch("lintgate.versioning._installed_version", return_value=None),
            mock.patch("lintgate.versioning._which", return_value=None),
        ):
            observations = inspect_tool_versions(reqs)
        ruff_obs = next(o for o in observations if o["tool"] == "ruff")
        assert ruff_obs["status"] == "missing"

    def test_version_mismatch(self):
        reqs = {
            tool: {"specifiers": [], "sources": [], "combined_specifier": ""}
            for tool in _TRACKED_TOOLS
        }
        reqs["ruff"]["combined_specifier"] = ">=0.6"

        def mock_version(spec, project_root=None):
            return "0.4.0" if spec.tool == "ruff" else "1.0.0"

        with (
            mock.patch("lintgate.versioning._installed_version", side_effect=mock_version),
            mock.patch("lintgate.versioning._which", return_value="/usr/bin/tool"),
        ):
            observations = inspect_tool_versions(reqs)
        ruff_obs = next(o for o in observations if o["tool"] == "ruff")
        assert ruff_obs["status"] == "mismatch"

    def test_missing_executable(self):
        reqs = {
            tool: {"specifiers": [], "sources": [], "combined_specifier": ""}
            for tool in _TRACKED_TOOLS
        }

        def mock_version(spec, project_root=None):
            return "1.0.0" if spec.tool == "ruff" else None

        def mock_which(exe, project_root=None):
            return None

        with (
            mock.patch("lintgate.versioning._installed_version", side_effect=mock_version),
            mock.patch("lintgate.versioning._which", side_effect=mock_which),
        ):
            observations = inspect_tool_versions(reqs)
        ruff_obs = next(o for o in observations if o["tool"] == "ruff")
        assert ruff_obs["status"] == "missing-executable"


# ── _attempt_repairs ─────────────────────────────────────────────────


class TestAttemptRepairs:
    def test_successful_repair(self):
        issues = [{"tool": "ruff", "status": "missing", "required_specifier": ">=0.4"}]
        fake_proc = SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
        with mock.patch("subprocess.run", return_value=fake_proc):
            fixes = _attempt_repairs(issues, "/usr/bin/python")
        assert len(fixes) == 1
        assert fixes[0]["success"] is True

    def test_failed_repair(self):
        issues = [{"tool": "ruff", "status": "mismatch", "required_specifier": ">=0.6"}]
        fake_proc = SimpleNamespace(returncode=1, stdout="", stderr="error\n")
        with mock.patch("subprocess.run", return_value=fake_proc):
            fixes = _attempt_repairs(issues, "/usr/bin/python")
        assert fixes[0]["success"] is False

    def test_timeout_repair(self):
        issues = [{"tool": "ruff", "status": "missing", "required_specifier": ">=0.4"}]
        with mock.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="pip", timeout=300),
        ):
            fixes = _attempt_repairs(issues, "/usr/bin/python")
        assert fixes[0]["success"] is False
        assert "timed out" in fixes[0]["error"]

    def test_skips_python(self):
        issues = [{"tool": "python", "status": "mismatch", "required_specifier": ">=3.10"}]
        fixes = _attempt_repairs(issues, "/usr/bin/python")
        assert fixes == []

    def test_skips_unknown_status(self):
        issues = [{"tool": "ruff", "status": "ok", "required_specifier": ">=0.4"}]
        fixes = _attempt_repairs(issues, "/usr/bin/python")
        assert fixes == []

    def test_no_specifier(self):
        issues = [{"tool": "ruff", "status": "missing-executable", "required_specifier": ""}]
        fake_proc = SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
        with mock.patch("subprocess.run", return_value=fake_proc):
            fixes = _attempt_repairs(issues, "/usr/bin/python")
        assert len(fixes) == 1
        assert "ruff" in fixes[0]["command"][-1]


# ── _verify_environment ─────────────────────────────────────────────


class TestVerifyEnvironment:
    def test_successful_check(self):
        fake_proc = SimpleNamespace(returncode=0, stdout="No issues\n", stderr="")
        with mock.patch("subprocess.run", return_value=fake_proc):
            result = _verify_environment("/usr/bin/python")
        assert result["ok"] is True

    def test_failed_check(self):
        fake_proc = SimpleNamespace(returncode=1, stdout="broken dep\n", stderr="warning\n")
        with mock.patch("subprocess.run", return_value=fake_proc):
            result = _verify_environment("/usr/bin/python")
        assert result["ok"] is False
        assert result["returncode"] == 1

    def test_timeout(self):
        with mock.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="pip", timeout=120),
        ):
            result = _verify_environment("/usr/bin/python")
        assert result["ok"] is False
        assert "timed out" in result["error"]


# ── run_version_audit ────────────────────────────────────────────────


class TestRunVersionAudit:
    def test_no_auto_fix(self, tmp_path):
        with (
            mock.patch(
                "lintgate.versioning.collect_required_version_specs",
                return_value={},
            ),
            mock.patch(
                "lintgate.versioning.inspect_tool_versions",
                return_value=[{"status": "ok", "tool": "ruff"}],
            ),
        ):
            result = run_version_audit(str(tmp_path))
        assert result["auto_fix_applied"] is False
        assert result["issue_count"] == 0

    def test_with_auto_fix(self, tmp_path):
        with (
            mock.patch(
                "lintgate.versioning.collect_required_version_specs",
                return_value={},
            ),
            mock.patch(
                "lintgate.versioning.inspect_tool_versions",
                side_effect=[
                    [{"status": "missing", "tool": "ruff"}],  # initial
                    [{"status": "ok", "tool": "ruff"}],  # post-fix
                ],
            ),
            mock.patch(
                "lintgate.versioning._attempt_repairs",
                return_value=[{"tool": "ruff", "success": True}],
            ),
            mock.patch(
                "lintgate.versioning._verify_environment",
                return_value={"ok": True},
            ),
        ):
            result = run_version_audit(str(tmp_path), auto_fix=True)
        assert result["auto_fix_applied"] is True
        assert result["post_fix_issue_count"] == 0

    def test_auto_fix_no_verify(self, tmp_path):
        with (
            mock.patch(
                "lintgate.versioning.collect_required_version_specs",
                return_value={},
            ),
            mock.patch(
                "lintgate.versioning.inspect_tool_versions",
                side_effect=[
                    [{"status": "missing", "tool": "ruff"}],
                    [{"status": "ok", "tool": "ruff"}],
                ],
            ),
            mock.patch(
                "lintgate.versioning._attempt_repairs",
                return_value=[],
            ),
        ):
            result = run_version_audit(str(tmp_path), auto_fix=True, verify_after_fix=False)
        assert result["verification"] is None


# ── format_version_audit_summary ─────────────────────────────────────


class TestFormatVersionAuditSummary:
    def test_basic_summary(self):
        audit = {
            "timestamp": 1000.0,
            "issues": [{"tool": "ruff"}],
            "tools": [{"tool": "ruff"}, {"tool": "mypy"}],
            "auto_fix_applied": False,
        }
        summary = format_version_audit_summary(audit)
        assert summary["issue_count"] == 1
        assert summary["tools_checked"] == 2
        assert summary["auto_fix_applied"] is False
        # When post_fix_issues key is absent, .get defaults to [] which is a list,
        # so len([]) == 0, not None
        assert summary["post_fix_issue_count"] == 0

    def test_with_post_fix(self):
        audit = {
            "timestamp": 2000.0,
            "issues": [{"tool": "ruff"}],
            "post_fix_issues": [],
            "tools": [{"tool": "ruff"}],
            "auto_fix_applied": True,
        }
        summary = format_version_audit_summary(audit)
        assert summary["post_fix_issue_count"] == 0
        assert summary["auto_fix_applied"] is True

    def test_empty_audit(self):
        audit: dict[str, Any] = {}
        summary = format_version_audit_summary(audit)
        assert summary["issue_count"] == 0
        assert summary["tools_checked"] == 0
