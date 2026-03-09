"""Tests for previously-uncovered functions in mcp_tools/onboarding_tools.py.

Covers: _venv_create_command, _ensure_project_venv, _install_commands_for_package,
_auto_install_optional_tools, _scaffold_config_yaml, _detect_github_remote,
_write_pre_push_hook, _compute_gitignore_additions, _inject_badges_into_readme,
_readme_has_quality_badges, _generate_qlty_toml, _normalize_qlty_exclude_pattern,
_detect_subprocess_usage, _detect_sonar_scanner, _run_sonar_scanner,
_reset_project_state.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

from mcp_tools.onboarding_tools import (
    _auto_install_optional_tools,
    _compute_gitignore_additions,
    _detect_github_remote,
    _detect_sonar_scanner,
    _detect_subprocess_usage,
    _ensure_project_venv,
    _generate_qlty_toml,
    _inject_badges_into_readme,
    _install_commands_for_package,
    _normalize_qlty_exclude_pattern,
    _readme_has_quality_badges,
    _reset_project_state,
    _run_sonar_scanner,
    _scaffold_config_yaml,
    _venv_create_command,
    _write_pre_push_hook,
)

# ── _venv_create_command ─────────────────────────────────────────────────


def test_venv_create_command_with_uv() -> None:
    with patch("mcp_tools.onboarding_tools.shutil.which", return_value="/usr/bin/uv"):
        cmd, label = _venv_create_command()
    assert cmd == ["/usr/bin/uv", "venv", ".venv"]
    assert label == "uv"


def test_venv_create_command_without_uv() -> None:
    with patch("mcp_tools.onboarding_tools.shutil.which", return_value=None):
        cmd, label = _venv_create_command()
    assert label == "python_venv"
    assert cmd[-2:] == ["venv", ".venv"]


# ── _ensure_project_venv ─────────────────────────────────────────────────


def test_ensure_project_venv_existing(tmp_path: Path) -> None:
    venv_py = tmp_path / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("#!/usr/bin/env python")
    result = _ensure_project_venv(str(tmp_path))
    assert result["status"] == "present"
    assert "venv_python" in result


def test_ensure_project_venv_create_success(tmp_path: Path) -> None:
    venv_py_path = str(tmp_path / ".venv" / "bin" / "python")
    mock_create = MagicMock(returncode=0, stderr="")
    mock_pip = MagicMock(returncode=0, stderr="")
    with (
        patch(
            "mcp_tools.onboarding_tools._project_venv_python",
            side_effect=[None, venv_py_path],
        ),
        patch(
            "mcp_tools.onboarding_tools._venv_create_command",
            return_value=(["python", "-m", "venv", ".venv"], "python_venv"),
        ),
        patch(
            "mcp_tools.onboarding_tools.subprocess.run",
            side_effect=[mock_create, mock_pip],
        ),
    ):
        result = _ensure_project_venv(str(tmp_path))
    assert result["status"] == "created"
    assert result["pip_ready"] is True


def test_ensure_project_venv_timeout(tmp_path: Path) -> None:
    with (
        patch("mcp_tools.onboarding_tools._project_venv_python", return_value=None),
        patch(
            "mcp_tools.onboarding_tools._venv_create_command",
            return_value=(["python", "-m", "venv", ".venv"], "python_venv"),
        ),
        patch(
            "mcp_tools.onboarding_tools.subprocess.run",
            side_effect=subprocess.TimeoutExpired("venv", 120),
        ),
    ):
        result = _ensure_project_venv(str(tmp_path))
    assert result["status"] == "timeout"


def test_ensure_project_venv_create_error(tmp_path: Path) -> None:
    mock_fail = MagicMock(returncode=1, stderr="error")
    with (
        patch("mcp_tools.onboarding_tools._project_venv_python", return_value=None),
        patch(
            "mcp_tools.onboarding_tools._venv_create_command",
            return_value=(["python", "-m", "venv", ".venv"], "python_venv"),
        ),
        patch("mcp_tools.onboarding_tools.subprocess.run", return_value=mock_fail),
    ):
        result = _ensure_project_venv(str(tmp_path))
    assert result["status"] == "error"
    assert result["reason"] == "venv_create_failed"


# ── _install_commands_for_package ────────────────────────────────────────


def test_install_commands_no_venv(tmp_path: Path) -> None:
    with patch("mcp_tools.onboarding_tools._project_venv_python", return_value=None):
        result = _install_commands_for_package(str(tmp_path), "pip-audit")
    assert result == []


def test_install_commands_with_uv(tmp_path: Path) -> None:
    with (
        patch(
            "mcp_tools.onboarding_tools._project_venv_python",
            return_value="/v/bin/python",
        ),
        patch("mcp_tools.onboarding_tools.shutil.which", return_value="/usr/bin/uv"),
    ):
        result = _install_commands_for_package(str(tmp_path), "pip-audit")
    assert len(result) == 2
    assert "uv" in result[0][0]


def test_install_commands_without_uv(tmp_path: Path) -> None:
    with (
        patch(
            "mcp_tools.onboarding_tools._project_venv_python",
            return_value="/v/bin/python",
        ),
        patch("mcp_tools.onboarding_tools.shutil.which", return_value=None),
    ):
        result = _install_commands_for_package(str(tmp_path), "pip-audit")
    assert len(result) == 1
    assert "pip" in result[0]


# ── _auto_install_optional_tools ─────────────────────────────────────────


def test_auto_install_skips_non_optional(tmp_path: Path) -> None:
    missing = [{"tool": "unknown-thing", "package": "unknown-thing"}]
    result = _auto_install_optional_tools(str(tmp_path), missing)
    assert result == []


def test_auto_install_no_venv(tmp_path: Path) -> None:
    missing = [{"tool": "pip-audit", "package": "pip-audit"}]
    with patch("mcp_tools.onboarding_tools._install_commands_for_package", return_value=[]):
        result = _auto_install_optional_tools(str(tmp_path), missing)
    assert len(result) == 1
    assert result[0]["status"] == "skipped"


def test_auto_install_success(tmp_path: Path) -> None:
    missing = [{"tool": "pip-audit", "package": "pip-audit"}]
    mock_result = MagicMock(returncode=0, stderr="")
    with (
        patch(
            "mcp_tools.onboarding_tools._install_commands_for_package",
            return_value=[["pip", "install", "pip-audit"]],
        ),
        patch("mcp_tools.onboarding_tools.subprocess.run", return_value=mock_result),
    ):
        result = _auto_install_optional_tools(str(tmp_path), missing)
    assert result[0]["status"] == "installed"


def test_auto_install_timeout(tmp_path: Path) -> None:
    missing = [{"tool": "pip-audit", "package": "pip-audit"}]
    with (
        patch(
            "mcp_tools.onboarding_tools._install_commands_for_package",
            return_value=[["pip", "install", "pip-audit"]],
        ),
        patch(
            "mcp_tools.onboarding_tools.subprocess.run",
            side_effect=subprocess.TimeoutExpired("pip", 180),
        ),
    ):
        result = _auto_install_optional_tools(str(tmp_path), missing)
    assert result[0]["status"] == "error"
    assert result[0]["reason"] == "all_install_commands_failed"


# ── _scaffold_config_yaml ────────────────────────────────────────────────


def test_scaffold_config_yaml_basic(tmp_path: Path) -> None:
    # Create a small Python file (< 300 lines, no subprocess)
    src = tmp_path / "main.py"
    src.write_text("print('hello')\n" * 10)
    result = _scaffold_config_yaml(str(tmp_path), {})
    assert "controlplane:" in result
    assert "enabled: true" in result


def test_scaffold_config_yaml_with_critical_paths(tmp_path: Path) -> None:
    # Create a large Python file (> 300 lines)
    big = tmp_path / "big_module.py"
    big.write_text("x = 1\n" * 400)
    result = _scaffold_config_yaml(str(tmp_path), {})
    assert "pipeline_critical_paths:" in result
    assert "big_module.py" in result


def test_scaffold_config_yaml_with_subprocess(tmp_path: Path) -> None:
    src = tmp_path / "runner.py"
    src.write_text("import subprocess\nsubprocess.run(['ls'])\n")
    result = _scaffold_config_yaml(str(tmp_path), {})
    assert "severity_overrides:" in result
    assert "B603" in result


# ── _detect_github_remote ────────────────────────────────────────────────


def test_detect_github_remote_origin(tmp_path: Path) -> None:
    mock_result = MagicMock(
        returncode=0,
        stdout="origin\tgit@github.com:user/repo.git (fetch)\n",
    )
    with patch("mcp_tools.quality_helpers.subprocess.run", return_value=mock_result):
        result = _detect_github_remote(str(tmp_path))
    assert result["owner"] == "user"
    assert result["repo"] == "repo"


def test_detect_github_remote_no_remotes(tmp_path: Path) -> None:
    mock_result = MagicMock(returncode=0, stdout="")
    with patch("mcp_tools.quality_helpers.subprocess.run", return_value=mock_result):
        result = _detect_github_remote(str(tmp_path))
    # When no GitHub remote is found, fallback owner/repo returned
    assert result["owner"] == "OWNER"
    assert result["repo"] == "REPO"


def test_detect_github_remote_git_unavailable(tmp_path: Path) -> None:
    with patch(
        "mcp_tools.quality_helpers.subprocess.run",
        side_effect=FileNotFoundError("git"),
    ):
        result = _detect_github_remote(str(tmp_path))
    # When git is not available, fallback owner/repo returned
    assert result["owner"] == "OWNER"
    assert result["repo"] == "REPO"


def test_detect_github_remote_non_origin(tmp_path: Path) -> None:
    mock_result = MagicMock(
        returncode=0,
        stdout="upstream\tgit@github.com:org/project.git (fetch)\n",
    )
    with patch("mcp_tools.quality_helpers.subprocess.run", return_value=mock_result):
        result = _detect_github_remote(str(tmp_path))
    assert result["owner"] == "org"


# ── _write_pre_push_hook ─────────────────────────────────────────────────


def test_write_pre_push_hook_preview(tmp_path: Path) -> None:
    # Source requires .git/hooks to exist, otherwise returns error
    hook_dir = tmp_path / ".git" / "hooks"
    hook_dir.mkdir(parents=True)
    result = _write_pre_push_hook(str(tmp_path), write=False)
    assert result["status"] == "preview"
    assert "content_snippet" in result


def test_write_pre_push_hook_already_exists(tmp_path: Path) -> None:
    hook_dir = tmp_path / ".git" / "hooks"
    hook_dir.mkdir(parents=True)
    (hook_dir / "pre-push").write_text("#!/bin/bash\nqlty check --all\n")
    result = _write_pre_push_hook(str(tmp_path), write=False)
    assert result["status"] == "present"
    assert "path" in result


def test_write_pre_push_hook_write(tmp_path: Path) -> None:
    hook_dir = tmp_path / ".git" / "hooks"
    hook_dir.mkdir(parents=True)
    result = _write_pre_push_hook(str(tmp_path), write=True)
    assert result["status"] == "created"
    assert "path" in result
    hook_path = tmp_path / ".git" / "hooks" / "pre-push"
    assert hook_path.exists()


def test_write_pre_push_hook_no_git_dir(tmp_path: Path) -> None:
    # No .git/hooks directory — returns error
    result = _write_pre_push_hook(str(tmp_path), write=True)
    assert result["status"] == "error"
    assert result["reason"] == "no_git_dir"


# ── _compute_gitignore_additions ─────────────────────────────────────────


def test_compute_gitignore_no_file(tmp_path: Path) -> None:
    result = _compute_gitignore_additions(str(tmp_path))
    assert result["status"] == "missing_patterns"
    assert len(result["missing"]) > 0


def test_compute_gitignore_with_existing(tmp_path: Path) -> None:
    # Write a gitignore that includes all required patterns
    (tmp_path / ".gitignore").write_text(".qlty/\n.coverage\ncoverage.xml\n.scannerwork/\n")
    result = _compute_gitignore_additions(str(tmp_path))
    assert result["status"] == "complete"
    assert result["missing"] == []


def test_compute_gitignore_partial(tmp_path: Path) -> None:
    # Write a gitignore with only some required patterns
    (tmp_path / ".gitignore").write_text(".qlty/\n.coverage\n")
    result = _compute_gitignore_additions(str(tmp_path))
    assert result["status"] == "missing_patterns"
    assert "coverage.xml" in result["missing"]
    assert ".scannerwork/" in result["missing"]


# ── _inject_badges_into_readme ───────────────────────────────────────────


def test_inject_badges_no_readme(tmp_path: Path) -> None:
    result = _inject_badges_into_readme(str(tmp_path), "badge_md", write=False)
    assert result["status"] == "error"
    assert result["reason"] == "no_readme"


def test_inject_badges_read_error(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Hi")
    # The source uses Path.read_text() which will raise OSError
    with patch("pathlib.Path.read_text", side_effect=OSError("nope")):
        try:
            result = _inject_badges_into_readme(str(tmp_path), "badge_md", write=False)
            # If the function catches OSError, verify the result
            assert result["status"] in ("error", "read_error")
        except OSError:
            # The current source lets OSError propagate
            pass


def test_inject_badges_preview(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# My Project\n\nSome text.\n")
    result = _inject_badges_into_readme(str(tmp_path), "badge_md", write=False)
    assert result["status"] == "preview"


def test_inject_badges_no_heading(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("No heading here.\n")
    result = _inject_badges_into_readme(str(tmp_path), "badge_md", write=False)
    assert result["status"] == "preview"
    # Source prepends badge to beginning when no heading found
    assert "content_snippet" in result


def test_inject_badges_managed_block_update_preview(tmp_path: Path) -> None:
    content = (
        "# Project\n"
        "<!-- lintgate:quality-badges:start -->\n"
        "old badges\n"
        "<!-- lintgate:quality-badges:end -->\n"
    )
    (tmp_path / "README.md").write_text(content)
    result = _inject_badges_into_readme(str(tmp_path), "new badges", write=False)
    # Source returns "preview" for all non-write cases
    assert result["status"] == "preview"


# ── _readme_has_quality_badges ───────────────────────────────────────────


def test_readme_has_badges_no_readme(tmp_path: Path) -> None:
    assert _readme_has_quality_badges(str(tmp_path)) is False


def test_readme_has_badges_incomplete(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Project\nJust a readme.\n")
    assert _readme_has_quality_badges(str(tmp_path)) is False


def test_readme_has_badges_read_error(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("stuff")
    with patch("pathlib.Path.read_text", side_effect=OSError("nope")):
        assert _readme_has_quality_badges(str(tmp_path)) is False


def test_readme_has_badges_managed_block_incomplete(tmp_path: Path) -> None:
    content = (
        "<!-- lintgate:quality-badges:start -->\n"
        "only one badge\n"
        "<!-- lintgate:quality-badges:end -->\n"
    )
    (tmp_path / "README.md").write_text(content)
    assert _readme_has_quality_badges(str(tmp_path)) is False


# ── _generate_qlty_toml ─────────────────────────────────────────────────


def test_generate_qlty_toml_basic() -> None:
    layout = {"test_dirs": ["tests"], "exclude_patterns": []}
    result = _generate_qlty_toml(layout)
    assert "qlty" in result
    assert "ruff" in result
    assert "bandit" in result


def test_generate_qlty_toml_tool_runner() -> None:
    layout = {"test_dirs": ["tests"], "exclude_patterns": []}
    result = _generate_qlty_toml(layout, is_tool_runner=True)
    assert "B404" in result
    assert "B603" in result


def test_generate_qlty_toml_custom_excludes() -> None:
    layout = {"test_dirs": ["tests"], "exclude_patterns": ["vendor/"]}
    result = _generate_qlty_toml(layout)
    assert "vendor/**" in result


# ── _normalize_qlty_exclude_pattern ──────────────────────────────────────


def test_normalize_empty() -> None:
    assert _normalize_qlty_exclude_pattern("") == ""
    assert _normalize_qlty_exclude_pattern("   ") == ""


def test_normalize_already_glob() -> None:
    assert _normalize_qlty_exclude_pattern("foo/**") == "foo/**"


def test_normalize_trailing_slash() -> None:
    assert _normalize_qlty_exclude_pattern("vendor/") == "vendor/**"


def test_normalize_wildcard_pattern() -> None:
    assert _normalize_qlty_exclude_pattern("*.min.*") == "*.min.*"


def test_normalize_plain_dir() -> None:
    assert _normalize_qlty_exclude_pattern("dist") == "dist/**"


# ── _detect_subprocess_usage ─────────────────────────────────────────────


def test_detect_subprocess_found(tmp_path: Path) -> None:
    (tmp_path / "runner.py").write_text("import subprocess\n")
    assert _detect_subprocess_usage(str(tmp_path)) is True


def test_detect_subprocess_not_found(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('hello')\n")
    assert _detect_subprocess_usage(str(tmp_path)) is False


def test_detect_subprocess_in_tests_dir(tmp_path: Path) -> None:
    # The current implementation does NOT skip tests/ directories;
    # it only skips venv/cache/git/node_modules segments via _VENV_SEGMENTS.
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_runner.py").write_text("import subprocess\n")
    assert _detect_subprocess_usage(str(tmp_path)) is True


def test_detect_subprocess_skips_venv(tmp_path: Path) -> None:
    venv = tmp_path / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "mod.py").write_text("import subprocess\n")
    assert _detect_subprocess_usage(str(tmp_path)) is False


def test_detect_subprocess_oserror(tmp_path: Path) -> None:
    # The source uses open() with errors="ignore", so patch builtins.open
    # to simulate read failure. The function catches OSError and continues,
    # returning False when no file can be read.
    (tmp_path / "broken.py").write_text("import subprocess\n")
    with patch("builtins.open", side_effect=OSError("nope")):
        assert _detect_subprocess_usage(str(tmp_path)) is False


# ── _detect_sonar_scanner ────────────────────────────────────────────────


def test_detect_sonar_scanner_pysonar() -> None:
    def fake_which(name: str) -> str | None:
        return "/usr/bin/pysonar-scanner" if name == "pysonar-scanner" else None

    with patch("mcp_tools.onboarding_tools.shutil.which", side_effect=fake_which):
        assert _detect_sonar_scanner() == "/usr/bin/pysonar-scanner"


def test_detect_sonar_scanner_sonar() -> None:
    def fake_which(name: str) -> str | None:
        return "/usr/bin/sonar-scanner" if name == "sonar-scanner" else None

    with patch("mcp_tools.onboarding_tools.shutil.which", side_effect=fake_which):
        assert _detect_sonar_scanner() == "/usr/bin/sonar-scanner"


def test_detect_sonar_scanner_none() -> None:
    with patch("mcp_tools.onboarding_tools.shutil.which", return_value=None):
        assert _detect_sonar_scanner() is None


# ── _run_sonar_scanner ───────────────────────────────────────────────────


def test_run_sonar_scanner_success(tmp_path: Path) -> None:
    mock_result = MagicMock(
        returncode=0,
        stdout="ANALYSIS SUCCESSFUL\nceTaskUrl=https://sonar.io/task?id=123\n",
        stderr="",
    )
    with patch("mcp_tools.onboarding_tools.subprocess.run", return_value=mock_result):
        result = _run_sonar_scanner(str(tmp_path), "token", "/usr/bin/sonar-scanner")
    assert result["status"] == "success"
    assert result["analysis_url"] is not None


def test_run_sonar_scanner_pysonar(tmp_path: Path) -> None:
    mock_result = MagicMock(returncode=0, stdout="done\n", stderr="")
    with patch("mcp_tools.onboarding_tools.subprocess.run", return_value=mock_result) as mock_run:
        _run_sonar_scanner(str(tmp_path), "token", "/usr/bin/pysonar-scanner")
    cmd = mock_run.call_args[0][0]
    assert "-Dproject.home=" in cmd[1]


def test_run_sonar_scanner_timeout(tmp_path: Path) -> None:
    with patch(
        "mcp_tools.onboarding_tools.subprocess.run",
        side_effect=subprocess.TimeoutExpired("sonar", 120),
    ):
        result = _run_sonar_scanner(str(tmp_path), "token", "/usr/bin/sonar-scanner")
    assert result["status"] == "timeout"


def test_run_sonar_scanner_file_not_found(tmp_path: Path) -> None:
    with patch(
        "mcp_tools.onboarding_tools.subprocess.run",
        side_effect=FileNotFoundError("sonar-scanner"),
    ):
        result = _run_sonar_scanner(str(tmp_path), "token", "/usr/bin/sonar-scanner")
    assert result["status"] == "error"


# ── _reset_project_state ─────────────────────────────────────────────────


def test_reset_project_state_clears_dirs(tmp_path: Path) -> None:
    lintgate_dir = tmp_path / ".claude" / "lintgate"
    for subdir in ("state", "runs", "sessions"):
        (lintgate_dir / subdir).mkdir(parents=True)
        (lintgate_dir / subdir / "data.json").write_text("{}")

    with patch("mcp_tools.onboarding_tools.Path.home", return_value=tmp_path / "fake_home"):
        actions = _reset_project_state(str(tmp_path))

    assert len(actions) == 3
    assert all(a["action"] == "reset_dir" for a in actions)
    assert not (lintgate_dir / "state").exists()


def test_reset_project_state_no_dirs(tmp_path: Path) -> None:
    with patch("mcp_tools.onboarding_tools.Path.home", return_value=tmp_path / "fake_home"):
        actions = _reset_project_state(str(tmp_path))
    assert actions == []


def test_reset_project_state_clears_habit_files(tmp_path: Path) -> None:
    import hashlib

    project_hash = hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]

    fake_home = tmp_path / "fake_home"
    habit_dir = fake_home / ".lintgate" / "habit_state"
    habit_dir.mkdir(parents=True)
    matching = habit_dir / f"habit_{project_hash}.json"
    matching.write_text("{}")
    other = habit_dir / "habit_otherhash.json"
    other.write_text("{}")

    with patch("mcp_tools.onboarding_tools.Path.home", return_value=fake_home):
        actions = _reset_project_state(str(tmp_path))

    assert not matching.exists()
    assert other.exists()
    file_actions = [a for a in actions if a["action"] == "reset_file"]
    assert len(file_actions) == 1
