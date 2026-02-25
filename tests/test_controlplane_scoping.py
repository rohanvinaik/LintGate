"""Tests for the ControlPlane scoping contract in mcp_tools/controlplane_tools.py.

This verifies that _resolve_scope correctly handles project, changed, staged, and explicit file lists.
"""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from mcp_tools.controlplane_tools import _resolve_scope


@pytest.fixture
def mock_project(tmp_path):
    """Create a mock project structure."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    # Create some python files
    (project_root / "app.py").write_text("print('app')")
    (project_root / "utils.py").write_text("print('utils')")
    (project_root / "subdir").mkdir()
    (project_root / "subdir" / "tool.py").write_text("print('tool')")
    (project_root / "README.md").write_text("Hello")

    return project_root


class TestResolveScope:
    def test_scope_project(self, mock_project):
        """Verify scope='project' finds all Python files."""
        files = _resolve_scope(mock_project, "project")
        assert len(files) == 3
        assert "app.py" in files
        assert "utils.py" in files
        assert "subdir/tool.py" in files
        assert "README.md" not in files

    def test_scope_project_limit(self, mock_project):
        """Verify scope='project' respects the 50-file limit."""
        for i in range(100):
            (mock_project / f"file_{i}.py").write_text("# test")

        files = _resolve_scope(mock_project, "project")
        assert len(files) == 50

    def test_scope_changed_success(self, mock_project):
        """Verify scope='changed' uses git diff."""
        mock_stdout = "app.py\nsubdir/tool.py\nother.txt\n"
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=0, stdout=mock_stdout, stderr="")
            files = _resolve_scope(mock_project, "changed")

            # Check git command
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "git" in args
            assert "diff" in args
            assert "HEAD" in args

            # Check output
            assert len(files) == 2
            assert "app.py" in files
            assert "subdir/tool.py" in files

    def test_scope_changed_git_failure_falls_back(self, mock_project):
        """Verify scope='changed' falls back to project scope on git error."""
        with mock.patch("subprocess.run", side_effect=subprocess.SubprocessError):
            files = _resolve_scope(mock_project, "changed")
            assert len(files) == 3  # All files in project

    def test_scope_staged_success(self, mock_project):
        """Verify scope='staged' uses git diff --staged."""
        mock_stdout = "utils.py\n"
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=0, stdout=mock_stdout, stderr="")
            files = _resolve_scope(mock_project, "staged")

            # Check git command
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "--staged" in args or "--cached" in args

            assert files == ["utils.py"]

    def test_scope_files_relative(self, mock_project):
        """Verify scope='files' with relative paths."""
        explicit = ["app.py", "subdir/tool.py"]
        files = _resolve_scope(mock_project, "files", files=explicit)
        assert files == explicit

    def test_scope_files_absolute(self, mock_project):
        """Verify scope='files' with absolute paths is normalized to relative."""
        abs_path = str((mock_project / "app.py").resolve())
        files = _resolve_scope(mock_project, "files", files=[abs_path])
        assert files == ["app.py"]

    def test_scope_files_outside_project_skipped(self, mock_project, tmp_path):
        """Verify files outside project root are skipped."""
        outside = tmp_path / "outside.py"
        outside.write_text("outside")

        files = _resolve_scope(mock_project, "files", files=[str(outside.resolve()), "app.py"])
        assert files == ["app.py"]

    def test_scope_files_empty_raises(self, mock_project):
        """Verify scope='files' with no files raises ValueError."""
        with pytest.raises(ValueError, match="requires non-empty 'files' parameter"):
            _resolve_scope(mock_project, "files", files=[])

        with pytest.raises(ValueError, match="requires non-empty 'files' parameter"):
            _resolve_scope(mock_project, "files", files=None)

    def test_scope_files_limit(self, mock_project):
        """Verify scope='files' respects the limit."""
        explicit = [f"f{i}.py" for i in range(100)]
        files = _resolve_scope(mock_project, "files", files=explicit)
        assert len(files) == 50
