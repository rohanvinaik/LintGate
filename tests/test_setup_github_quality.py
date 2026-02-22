"""Tests for the setup_github_quality MCP tool and its helpers."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

from mcp_tools.onboarding_tools import (
    _build_quality_guidance,
    _compute_gitignore_additions,
    _detect_github_remote,
    _detect_project_layout,
    _detect_subprocess_usage,
    _generate_badge_markdown,
    _generate_codeclimate_yml,
    _generate_qlty_workflow,
    _generate_qlty_toml,
    _generate_sonar_properties,
    _generate_sonar_workflow,
    _inject_badges_into_readme,
)

# ── GitHub Remote Detection ──────────────────────────────────────────────


class TestDetectGitHubRemote:
    """Tests for _detect_github_remote."""

    def test_https_remote(self, tmp_path: Path) -> None:
        """Detect owner/repo from HTTPS remote."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        stdout = "origin\thttps://github.com/alice/myrepo.git (fetch)\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = stdout
            result = _detect_github_remote(str(tmp_path))

        assert result["detected"] is True
        assert result["owner"] == "alice"
        assert result["repo"] == "myrepo"

    def test_ssh_remote(self, tmp_path: Path) -> None:
        """Detect owner/repo from SSH remote."""
        stdout = "origin\tgit@github.com:bob/cool-project.git (fetch)\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = stdout
            result = _detect_github_remote(str(tmp_path))

        assert result["detected"] is True
        assert result["owner"] == "bob"
        assert result["repo"] == "cool-project"

    def test_no_git_repo(self, tmp_path: Path) -> None:
        """Graceful fallback when not a git repo."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 128
            mock_run.return_value.stdout = ""
            result = _detect_github_remote(str(tmp_path))

        assert result["detected"] is False

    def test_non_github_remote(self, tmp_path: Path) -> None:
        """Non-GitHub remotes are not detected."""
        stdout = "origin\thttps://gitlab.com/alice/myrepo.git (fetch)\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = stdout
            result = _detect_github_remote(str(tmp_path))

        assert result["detected"] is False

    def test_prefers_origin(self, tmp_path: Path) -> None:
        """Prefers origin remote when multiple exist."""
        stdout = (
            "upstream\thttps://github.com/upstream-org/repo.git (fetch)\n"
            "origin\thttps://github.com/my-org/my-repo.git (fetch)\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = stdout
            result = _detect_github_remote(str(tmp_path))

        assert result["owner"] == "my-org"
        assert result["repo"] == "my-repo"

    def test_repo_name_with_dot(self, tmp_path: Path) -> None:
        """Detect repository names that contain dots."""
        stdout = "origin\thttps://github.com/alice/my.repo.git (fetch)\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = stdout
            result = _detect_github_remote(str(tmp_path))

        assert result["detected"] is True
        assert result["owner"] == "alice"
        assert result["repo"] == "my.repo"


# ── Project Layout Detection ─────────────────────────────────────────────


class TestDetectProjectLayout:
    """Tests for _detect_project_layout."""

    def test_detects_source_and_test_dirs(self, tmp_path: Path) -> None:
        """Detect Python packages and test directories."""
        (tmp_path / "mypackage" / "__init__.py").parent.mkdir()
        (tmp_path / "mypackage" / "__init__.py").touch()
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_foo.py").touch()

        result = _detect_project_layout(str(tmp_path))

        assert "mypackage" in result["source_dirs"]
        assert "tests" in result["test_dirs"]

    def test_detects_python_version_from_pyproject(self, tmp_path: Path) -> None:
        """Extract Python version from pyproject.toml."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.11"\n'
        )
        result = _detect_project_layout(str(tmp_path))
        assert result["python_version"] == "3.11"

    def test_detects_mit_license(self, tmp_path: Path) -> None:
        """Detect MIT license from LICENSE file."""
        (tmp_path / "LICENSE").write_text("MIT License\n\nCopyright (c) 2026 Test\n")
        result = _detect_project_layout(str(tmp_path))
        assert result["license"] == "MIT"

    def test_detects_docs_dir(self, tmp_path: Path) -> None:
        """Detect docs directory."""
        (tmp_path / "docs").mkdir()
        result = _detect_project_layout(str(tmp_path))
        assert "docs" in result["doc_dirs"]

    def test_src_layout(self, tmp_path: Path) -> None:
        """Detect src-layout packages."""
        pkg = tmp_path / "src" / "mylib"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").touch()
        result = _detect_project_layout(str(tmp_path))
        assert "src/mylib" in result["source_dirs"]

    def test_no_dirs_returns_dot(self, tmp_path: Path) -> None:
        """Empty project defaults source_dirs to ['.']."""
        result = _detect_project_layout(str(tmp_path))
        assert result["source_dirs"] == ["."]

    def test_hidden_dirs_are_skipped(self, tmp_path: Path) -> None:
        """Hidden directories should not be considered source/test/doc roots."""
        hidden_pkg = tmp_path / ".github" / "actions"
        hidden_pkg.mkdir(parents=True)
        (hidden_pkg / "__init__.py").touch()

        result = _detect_project_layout(str(tmp_path))
        assert ".github" not in result["source_dirs"]


