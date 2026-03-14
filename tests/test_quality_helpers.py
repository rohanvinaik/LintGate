"""Tests for mcp_tools/quality_helpers.py facade and internal helpers.

Targets 32 functions across the facade module with exact value assertions.
Mocks subprocess, file I/O, and glob only where external side effects occur.
"""

from __future__ import annotations

import hashlib
import os
import re
import warnings
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, mock_open, patch

import pytest

from mcp_tools.quality_helpers import (
    _BADGE_BLOCK_END,
    _BADGE_BLOCK_START,
    _GITHUB_REMOTE_RE,
    _LICENSE_BADGE_MAP,
    _QLTY_MONITOR_RULES,
    _QLTY_TEST_TRIAGE_RULES,
    _QLTY_TOOL_RUNNER_TRIAGE_RULES,
    _README_NAMES,
    _REQUIRED_ARTIFACTS,
    _REQUIRED_BADGE_FINGERPRINTS,
    _VENV_SEGMENTS,
    _apply_managed_artifact,
    _build_quality_guidance,
    _compute_badge_markdown,
    _compute_bandit_ci_skips,
    _compute_gitignore_additions,
    _detect_github_remote,
    _detect_project_layout,
    _detect_sonar_scanner,
    _detect_subprocess_usage,
    _generate_badge_markdown,
    _generate_clusterfuzzlite_workflow,
    _generate_codeclimate_yml,
    _generate_codeql_workflow,
    _generate_coveragerc,
    _generate_dependabot_yml,
    _generate_gitleaks_toml,
    _generate_pre_push_hook,
    _generate_pypi_publish_workflow,
    _generate_qlty_toml,
    _generate_qlty_workflow,
    _generate_quality_infra_gate_workflow,
    _generate_scorecard_workflow,
    _generate_security_md,
    _generate_security_workflow,
    _generate_sonar_properties,
    _generate_sonar_workflow,
    _generate_tests_workflow,
    _inject_badges_into_readme,
    _normalize_qlty_exclude_pattern,
    _read_informational_bandit_codes,
    _run_sonar_scanner,
    _warn_deprecation,
    _write_pre_push_hook,
)


# ── Constants verification ───────────────────────────────────────────


class TestConstants:
    """Verify module-level constants have expected values."""

    def test_required_artifacts_keys(self) -> None:
        assert set(_REQUIRED_ARTIFACTS.keys()) == {
            "codeclimate",
            "sonar",
            "coveragerc",
            "gitleaks",
            "security_policy",
        }

    def test_required_artifacts_values(self) -> None:
        assert _REQUIRED_ARTIFACTS["codeclimate"] == ".codeclimate.yml"
        assert _REQUIRED_ARTIFACTS["sonar"] == "sonar-project.properties"
        assert _REQUIRED_ARTIFACTS["coveragerc"] == ".coveragerc"
        assert _REQUIRED_ARTIFACTS["gitleaks"] == ".gitleaks.toml"
        assert _REQUIRED_ARTIFACTS["security_policy"] == "SECURITY.md"

    def test_badge_block_markers(self) -> None:
        assert _BADGE_BLOCK_START == "<!-- lintgate:quality-badges:start -->"
        assert _BADGE_BLOCK_END == "<!-- lintgate:quality-badges:end -->"

    def test_github_remote_regex_matches_ssh(self) -> None:
        m = _GITHUB_REMOTE_RE.search("git@github.com:owner/repo.git (fetch)")
        assert m is not None
        assert m.group(1) == "owner"
        assert m.group(2) == "repo"

    def test_github_remote_regex_matches_https(self) -> None:
        m = _GITHUB_REMOTE_RE.search("https://github.com/myorg/myrepo.git (push)")
        assert m is not None
        assert m.group(1) == "myorg"
        assert m.group(2) == "myrepo"

    def test_github_remote_regex_no_match(self) -> None:
        m = _GITHUB_REMOTE_RE.search("https://gitlab.com/owner/repo.git")
        assert m is None

    def test_readme_names(self) -> None:
        assert _README_NAMES == ("README.md", "readme.md", "Readme.md", "README.MD")

    def test_required_badge_fingerprints_count(self) -> None:
        assert len(_REQUIRED_BADGE_FINGERPRINTS) == 5
        assert "actions/workflows/tests.yml/badge.svg" in _REQUIRED_BADGE_FINGERPRINTS
        assert "metric=coverage" in _REQUIRED_BADGE_FINGERPRINTS

    def test_license_badge_map_entries(self) -> None:
        assert _LICENSE_BADGE_MAP["MIT"] == "MIT"
        assert _LICENSE_BADGE_MAP["Apache-2.0"] == "Apache_2.0"
        assert _LICENSE_BADGE_MAP["GPL-3.0"] == "GPL_3.0"
        assert _LICENSE_BADGE_MAP["BSD-3-Clause"] == "BSD_3--Clause"

    def test_venv_segments(self) -> None:
        assert "/.venv/" in _VENV_SEGMENTS
        assert "/.git/" in _VENV_SEGMENTS
        assert "/node_modules/" in _VENV_SEGMENTS

    def test_qlty_triage_rules(self) -> None:
        assert _QLTY_TEST_TRIAGE_RULES == ["bandit:B101", "bandit:B108"]
        assert _QLTY_TOOL_RUNNER_TRIAGE_RULES == [
            "bandit:B404",
            "bandit:B603",
            "bandit:B607",
        ]

    def test_qlty_monitor_rules(self) -> None:
        assert len(_QLTY_MONITOR_RULES) == 1
        assert _QLTY_MONITOR_RULES[0][0] == "bandit:B311"


