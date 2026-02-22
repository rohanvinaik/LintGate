"""Tests for the hygiene precheck module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lintgate.hygiene import (
    _CHECK_REGISTRY,
    _COMMAND_CLASSES,
    _check_clean_working_tree,
    _check_gitignore_coverage,
    _check_lockfile_exists,
    _check_lockfile_fresh,
    _check_no_staged_secrets,
    _check_pinned_version,
    _check_quality_infra,
    _check_venv_active,
    _classify_command,
    classify_and_check,
)

# ── Command classification ───────────────────────────────────────────────


class TestClassifyCommand:
    def test_pip_install(self):
        assert _classify_command("pip install requests") == "pip_install"

    def test_pip3_install(self):
        assert _classify_command("pip3 install flask") == "pip_install"

    def test_uv_install(self):
        assert _classify_command("uv pip install django") == "pip_install"

    def test_uv_add(self):
        assert _classify_command("uv add httpx") == "pip_install"

    def test_git_commit(self):
        assert _classify_command("git commit -m 'fix'") == "git_commit"

    def test_git_push(self):
        assert _classify_command("git push origin main") == "git_commit"

    def test_env_edit(self):
        assert _classify_command("edit .env file") == "env_edit"

    def test_export_var(self):
        assert _classify_command("export API_KEY=abc123") == "env_edit"

    def test_publish(self):
        assert _classify_command("twine upload dist/*") == "publish_build"

    def test_uv_publish(self):
        assert _classify_command("uv publish") == "publish_build"

    def test_python_build(self):
        assert _classify_command("python -m build") == "publish_build"

    def test_unrecognized(self):
        assert _classify_command("ls -la") is None

    def test_run_tests(self):
        assert _classify_command("pytest tests/") is None

    def test_case_insensitive(self):
        assert _classify_command("PIP INSTALL requests") == "pip_install"


# ── Individual check functions ───────────────────────────────────────────


class TestCheckVenvActive:
    def test_venv_exists(self, tmp_path):
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        result = _check_venv_active("pip install x", str(tmp_path))
        assert result is None  # No warning

    def test_venv_missing(self, tmp_path):
        result = _check_venv_active("pip install x", str(tmp_path))
        assert result is not None
        assert result.check == "venv_active"
        assert result.actionability == "immediate"

    def test_venv_dir_without_bin(self, tmp_path):
        (tmp_path / ".venv").mkdir()
        result = _check_venv_active("pip install x", str(tmp_path))
        assert result is not None  # venv dir exists but no bin/

    def test_alternative_venv_name(self, tmp_path):
        (tmp_path / "venv" / "bin").mkdir(parents=True)
        result = _check_venv_active("pip install x", str(tmp_path))
        assert result is None


class TestCheckLockfileExists:
    def test_lockfile_present(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
        (tmp_path / "uv.lock").write_text("")
        result = _check_lockfile_exists("pip install x", str(tmp_path))
        assert result is None

    def test_lockfile_missing_with_manifest(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
        result = _check_lockfile_exists("pip install x", str(tmp_path))
        assert result is not None
        assert result.check == "lockfile_exists"

    def test_no_manifest_no_warning(self, tmp_path):
        result = _check_lockfile_exists("pip install x", str(tmp_path))
        assert result is None

    def test_requirements_txt_counts(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
        (tmp_path / "requirements.txt").write_text("flask>=2.0\n")
        result = _check_lockfile_exists("pip install x", str(tmp_path))
        assert result is None


class TestCheckPinnedVersion:
    def test_pinned(self):
        result = _check_pinned_version("pip install requests>=2.28", "/tmp")
        assert result is None

    def test_unpinned(self):
        result = _check_pinned_version("pip install requests", "/tmp")
        assert result is not None
        assert result.check == "pinned_version"
        assert "requests" in result.message

    def test_multiple_unpinned(self):
        result = _check_pinned_version("pip install requests flask", "/tmp")
        assert result is not None
        assert "requests" in result.message

    def test_mixed(self):
        result = _check_pinned_version("pip install requests>=2.28 flask", "/tmp")
        assert result is not None
        assert "flask" in result.message
        assert "requests" not in result.message

    def test_flags_ignored(self):
        result = _check_pinned_version("pip install -r requirements.txt", "/tmp")
        assert result is None  # -r is a flag, not a package

    def test_uv_add_unpinned(self):
        result = _check_pinned_version("uv add httpx", "/tmp")
        assert result is not None
        assert "httpx" in result.message

    def test_uv_add_pinned(self):
        result = _check_pinned_version("uv add httpx>=0.24", "/tmp")
        assert result is None


class TestCheckLockfileFresh:
    def test_fresh_lockfile(self, tmp_path):
        manifest = tmp_path / "pyproject.toml"
        lockfile = tmp_path / "uv.lock"
        manifest.write_text("[project]\nname='test'\n")
        lockfile.write_text("")
        # Ensure lockfile is newer
        import time

        time.sleep(0.01)
        lockfile.write_text("fresh")

        result = _check_lockfile_fresh("git commit", str(tmp_path))
        assert result is None

    def test_stale_lockfile(self, tmp_path):
        lockfile = tmp_path / "uv.lock"
        lockfile.write_text("")
        import time

        time.sleep(0.01)
        manifest = tmp_path / "pyproject.toml"
        manifest.write_text("[project]\nname='test'\n")

        result = _check_lockfile_fresh("git commit", str(tmp_path))
        assert result is not None
        assert result.check == "lockfile_fresh"

    def test_no_manifest(self, tmp_path):
        result = _check_lockfile_fresh("git commit", str(tmp_path))
        assert result is None


class TestCheckGitignoreCoverage:
    def test_env_covered(self, tmp_path):
        (tmp_path / ".gitignore").write_text(".env\n")
        result = _check_gitignore_coverage("edit .env", str(tmp_path))
        assert result is None

    def test_env_not_covered(self, tmp_path):
        (tmp_path / ".gitignore").write_text("*.pyc\n")
        result = _check_gitignore_coverage("edit .env", str(tmp_path))
        assert result is not None
        assert result.check == "gitignore_coverage"

    def test_no_gitignore(self, tmp_path):
        result = _check_gitignore_coverage("edit .env", str(tmp_path))
        assert result is not None
        assert "No .gitignore" in result.message

    def test_wildcard_pattern(self, tmp_path):
        (tmp_path / ".gitignore").write_text("*.env\n")
        result = _check_gitignore_coverage("edit .env", str(tmp_path))
        assert result is None


class TestCheckCleanWorkingTree:
    def test_clean_tree(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            result = _check_clean_working_tree("uv publish", str(tmp_path))

        assert result is None

    def test_dirty_tree(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = " M file.py\n?? new.py\n"

        with patch("subprocess.run", return_value=mock_result):
            result = _check_clean_working_tree("uv publish", str(tmp_path))

        assert result is not None
        assert result.check == "clean_working_tree"
        assert "2 uncommitted" in result.message

    def test_not_git_repo(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 128

        with patch("subprocess.run", return_value=mock_result):
            result = _check_clean_working_tree("uv publish", str(tmp_path))

        assert result is None


class TestCheckNoStagedSecrets:
    def test_no_secrets(self, tmp_path):
        with patch("lintgate.channels.git_channel._check_diff_secrets", return_value=[]):
            result = _check_no_staged_secrets("git commit", str(tmp_path))
        assert result is None

    def test_secrets_found(self, tmp_path):
        fake_finding = MagicMock()
        with patch(
            "lintgate.channels.git_channel._check_diff_secrets",
            return_value=[fake_finding],
        ):
            result = _check_no_staged_secrets("git commit", str(tmp_path))
        assert result is not None
        assert result.check == "no_staged_secrets"
        assert "1 potential secret" in result.message


# ── Integration: classify_and_check ──────────────────────────────────────


class TestClassifyAndCheck:
    def test_pip_install_returns_hygiene(self, tmp_path):
        result = classify_and_check("pip install requests", str(tmp_path))
        assert result.command_class == "pip_install"
        # Should have at least venv_active warning (no venv in tmp_path)
        checks = [w.check for w in result.warnings]
        assert "venv_active" in checks

    def test_unrecognized_command(self, tmp_path):
        result = classify_and_check("ls -la", str(tmp_path))
        assert result.command_class is None
        assert len(result.warnings) == 0

    def test_git_commit_checks(self, tmp_path):
        with patch("lintgate.channels.git_channel._check_diff_secrets", return_value=[]):
            result = classify_and_check("git commit -m 'test'", str(tmp_path))
        assert result.command_class == "git_commit"

    def test_publish_checks(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = " M dirty.py\n"

        with patch("subprocess.run", return_value=mock_result):
            result = classify_and_check("twine upload dist/*", str(tmp_path))

        assert result.command_class == "publish_build"
        checks = [w.check for w in result.warnings]
        assert "clean_working_tree" in checks

    def test_recommendation_on_warnings(self, tmp_path):
        result = classify_and_check("pip install requests", str(tmp_path))
        assert "Address before proceeding" in result.recommendation

    def test_recommendation_no_warnings(self, tmp_path):
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / "uv.lock").write_text("")
        result = classify_and_check("pip install requests>=2.0", str(tmp_path))
        # Even with venv + lockfile, pinned is satisfied too
        if not result.warnings:
            assert "passed" in result.recommendation

    def test_graceful_degradation_on_error(self, tmp_path):
        """If a check function raises, it should not crash classify_and_check."""
        with patch(
            "lintgate.hygiene._check_venv_active",
            side_effect=RuntimeError("unexpected"),
        ):
            result = classify_and_check("pip install requests", str(tmp_path))
        # Should still return a result, just without that check
        assert result.command_class == "pip_install"


# ── Quality infrastructure check ─────────────────────────────────────────


class TestCheckQualityInfra:
    def test_no_git_returns_none(self, tmp_path):
        """Non-git project returns None (no quality infra expected)."""
        result = _check_quality_infra("git push", str(tmp_path))
        assert result is None

    @patch("lintgate.quality_infra._has_github_remote", return_value=False)
    def test_no_github_remote_returns_none(self, mock_remote, tmp_path):
        """Git repo without GitHub remote returns None."""
        (tmp_path / ".git").mkdir()
        result = _check_quality_infra("git push", str(tmp_path))
        assert result is None

    @patch("lintgate.quality_infra._has_github_remote", return_value=True)
    def test_missing_artifacts_returns_warning(self, mock_remote, tmp_path):
        """GitHub project with missing artifacts returns HygieneWarning."""
        (tmp_path / ".git").mkdir()
        result = _check_quality_infra("git push", str(tmp_path))
        assert result is not None
        assert result.check == "quality_infrastructure"
        assert result.actionability == "immediate"
        assert result.confidence == 0.90
        assert "missing" in result.evidence

    @patch("lintgate.quality_infra._has_github_remote", return_value=True)
    @patch("lintgate.quality_infra.audit_quality_infrastructure")
    def test_complete_infra_returns_none(self, mock_audit, mock_remote, tmp_path):
        """Complete quality infra returns None."""
        from lintgate.quality_infra import QualityAuditResult

        mock_audit.return_value = QualityAuditResult(
            complete=True,
            present=["all"],
            missing=[],
            has_github_remote=True,
            badge_fingerprints_ok=True,
        )
        result = _check_quality_infra("git push", str(tmp_path))
        assert result is None

    def test_quality_infra_in_git_commit_class(self):
        """quality_infrastructure is in the git_commit command class checks."""
        checks = _COMMAND_CLASSES["git_commit"]["checks"]
        assert "quality_infrastructure" in checks

    def test_quality_infra_in_check_registry(self):
        """quality_infrastructure is registered in the check registry."""
        assert "quality_infrastructure" in _CHECK_REGISTRY
        assert _CHECK_REGISTRY["quality_infrastructure"] is _check_quality_infra

    def test_graceful_on_import_error(self, tmp_path):
        """If quality_infra import fails, returns None gracefully."""
        with patch(
            "lintgate.hygiene._check_quality_infra",
            side_effect=ImportError("mocked"),
        ):
            # Direct call should not crash classify_and_check
            result = classify_and_check("git push origin main", str(tmp_path))
            assert result.command_class == "git_commit"

    def test_audit_crash_returns_none(self, tmp_path):
        """audit_quality_infrastructure crash → graceful None return."""
        with patch(
            "lintgate.quality_infra.audit_quality_infrastructure",
            side_effect=RuntimeError("boom"),
        ):
            result = _check_quality_infra("git push", str(tmp_path))
        assert result is None