# ── Code Climate YAML Generation ─────────────────────────────────────────


class TestGenerateCodeClimateYml:
    """Tests for _generate_codeclimate_yml."""

    def test_valid_yaml_structure(self) -> None:
        """Generated YAML has required sections."""
        layout = {"exclude_patterns": ["tests/", "docs/"]}
        content = _generate_codeclimate_yml(layout)

        assert 'version: "2"' in content
        assert "method-complexity:" in content
        assert "radon:" in content
        assert "duplication:" in content
        assert "exclude_patterns:" in content
        assert '"tests/"' in content
        assert '"docs/"' in content

    def test_deduplicates_excludes(self) -> None:
        """Duplicate exclude patterns are removed."""
        layout = {"exclude_patterns": ["tests/", "tests/", "docs/"]}
        content = _generate_codeclimate_yml(layout)
        assert content.count('"tests/"') == 1


# ── Sonar Properties Generation ──────────────────────────────────────────


class TestGenerateSonarProperties:
    """Tests for _generate_sonar_properties."""

    def test_uses_github_info(self) -> None:
        """Properties use detected GitHub owner/repo."""
        github = {"detected": True, "owner": "alice", "repo": "myrepo"}
        layout = {"source_dirs": ["src"], "test_dirs": ["tests"], "python_version": "3.12",
                  "exclude_patterns": ["tests/", "docs/"]}
        content = _generate_sonar_properties(github, layout)

        assert "sonar.projectKey=alice_myrepo" in content
        assert "sonar.organization=alice" in content
        assert "sonar.projectName=myrepo" in content
        assert "sonar.sources=src" in content
        assert "sonar.tests=tests" in content
        assert "sonar.python.version=3.12" in content

    def test_placeholder_without_github(self) -> None:
        """Falls back to OWNER/REPO when no GitHub detected."""
        github = {"detected": False}
        layout = {"source_dirs": ["."], "test_dirs": [], "python_version": "3",
                  "exclude_patterns": []}
        content = _generate_sonar_properties(github, layout)

        assert "sonar.projectKey=OWNER_REPO" in content


class TestGenerateSonarWorkflow:
    """Tests for _generate_sonar_workflow."""

    def test_includes_push_pr_and_dispatch(self) -> None:
        layout = {"python_version": "3.12"}
        content = _generate_sonar_workflow(layout)

        assert "on:" in content
        assert "push:" in content
        assert "pull_request:" in content
        assert "workflow_dispatch:" in content
        assert "SonarSource/sonarqube-scan-action@v7" in content
        assert 'python-version: "3.12"' in content
        assert "SONAR_TOKEN" in content

    def test_fallbacks_python_version_for_unexpected_input(self) -> None:
        content = _generate_sonar_workflow({"python_version": ">=3.11"})
        assert 'python-version: "3.11"' in content


class TestGenerateQltyWorkflow:
    """Tests for _generate_qlty_workflow."""

    def test_includes_push_pr_and_dispatch(self) -> None:
        content = _generate_qlty_workflow()

        assert "on:" in content
        assert "push:" in content
        assert "pull_request:" in content
        assert "workflow_dispatch:" in content
        assert "curl -fsSL https://qlty.sh | sh" in content
        assert "check --all" in content