# ── _warn_deprecation ────────────────────────────────────────────────


class TestWarnDeprecation:
    def test_emits_deprecation_warning(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_deprecation("some_function")
        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)
        assert "some_function" in str(w[0].message)
        assert "mcp_tools.quality.*" in str(w[0].message)

    def test_different_function_names(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_deprecation("foo_bar")
        assert "foo_bar" in str(w[0].message)


# ── Facade deprecation pass-through tests ────────────────────────────


class TestFacadeDeprecation:
    """Each facade function should emit a DeprecationWarning and delegate."""

    def test_detect_project_layout_warns(self, tmp_path: Path) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _detect_project_layout(str(tmp_path))
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert isinstance(result, dict)
        assert "source_dirs" in result

    def test_generate_coveragerc_warns(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _generate_coveragerc()
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert "[run]" in result

    def test_generate_dependabot_yml_warns(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _generate_dependabot_yml()
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert "version: 2" in result

    def test_generate_gitleaks_toml_warns(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _generate_gitleaks_toml()
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert "Gitleaks" in result

    def test_generate_codeclimate_yml_warns(self) -> None:
        layout = {"exclude_patterns": ["tests/"]}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _generate_codeclimate_yml(layout)
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert 'version: "2"' in result

    def test_generate_sonar_properties_warns(self) -> None:
        github = {"owner": "testowner", "repo": "testrepo"}
        layout = {"source_dirs": ["src"], "test_dirs": ["tests"]}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _generate_sonar_properties(github, layout)
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert "sonar.projectKey=testowner_testrepo" in result

    def test_generate_security_md_warns(self) -> None:
        github = {"owner": "o", "repo": "r"}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _generate_security_md(github)
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert "# Security Policy" in result

    def test_generate_scorecard_workflow_warns(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _generate_scorecard_workflow()
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert "OpenSSF Scorecard" in result

    def test_generate_codeql_workflow_warns(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _generate_codeql_workflow()
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert "CodeQL" in result

    def test_generate_clusterfuzzlite_workflow_warns(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _generate_clusterfuzzlite_workflow()
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert "ClusterFuzzLite" in result

    def test_generate_pypi_publish_workflow_warns(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _generate_pypi_publish_workflow()
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert "Publish to PyPI" in result

    def test_generate_quality_infra_gate_workflow_warns(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _generate_quality_infra_gate_workflow()
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert "Quality Infrastructure Gate" in result

    def test_generate_qlty_workflow_warns(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _generate_qlty_workflow()
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert "Qlty" in result

    def test_generate_security_workflow_warns(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _generate_security_workflow()
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert "Security Lite" in result

    def test_generate_tests_workflow_warns(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _generate_tests_workflow()
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert "tests" in result

    def test_normalize_qlty_exclude_pattern_warns(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _normalize_qlty_exclude_pattern("tests/")
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert result == "tests/**"

    def test_detect_sonar_scanner_warns(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            with patch("shutil.which", return_value=None):
                result = _detect_sonar_scanner()
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert result is None

    def test_run_sonar_scanner_warns(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ANALYSIS SUCCESSFUL"
        mock_result.stderr = ""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            with patch("subprocess.run", return_value=mock_result):
                result = _run_sonar_scanner("/tmp/proj", "tok", "/usr/bin/sonar-scanner")
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert result["status"] == "success"


# ── _detect_github_remote ────────────────────────────────────────────


class TestDetectGithubRemote:
    def test_parses_ssh_remote(self) -> None:
        mock_result = MagicMock()
        mock_result.stdout = "origin\tgit@github.com:alice/myproject.git (fetch)\n"
        with patch("mcp_tools.quality_helpers.subprocess.run", return_value=mock_result):
            result = _detect_github_remote("/some/path")
        assert result == {"owner": "alice", "repo": "myproject"}

    def test_parses_https_remote(self) -> None:
        mock_result = MagicMock()
        mock_result.stdout = (
            "origin\thttps://github.com/bob/cool-repo.git (fetch)\n"
        )
        with patch("mcp_tools.quality_helpers.subprocess.run", return_value=mock_result):
            result = _detect_github_remote("/some/path")
        assert result == {"owner": "bob", "repo": "cool-repo"}

    def test_no_github_remote_returns_defaults(self) -> None:
        mock_result = MagicMock()
        mock_result.stdout = "origin\thttps://gitlab.com/foo/bar.git (fetch)\n"
        with patch("mcp_tools.quality_helpers.subprocess.run", return_value=mock_result):
            result = _detect_github_remote("/some/path")
        assert result == {"owner": "OWNER", "repo": "REPO"}

    def test_subprocess_error_returns_defaults(self) -> None:
        from subprocess import CalledProcessError

        with patch(
            "mcp_tools.quality_helpers.subprocess.run",
            side_effect=CalledProcessError(1, "git"),
        ):
            result = _detect_github_remote("/some/path")
        assert result == {"owner": "OWNER", "repo": "REPO"}

    def test_file_not_found_returns_defaults(self) -> None:
        with patch(
            "mcp_tools.quality_helpers.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            result = _detect_github_remote("/some/path")
        assert result == {"owner": "OWNER", "repo": "REPO"}


# ── _detect_subprocess_usage ─────────────────────────────────────────


class TestDetectSubprocessUsage:
    def test_detects_import_subprocess(self, tmp_path: Path) -> None:
        py_file = tmp_path / "main.py"
        py_file.write_text("import subprocess\nsubprocess.run(['ls'])\n")
        result = _detect_subprocess_usage(str(tmp_path))
        assert result is True

    def test_detects_from_subprocess_import(self, tmp_path: Path) -> None:
        py_file = tmp_path / "main.py"
        py_file.write_text("from subprocess import run\nrun(['ls'])\n")
        result = _detect_subprocess_usage(str(tmp_path))
        assert result is True

    def test_no_subprocess_returns_false(self, tmp_path: Path) -> None:
        py_file = tmp_path / "main.py"
        py_file.write_text("import os\nos.listdir('.')\n")
        result = _detect_subprocess_usage(str(tmp_path))
        assert result is False

    def test_skips_venv_files(self, tmp_path: Path) -> None:
        venv_dir = tmp_path / ".venv" / "lib"
        venv_dir.mkdir(parents=True)
        venv_file = venv_dir / "some.py"
        venv_file.write_text("import subprocess\n")
        result = _detect_subprocess_usage(str(tmp_path))
        assert result is False

    def test_empty_directory_returns_false(self, tmp_path: Path) -> None:
        result = _detect_subprocess_usage(str(tmp_path))
        assert result is False


# ── _generate_badge_markdown ─────────────────────────────────────────


class TestGenerateBadgeMarkdown:
    def test_basic_badge_output(self) -> None:
        github = {"owner": "alice", "repo": "proj"}
        layout: dict[str, Any] = {}
        result = _generate_badge_markdown(github, layout)
        assert result.startswith(_BADGE_BLOCK_START)
        assert result.endswith(_BADGE_BLOCK_END)
        assert "alice/proj/actions/workflows/tests.yml/badge.svg" in result
        assert "alice/proj/actions/workflows/security-lite.yml/badge.svg" in result

    def test_sonarcloud_project_key(self) -> None:
        github = {"owner": "org", "repo": "my-repo"}
        layout: dict[str, Any] = {}
        result = _generate_badge_markdown(github, layout)
        assert "project=org_my-repo" in result
        assert "metric=alert_status" in result
        assert "metric=coverage" in result
        assert "metric=security_rating" in result

    def test_default_owner_repo(self) -> None:
        result = _generate_badge_markdown({}, {})
        assert "OWNER/REPO" in result

    def test_special_chars_in_owner_sanitized(self) -> None:
        github = {"owner": "my org!", "repo": "my repo?"}
        result = _generate_badge_markdown(github, {})
        # project_key uses re.sub to replace non-alphanumeric chars
        assert "project=my_org__my_repo_" in result


# ── _inject_badges_into_readme ───────────────────────────────────────


class TestInjectBadgesIntoReadme:
    def test_no_readme_returns_error(self, tmp_path: Path) -> None:
        result = _inject_badges_into_readme(str(tmp_path), "badges", write=False)
        assert result == {"status": "error", "reason": "no_readme"}

    def test_inject_after_h1(self, tmp_path: Path) -> None:
        readme = tmp_path / "README.md"
        readme.write_text("# My Project\n\nSome content.\n")
        badge_md = f"{_BADGE_BLOCK_START}\nbadge\n{_BADGE_BLOCK_END}"
        result = _inject_badges_into_readme(str(tmp_path), badge_md, write=False)
        assert result["status"] == "preview"
        assert result["path"] == str(readme)

    def test_inject_writes_when_write_true(self, tmp_path: Path) -> None:
        readme = tmp_path / "README.md"
        readme.write_text("# My Project\n\nSome content.\n")
        badge_md = f"{_BADGE_BLOCK_START}\nbadge\n{_BADGE_BLOCK_END}"
        result = _inject_badges_into_readme(str(tmp_path), badge_md, write=True)
        assert result["status"] == "injected"
        content = readme.read_text()
        assert _BADGE_BLOCK_START in content

    def test_update_existing_badge_block(self, tmp_path: Path) -> None:
        readme = tmp_path / "README.md"
        old_badge = f"{_BADGE_BLOCK_START}\nold badge\n{_BADGE_BLOCK_END}"
        readme.write_text(f"# My Project\n\n{old_badge}\n\nContent.\n")
        new_badge = f"{_BADGE_BLOCK_START}\nnew badge\n{_BADGE_BLOCK_END}"
        result = _inject_badges_into_readme(str(tmp_path), new_badge, write=True)
        assert result["status"] == "updated"
        content = readme.read_text()
        assert "new badge" in content
        assert "old badge" not in content

    def test_no_change_when_identical(self, tmp_path: Path) -> None:
        badge_md = f"{_BADGE_BLOCK_START}\nsame badge\n{_BADGE_BLOCK_END}"
        readme = tmp_path / "README.md"
        readme.write_text(f"# My Project\n\n{badge_md}\n\nContent.\n")
        result = _inject_badges_into_readme(str(tmp_path), badge_md, write=True)
        assert result["status"] == "no_change"

    def test_inject_prepend_no_h1(self, tmp_path: Path) -> None:
        readme = tmp_path / "README.md"
        readme.write_text("No heading here.\nJust content.\n")
        badge_md = f"{_BADGE_BLOCK_START}\nbadge\n{_BADGE_BLOCK_END}"
        result = _inject_badges_into_readme(str(tmp_path), badge_md, write=True)
        assert result["status"] == "injected"
        content = readme.read_text()
        assert content.startswith(_BADGE_BLOCK_START)

    def test_preview_does_not_write(self, tmp_path: Path) -> None:
        readme = tmp_path / "README.md"
        readme.write_text("# Title\nContent\n")
        badge_md = f"{_BADGE_BLOCK_START}\nbadge\n{_BADGE_BLOCK_END}"
        result = _inject_badges_into_readme(str(tmp_path), badge_md, write=False)
        assert result["status"] == "preview"
        content = readme.read_text()
        assert _BADGE_BLOCK_START not in content


# ── _build_quality_guidance ──────────────────────────────────────────


class TestBuildQualityGuidance:
    def test_structure(self) -> None:
        github = {"owner": "alice", "repo": "proj"}
        layout: dict[str, Any] = {}
        result = _build_quality_guidance(github, layout, scanner_path=None)
        assert "three_layer_stack" in result
        assert "next_steps" in result
        stack = result["three_layer_stack"]
        assert "development" in stack
        assert "automation" in stack
        assert "authoritative" in stack

    def test_development_layer(self) -> None:
        result = _build_quality_guidance({"owner": "o", "repo": "r"}, {}, None)
        dev = result["three_layer_stack"]["development"]
        assert dev["tool"] == "qlty"
        assert dev["command"] == "qlty check --all"

    def test_automation_layer(self) -> None:
        result = _build_quality_guidance({"owner": "o", "repo": "r"}, {}, None)
        auto = result["three_layer_stack"]["automation"]
        assert auto["tool"] == "GitHub Actions"
        assert ".github/workflows/qlty.yml" in auto["files"]
        assert ".github/workflows/tests.yml" in auto["files"]

    def test_authoritative_dashboard_url(self) -> None:
        result = _build_quality_guidance({"owner": "me", "repo": "thing"}, {}, None)
        auth = result["three_layer_stack"]["authoritative"]
        assert "sonarcloud.io" in auth["dashboard"]
        assert "me_thing" in auth["dashboard"]

    def test_next_steps_count(self) -> None:
        result = _build_quality_guidance({"owner": "o", "repo": "r"}, {}, None)
        assert len(result["next_steps"]) == 4


# ── _compute_gitignore_additions ─────────────────────────────────────


class TestComputeGitignoreAdditions:
    def test_all_missing(self, tmp_path: Path) -> None:
        result = _compute_gitignore_additions(str(tmp_path))
        assert result["status"] == "missing_patterns"
        assert ".qlty/" in result["missing"]
        assert ".coverage" in result["missing"]
        assert "coverage.xml" in result["missing"]
        assert ".scannerwork/" in result["missing"]

    def test_all_present(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(".qlty/\n.coverage\ncoverage.xml\n.scannerwork/\n")
        result = _compute_gitignore_additions(str(tmp_path))
        assert result["status"] == "complete"
        assert result["missing"] == []

    def test_partial_missing(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(".qlty/\n.coverage\n")
        result = _compute_gitignore_additions(str(tmp_path))
        assert result["status"] == "missing_patterns"
        assert "coverage.xml" in result["missing"]
        assert ".scannerwork/" in result["missing"]
        assert ".qlty/" not in result["missing"]


# ── _write_pre_push_hook ─────────────────────────────────────────────


class TestWritePrePushHook:
    def test_no_git_dir(self, tmp_path: Path) -> None:
        result = _write_pre_push_hook(str(tmp_path), write=False)
        assert result == {"status": "error", "reason": "no_git_dir"}

    def test_preview_mode(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        result = _write_pre_push_hook(str(tmp_path), write=False)
        assert result["status"] == "preview"
        assert "qlty check" in result["content_snippet"]

    def test_creates_hook(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        result = _write_pre_push_hook(str(tmp_path), write=True)
        assert result["status"] == "created"
        hook_path = hooks_dir / "pre-push"
        assert hook_path.exists()
        content = hook_path.read_text()
        assert "qlty check --all" in content
        # Check executable permission
        assert os.access(hook_path, os.X_OK)

    def test_existing_hook_with_qlty(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        hook_path = hooks_dir / "pre-push"
        hook_path.write_text("#!/bin/sh\nqlty check --all\n")
        result = _write_pre_push_hook(str(tmp_path), write=True)
        assert result["status"] == "present"


# ── _generate_pre_push_hook (legacy alias) ───────────────────────────


class TestGeneratePrePushHook:
    def test_delegates_to_write_pre_push_hook(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _generate_pre_push_hook(str(tmp_path), write=False)
        assert result["status"] == "preview"
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1


# ── _generate_qlty_toml ─────────────────────────────────────────────


class TestGenerateQltyToml:
    def test_basic_output(self) -> None:
        layout: dict[str, Any] = {"python_version": "3.12", "exclude_patterns": []}
        result = _generate_qlty_toml(layout)
        assert "[project]" in result
        assert 'python_version = "3.12"' in result
        assert "[linter.bandit]" in result
        assert "[linter.pyright]" in result
        assert "[linter.ruff]" in result

    def test_triage_rules_included(self) -> None:
        layout: dict[str, Any] = {"exclude_patterns": []}
        result = _generate_qlty_toml(layout)
        for rule in _QLTY_TEST_TRIAGE_RULES:
            assert f'"{rule}"' in result

    def test_tool_runner_triage(self) -> None:
        layout: dict[str, Any] = {"exclude_patterns": []}
        result = _generate_qlty_toml(layout, is_tool_runner=True)
        for rule in _QLTY_TOOL_RUNNER_TRIAGE_RULES:
            assert f'"{rule}"' in result

    def test_tool_runner_false(self) -> None:
        layout: dict[str, Any] = {"exclude_patterns": []}
        result = _generate_qlty_toml(layout, is_tool_runner=False)
        assert '"bandit:B404"' not in result

    def test_exclude_patterns(self) -> None:
        layout = {"exclude_patterns": ["tests/", "docs/"]}
        result = _generate_qlty_toml(layout)
        assert "[[exclude]]" in result
        assert "patterns = [" in result
        # patterns get normalized
        assert '"tests/**"' in result
        assert '"docs/**"' in result

    def test_no_exclude_patterns(self) -> None:
        layout: dict[str, Any] = {"exclude_patterns": []}
        result = _generate_qlty_toml(layout)
        assert "[[exclude]]" not in result

    def test_default_python_version(self) -> None:
        layout: dict[str, Any] = {"exclude_patterns": []}
        result = _generate_qlty_toml(layout)
        assert 'python_version = "3.11"' in result

    def test_monitor_rules_included(self) -> None:
        layout: dict[str, Any] = {"exclude_patterns": []}
        result = _generate_qlty_toml(layout)
        assert "[[linter.bandit.monitor]]" in result
        assert '"bandit:B311"' in result


# ── _read_informational_bandit_codes ─────────────────────────────────


class TestReadInformationalBanditCodes:
    def test_returns_expected_codes(self) -> None:
        result = _read_informational_bandit_codes()
        assert result == ["B101", "B108", "B311", "B404", "B603", "B607"]

    def test_returns_list(self) -> None:
        result = _read_informational_bandit_codes()
        assert isinstance(result, list)
        assert len(result) == 6


# ── _compute_bandit_ci_skips ─────────────────────────────────────────


class TestComputeBanditCiSkips:
    def test_without_subprocess_usage(self, tmp_path: Path) -> None:
        py = tmp_path / "app.py"
        py.write_text("import os\n")
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = _compute_bandit_ci_skips(str(tmp_path))
        codes = result.split(",")
        assert "B101" in codes
        assert "B108" in codes
        assert "B311" in codes

    def test_with_subprocess_usage(self, tmp_path: Path) -> None:
        py = tmp_path / "runner.py"
        py.write_text("import subprocess\n")
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = _compute_bandit_ci_skips(str(tmp_path))
        codes = result.split(",")
        # Should have subprocess-related codes
        assert "B404" in codes
        assert "B603" in codes
        assert "B607" in codes
        # Codes are sorted and deduplicated
        assert codes == sorted(set(codes))

    def test_result_is_comma_separated(self, tmp_path: Path) -> None:
        py = tmp_path / "app.py"
        py.write_text("print('hello')\n")
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = _compute_bandit_ci_skips(str(tmp_path))
        assert isinstance(result, str)
        assert "," in result


# ── _generate_sonar_workflow ─────────────────────────────────────────


class TestGenerateSonarWorkflow:
    def test_returns_deprecation_message(self) -> None:
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = _generate_sonar_workflow()
        assert "deprecated" in result.lower() or "Sonar workflow" in result

    def test_emits_deprecation_warning(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _generate_sonar_workflow()
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1


# ── _compute_badge_markdown (legacy alias) ───────────────────────────


class TestComputeBadgeMarkdown:
    def test_delegates_to_generate_badge_markdown(self) -> None:
        github = {"owner": "x", "repo": "y"}
        layout: dict[str, Any] = {}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _compute_badge_markdown(github, layout)
        assert _BADGE_BLOCK_START in result
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1

    def test_output_matches_generate_badge_markdown(self) -> None:
        github = {"owner": "org", "repo": "lib"}
        layout: dict[str, Any] = {}
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            legacy = _compute_badge_markdown(github, layout)
        direct = _generate_badge_markdown(github, layout)
        assert legacy == direct


# ── _apply_managed_artifact ──────────────────────────────────────────


class TestApplyManagedArtifact:
    def test_write_new_file(self, tmp_path: Path) -> None:
        path = str(tmp_path / "new_file.txt")
        result = _apply_managed_artifact(path, "content here", exists=False, write=True)
        assert result["status"] == "written"
        assert os.path.exists(path)
        with open(path) as f:
            assert f.read() == "content here"

    def test_preview_new_file(self, tmp_path: Path) -> None:
        path = str(tmp_path / "new_file.txt")
        result = _apply_managed_artifact(
            path, "content here", exists=False, write=False
        )
        assert result["status"] == "preview"
        assert result["content"] == "content here"
        assert not os.path.exists(path)

    def test_already_exists_same_content(self, tmp_path: Path) -> None:
        path = tmp_path / "existing.txt"
        path.write_text("same content")
        result = _apply_managed_artifact(
            str(path), "same content", exists=True, write=True
        )
        assert result["status"] == "already_exists"

    def test_drift_repaired(self, tmp_path: Path) -> None:
        path = tmp_path / "existing.txt"
        path.write_text("old content")
        result = _apply_managed_artifact(
            str(path), "new content", exists=True, write=True
        )
        assert result["status"] == "drift_repaired"
        assert "previous_hash" in result
        assert "new_hash" in result
        old_hash = hashlib.sha256(b"old content").hexdigest()[:16]
        new_hash = hashlib.sha256(b"new content").hexdigest()[:16]
        assert result["previous_hash"] == old_hash
        assert result["new_hash"] == new_hash

    def test_outdated_no_write(self, tmp_path: Path) -> None:
        path = tmp_path / "existing.txt"
        path.write_text("old content")
        result = _apply_managed_artifact(
            str(path), "new content", exists=True, write=False
        )
        assert result["status"] == "outdated"
        assert "current_hash" in result
        assert "expected_hash" in result

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        path = str(tmp_path / "sub" / "dir" / "file.txt")
        result = _apply_managed_artifact(path, "nested", exists=False, write=True)
        assert result["status"] == "written"
        assert os.path.exists(path)

    def test_os_error_reading_existing(self, tmp_path: Path) -> None:
        path = str(tmp_path / "unreadable.txt")
        # When the file can't be read, existing_content defaults to ""
        with patch("builtins.open", side_effect=OSError("Permission denied")):
            result = _apply_managed_artifact(path, "new", exists=True, write=False)
        # SHA of "" != SHA of "new", so status is "outdated"
        assert result["status"] == "outdated"


# ── Workflow content verification ────────────────────────────────────


class TestWorkflowContentVerification:
    """Verify that delegated workflow generators produce expected YAML content."""

    def test_scorecard_has_ossf_action(self) -> None:
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = _generate_scorecard_workflow()
        assert "ossf/scorecard-action" in result

    def test_codeql_has_init_and_analyze(self) -> None:
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = _generate_codeql_workflow()
        assert "Initialize CodeQL" in result
        assert "Perform CodeQL Analysis" in result

    def test_tests_workflow_has_pytest(self) -> None:
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = _generate_tests_workflow()
        assert "pytest" in result
        assert "--cov" in result

    def test_security_workflow_has_bandit(self) -> None:
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = _generate_security_workflow()
        assert "bandit" in result.lower()
        assert "pip-audit" in result
