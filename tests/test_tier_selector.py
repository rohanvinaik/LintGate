"""Targeted tests for tier selector project-scan helpers."""

from __future__ import annotations

import os

from lintgate.tier_selector import _collect_project_python_files


def test_collect_project_python_files_excludes_backup_like_dirs(tmp_path) -> None:
    backup_dir = tmp_path / "archive_backup"
    backup_dir.mkdir()
    (backup_dir / "old.py").write_text("x = 1\n")

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "live.py").write_text("y = 2\n")

    files = _collect_project_python_files(str(tmp_path), limit=50)
    basenames = {os.path.basename(f) for f in files}
    assert "live.py" in basenames
    assert "old.py" not in basenames
