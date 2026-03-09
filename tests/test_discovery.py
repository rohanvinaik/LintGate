"""Tests for lintgate.discovery — canonical file discovery module."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from lintgate.discovery import (
    CANONICAL_EXCLUDE_DIRS,
    _git_discover,
    _walk_discover,
    discover_project_files,
    should_skip_dir,
)

if TYPE_CHECKING:
    from pathlib import Path


# ── should_skip_dir ──────────────────────────────────────────────────────


class TestShouldSkipDir:
    """Test should_skip_dir covers CANONICAL_EXCLUDE_DIRS + hidden + backup."""

    @pytest.mark.parametrize("dirname", sorted(CANONICAL_EXCLUDE_DIRS))
    def test_canonical_dirs_skipped(self, dirname: str) -> None:
        assert should_skip_dir(dirname) is True

    @pytest.mark.parametrize("dirname", [".hidden", ".secret", ".cache"])
    def test_hidden_dirs_skipped(self, dirname: str) -> None:
        assert should_skip_dir(dirname) is True

    @pytest.mark.parametrize("dirname", ["backup", "bak", "archive", "old_versions"])
    def test_backup_dirs_skipped(self, dirname: str) -> None:
        assert should_skip_dir(dirname) is True

    @pytest.mark.parametrize("dirname", ["src", "lib", "lintgate", "tests", "mcp_tools"])
    def test_normal_dirs_not_skipped(self, dirname: str) -> None:
        assert should_skip_dir(dirname) is False

    def test_empty_string(self) -> None:
        assert should_skip_dir("") is False


# ── _walk_discover (fallback path) ───────────────────────────────────────


class TestWalkDiscover:
    """Test os.walk fallback discovery."""

    def test_excludes_canonical_dirs(self, tmp_path: Path) -> None:
        """Dirs in CANONICAL_EXCLUDE_DIRS are skipped."""
        # Create canonical dirs with .py files
        for dirname in (".venv", "mutants", "__pycache__", "node_modules"):
            d = tmp_path / dirname
            d.mkdir()
            (d / "fake.py").write_text("x = 1")

        # Create a real source file
        (tmp_path / "real.py").write_text("x = 1")

        files = _walk_discover(str(tmp_path), ".py")
        basenames = [os.path.basename(f) for f in files]
        assert "real.py" in basenames
        assert "fake.py" not in basenames

    def test_excludes_hidden_dirs(self, tmp_path: Path) -> None:
        """Directories starting with '.' are skipped."""
        hidden = tmp_path / ".secret"
        hidden.mkdir()
        (hidden / "leak.py").write_text("x = 1")
        (tmp_path / "visible.py").write_text("x = 1")

        files = _walk_discover(str(tmp_path), ".py")
        basenames = [os.path.basename(f) for f in files]
        assert "visible.py" in basenames
        assert "leak.py" not in basenames

    def test_excludes_backup_dirs(self, tmp_path: Path) -> None:
        """Backup-like directories are skipped."""
        bak = tmp_path / "backup"
        bak.mkdir()
        (bak / "old.py").write_text("x = 1")
        (tmp_path / "current.py").write_text("x = 1")

        files = _walk_discover(str(tmp_path), ".py")
        basenames = [os.path.basename(f) for f in files]
        assert "current.py" in basenames
        assert "old.py" not in basenames

    def test_extra_exclude_dirs(self, tmp_path: Path) -> None:
        """Extra exclude dirs extend the canonical set."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "script.py").write_text("x = 1")
        (tmp_path / "app.py").write_text("x = 1")

        files = _walk_discover(str(tmp_path), ".py", extra_exclude_dirs=frozenset({"deploy"}))
        basenames = [os.path.basename(f) for f in files]
        assert "app.py" in basenames
        assert "script.py" not in basenames

    def test_suffix_filtering(self, tmp_path: Path) -> None:
        """Only files with the requested suffix are returned."""
        (tmp_path / "code.py").write_text("x = 1")
        (tmp_path / "readme.md").write_text("# hi")

        py_files = _walk_discover(str(tmp_path), ".py")
        md_files = _walk_discover(str(tmp_path), ".md")
        assert len(py_files) == 1
        assert len(md_files) == 1
        assert py_files[0].endswith(".py")
        assert md_files[0].endswith(".md")


# ── _git_discover ────────────────────────────────────────────────────────


