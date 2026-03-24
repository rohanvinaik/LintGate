"""Tests for lintgate.subprocess_utils.resolve_python."""

from __future__ import annotations

import sys
from pathlib import Path

from lintgate.subprocess_utils import resolve_python


class TestResolvePython:
    def test_returns_venv_python_when_present(self, tmp_path: Path):
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        python = venv_bin / "python"
        python.touch()
        python.chmod(0o755)

        result = resolve_python(str(tmp_path))
        assert result == str(python)

    def test_prefers_dot_venv_over_venv(self, tmp_path: Path):
        for name in (".venv", "venv"):
            b = tmp_path / name / "bin"
            b.mkdir(parents=True)
            p = b / "python"
            p.touch()
            p.chmod(0o755)

        result = resolve_python(str(tmp_path))
        assert ".venv" in result

    def test_falls_back_to_venv(self, tmp_path: Path):
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        python = venv_bin / "python"
        python.touch()
        python.chmod(0o755)

        result = resolve_python(str(tmp_path))
        assert result == str(python)

    def test_falls_back_to_env(self, tmp_path: Path):
        env_bin = tmp_path / "env" / "bin"
        env_bin.mkdir(parents=True)
        python = env_bin / "python"
        python.touch()
        python.chmod(0o755)

        result = resolve_python(str(tmp_path))
        assert result == str(python)

    def test_falls_back_to_sys_executable_when_no_venv(self, tmp_path: Path):
        result = resolve_python(str(tmp_path))
        assert result == sys.executable

    def test_falls_back_to_sys_executable_when_none(self):
        result = resolve_python(None)
        assert result == sys.executable

    def test_falls_back_to_sys_executable_when_no_arg(self):
        result = resolve_python()
        assert result == sys.executable

    def test_skips_directory_without_python_file(self, tmp_path: Path):
        # .venv/bin exists but no python file inside
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        # venv/bin/python does exist
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        python = venv_bin / "python"
        python.touch()
        python.chmod(0o755)

        result = resolve_python(str(tmp_path))
        assert result == str(python)
