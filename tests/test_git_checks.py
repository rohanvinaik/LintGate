"""Tests for lintgate/channels/_git_checks.py.

Covers working tree scope, large changes, lockfile freshness,
sensitive files, and quality infrastructure checks.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from lintgate.channels._git_checks import (
    _check_large_changes,
    _check_lockfile_freshness,
    _check_sensitive_files,
    _check_working_tree_scope,
)


def _mock_run_cmd(stdout: str):
    result = MagicMock()
    result.stdout = stdout
    return result


# ── _check_working_tree_scope ────────────────────────────────────


class TestCheckWorkingTreeScope:
    @patch("lintgate.channels._git_checks.run_cmd")
    def test_few_files_no_finding(self, mock_run):
        mock_run.return_value = _mock_run_cmd("M  file1.py\nM  file2.py\n")
        result = _check_working_tree_scope("/project")
        assert result == []

    @patch("lintgate.channels._git_checks.run_cmd")
    def test_many_files_triggers_finding(self, mock_run):
        lines = "\n".join(f"M  file_{i}.py" for i in range(15))
        mock_run.return_value = _mock_run_cmd(lines)
        result = _check_working_tree_scope("/project")
        assert len(result) == 1
        assert result[0].kind == "wide_working_tree"
        assert result[0].evidence["modified_count"] == 15
        assert result[0].evidence["untracked_count"] == 0

    @patch("lintgate.channels._git_checks.run_cmd")
    def test_counts_untracked_separately(self, mock_run):
        lines = "\n".join(
            [f"M  mod_{i}.py" for i in range(6)] + [f"?? new_{i}.py" for i in range(6)]
        )
        mock_run.return_value = _mock_run_cmd(lines)
        result = _check_working_tree_scope("/project")
        assert len(result) == 1
        assert result[0].evidence["modified_count"] == 6
        assert result[0].evidence["untracked_count"] == 6

    @patch("lintgate.channels._git_checks.run_cmd", return_value=None)
    def test_git_failure_returns_empty(self, _mock):
        assert _check_working_tree_scope("/project") == []

    @patch("lintgate.channels._git_checks.run_cmd")
    def test_exactly_10_no_finding(self, mock_run):
        lines = "\n".join(f"M  file_{i}.py" for i in range(10))
        mock_run.return_value = _mock_run_cmd(lines)
        result = _check_working_tree_scope("/project")
        assert result == []


# ── _check_large_changes ─────────────────────────────────────────


class TestCheckLargeChanges:
    @patch("lintgate.channels._git_checks.run_cmd")
    def test_small_change_no_finding(self, mock_run):
        mock_run.return_value = _mock_run_cmd(
            " 2 files changed, 50 insertions(+), 30 deletions(-)\n"
        )
        result = _check_large_changes("/project")
        assert result == []

    @patch("lintgate.channels._git_checks.run_cmd")
    def test_large_change_triggers(self, mock_run):
        mock_run.return_value = _mock_run_cmd(
            " 10 files changed, 400 insertions(+), 200 deletions(-)\n"
        )
        result = _check_large_changes("/project")
        assert len(result) == 1
        assert result[0].kind == "large_staged_changes"
        assert "600" in result[0].message

    @patch("lintgate.channels._git_checks.run_cmd", return_value=None)
    def test_git_failure_returns_empty(self, _mock):
        assert _check_large_changes("/project") == []


# ── _check_lockfile_freshness ────────────────────────────────────


class TestCheckLockfileFreshness:
    def test_no_manifest_returns_empty(self, tmp_path):
        findings, repairs = _check_lockfile_freshness(str(tmp_path))
        assert findings == []
        assert repairs == []

    def test_no_lockfile_missing_finding(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='test'")
        findings, repairs = _check_lockfile_freshness(str(tmp_path))
        assert len(findings) == 1
        assert findings[0].kind == "missing_lockfile"
        assert len(repairs) == 1
        assert repairs[0].safe is True

    def test_alt_lockfile_no_finding(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]")
        (tmp_path / "requirements.txt").write_text("flask==2.0")
        findings, repairs = _check_lockfile_freshness(str(tmp_path))
        assert findings == []

    def test_stale_lockfile(self, tmp_path):
        lock = tmp_path / "uv.lock"
        lock.write_text("# lock")
        time.sleep(0.05)
        manifest = tmp_path / "pyproject.toml"
        manifest.write_text("[project]")
        findings, repairs = _check_lockfile_freshness(str(tmp_path))
        assert len(findings) == 1
        assert findings[0].kind == "stale_lockfile"
        assert len(repairs) == 1

    def test_fresh_lockfile_no_finding(self, tmp_path):
        manifest = tmp_path / "pyproject.toml"
        manifest.write_text("[project]")
        time.sleep(0.05)
        lock = tmp_path / "uv.lock"
        lock.write_text("# lock")
        findings, repairs = _check_lockfile_freshness(str(tmp_path))
        assert findings == []


# ── _check_sensitive_files ───────────────────────────────────────


class TestCheckSensitiveFiles:
    @patch("lintgate.channels._git_checks.run_cmd")
    def test_env_file_detected(self, mock_run):
        mock_run.return_value = _mock_run_cmd("?? .env\n")
        result = _check_sensitive_files("/project")
        assert len(result) == 1
        assert result[0].kind == "sensitive_file"
        assert ".env" in result[0].message

    @patch("lintgate.channels._git_checks.run_cmd")
    def test_credentials_json_detected(self, mock_run):
        mock_run.return_value = _mock_run_cmd("A  credentials.json\n")
        result = _check_sensitive_files("/project")
        assert len(result) == 1

    @patch("lintgate.channels._git_checks.run_cmd")
    def test_normal_file_not_flagged(self, mock_run):
        mock_run.return_value = _mock_run_cmd("M  src/main.py\n")
        result = _check_sensitive_files("/project")
        assert result == []

    @patch("lintgate.channels._git_checks.run_cmd", return_value=None)
    def test_git_failure_returns_empty(self, _mock):
        assert _check_sensitive_files("/project") == []

    @patch("lintgate.channels._git_checks.run_cmd")
    def test_multiple_sensitive_files(self, mock_run):
        mock_run.return_value = _mock_run_cmd("?? .env\n?? .env.local\nA  id_rsa\n")
        result = _check_sensitive_files("/project")
        assert len(result) == 3
