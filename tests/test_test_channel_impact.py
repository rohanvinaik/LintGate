"""Tests for lintgate.channels._test_channel_impact."""

from __future__ import annotations

from pathlib import Path

from lintgate.channels._test_channel_impact import (
    _build_search_dirs,
    _find_joined_test,
    find_impacted_tests,
)


# ── _build_search_dirs ───────────────────────────────────────────────


class TestBuildSearchDirs:
    def test_basic_dirs(self, tmp_path):
        src = tmp_path / "lintgate" / "foo.py"
        src.parent.mkdir(parents=True)
        src.touch()
        dirs = _build_search_dirs(tmp_path, src)
        assert tmp_path / "tests" in dirs
        assert tmp_path / "test" in dirs
        assert src.parent in dirs

    def test_package_subdir_added(self, tmp_path):
        src = tmp_path / "lintgate" / "sub" / "bar.py"
        src.parent.mkdir(parents=True)
        src.touch()
        dirs = _build_search_dirs(tmp_path, src)
        # lintgate is stripped, so tests/sub should be added
        assert tmp_path / "tests" / "sub" in dirs

    def test_src_outside_root_no_crash(self, tmp_path):
        outside = Path("/tmp/other_project/foo.py")
        dirs = _build_search_dirs(tmp_path, outside)
        # Should not crash even if src_path is not relative to root
        assert tmp_path / "tests" in dirs


# ── _find_joined_test ────────────────────────────────────────────────


class TestFindJoinedTest:
    def test_finds_joined_test(self, tmp_path):
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        joined_file = test_dir / "test_lintgate_foo.py"
        joined_file.touch()

        src = tmp_path / "lintgate" / "foo.py"
        src.parent.mkdir(parents=True)
        src.touch()

        result = _find_joined_test(tmp_path, src)
        assert result == str(joined_file)

    def test_returns_none_when_not_found(self, tmp_path):
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        src = tmp_path / "lintgate" / "foo.py"
        src.parent.mkdir(parents=True)
        src.touch()

        result = _find_joined_test(tmp_path, src)
        assert result is None


# ── find_impacted_tests ──────────────────────────────────────────────


class TestFindImpactedTests:
    def test_finds_test_for_source(self, tmp_path):
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        test_file = test_dir / "test_foo.py"
        test_file.touch()
        src = tmp_path / "foo.py"
        src.touch()

        result = find_impacted_tests([str(src)], str(tmp_path))
        assert str(test_file) in result

    def test_test_file_passed_directly(self, tmp_path):
        test_file = tmp_path / "test_bar.py"
        test_file.touch()

        result = find_impacted_tests([str(test_file)], str(tmp_path))
        assert str(test_file) in result

    def test_non_py_files_skipped(self, tmp_path):
        txt = tmp_path / "readme.txt"
        txt.touch()
        result = find_impacted_tests([str(txt)], str(tmp_path))
        assert result == []

    def test_deduplication(self, tmp_path):
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        test_file = test_dir / "test_foo.py"
        test_file.touch()
        src = tmp_path / "foo.py"
        src.touch()

        # Pass the same source file twice
        result = find_impacted_tests([str(src), str(src)], str(tmp_path))
        assert result.count(str(test_file)) == 1