class TestGitDiscover:
    """Test git ls-files primary path."""

    def test_returns_tracked_files(self, tmp_path: Path) -> None:
        """Git-based discovery returns tracked files."""
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="src/app.py\nlib/util.py\n", stderr=""
        )
        # Create the files so os.path.isfile passes
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("x = 1")
        (tmp_path / "lib").mkdir()
        (tmp_path / "lib" / "util.py").write_text("x = 1")

        with patch("lintgate.discovery.subprocess.run", return_value=mock_result):
            files = _git_discover(str(tmp_path), ".py")

        assert files is not None
        assert len(files) == 2
        assert all(f.endswith(".py") for f in files)

    def test_excludes_nonexistent_files(self, tmp_path: Path) -> None:
        """Files listed by git but not on disk are excluded."""
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="deleted.py\n", stderr=""
        )
        with patch("lintgate.discovery.subprocess.run", return_value=mock_result):
            files = _git_discover(str(tmp_path), ".py")

        assert files is not None
        assert len(files) == 0

    def test_returns_none_on_git_failure(self, tmp_path: Path) -> None:
        """Returns None when git command fails."""
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr="not a git repo"
        )
        with patch("lintgate.discovery.subprocess.run", return_value=mock_result):
            result = _git_discover(str(tmp_path), ".py")

        assert result is None

    def test_returns_none_when_git_not_found(self, tmp_path: Path) -> None:
        """Returns None when git is not installed."""
        with patch("lintgate.discovery.subprocess.run", side_effect=FileNotFoundError):
            result = _git_discover(str(tmp_path), ".py")

        assert result is None


# ── discover_project_files (integration) ─────────────────────────────────


class TestDiscoverProjectFiles:
    """Test the main discover_project_files entry point."""

    def test_fallback_on_non_git_repo(self, tmp_path: Path) -> None:
        """Falls back to os.walk when git fails."""
        (tmp_path / "main.py").write_text("x = 1")
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pkg.py").write_text("x = 1")

        with patch("lintgate.discovery.subprocess.run", side_effect=FileNotFoundError):
            files = discover_project_files(str(tmp_path))

        basenames = [os.path.basename(f) for f in files]
        assert "main.py" in basenames
        assert "pkg.py" not in basenames

    def test_respects_limit(self, tmp_path: Path) -> None:
        """Limit parameter caps the number of returned files."""
        for i in range(5):
            (tmp_path / f"file_{i}.py").write_text("x = 1")

        with patch("lintgate.discovery.subprocess.run", side_effect=FileNotFoundError):
            files = discover_project_files(str(tmp_path), limit=3)

        assert len(files) == 3

    def test_source_paths_constrains_scope(self, tmp_path: Path) -> None:
        """source_paths restricts to specific subdirectories."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("x = 1")
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "deploy.py").write_text("x = 1")

        with patch("lintgate.discovery.subprocess.run", side_effect=FileNotFoundError):
            files = discover_project_files(str(tmp_path), source_paths=["src"])

        basenames = [os.path.basename(f) for f in files]
        assert "app.py" in basenames
        assert "deploy.py" not in basenames

    def test_site_packages_filtered(self, tmp_path: Path) -> None:
        """Symlinks into site-packages are excluded."""
        real_pkg = tmp_path / "real_site_packages" / "site-packages" / "pkg"
        real_pkg.mkdir(parents=True)
        (real_pkg / "mod.py").write_text("x = 1")

        link = tmp_path / "linked_pkg"
        link.symlink_to(real_pkg)

        (tmp_path / "app.py").write_text("x = 1")

        with patch("lintgate.discovery.subprocess.run", side_effect=FileNotFoundError):
            files = discover_project_files(str(tmp_path))

        basenames = [os.path.basename(f) for f in files]
        assert "app.py" in basenames
        assert "mod.py" not in basenames

    def test_returns_sorted(self, tmp_path: Path) -> None:
        """Results are sorted."""
        for name in ("z.py", "a.py", "m.py"):
            (tmp_path / name).write_text("x = 1")

        with patch("lintgate.discovery.subprocess.run", side_effect=FileNotFoundError):
            files = discover_project_files(str(tmp_path))

        assert files == sorted(files)

    def test_empty_for_invalid_root(self) -> None:
        """Returns empty list for non-existent directory."""
        assert discover_project_files("/nonexistent/path") == []

    def test_mutants_excluded(self, tmp_path: Path) -> None:
        """The mutants directory is excluded — the smoking gun for #245."""
        mutants = tmp_path / "mutants"
        mutants.mkdir()
        (mutants / "generated.py").write_text("x = 1")
        (tmp_path / "real.py").write_text("x = 1")

        with patch("lintgate.discovery.subprocess.run", side_effect=FileNotFoundError):
            files = discover_project_files(str(tmp_path))

        basenames = [os.path.basename(f) for f in files]
        assert "real.py" in basenames
        assert "generated.py" not in basenames