# ── Gitignore Additions ──────────────────────────────────────────────────


class TestComputeGitignoreAdditions:
    """Tests for _compute_gitignore_additions."""

    def test_creates_additions_for_empty_project(self, tmp_path: Path) -> None:
        """Project with no .gitignore gets all standard patterns."""
        result = _compute_gitignore_additions(str(tmp_path))
        assert result["gitignore_exists"] is False
        assert len(result["additions"]) > 10
        assert ".venv/" in result["additions"]

    def test_detects_already_present(self, tmp_path: Path) -> None:
        """Patterns already in .gitignore are not duplicated."""
        (tmp_path / ".gitignore").write_text(".venv/\n__pycache__/\n")
        result = _compute_gitignore_additions(str(tmp_path))
        assert ".venv/" in result["already_present"]
        assert "__pycache__/" in result["already_present"]
        assert ".venv/" not in result["additions"]

    def test_computes_delta(self, tmp_path: Path) -> None:
        """Only missing patterns appear in additions."""
        (tmp_path / ".gitignore").write_text(".venv/\n")
        result = _compute_gitignore_additions(str(tmp_path))
        assert ".venv/" not in result["additions"]
        assert "__pycache__/" in result["additions"]


# ── Badge Generation ─────────────────────────────────────────────────────


class TestGenerateBadgeMarkdown:
    """Tests for _generate_badge_markdown."""

    def test_generates_codeclimate_and_sonar_badges(self) -> None:
        """Both Code Climate and SonarCloud badges generated."""
        github = {"owner": "alice", "repo": "myrepo"}
        layout = {"license": None}
        badges = _generate_badge_markdown(github, layout)

        assert "codeclimate.com" in badges
        assert "PLACEHOLDER" in badges
        assert "sonarcloud.io" in badges
        assert "alice_myrepo" in badges
        assert "metric=coverage" in badges

    def test_includes_license_badge(self) -> None:
        """License badge generated when license detected."""
        github = {"owner": "alice", "repo": "myrepo"}
        layout = {"license": "MIT"}
        badges = _generate_badge_markdown(github, layout)

        assert "License-MIT" in badges
        assert "shields.io" in badges

    def test_no_license_badge_when_none(self) -> None:
        """No license badge when no license detected."""
        github = {"owner": "alice", "repo": "myrepo"}
        layout = {"license": None}
        badges = _generate_badge_markdown(github, layout)

        assert "License" not in badges


# ── README Badge Injection ───────────────────────────────────────────────


class TestInjectBadgesIntoReadme:
    """Tests for _inject_badges_into_readme."""

    def test_injects_after_title(self, tmp_path: Path) -> None:
        """Badges injected after first heading."""
        readme = tmp_path / "README.md"
        readme.write_text("# My Project\n\nSome description.\n")
        result = _inject_badges_into_readme(str(tmp_path), "[![Badge](url)](link)", write=True)

        assert result["status"] == "injected"
        content = readme.read_text()
        lines = content.split("\n")
        assert lines[0] == "# My Project"
        assert "[![Badge](url)](link)" in content

    def test_skips_existing_badges(self, tmp_path: Path) -> None:
        """Does not inject if badges already present."""
        readme = tmp_path / "README.md"
        readme.write_text(
            "# My Project\n\n"
            "[![X](https://api.codeclimate.com/v1/badges/abc/maintainability)](link)\n"
        )
        result = _inject_badges_into_readme(str(tmp_path), "new badges", write=True)
        assert result["status"] == "badges_already_present"

    def test_preview_mode_no_write(self, tmp_path: Path) -> None:
        """Preview mode does not modify the file."""
        readme = tmp_path / "README.md"
        readme.write_text("# Project\n\nHello.\n")
        original = readme.read_text()

        result = _inject_badges_into_readme(str(tmp_path), "[![Badge](url)](link)", write=False)
        assert result["status"] == "preview"
        assert readme.read_text() == original

    def test_no_readme_returns_status(self, tmp_path: Path) -> None:
        """Returns no_readme_found when no README exists."""
        result = _inject_badges_into_readme(str(tmp_path), "badges", write=True)
        assert result["status"] == "no_readme_found"

    def test_case_insensitive_readme(self, tmp_path: Path) -> None:
        """Finds readme.md (lowercase) — macOS filesystems are case-insensitive."""
        readme = tmp_path / "readme.md"
        readme.write_text("# project\n\nhi.\n")
        result = _inject_badges_into_readme(str(tmp_path), "badges", write=False)
        assert result["status"] == "preview"
        # On case-insensitive filesystems, README.md matches readme.md
        assert os.path.basename(result["path"]).lower() == "readme.md"


