"""Tests for lintgate.linters.version_checker."""

from __future__ import annotations

import os

from lintgate.linters.version_checker import (
    VersionChecker,
    _source_file_path,
)

# ── _source_file_path ────────────────────────────────────────────────


class TestSourceFilePath:
    def test_resolves_claude_config(self):
        sources = [".claude/lintgate.yaml:ruff"]
        result = _source_file_path(sources, "/project")
        expected = os.path.normpath("/project/.claude/lintgate.yaml")
        assert result == expected

    def test_resolves_txt_file(self):
        sources = ["requirements.txt:mypy>=1.0"]
        result = _source_file_path(sources, "/project")
        expected = os.path.normpath("/project/requirements.txt")
        assert result == expected

    def test_resolves_toml_file(self):
        sources = ["pyproject.toml:black"]
        result = _source_file_path(sources, "/project")
        expected = os.path.normpath("/project/pyproject.toml")
        assert result == expected

    def test_returns_none_for_non_list(self):
        assert _source_file_path("not a list", "/project") is None
        assert _source_file_path(42, "/project") is None

    def test_returns_none_for_empty_list(self):
        assert _source_file_path([], "/project") is None

    def test_returns_none_for_unrecognized_paths(self):
        sources = ["some_random_source"]
        result = _source_file_path(sources, "/project")
        assert result is None

    def test_non_string_entries_skipped(self):
        sources = [42, ".claude/config.yaml:tool"]
        result = _source_file_path(sources, "/project")
        expected = os.path.normpath("/project/.claude/config.yaml")
        assert result == expected

    def test_first_matching_source_wins(self):
        sources = ["requirements.txt:ruff", ".claude/lintgate.yaml:ruff"]
        result = _source_file_path(sources, "/project")
        # First match is requirements.txt
        expected = os.path.normpath("/project/requirements.txt")
        assert result == expected


# ── VersionChecker class ─────────────────────────────────────────────


class TestVersionCheckerClass:
    def test_attributes(self):
        checker = VersionChecker()
        assert checker.name == "version_checker"
        assert checker.tier == 1
        assert checker.timeout_ms == 3000
        assert checker.required_tool is None
