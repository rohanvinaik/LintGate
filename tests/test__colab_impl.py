"""Tests for mcp_tools/_colab_impl.py helper functions."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from mcp_tools._colab_impl import (
    _build_notebook,
    _count_cached_profiles,
    _count_source_files,
    _get_git_info,
)

# ---------------------------------------------------------------------------
# _get_git_info
# ---------------------------------------------------------------------------


class TestGetGitInfo:
    def test_extracts_repo_url_and_branch(self):
        url_proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="https://github.com/org/repo.git\n"
        )
        branch_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="feature-x\n")
        with patch("subprocess.run", side_effect=[url_proc, branch_proc]):
            info = _get_git_info("/tmp/proj")
            assert info["repo_url"] == "https://github.com/org/repo.git"
            assert info["branch"] == "feature-x"

    def test_defaults_on_git_failure(self):
        url_proc = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="error")
        branch_proc = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="error")
        with patch("subprocess.run", side_effect=[url_proc, branch_proc]):
            info = _get_git_info("/tmp/proj")
            assert info["repo_url"] == ""
            assert info["branch"] == "main"

    def test_handles_os_error(self):
        with patch("subprocess.run", side_effect=OSError("no git")):
            info = _get_git_info("/tmp/proj")
            assert info["repo_url"] == ""
            assert info["branch"] == "main"

    def test_handles_timeout(self):
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5),
        ):
            info = _get_git_info("/tmp/proj")
            assert info["repo_url"] == ""
            assert info["branch"] == "main"


# ---------------------------------------------------------------------------
# _count_cached_profiles
# ---------------------------------------------------------------------------


class TestCountCachedProfiles:
    def test_counts_json_files(self, tmp_path):
        cache_dir = tmp_path / ".lintgate" / "mutation"
        cache_dir.mkdir(parents=True)
        (cache_dir / "func1.json").write_text("{}")
        (cache_dir / "func2.json").write_text("{}")
        (cache_dir / "readme.txt").write_text("not counted")
        assert _count_cached_profiles(str(tmp_path)) == 2

    def test_returns_zero_when_dir_missing(self, tmp_path):
        assert _count_cached_profiles(str(tmp_path)) == 0

    def test_returns_zero_when_empty(self, tmp_path):
        cache_dir = tmp_path / ".lintgate" / "mutation"
        cache_dir.mkdir(parents=True)
        assert _count_cached_profiles(str(tmp_path)) == 0


# ---------------------------------------------------------------------------
# _count_source_files
# ---------------------------------------------------------------------------


class TestCountSourceFiles:
    def test_counts_py_files_in_expected_dirs(self, tmp_path):
        lg = tmp_path / "lintgate"
        lg.mkdir()
        (lg / "core.py").write_text("# code")
        (lg / "util.py").write_text("# code")
        mcp = tmp_path / "mcp_tools"
        mcp.mkdir()
        (mcp / "tool.py").write_text("# code")
        assert _count_source_files(str(tmp_path)) == 3

    def test_excludes_test_files(self, tmp_path):
        lg = tmp_path / "lintgate"
        lg.mkdir()
        (lg / "core.py").write_text("# code")
        (lg / "test_core.py").write_text("# test")
        (lg / "core_test.py").write_text("# test")
        assert _count_source_files(str(tmp_path)) == 1

    def test_returns_zero_when_no_subdirs(self, tmp_path):
        assert _count_source_files(str(tmp_path)) == 0


# ---------------------------------------------------------------------------
# _build_notebook
# ---------------------------------------------------------------------------


class TestBuildNotebook:
    def test_notebook_structure(self):
        nb = _build_notebook(
            repo_url="https://github.com/org/repo.git",
            workers=2,
            budget_ms=500,
            cached_count=10,
            source_count=50,
            local_project_path="/home/user/repo",
        )
        assert nb["nbformat"] == 4
        assert "cells" in nb
        assert "metadata" in nb
        assert nb["metadata"]["colab"]["name"] == "LintGate Mutation Sweep"
        assert nb["metadata"]["kernelspec"]["name"] == "python3"

    def test_notebook_has_markdown_and_code_cells(self):
        nb = _build_notebook(
            repo_url="https://github.com/org/repo.git",
            workers=4,
            budget_ms=1000,
            cached_count=0,
            source_count=100,
            local_project_path="/proj",
        )
        cell_types = [c["cell_type"] for c in nb["cells"]]
        assert "markdown" in cell_types
        assert "code" in cell_types
        # At least 5 markdown + 4 code cells
        assert len(nb["cells"]) >= 9

    def test_repo_url_embedded_in_code_cell(self):
        nb = _build_notebook(
            repo_url="https://github.com/test/mylib.git",
            workers=1,
            budget_ms=200,
            cached_count=5,
            source_count=20,
            local_project_path="/x",
        )
        code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
        # The clone cell should contain the repo URL
        clone_cell_source = "".join(code_cells[0]["source"])
        assert "https://github.com/test/mylib.git" in clone_cell_source

    def test_cached_and_source_counts_in_markdown(self):
        nb = _build_notebook(
            repo_url="https://github.com/a/b.git",
            workers=2,
            budget_ms=500,
            cached_count=7,
            source_count=42,
            local_project_path="/p",
        )
        md_cells = [c for c in nb["cells"] if c["cell_type"] == "markdown"]
        first_md = "".join(md_cells[0]["source"])
        assert "42" in first_md
        assert "7" in first_md
        # ~35 estimated new profiles
        assert "35" in first_md