# ── Full Tool Integration ────────────────────────────────────────────────


class TestSetupGithubQualityTool:
    """Integration tests for the setup_github_quality MCP tool."""

    def test_preview_mode(self, tmp_path: Path) -> None:
        """Preview mode returns all sections without writing files."""
        (tmp_path / "mypackage" / "__init__.py").parent.mkdir()
        (tmp_path / "mypackage" / "__init__.py").touch()
        (tmp_path / "tests").mkdir()
        (tmp_path / "README.md").write_text("# Test\n\nHello.\n")

        from mcp_server import setup_github_quality

        with patch(
            "mcp_tools.onboarding_tools._detect_github_remote",
            return_value={"detected": True, "owner": "alice", "repo": "test"},
        ):
            result = json.loads(setup_github_quality(str(tmp_path), write=False))

        assert result["status"] == "preview"
        assert result["codeclimate"]["status"] == "preview"
        assert result["sonar"]["status"] == "preview"
        assert result["workflow"]["status"] == "preview"
        assert result["qlty_workflow"]["status"] == "preview"
        assert "content" in result["codeclimate"]
        assert "content" in result["sonar"]
        assert "content" in result["workflow"]
        assert "content" in result["qlty_workflow"]
        # Files should NOT exist
        assert not (tmp_path / ".codeclimate.yml").exists()
        assert not (tmp_path / "sonar-project.properties").exists()
        assert not (tmp_path / ".github" / "workflows" / "sonarcloud.yml").exists()
        assert not (tmp_path / ".github" / "workflows" / "qlty.yml").exists()

    def test_write_mode_creates_files(self, tmp_path: Path) -> None:
        """Write mode creates config files and injects badges."""
        (tmp_path / "mypackage" / "__init__.py").parent.mkdir()
        (tmp_path / "mypackage" / "__init__.py").touch()
        (tmp_path / "README.md").write_text("# Test\n\nHello.\n")

        from mcp_server import setup_github_quality

        with patch(
            "mcp_tools.onboarding_tools._detect_github_remote",
            return_value={"detected": True, "owner": "alice", "repo": "test"},
        ):
            result = json.loads(setup_github_quality(str(tmp_path), write=True))

        assert result["status"] == "written"
        assert (tmp_path / ".codeclimate.yml").exists()
        assert (tmp_path / "sonar-project.properties").exists()
        assert (tmp_path / ".github" / "workflows" / "sonarcloud.yml").exists()
        assert (tmp_path / ".github" / "workflows" / "qlty.yml").exists()
        assert result["codeclimate"]["status"] == "written"
        assert result["sonar"]["status"] == "written"
        assert result["workflow"]["status"] == "written"
        assert result["qlty_workflow"]["status"] == "written"
        # README should have badges
        readme_content = (tmp_path / "README.md").read_text()
        assert "sonarcloud.io" in readme_content

    def test_preserves_existing_configs(self, tmp_path: Path) -> None:
        """Does not overwrite existing config files."""
        (tmp_path / ".codeclimate.yml").write_text("existing: true\n")
        (tmp_path / "sonar-project.properties").write_text("existing=true\n")
        workflow_dir = tmp_path / ".github" / "workflows"
        workflow_dir.mkdir(parents=True)
        (workflow_dir / "sonarcloud.yml").write_text("name: existing\n")
        (workflow_dir / "qlty.yml").write_text("name: existing qlty\n")
        (tmp_path / "README.md").write_text("# Test\n")

        from mcp_server import setup_github_quality

        with patch(
            "mcp_tools.onboarding_tools._detect_github_remote",
            return_value={"detected": True, "owner": "alice", "repo": "test"},
        ):
            result = json.loads(setup_github_quality(str(tmp_path), write=True))

        assert result["codeclimate"]["status"] == "already_exists"
        assert result["sonar"]["status"] == "already_exists"
        assert result["workflow"]["status"] == "already_exists"
        assert result["qlty_workflow"]["status"] == "already_exists"
        # Original content preserved
        assert (tmp_path / ".codeclimate.yml").read_text() == "existing: true\n"

    def test_no_github_remote(self, tmp_path: Path) -> None:
        """Works without GitHub remote — badges skipped."""
        (tmp_path / "README.md").write_text("# Test\n")

        from mcp_server import setup_github_quality

        with patch(
            "mcp_tools.onboarding_tools._detect_github_remote",
            return_value={"detected": False, "reason": "no_github_remote_found"},
        ):
            result = json.loads(setup_github_quality(str(tmp_path), write=False))

        assert result["badges"]["status"] == "skipped"

    def test_gitignore_augmented(self, tmp_path: Path) -> None:
        """Gitignore is augmented with missing patterns."""
        (tmp_path / ".gitignore").write_text("*.pyc\n")
        (tmp_path / "README.md").write_text("# Test\n")

        from mcp_server import setup_github_quality

        with patch(
            "mcp_tools.onboarding_tools._detect_github_remote",
            return_value={"detected": True, "owner": "a", "repo": "b"},
        ):
            result = json.loads(setup_github_quality(str(tmp_path), write=True))

        assert result["gitignore"]["status"] == "augmented"
        gi_content = (tmp_path / ".gitignore").read_text()
        assert "# Added by LintGate" in gi_content
        assert ".venv/" in gi_content

    def test_idempotent_gitignore(self, tmp_path: Path) -> None:
        """Running twice does not duplicate gitignore patterns."""
        (tmp_path / "README.md").write_text("# Test\n")

        from mcp_server import setup_github_quality

        gh_mock = {"detected": True, "owner": "a", "repo": "b"}
        with patch("mcp_tools.onboarding_tools._detect_github_remote", return_value=gh_mock):
            setup_github_quality(str(tmp_path), write=True)

        # Second run — badges now exist, gitignore should need no changes
        with patch("mcp_tools.onboarding_tools._detect_github_remote", return_value=gh_mock):
            result = json.loads(setup_github_quality(str(tmp_path), write=True))

        assert result["gitignore"]["status"] == "no_changes_needed"
        assert result["badges"]["status"] == "badges_already_present"
        assert result["codeclimate"]["status"] == "already_exists"
        assert result["sonar"]["status"] == "already_exists"
        assert result["workflow"]["status"] == "already_exists"
        assert result["qlty_workflow"]["status"] == "already_exists"

    def test_qlty_toml_created(self, tmp_path: Path) -> None:
        """Write mode creates .qlty/qlty.toml."""
        (tmp_path / "README.md").write_text("# Test\n")

        from mcp_server import setup_github_quality

        gh_mock = {"detected": True, "owner": "a", "repo": "b"}
        with patch("mcp_tools.onboarding_tools._detect_github_remote", return_value=gh_mock):
            result = json.loads(setup_github_quality(str(tmp_path), write=True))

        assert result["qlty"]["status"] == "written"
        assert result["qlty"]["local_only"] is True
        assert result["qlty"]["tracked_in_git"] is False
        assert (tmp_path / ".qlty" / "qlty.toml").exists()
        toml_content = (tmp_path / ".qlty" / "qlty.toml").read_text()
        assert 'config_version = "0"' in toml_content
        assert "[[triage]]" in toml_content

    def test_qlty_preserves_existing(self, tmp_path: Path) -> None:
        """Does not overwrite existing .qlty/qlty.toml."""
        qlty_dir = tmp_path / ".qlty"
        qlty_dir.mkdir()
        (qlty_dir / "qlty.toml").write_text("existing = true\n")
        (tmp_path / "README.md").write_text("# Test\n")

        from mcp_server import setup_github_quality

        gh_mock = {"detected": True, "owner": "a", "repo": "b"}
        with patch("mcp_tools.onboarding_tools._detect_github_remote", return_value=gh_mock):
            result = json.loads(setup_github_quality(str(tmp_path), write=True))

        assert result["qlty"]["status"] == "already_exists"
        assert (qlty_dir / "qlty.toml").read_text() == "existing = true\n"

    def test_guidance_included(self, tmp_path: Path) -> None:
        """Output includes guidance section with three-layer stack."""
        (tmp_path / "README.md").write_text("# Test\n")

        from mcp_server import setup_github_quality

        gh_mock = {"detected": True, "owner": "a", "repo": "b"}
        with patch("mcp_tools.onboarding_tools._detect_github_remote", return_value=gh_mock):
            result = json.loads(setup_github_quality(str(tmp_path), write=False))

        assert "guidance" in result
        assert "three_layer_stack" in result["guidance"]
        assert "silencing_invalid_issues" in result["guidance"]

    def test_sonar_token_preview(self, tmp_path: Path) -> None:
        """Preview mode with token shows scanner status without running."""
        (tmp_path / "README.md").write_text("# Test\n")

        from mcp_server import setup_github_quality

        gh_mock = {"detected": True, "owner": "a", "repo": "b"}
        with patch("mcp_tools.onboarding_tools._detect_github_remote", return_value=gh_mock):
            result = json.loads(setup_github_quality(
                str(tmp_path), write=False, sonar_token="fake_token",
            ))

        assert result["scanner"]["status"] == "preview"
        assert "never written to disk" in result["scanner"]["note"]

    def test_sonar_token_write_no_scanner(self, tmp_path: Path) -> None:
        """Write with token reports scanner not found when not installed."""
        (tmp_path / "README.md").write_text("# Test\n")
        (tmp_path / "sonar-project.properties").write_text("sonar.projectKey=a_b\n")

        from mcp_server import setup_github_quality

        gh_mock = {"detected": True, "owner": "a", "repo": "b"}
        with patch("mcp_tools.onboarding_tools._detect_github_remote", return_value=gh_mock), \
             patch("mcp_tools.onboarding_tools._detect_sonar_scanner", return_value=None):
            result = json.loads(setup_github_quality(
                str(tmp_path), write=True, sonar_token="fake_token",
            ))

        assert result["scanner"]["status"] == "scanner_not_found"


