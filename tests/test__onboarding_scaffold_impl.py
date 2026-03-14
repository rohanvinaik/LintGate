"""Tests for mcp_tools/_onboarding_scaffold_impl.py helper functions."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from mcp_tools._onboarding_scaffold_impl import (
    _collect_python_files,
    _find_critical_paths,
    _has_subprocess_usage,
    _readme_has_quality_badges,
    _reset_project_state,
    _scaffold_config_yaml,
)

# ---------------------------------------------------------------------------
# _collect_python_files
# ---------------------------------------------------------------------------


class TestCollectPythonFiles:
    def test_collects_py_files(self, tmp_path):
        (tmp_path / "main.py").write_text("# main")
        (tmp_path / "util.py").write_text("# util")
        files = _collect_python_files(str(tmp_path))
        assert len(files) == 2
        assert all(f.endswith(".py") for f in files)

    def test_excludes_venv_and_pycache(self, tmp_path):
        (tmp_path / "app.py").write_text("# app")
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "mod.py").write_text("# venv")
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "mod.cpython-311.py").write_text("# cache")
        files = _collect_python_files(str(tmp_path))
        assert len(files) == 1
        assert files[0].endswith("app.py")

    def test_returns_sorted_list(self, tmp_path):
        (tmp_path / "z.py").write_text("")
        (tmp_path / "a.py").write_text("")
        (tmp_path / "m.py").write_text("")
        files = _collect_python_files(str(tmp_path))
        basenames = [os.path.basename(f) for f in files]
        assert basenames == ["a.py", "m.py", "z.py"]


# ---------------------------------------------------------------------------
# _find_critical_paths
# ---------------------------------------------------------------------------


class TestFindCriticalPaths:
    def test_finds_files_over_300_lines(self, tmp_path):
        big_file = tmp_path / "big.py"
        big_file.write_text("\n".join(f"line {i}" for i in range(301)))
        small_file = tmp_path / "small.py"
        small_file.write_text("# short\n")
        result = _find_critical_paths([str(big_file), str(small_file)], str(tmp_path))
        assert len(result) == 1
        assert "big.py" in result[0]

    def test_returns_relative_paths(self, tmp_path):
        big = tmp_path / "src" / "core.py"
        big.parent.mkdir()
        big.write_text("\n" * 301)
        result = _find_critical_paths([str(big)], str(tmp_path))
        assert result[0] == os.path.join("src", "core.py")

    def test_limits_to_10(self, tmp_path):
        files = []
        for i in range(15):
            f = tmp_path / f"mod{i:02d}.py"
            f.write_text("\n" * 400)
            files.append(str(f))
        result = _find_critical_paths(files, str(tmp_path))
        assert len(result) == 10


# ---------------------------------------------------------------------------
# _has_subprocess_usage
# ---------------------------------------------------------------------------


class TestHasSubprocessUsage:
    def test_detects_subprocess_import(self, tmp_path):
        f = tmp_path / "runner.py"
        f.write_text("import subprocess\nsubprocess.run(['ls'])\n")
        assert _has_subprocess_usage([str(f)]) is True

    def test_returns_false_when_absent(self, tmp_path):
        f = tmp_path / "pure.py"
        f.write_text("import os\nprint('hello')\n")
        assert _has_subprocess_usage([str(f)]) is False

    def test_checks_at_most_50_files(self, tmp_path):
        files = []
        for i in range(60):
            f = tmp_path / f"mod{i}.py"
            f.write_text("# clean code\n")
            files.append(str(f))
        # The 51st file has subprocess but should not be checked
        late = tmp_path / "mod50.py"
        late.write_text("import subprocess\n")
        # Already in list, replace
        files[50] = str(late)
        assert _has_subprocess_usage(files) is False


# ---------------------------------------------------------------------------
# _scaffold_config_yaml
# ---------------------------------------------------------------------------


class TestScaffoldConfigYaml:
    def test_includes_controlplane_block(self, tmp_path):
        yaml = _scaffold_config_yaml(str(tmp_path), {})
        assert "controlplane:" in yaml
        assert "enabled: true" in yaml

    def test_includes_critical_paths_for_large_files(self, tmp_path):
        big = tmp_path / "engine.py"
        big.write_text("\n" * 400)
        yaml = _scaffold_config_yaml(str(tmp_path), {})
        assert "pipeline_critical_paths:" in yaml
        assert "engine.py" in yaml

    def test_includes_severity_overrides_for_subprocess(self, tmp_path):
        f = tmp_path / "cmd.py"
        f.write_text("import subprocess\nsubprocess.run(['echo'])\n")
        yaml = _scaffold_config_yaml(str(tmp_path), {})
        assert "severity_overrides:" in yaml
        assert "B603" in yaml


# ---------------------------------------------------------------------------
# _readme_has_quality_badges
# ---------------------------------------------------------------------------


class TestReadmeHasQualityBadges:
    def test_returns_false_when_no_readme(self, tmp_path):
        assert _readme_has_quality_badges(str(tmp_path)) is False

    def test_returns_false_when_badges_missing(self, tmp_path):
        (tmp_path / "README.md").write_text("# My Project\nNo badges here.\n")
        assert _readme_has_quality_badges(str(tmp_path)) is False

    def test_returns_true_when_all_fingerprints_present(self, tmp_path):
        from mcp_tools.quality_helpers import (
            _BADGE_BLOCK_END,
            _BADGE_BLOCK_START,
            _REQUIRED_BADGE_FINGERPRINTS,
        )

        badge_content = "\n".join(
            [_BADGE_BLOCK_START]
            + [f"![badge](https://example.com/{fp})" for fp in _REQUIRED_BADGE_FINGERPRINTS]
            + [_BADGE_BLOCK_END]
        )
        (tmp_path / "README.md").write_text(f"# Proj\n{badge_content}\n")
        assert _readme_has_quality_badges(str(tmp_path)) is True


# ---------------------------------------------------------------------------
# _reset_project_state
# ---------------------------------------------------------------------------


class TestResetProjectState:
    def test_clears_state_dirs(self, tmp_path):
        lintgate_dir = tmp_path / ".claude" / "lintgate"
        for subdir in ("state", "runs", "sessions"):
            d = lintgate_dir / subdir
            d.mkdir(parents=True)
            (d / "data.json").write_text("{}")

        mock_ot = MagicMock()
        mock_ot.Path = Path
        mock_ot.Path.home = MagicMock(return_value=tmp_path / "fakehome")

        with patch("mcp_tools._onboarding_scaffold_impl._ot", return_value=mock_ot):
            actions = _reset_project_state(str(tmp_path))

        assert len(actions) == 3
        assert all(a["action"] == "reset_dir" for a in actions)
        # Dirs should be removed
        for subdir in ("state", "runs", "sessions"):
            assert not (lintgate_dir / subdir).exists()

    def test_preserves_config_and_issue_memory(self, tmp_path):
        lintgate_dir = tmp_path / ".claude" / "lintgate"
        lintgate_dir.mkdir(parents=True)
        config = tmp_path / ".claude" / "lintgate.yaml"
        config.write_text("enabled: true\n")
        issue_mem = lintgate_dir / "issue_memory.json"
        issue_mem.write_text("{}")

        mock_ot = MagicMock()
        mock_ot.Path = Path
        mock_ot.Path.home = MagicMock(return_value=tmp_path / "fakehome")

        with patch("mcp_tools._onboarding_scaffold_impl._ot", return_value=mock_ot):
            _reset_project_state(str(tmp_path))

        assert config.exists()
        assert issue_mem.exists()

    def test_clears_habit_state_for_project(self, tmp_path):
        import hashlib

        project_hash = hashlib.sha256(
            os.path.abspath(str(tmp_path)).encode()
        ).hexdigest()[:12]

        mock_ot = MagicMock()
        mock_ot.Path = Path
        habit_base = tmp_path / "fakehome" / ".lintgate" / "habit_state"
        habit_base.mkdir(parents=True)
        matching_file = habit_base / f"habit_{project_hash}_session.json"
        matching_file.write_text("{}")
        other_file = habit_base / "habit_otherhash_session.json"
        other_file.write_text("{}")
        mock_ot.Path.home = MagicMock(return_value=tmp_path / "fakehome")

        with patch("mcp_tools._onboarding_scaffold_impl._ot", return_value=mock_ot):
            actions = _reset_project_state(str(tmp_path))

        file_resets = [a for a in actions if a["action"] == "reset_file"]
        assert len(file_resets) == 1
        assert project_hash in file_resets[0]["path"]
        assert not matching_file.exists()
        assert other_file.exists()
