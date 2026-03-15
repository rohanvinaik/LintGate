"""Tests for mcp_tools/_gh_helpers.py helper functions."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

from mcp_tools._gh_helpers import (
    _GITHUB_REMOTE_RE,
    _clone_wiki,
    _detect_repo,
    _push_wiki,
    _repo_full_name,
    _run_gh,
)

# ---------------------------------------------------------------------------
# _GITHUB_REMOTE_RE
# ---------------------------------------------------------------------------


class TestGithubRemoteRe:
    def test_matches_https_url(self):
        m = _GITHUB_REMOTE_RE.search("origin\thttps://github.com/acme/repo.git (fetch)")
        assert m is not None
        assert m.group(1) == "acme"
        assert m.group(2) == "repo"

    def test_matches_ssh_url(self):
        m = _GITHUB_REMOTE_RE.search("origin\tgit@github.com:user/proj.git (push)")
        assert m is not None
        assert m.group(1) == "user"
        assert m.group(2) == "proj"

    def test_no_match_for_non_github(self):
        m = _GITHUB_REMOTE_RE.search("origin\thttps://gitlab.com/org/repo.git (fetch)")
        assert m is None


# ---------------------------------------------------------------------------
# _run_gh
# ---------------------------------------------------------------------------


class TestRunGh:
    def test_success_with_json_output(self):
        data = {"login": "testuser"}
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(data)
        )
        with patch("subprocess.run", return_value=proc):
            result = _run_gh(["auth", "status"])
            assert result == {"login": "testuser"}

    def test_success_with_empty_output(self):
        proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        with patch("subprocess.run", return_value=proc):
            result = _run_gh(["issue", "close", "42"])
            assert result == {}

    def test_success_with_non_json_output(self):
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="plain text result\n"
        )
        with patch("subprocess.run", return_value=proc):
            result = _run_gh(["pr", "view"])
            assert result == {"raw": "plain text result"}

    def test_error_returncode(self):
        proc = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="permission denied"
        )
        with patch("subprocess.run", return_value=proc):
            result = _run_gh(["repo", "list"])
            assert result == {"error": "permission denied"}

    def test_error_returncode_empty_stderr(self):
        proc = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr=""
        )
        with patch("subprocess.run", return_value=proc):
            result = _run_gh(["repo", "list"])
            assert result == {"error": "gh exited with 128"}

    def test_file_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = _run_gh(["auth", "status"])
            assert "gh CLI not found" in result["error"]

    def test_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=30)):
            result = _run_gh(["repo", "view"])
            assert "timed out" in result["error"]


# ---------------------------------------------------------------------------
# _detect_repo
# ---------------------------------------------------------------------------


class TestDetectRepo:
    def test_detects_owner_repo_from_remote(self):
        proc = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="origin\thttps://github.com/myorg/myrepo.git (fetch)\norigin\thttps://github.com/myorg/myrepo.git (push)\n",
        )
        with patch("subprocess.run", return_value=proc):
            info = _detect_repo("/tmp/proj")
            assert info == {"owner": "myorg", "repo": "myrepo"}

    def test_returns_empty_on_no_remote(self):
        proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        with patch("subprocess.run", return_value=proc):
            info = _detect_repo("/tmp/proj")
            assert info == {"owner": "", "repo": ""}

    def test_returns_empty_on_error(self):
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "git")):
            info = _detect_repo("/tmp/proj")
            assert info == {"owner": "", "repo": ""}


# ---------------------------------------------------------------------------
# _repo_full_name
# ---------------------------------------------------------------------------


class TestRepoFullName:
    def test_returns_owner_slash_repo(self):
        with patch(
            "mcp_tools._gh_helpers._detect_repo",
            return_value={"owner": "acme", "repo": "lib"},
        ):
            assert _repo_full_name("/tmp/proj") == "acme/lib"

    def test_returns_empty_when_no_owner(self):
        with patch(
            "mcp_tools._gh_helpers._detect_repo",
            return_value={"owner": "", "repo": ""},
        ):
            assert _repo_full_name("/tmp/proj") == ""


# ---------------------------------------------------------------------------
# _clone_wiki
# ---------------------------------------------------------------------------


class TestCloneWiki:
    def test_success(self):
        proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        with patch("subprocess.run", return_value=proc):
            result = _clone_wiki("acme/repo", "/tmp/wiki")
            assert result == {"ok": True}

    def test_clone_failure(self):
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(
                128, "git", stderr="repository not found"
            ),
        ):
            result = _clone_wiki("acme/repo", "/tmp/wiki")
            assert "error" in result
            assert "clone failed" in result["error"].lower()

    def test_clone_timeout(self):
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30),
        ):
            result = _clone_wiki("acme/repo", "/tmp/wiki")
            assert "timed out" in result["error"]


# ---------------------------------------------------------------------------
# _push_wiki
# ---------------------------------------------------------------------------


class TestPushWiki:
    def test_no_changes(self):
        """When git status --porcelain returns empty, no commit/push needed."""
        add_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        status_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="")

        with patch("subprocess.run", side_effect=[add_proc, status_proc]):
            result = _push_wiki("/tmp/wiki", "update")
            assert result == {"ok": True, "message": "No changes to push"}

    def test_success_with_changes(self):
        add_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        status_proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="M Home.md\n"
        )
        commit_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        push_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="")

        with patch(
            "subprocess.run",
            side_effect=[add_proc, status_proc, commit_proc, push_proc],
        ):
            result = _push_wiki("/tmp/wiki", "update pages")
            assert result == {"ok": True}

    def test_push_failure(self):
        add_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        status_proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="M file.md\n"
        )
        commit_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="")

        with patch(
            "subprocess.run",
            side_effect=[
                add_proc,
                status_proc,
                commit_proc,
                subprocess.CalledProcessError(1, "git", stderr="auth failed"),
            ],
        ):
            result = _push_wiki("/tmp/wiki", "msg")
            assert "error" in result
            assert "push failed" in result["error"].lower()