# ── qlty TOML Generation ────────────────────────────────────────────────


class TestGenerateQltyToml:
    """Tests for _generate_qlty_toml."""

    def test_basic_structure(self) -> None:
        """Generated TOML has required sections."""
        layout = {"test_dirs": ["tests"], "exclude_patterns": ["tests/", "docs/"]}
        content = _generate_qlty_toml(layout)

        assert 'config_version = "0"' in content
        assert "[[plugin]]" in content
        assert "[[triage]]" in content
        assert '"bandit"' in content
        assert '"ruff"' in content

    def test_tool_runner_triage(self) -> None:
        """Tool-runner projects get subprocess triage rules."""
        layout = {"test_dirs": ["tests"], "exclude_patterns": []}
        content = _generate_qlty_toml(layout, is_tool_runner=True)

        assert "bandit:B404" in content
        assert "bandit:B603" in content
        assert "bandit:B607" in content

    def test_non_tool_runner_no_subprocess_triage(self) -> None:
        """Non-tool-runner projects don't suppress subprocess rules."""
        layout = {"test_dirs": ["tests"], "exclude_patterns": []}
        content = _generate_qlty_toml(layout, is_tool_runner=False)

        assert "bandit:B404" not in content
        assert "bandit:B603" not in content

    def test_test_triage_always_present(self) -> None:
        """Test-file triage rules always present (B101, B108)."""
        layout = {"test_dirs": ["tests"], "exclude_patterns": []}
        content = _generate_qlty_toml(layout)

        assert "bandit:B101" in content
        assert "bandit:B108" in content

    def test_monitor_rules(self) -> None:
        """Monitor-mode rules for float equality and unused vars."""
        layout = {"test_dirs": ["tests"], "exclude_patterns": []}
        content = _generate_qlty_toml(layout)

        assert 'set.mode = "monitor"' in content
        assert "radarlint-python:python:S1244" in content

    def test_preserves_wildcard_file_globs(self) -> None:
        """File-glob excludes should remain file globs, not be rewritten as dirs."""
        layout = {"test_dirs": ["tests"], "exclude_patterns": []}
        content = _generate_qlty_toml(layout)

        assert '"*_min.*"' in content
        assert '"*.min.*"' in content
        assert '"*_min./**"' not in content
        assert '"*.min./**"' not in content

    def test_normalizes_directory_excludes(self) -> None:
        """Directory-like excludes should be normalized to /** form."""
        layout = {"test_dirs": ["tests"], "exclude_patterns": ["docs/", ".claude/"]}
        content = _generate_qlty_toml(layout)

        assert '"docs/**"' in content
        assert '".claude/**"' in content


