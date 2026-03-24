"""Tests for the setup_github_quality MCP tool and its helpers."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

from mcp_tools.onboarding_tools import (
    _readme_has_quality_badges,
)
from mcp_tools.quality_helpers import (
    _build_quality_guidance,
    _compute_gitignore_additions,
    _detect_github_remote,
    _detect_project_layout,
    _detect_subprocess_usage,
    _generate_badge_markdown,
    _generate_clusterfuzzlite_workflow,
    _generate_codeclimate_yml,
    _generate_coveragerc,
    _generate_gitleaks_toml,
    _generate_pypi_publish_workflow,
    _generate_qlty_toml,
    _generate_qlty_workflow,
    _generate_security_workflow,
    _generate_sonar_properties,
    _generate_sonar_workflow,
    _generate_tests_workflow,
    _inject_badges_into_readme,
    _write_pre_push_hook,
)

def _load_tool_result(json_str):
    import json as _j, os as _os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and _os.path.isfile(r.get("file","")):
        with open(r["file"]) as f: return _j.loads(f.read())
    return r


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

        assert result["owner"] == "alice"
        assert result["repo"] == "myrepo"

    def test_ssh_remote(self, tmp_path: Path) -> None:
        """Detect owner/repo from SSH remote."""
        stdout = "origin\tgit@github.com:bob/cool-project.git (fetch)\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = stdout
            result = _detect_github_remote(str(tmp_path))

        assert result["owner"] == "bob"
        assert result["repo"] == "cool-project"

    def test_no_git_repo(self, tmp_path: Path) -> None:
        """Graceful fallback when not a git repo."""
        import subprocess

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(128, "git")
            result = _detect_github_remote(str(tmp_path))

        assert result["owner"] == "OWNER"
        assert result["repo"] == "REPO"

    def test_non_github_remote(self, tmp_path: Path) -> None:
        """Non-GitHub remotes fall back to OWNER/REPO placeholders."""
        stdout = "origin\thttps://gitlab.com/alice/myrepo.git (fetch)\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = stdout
            result = _detect_github_remote(str(tmp_path))

        assert result["owner"] == "OWNER"
        assert result["repo"] == "REPO"

    def test_uses_first_match(self, tmp_path: Path) -> None:
        """Uses the first GitHub remote match found."""
        stdout = (
            "upstream\thttps://github.com/upstream-org/repo.git (fetch)\n"
            "origin\thttps://github.com/my-org/my-repo.git (fetch)\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = stdout
            result = _detect_github_remote(str(tmp_path))

        assert result["owner"] == "upstream-org"
        assert result["repo"] == "repo"

    def test_repo_name_with_dot(self, tmp_path: Path) -> None:
        """Detect repository names that contain dots."""
        stdout = "origin\thttps://github.com/alice/my.repo.git (fetch)\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = stdout
            result = _detect_github_remote(str(tmp_path))

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
        (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.11"\n')
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
        layout = {
            "source_dirs": ["src"],
            "test_dirs": ["tests"],
            "python_version": "3.12",
            "exclude_patterns": ["tests/", "docs/"],
        }
        content = _generate_sonar_properties(github, layout)

        assert "sonar.projectKey=alice_myrepo" in content
        assert "sonar.organization=alice" in content
        assert "sonar.projectName=myrepo" in content
        assert "sonar.sources=src" in content
        assert "sonar.tests=tests" in content
        assert "sonar.python.version=3.12" in content
        assert "sonar.issue.ignore.multicriteria=fp1,fp2,fp3" in content
        assert "pythonsecurity:S2083" in content
        assert "pythonsecurity:S6549" in content

    def test_placeholder_without_github(self) -> None:
        """Falls back to OWNER/REPO when no GitHub detected."""
        github = {"detected": False}
        layout = {
            "source_dirs": ["."],
            "test_dirs": [],
            "python_version": "3",
            "exclude_patterns": [],
        }
        content = _generate_sonar_properties(github, layout)

        assert "sonar.projectKey=OWNER_REPO" in content

    def test_excludes_shell_scripts(self) -> None:
        """Shell scripts must be excluded from SonarCloud Python analysis."""
        github = {"detected": True, "owner": "alice", "repo": "myrepo"}
        layout = {
            "source_dirs": ["src"],
            "test_dirs": ["tests"],
            "python_version": "3.12",
            "exclude_patterns": ["tests/", "docs/"],
        }
        content = _generate_sonar_properties(github, layout)
        assert "*.sh" in content

    def test_includes_coverage_exclusions_example(self) -> None:
        """Template includes commented coverage exclusions example."""
        github = {"detected": True, "owner": "alice", "repo": "myrepo"}
        layout = {
            "source_dirs": ["src"],
            "test_dirs": ["tests"],
            "python_version": "3.12",
            "exclude_patterns": ["tests/"],
        }
        content = _generate_sonar_properties(github, layout)
        assert "sonar.coverage.exclusions" in content

    def test_file_glob_patterns_not_corrupted(self) -> None:
        """File-extension globs like *.sh must not become *.sh** in exclusions."""
        github = {"detected": True, "owner": "alice", "repo": "myrepo"}
        layout = {
            "source_dirs": ["src"],
            "test_dirs": ["tests"],
            "python_version": "3.12",
            "exclude_patterns": ["tests/", "*.sh"],
        }
        content = _generate_sonar_properties(github, layout)
        assert "*.sh" in content
        assert "*.sh**" not in content


class TestGenerateCoveragerc:
    """Tests for _generate_coveragerc."""

    def test_includes_source_and_omit_rules(self) -> None:
        content = _generate_coveragerc()
        assert "[run]" in content
        assert "source =" in content
        assert "lintgate" in content
        assert "mcp_tools" in content
        assert "lintgate/hook_posttooluse.py" in content


class TestGenerateGitleaksToml:
    """Tests for _generate_gitleaks_toml."""

    def test_extends_default_configuration(self) -> None:
        content = _generate_gitleaks_toml()
        assert "[extend]" in content
        assert "useDefault = true" in content
        assert "[allowlist]" in content


class TestGenerateSonarWorkflow:
    """Tests for _generate_sonar_workflow.

    The facade _generate_sonar_workflow() is deprecated and returns a
    deprecation notice string.  The real workflow is generated by the
    workflow_gen module; setup_github_quality calls it directly.
    """

    def test_returns_deprecation_notice(self) -> None:
        """Facade returns a deprecation notice, not a full workflow."""
        content = _generate_sonar_workflow()
        assert "deprecated" in content.lower() or "sonar-project.properties" in content


class TestGenerateTestsWorkflow:
    """Tests for _generate_tests_workflow."""

    def test_includes_push_pr_and_dispatch(self) -> None:
        content = _generate_tests_workflow()
        assert "name: tests" in content
        assert "push:" in content
        assert "pull_request:" in content
        assert "workflow_dispatch:" in content

    def test_runs_pytest(self) -> None:
        content = _generate_tests_workflow()
        assert "pytest" in content

    def test_includes_coverage(self) -> None:
        """Tests workflow includes coverage steps."""
        content = _generate_tests_workflow()
        assert "--cov" in content


class TestGenerateQltyWorkflow:
    """Tests for _generate_qlty_workflow."""

    def test_includes_push_pr_and_dispatch(self) -> None:
        content = _generate_qlty_workflow()

        assert "on:" in content
        assert "push:" in content
        assert "pull_request:" in content
        assert "workflow_dispatch:" in content
        assert "check --all" in content

    def test_uses_official_action(self) -> None:
        """Must use qltysh/qlty-action/install@main, not curl | sh."""
        content = _generate_qlty_workflow()
        assert "qltysh/qlty-action/install@" in content
        assert "curl" not in content
        assert "QLTY_BIN" not in content


class TestGenerateSecurityWorkflow:
    """Tests for _generate_security_workflow."""

    def test_includes_push_pr_and_dispatch(self) -> None:
        content = _generate_security_workflow()

        assert "on:" in content
        assert "push:" in content
        assert "pull_request:" in content
        assert "workflow_dispatch:" in content
        assert "bandit" in content.lower()

    def test_includes_bandit_step(self) -> None:
        """Security workflow includes a Bandit scanning step."""
        content = _generate_security_workflow()
        assert "Bandit" in content or "bandit" in content

    def test_includes_pip_audit(self) -> None:
        """Security workflow includes pip-audit."""
        content = _generate_security_workflow()
        assert "pip-audit" in content


class TestGenerateClusterFuzzLiteWorkflow:
    """Tests for _generate_clusterfuzzlite_workflow."""

    def test_uses_clusterfuzzlite_actions(self) -> None:
        content = _generate_clusterfuzzlite_workflow()
        assert "google/clusterfuzzlite/actions/build_fuzzers@" in content
        assert "google/clusterfuzzlite/actions/run_fuzzers@" in content

    def test_includes_schedule_and_dispatch(self) -> None:
        content = _generate_clusterfuzzlite_workflow()
        assert "schedule:" in content
        assert "workflow_dispatch:" in content
        assert "push:" in content
        assert "pull_request:" in content

    def test_pins_actions_to_sha(self) -> None:
        content = _generate_clusterfuzzlite_workflow()
        assert "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5" in content
        assert "52ecc61cb587ee99c26825a112a21abf19c7448c" in content

    def test_sets_python_language(self) -> None:
        content = _generate_clusterfuzzlite_workflow()
        assert "language: python" in content

    def test_batch_fuzzing_mode(self) -> None:
        content = _generate_clusterfuzzlite_workflow()
        assert "mode: batch" in content
        assert "fuzz-seconds: 300" in content


class TestGeneratePypiPublishWorkflow:
    """Tests for _generate_pypi_publish_workflow."""

    def test_triggers_on_release(self) -> None:
        content = _generate_pypi_publish_workflow()
        assert "release:" in content
        assert "types: [published]" in content

    def test_three_job_pipeline(self) -> None:
        content = _generate_pypi_publish_workflow()
        assert "name: Build distribution" in content
        assert "name: Publish to PyPI" in content
        assert "name: Sign with Sigstore" in content

    def test_uses_trusted_publishing(self) -> None:
        content = _generate_pypi_publish_workflow()
        assert "pypa/gh-action-pypi-publish@" in content
        assert "id-token: write" in content
        # No API token references
        assert "PYPI_TOKEN" not in content
        assert "TWINE" not in content

    def test_uses_sigstore(self) -> None:
        content = _generate_pypi_publish_workflow()
        assert "sigstore/gh-action-sigstore-python@" in content
        assert "*.sigstore.json" in content

    def test_pins_all_actions_to_sha(self) -> None:
        content = _generate_pypi_publish_workflow()
        assert "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5" in content
        assert "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in content
        assert "ed0c53931b1dc9bd32cbe73a98c7f6766f8a527e" in content
        assert "a5caf349bc536fbef3668a10ed7f5cd309a4b53d" in content

    def test_pins_pip(self) -> None:
        content = _generate_pypi_publish_workflow()
        assert "pip==25.0.1" in content


class TestGeneratePrePushHook:
    """Tests for _write_pre_push_hook."""

    def test_creates_hook_with_qlty(self, tmp_path: Path) -> None:
        """Pre-push hook includes qlty check --all."""
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        result = _write_pre_push_hook(str(tmp_path), write=True)
        assert result["status"] == "created"
        assert "qlty check" in result["content_snippet"]

    def test_preview_mode_does_not_write(self, tmp_path: Path) -> None:
        """Preview mode returns preview status without creating the file."""
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        result = _write_pre_push_hook(str(tmp_path), write=False)
        assert result["status"] == "preview"
        assert not (hooks_dir / "pre-push").exists()

    def test_no_git_dir_returns_error(self, tmp_path: Path) -> None:
        """Returns error when .git/hooks does not exist."""
        result = _write_pre_push_hook(str(tmp_path), write=False)
        assert result["status"] == "error"


# ── Gitignore Additions ──────────────────────────────────────────────────


class TestComputeGitignoreAdditions:
    """Tests for _compute_gitignore_additions."""

    def test_missing_patterns_for_empty_project(self, tmp_path: Path) -> None:
        """Project with no .gitignore reports missing patterns."""
        result = _compute_gitignore_additions(str(tmp_path))
        assert result["status"] == "missing_patterns"
        assert len(result["missing"]) > 0

    def test_already_present_not_in_missing(self, tmp_path: Path) -> None:
        """Patterns already in .gitignore are not listed as missing."""
        (tmp_path / ".gitignore").write_text(".qlty/\n.coverage\ncoverage.xml\n.scannerwork/\n")
        result = _compute_gitignore_additions(str(tmp_path))
        assert result["status"] == "complete"
        assert len(result["missing"]) == 0

    def test_computes_delta(self, tmp_path: Path) -> None:
        """Only missing patterns appear in the missing list."""
        (tmp_path / ".gitignore").write_text(".qlty/\n")
        result = _compute_gitignore_additions(str(tmp_path))
        assert ".qlty/" not in result["missing"]
        assert ".coverage" in result["missing"]


# ── Badge Generation ─────────────────────────────────────────────────────


class TestGenerateBadgeMarkdown:
    """Tests for _generate_badge_markdown."""

    def test_generates_workflow_and_sonar_badges(self) -> None:
        """Tests, Security workflow badges and SonarCloud badges generated."""
        github = {"owner": "alice", "repo": "myrepo"}
        layout = {"license": None}
        badges = _generate_badge_markdown(github, layout)

        # Tests badge first
        assert "actions/workflows/tests.yml/badge.svg" in badges
        # Security workflow badge
        assert "actions/workflows/security-lite.yml/badge.svg" in badges
        assert "PLACEHOLDER" not in badges
        assert "codeclimate.com" not in badges
        # SonarCloud badges still present
        assert "sonarcloud.io" in badges
        assert "alice_myrepo" in badges
        assert "metric=coverage" in badges
        assert "metric=security_rating" in badges

    def test_tests_badge_comes_first(self) -> None:
        """Tests badge must appear before Security badge."""
        github = {"owner": "alice", "repo": "myrepo"}
        layout = {"license": None}
        badges = _generate_badge_markdown(github, layout)
        tests_pos = badges.index("tests.yml")
        security_pos = badges.index("security-lite.yml")
        assert tests_pos < security_pos

    def test_no_license_badge(self) -> None:
        """Badge markdown does not include license badges (not in current scope)."""
        github = {"owner": "alice", "repo": "myrepo"}
        layout = {"license": "MIT"}
        badges = _generate_badge_markdown(github, layout)

        # Current implementation does not emit license badges
        assert "License-MIT" not in badges


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

    def test_updates_existing_badge_block(self, tmp_path: Path) -> None:
        """Updates existing managed badge block in-place."""
        readme = tmp_path / "README.md"
        badge_block = _generate_badge_markdown(
            {"owner": "alice", "repo": "myrepo"},
            {"license": None},
        )
        readme.write_text(f"# My Project\n\n{badge_block}\n")
        result = _inject_badges_into_readme(str(tmp_path), "new badges", write=True)
        # Existing managed block gets replaced — status is "updated"
        assert result["status"] == "updated"

    def test_updates_managed_badge_block(self, tmp_path: Path) -> None:
        """Managed badge block is replaced in-place when content changes."""
        readme = tmp_path / "README.md"
        readme.write_text(
            "# My Project\n\n"
            "<!-- lintgate:quality-badges:start -->\n"
            "old\n"
            "<!-- lintgate:quality-badges:end -->\n",
        )
        new_badges = _generate_badge_markdown(
            {"owner": "alice", "repo": "myrepo"},
            {"license": None},
        )
        result = _inject_badges_into_readme(str(tmp_path), new_badges, write=True)
        assert result["status"] == "updated"
        content = readme.read_text()
        assert "metric=security_rating" in content
        assert content.count("lintgate:quality-badges:start") == 1

    def test_readme_has_quality_badges_requires_all_fingerprints(self, tmp_path: Path) -> None:
        """README should fail quality badge check if any required fingerprint is missing."""
        readme = tmp_path / "README.md"
        # Missing tests.yml badge and security_rating metric
        readme.write_text(
            "# My Project\n\n"
            "[![Security](https://github.com/a/b/actions/workflows/security-lite.yml/badge.svg)]"
            "(https://github.com/a/b/actions/workflows/security-lite.yml)\n"
            "[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?"
            "project=a_b&metric=alert_status)](link)\n"
            "[![Coverage](https://sonarcloud.io/api/project_badges/measure?"
            "project=a_b&metric=coverage)](link)\n"
        )
        assert _readme_has_quality_badges(str(tmp_path)) is False

    def test_readme_has_quality_badges_requires_tests_badge(self, tmp_path: Path) -> None:
        """README should fail quality badge check if tests.yml badge is missing."""
        readme = tmp_path / "README.md"
        readme.write_text(
            "# My Project\n\n"
            "[![Security](https://github.com/a/b/actions/workflows/security-lite.yml/badge.svg)]"
            "(https://github.com/a/b/actions/workflows/security-lite.yml)\n"
            "[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?"
            "project=a_b&metric=alert_status)](link)\n"
            "[![Coverage](https://sonarcloud.io/api/project_badges/measure?"
            "project=a_b&metric=coverage)](link)\n"
            "[![Security Rating](https://sonarcloud.io/api/project_badges/measure?"
            "project=a_b&metric=security_rating)](link)\n"
        )
        assert _readme_has_quality_badges(str(tmp_path)) is False

    def test_preview_mode_no_write(self, tmp_path: Path) -> None:
        """Preview mode does not modify the file."""
        readme = tmp_path / "README.md"
        readme.write_text("# Project\n\nHello.\n")
        original = readme.read_text()

        result = _inject_badges_into_readme(str(tmp_path), "[![Badge](url)](link)", write=False)
        assert result["status"] == "preview"
        assert readme.read_text() == original

    def test_no_readme_returns_status(self, tmp_path: Path) -> None:
        """Returns error status when no README exists."""
        result = _inject_badges_into_readme(str(tmp_path), "badges", write=True)
        assert result["status"] == "error"
        assert result["reason"] == "no_readme"

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

        from mcp_tools.setup_github_quality import setup_github_quality

        with patch(
            "mcp_tools.setup_github_quality._detect_github_remote",
            return_value={"owner": "alice", "repo": "test"},
        ):
            result = _load_tool_result(setup_github_quality(str(tmp_path), write=False))

        assert result["status"] == "preview"
        assert result["codeclimate"]["status"] == "preview"
        assert result["sonar"]["status"] == "preview"
        assert result["coveragerc"]["status"] == "preview"
        assert result["gitleaks"]["status"] == "preview"
        assert result["github_actions"]["sonarcloud"]["status"] == "preview"
        assert result["github_actions"]["tests"]["status"] == "preview"
        assert result["github_actions"]["qlty"]["status"] == "preview"
        assert result["github_actions"]["security"]["status"] == "preview"
        assert result["github_actions"]["clusterfuzzlite"]["status"] == "preview"
        assert result["github_actions"]["pypi_publish"]["status"] == "preview"
        assert "content" in result["codeclimate"]
        assert "content" in result["sonar"]
        assert "content" in result["coveragerc"]
        assert "content" in result["gitleaks"]
        assert "content" in result["github_actions"]["sonarcloud"]
        assert "content" in result["github_actions"]["tests"]
        assert "content" in result["github_actions"]["qlty"]
        assert "content" in result["github_actions"]["security"]
        # Files should NOT exist
        assert not (tmp_path / ".codeclimate.yml").exists()
        assert not (tmp_path / "sonar-project.properties").exists()
        assert not (tmp_path / ".coveragerc").exists()
        assert not (tmp_path / ".gitleaks.toml").exists()
        assert not (tmp_path / ".github" / "workflows" / "sonarcloud.yml").exists()
        assert not (tmp_path / ".github" / "workflows" / "tests.yml").exists()
        assert not (tmp_path / ".github" / "workflows" / "qlty.yml").exists()
        assert not (tmp_path / ".github" / "workflows" / "security-lite.yml").exists()

    def test_write_mode_creates_files(self, tmp_path: Path) -> None:
        """Write mode creates config files and injects badges."""
        (tmp_path / "mypackage" / "__init__.py").parent.mkdir()
        (tmp_path / "mypackage" / "__init__.py").touch()
        (tmp_path / "README.md").write_text("# Test\n\nHello.\n")
        # Create .git/hooks so pre-push hook can be written
        (tmp_path / ".git" / "hooks").mkdir(parents=True)

        from mcp_tools.setup_github_quality import setup_github_quality

        with patch(
            "mcp_tools.setup_github_quality._detect_github_remote",
            return_value={"owner": "alice", "repo": "test"},
        ):
            result = _load_tool_result(setup_github_quality(str(tmp_path), write=True))

        assert result["status"] == "written"
        assert (tmp_path / ".codeclimate.yml").exists()
        assert (tmp_path / "sonar-project.properties").exists()
        assert (tmp_path / ".coveragerc").exists()
        assert (tmp_path / ".gitleaks.toml").exists()
        assert (tmp_path / ".github" / "workflows" / "sonarcloud.yml").exists()
        assert (tmp_path / ".github" / "workflows" / "tests.yml").exists()
        assert (tmp_path / ".github" / "workflows" / "qlty.yml").exists()
        assert (tmp_path / ".github" / "workflows" / "security-lite.yml").exists()
        assert (tmp_path / ".git" / "hooks" / "pre-push").exists()
        assert (tmp_path / ".github" / "workflows" / "cif.yml").exists()
        assert (tmp_path / ".github" / "workflows" / "pypi-publish.yml").exists()
        assert result["codeclimate"]["status"] == "written"
        assert result["sonar"]["status"] == "written"
        assert result["coveragerc"]["status"] == "written"
        assert result["gitleaks"]["status"] == "written"
        assert result["github_actions"]["sonarcloud"]["status"] == "written"
        assert result["github_actions"]["tests"]["status"] == "written"
        assert result["github_actions"]["qlty"]["status"] == "written"
        assert result["github_actions"]["security"]["status"] == "written"
        assert result["pre_push_hook"]["status"] == "created"
        assert result["github_actions"]["clusterfuzzlite"]["status"] in (
            "written",
            "drift_repaired",
        )
        assert result["github_actions"]["pypi_publish"]["status"] in (
            "written",
            "drift_repaired",
        )
        # README should have badges
        readme_content = (tmp_path / "README.md").read_text()
        assert "sonarcloud.io" in readme_content
        assert "metric=security_rating" in readme_content

    def test_preserves_existing_configs(self, tmp_path: Path) -> None:
        """Does not overwrite existing config files."""
        (tmp_path / ".codeclimate.yml").write_text("existing: true\n")
        (tmp_path / "sonar-project.properties").write_text("existing=true\n")
        (tmp_path / ".coveragerc").write_text("[run]\nsource=lintgate\n")
        (tmp_path / ".gitleaks.toml").write_text("[extend]\nuseDefault=true\n")
        workflow_dir = tmp_path / ".github" / "workflows"
        workflow_dir.mkdir(parents=True)
        (workflow_dir / "sonarcloud.yml").write_text("name: existing\n")
        (workflow_dir / "tests.yml").write_text("name: existing tests\n")
        (workflow_dir / "qlty.yml").write_text("name: existing qlty\n")
        (workflow_dir / "security-lite.yml").write_text("name: existing security\n")
        # Create .git/hooks with an existing pre-push hook containing qlty check
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "pre-push").write_text("#!/bin/sh\nqlty check --all\n")
        (tmp_path / "README.md").write_text("# Test\n")

        from mcp_tools.setup_github_quality import setup_github_quality

        with patch(
            "mcp_tools.setup_github_quality._detect_github_remote",
            return_value={"owner": "alice", "repo": "test"},
        ):
            result = _load_tool_result(setup_github_quality(str(tmp_path), write=True))

        assert result["codeclimate"]["status"] in ("already_exists", "drift_repaired")
        assert result["sonar"]["status"] in ("already_exists", "drift_repaired")
        assert result["coveragerc"]["status"] in ("already_exists", "drift_repaired")
        assert result["gitleaks"]["status"] in ("already_exists", "drift_repaired")
        assert result["github_actions"]["sonarcloud"]["status"] in (
            "already_exists",
            "drift_repaired",
        )
        assert result["github_actions"]["tests"]["status"] in (
            "already_exists",
            "drift_repaired",
        )
        assert result["github_actions"]["qlty"]["status"] in (
            "already_exists",
            "drift_repaired",
        )
        assert result["github_actions"]["security"]["status"] in (
            "already_exists",
            "drift_repaired",
        )
        assert result["pre_push_hook"]["status"] == "present"
        # Original content preserved
        assert (tmp_path / ".codeclimate.yml").read_text() == "existing: true\n"

    def test_no_github_remote(self, tmp_path: Path) -> None:
        """Works without GitHub remote — badges skipped."""
        (tmp_path / "README.md").write_text("# Test\n")

        from mcp_tools.setup_github_quality import setup_github_quality

        with patch(
            "mcp_tools.setup_github_quality._detect_github_remote",
            return_value={"owner": "OWNER", "repo": "REPO"},
        ):
            result = _load_tool_result(setup_github_quality(str(tmp_path), write=False))

        assert result["badges"]["status"] == "skipped_no_remote"

    def test_gitignore_augmented(self, tmp_path: Path) -> None:
        """Gitignore is augmented with missing patterns."""
        (tmp_path / ".gitignore").write_text("*.pyc\n")
        (tmp_path / "README.md").write_text("# Test\n")

        from mcp_tools.setup_github_quality import setup_github_quality

        with patch(
            "mcp_tools.setup_github_quality._detect_github_remote",
            return_value={"owner": "a", "repo": "b"},
        ):
            result = _load_tool_result(setup_github_quality(str(tmp_path), write=True))

        assert result["gitignore"]["status"] == "augmented"
        gi_content = (tmp_path / ".gitignore").read_text()
        # The required patterns (.qlty/, .coverage, etc.) are added
        assert ".qlty/" in gi_content or ".coverage" in gi_content

    def test_idempotent_gitignore(self, tmp_path: Path) -> None:
        """Running twice does not duplicate gitignore patterns."""
        (tmp_path / "README.md").write_text("# Test\n")

        from mcp_tools.setup_github_quality import setup_github_quality

        gh_mock = {"owner": "a", "repo": "b"}
        with patch("mcp_tools.setup_github_quality._detect_github_remote", return_value=gh_mock):
            setup_github_quality(str(tmp_path), write=True)

        # Second run — gitignore patterns already present, badges block exists
        with patch("mcp_tools.setup_github_quality._detect_github_remote", return_value=gh_mock):
            result = _load_tool_result(setup_github_quality(str(tmp_path), write=True))

        assert result["gitignore"]["status"] == "no_changes_needed"
        # Badge block exists so it gets "no_change" (same content replaced)
        assert result["badges"]["status"] == "no_change"
        assert result["codeclimate"]["status"] == "already_exists"
        assert result["sonar"]["status"] == "already_exists"
        assert result["coveragerc"]["status"] == "already_exists"
        assert result["gitleaks"]["status"] == "already_exists"
        assert result["github_actions"]["sonarcloud"]["status"] == "already_exists"
        assert result["github_actions"]["tests"]["status"] == "already_exists"
        assert result["github_actions"]["qlty"]["status"] == "already_exists"
        assert result["github_actions"]["security"]["status"] == "already_exists"

    def test_qlty_toml_created(self, tmp_path: Path) -> None:
        """Write mode creates .qlty/qlty.toml."""
        (tmp_path / "README.md").write_text("# Test\n")

        from mcp_tools.setup_github_quality import setup_github_quality

        gh_mock = {"owner": "a", "repo": "b"}
        with patch("mcp_tools.setup_github_quality._detect_github_remote", return_value=gh_mock):
            result = _load_tool_result(setup_github_quality(str(tmp_path), write=True))

        assert result["qlty"]["status"] == "written"
        assert result["qlty"]["local_only"] is False
        assert (tmp_path / ".qlty" / "qlty.toml").exists()
        assert (tmp_path / ".qlty" / ".gitignore").exists()
        toml_content = (tmp_path / ".qlty" / "qlty.toml").read_text()
        assert "[project]" in toml_content
        assert "[linter.bandit]" in toml_content

    def test_qlty_preserves_existing(self, tmp_path: Path) -> None:
        """Does not overwrite existing .qlty/qlty.toml."""
        qlty_dir = tmp_path / ".qlty"
        qlty_dir.mkdir()
        (qlty_dir / "qlty.toml").write_text("existing = true\n")
        (tmp_path / "README.md").write_text("# Test\n")

        from mcp_tools.setup_github_quality import setup_github_quality

        gh_mock = {"owner": "a", "repo": "b"}
        with patch("mcp_tools.setup_github_quality._detect_github_remote", return_value=gh_mock):
            result = _load_tool_result(setup_github_quality(str(tmp_path), write=True))

        # drift_repaired is re-normalized to already_exists for qlty in setup_github_quality
        assert result["qlty"]["status"] in ("already_exists", "drift_repaired")

    def test_guidance_included(self, tmp_path: Path) -> None:
        """Output includes guidance section with three-layer stack."""
        (tmp_path / "README.md").write_text("# Test\n")

        from mcp_tools.setup_github_quality import setup_github_quality

        gh_mock = {"owner": "a", "repo": "b"}
        with patch("mcp_tools.setup_github_quality._detect_github_remote", return_value=gh_mock):
            result = _load_tool_result(setup_github_quality(str(tmp_path), write=False))

        assert "guidance" in result
        assert "three_layer_stack" in result["guidance"]
        assert "next_steps" in result["guidance"]

    def test_sonar_token_preview(self, tmp_path: Path) -> None:
        """Preview mode with token shows scanner status without running."""
        (tmp_path / "README.md").write_text("# Test\n")

        from mcp_tools.setup_github_quality import setup_github_quality

        gh_mock = {"owner": "a", "repo": "b"}
        with patch("mcp_tools.setup_github_quality._detect_github_remote", return_value=gh_mock):
            result = json.loads(
                setup_github_quality(
                    str(tmp_path),
                    write=False,
                    sonar_token="fake_token",
                )
            )

        assert result["scanner"]["status"] == "preview"

    def test_sonar_token_write_no_scanner(self, tmp_path: Path) -> None:
        """Write with token reports scanner not found when not installed."""
        (tmp_path / "README.md").write_text("# Test\n")
        (tmp_path / "sonar-project.properties").write_text("sonar.projectKey=a_b\n")

        from mcp_tools.setup_github_quality import setup_github_quality

        gh_mock = {"owner": "a", "repo": "b"}
        with (
            patch(
                "mcp_tools.setup_github_quality._detect_github_remote",
                return_value=gh_mock,
            ),
            patch(
                "mcp_tools.setup_github_quality._detect_sonar_scanner",
                return_value=None,
            ),
        ):
            result = json.loads(
                setup_github_quality(
                    str(tmp_path),
                    write=True,
                    sonar_token="fake_token",
                )
            )

        assert result["scanner"]["status"] == "scanner_not_found"


# ── qlty TOML Generation ────────────────────────────────────────────────


class TestGenerateQltyToml:
    """Tests for _generate_qlty_toml."""

    def test_basic_structure(self) -> None:
        """Generated TOML has required sections."""
        layout = {"test_dirs": ["tests"], "exclude_patterns": ["tests/", "docs/"]}
        content = _generate_qlty_toml(layout)

        assert "[project]" in content
        assert "[linter.bandit]" in content
        assert "[linter.ruff]" in content
        assert "enabled = true" in content

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
        """Monitor-mode rules for pseudo-random generators."""
        layout = {"test_dirs": ["tests"], "exclude_patterns": []}
        content = _generate_qlty_toml(layout)

        assert "[[linter.bandit.monitor]]" in content
        assert "bandit:B311" in content

    def test_preserves_wildcard_file_globs(self) -> None:
        """Wildcard patterns in exclude_patterns are preserved as-is."""
        layout = {"test_dirs": ["tests"], "exclude_patterns": ["*.sh", "*.min.js"]}
        content = _generate_qlty_toml(layout)

        # Wildcard file globs should not get /** appended
        assert '"*.sh"' in content
        assert '"*.min.js"' in content
        assert '"*.sh/**"' not in content

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

    def test_detects_test_subprocess(self, tmp_path: Path) -> None:
        """subprocess in test files is also detected."""
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_runner.py").write_text("import subprocess\n")

        assert _detect_subprocess_usage(str(tmp_path)) is True

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
        assert "automation" in guidance["three_layer_stack"]
        assert "authoritative" in guidance["three_layer_stack"]
        assert guidance["three_layer_stack"]["development"]["tool"] == "qlty"
        assert guidance["three_layer_stack"]["authoritative"]["tool"] == "SonarCloud"

    def test_next_steps_included(self) -> None:
        """Guidance includes next_steps list."""
        github = {"owner": "alice", "repo": "myrepo"}
        layout = {"test_dirs": ["tests"]}
        guidance = _build_quality_guidance(github, layout, scanner_path=None)

        assert "next_steps" in guidance
        assert len(guidance["next_steps"]) > 0

    def test_scanner_found(self) -> None:
        """When scanner found, guidance structure is unchanged (scanner not tracked)."""
        github = {"owner": "alice", "repo": "myrepo"}
        layout = {"test_dirs": ["tests"]}
        guidance = _build_quality_guidance(github, layout, scanner_path="/usr/bin/pysonar-scanner")

        assert "three_layer_stack" in guidance
        assert "next_steps" in guidance

    def test_scanner_not_found(self) -> None:
        """When scanner not found, guidance structure is unchanged."""
        github = {"owner": "alice", "repo": "myrepo"}
        layout = {"test_dirs": ["tests"]}
        guidance = _build_quality_guidance(github, layout, scanner_path=None)

        assert "three_layer_stack" in guidance
        assert "next_steps" in guidance