# ── Subprocess Detection ────────────────────────────────────────────────


class TestDetectSubprocessUsage:
    """Tests for _detect_subprocess_usage."""

    def test_detects_subprocess_import(self, tmp_path: Path) -> None:
        """Detects import subprocess in source files."""
        pkg = tmp_path / "mypackage"
        pkg.mkdir()
        (pkg / "__init__.py").touch()
        (pkg / "runner.py").write_text("import subprocess\n\ndef run(): pass\n")

        assert _detect_subprocess_usage(str(tmp_path)) is True

    def test_ignores_test_subprocess(self, tmp_path: Path) -> None:
        """subprocess in test files doesn't count."""
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_runner.py").write_text("import subprocess\n")

        assert _detect_subprocess_usage(str(tmp_path)) is False

    def test_no_subprocess(self, tmp_path: Path) -> None:
        """Project without subprocess returns False."""
        pkg = tmp_path / "mypackage"
        pkg.mkdir()
        (pkg / "__init__.py").touch()
        (pkg / "main.py").write_text("print('hello')\n")

        assert _detect_subprocess_usage(str(tmp_path)) is False


# ── Quality Guidance ────────────────────────────────────────────────────


class TestBuildQualityGuidance:
    """Tests for _build_quality_guidance."""

    def test_three_layer_stack(self) -> None:
        """Guidance includes the three-layer quality stack."""
        github = {"owner": "alice", "repo": "myrepo"}
        layout = {"test_dirs": ["tests"]}
        guidance = _build_quality_guidance(github, layout, scanner_path="/usr/bin/pysonar-scanner")

        assert "three_layer_stack" in guidance
        assert "development" in guidance["three_layer_stack"]
        assert "local_validation" in guidance["three_layer_stack"]
        assert "public_proof" in guidance["three_layer_stack"]
        assert (
            guidance["three_layer_stack"]["local_validation"]["workflow_path"]
            == ".github/workflows/qlty.yml"
        )
        assert (
            guidance["three_layer_stack"]["public_proof"]["workflow_path"]
            == ".github/workflows/sonarcloud.yml"
        )

    def test_silencing_guidance(self) -> None:
        """Guidance includes how to silence issues in each tool."""
        github = {"owner": "alice", "repo": "myrepo"}
        layout = {"test_dirs": ["tests"]}
        guidance = _build_quality_guidance(github, layout, scanner_path=None)

        assert "silencing_invalid_issues" in guidance
        assert "qlty" in guidance["silencing_invalid_issues"]
        assert "sonarcloud" in guidance["silencing_invalid_issues"]
        assert "lintgate" in guidance["silencing_invalid_issues"]

    def test_scanner_found(self) -> None:
        """When scanner found, guidance includes local_run command."""
        github = {"owner": "alice", "repo": "myrepo"}
        layout = {"test_dirs": ["tests"]}
        guidance = _build_quality_guidance(github, layout, scanner_path="/usr/bin/pysonar-scanner")

        assert "local_run" in guidance["sonar_scanner"]
        assert "github_actions" in guidance["sonar_scanner"]
        assert guidance["sonar_scanner"]["workflow_path"] == ".github/workflows/sonarcloud.yml"

    def test_scanner_not_found(self) -> None:
        """When scanner not found, guidance includes install instructions."""
        github = {"owner": "alice", "repo": "myrepo"}
        layout = {"test_dirs": ["tests"]}
        guidance = _build_quality_guidance(github, layout, scanner_path=None)

        assert "install" in guidance["sonar_scanner"]
        assert guidance["sonar_scanner"]["workflow_path"] == ".github/workflows/sonarcloud.yml"
