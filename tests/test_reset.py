"""Tests for lintgate.reset — scoped state reset and managed section handling.

Covers:
- _find_managed_sections() programmatic section detection
- _strip_managed_sections() section removal with offset-safe back-to-front deletion
- enumerate_project_state() state file discovery
- reset_*() functions with dry_run semantics
- Edge cases: nested sections, unpaired markers, blank line consumption
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from lintgate.reset import (
    ResetReport,
    _find_managed_sections,
    _lintgate_home,
    _project_hash,
    _strip_managed_sections,
    enumerate_project_state,
    reset_compass_only,
    reset_global,
    reset_project,
    reset_session_only,
)


# ── _find_managed_sections ───────────────────────────────────────────


class TestFindManagedSections:
    """Tests for the programmatic managed section detector."""

    def test_no_sections(self):
        assert _find_managed_sections("Hello world\nNo markers here.\n") == []

    def test_empty_string(self):
        assert _find_managed_sections("") == []

    def test_single_section(self):
        text = (
            "Before\n\n"
            "<!-- LINTGATE:BEGIN SIGNALS v1 -->\n"
            "Signal content here\n"
            "<!-- LINTGATE:END SIGNALS -->\n\n"
            "After\n"
        )
        sections = _find_managed_sections(text)
        assert len(sections) == 1
        start, end, section_id = sections[0]
        assert section_id == "SIGNALS"
        # Should capture the section including surrounding blank lines
        assert "LINTGATE:BEGIN" in text[start:end]
        assert "LINTGATE:END" in text[start:end]

    def test_multiple_sections(self):
        text = (
            "Header\n\n"
            "<!-- LINTGATE:BEGIN SIGNALS v1 -->\nContent A\n<!-- LINTGATE:END SIGNALS -->\n\n"
            "Middle\n\n"
            "<!-- LINTGATE:BEGIN CONSTRAINTS v2 -->\nContent B\n<!-- LINTGATE:END CONSTRAINTS -->\n\n"
            "Footer\n"
        )
        sections = _find_managed_sections(text)
        assert len(sections) == 2
        assert sections[0][2] == "SIGNALS"
        assert sections[1][2] == "CONSTRAINTS"

    def test_unpaired_begin_is_skipped(self):
        text = (
            "<!-- LINTGATE:BEGIN ORPHAN v1 -->\n"
            "No end marker\n"
        )
        sections = _find_managed_sections(text)
        assert len(sections) == 0

    def test_blank_line_consumption_leading(self):
        text = "Before\n\n\n<!-- LINTGATE:BEGIN X v1 -->\nContent\n<!-- LINTGATE:END X -->\nAfter"
        sections = _find_managed_sections(text)
        assert len(sections) == 1
        start, _, _ = sections[0]
        # Should consume the blank lines before BEGIN (start moves back past \n chars)
        assert start < text.index("<!-- LINTGATE:BEGIN")
        # The consumed region should include the blank lines
        assert text[start:].startswith("\n")

    def test_blank_line_consumption_trailing(self):
        text = "Before\n<!-- LINTGATE:BEGIN X v1 -->\nContent\n<!-- LINTGATE:END X -->\n\n\nAfter"
        sections = _find_managed_sections(text)
        assert len(sections) == 1
        _, end, _ = sections[0]
        # Should consume trailing newlines
        assert text[end:] == "After"

    def test_version_number_variations(self):
        for version in ["v1", "v2", "v99"]:
            text = f"<!-- LINTGATE:BEGIN FOO {version} -->\nX\n<!-- LINTGATE:END FOO -->\n"
            sections = _find_managed_sections(text)
            assert len(sections) == 1
            assert sections[0][2] == "FOO"

    def test_section_at_start_of_text(self):
        text = "<!-- LINTGATE:BEGIN A v1 -->\nContent\n<!-- LINTGATE:END A -->\nAfter"
        sections = _find_managed_sections(text)
        assert len(sections) == 1
        assert sections[0][0] == 0  # Starts at beginning

    def test_section_at_end_of_text(self):
        text = "Before\n<!-- LINTGATE:BEGIN A v1 -->\nContent\n<!-- LINTGATE:END A -->"
        sections = _find_managed_sections(text)
        assert len(sections) == 1
        _, end, _ = sections[0]
        assert end == len(text)

    def test_multiline_content(self):
        text = (
            "<!-- LINTGATE:BEGIN LONG v1 -->\n"
            "Line 1\n"
            "Line 2\n"
            "Line 3\n"
            "Line 4\n"
            "<!-- LINTGATE:END LONG -->\n"
        )
        sections = _find_managed_sections(text)
        assert len(sections) == 1
        start, end, _ = sections[0]
        extracted = text[start:end]
        assert "Line 1" in extracted
        assert "Line 4" in extracted

    def test_mismatched_section_ids_are_skipped(self):
        """BEGIN FOO ... END BAR should not match."""
        text = "<!-- LINTGATE:BEGIN FOO v1 -->\nContent\n<!-- LINTGATE:END BAR -->\n"
        sections = _find_managed_sections(text)
        assert len(sections) == 0


# ── _strip_managed_sections ──────────────────────────────────────────


class TestStripManagedSections:
    """Tests for managed section removal from files."""

    def test_strip_removes_section(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text(
            "Header\n\n"
            "<!-- LINTGATE:BEGIN X v1 -->\nContent\n<!-- LINTGATE:END X -->\n\n"
            "Footer\n"
        )
        report = ResetReport()
        _strip_managed_sections(f, report, dry_run=False)

        result = f.read_text()
        assert "LINTGATE:BEGIN" not in result
        assert "LINTGATE:END" not in result
        assert "Header" in result
        assert "Footer" in result
        assert len(report.deleted) == 1
        assert report.deleted[0]["type"] == "managed_section"
        assert report.deleted[0]["section_id"] == "X"

    def test_strip_dry_run_does_not_modify(self, tmp_path):
        content = (
            "Header\n\n"
            "<!-- LINTGATE:BEGIN Y v1 -->\nContent\n<!-- LINTGATE:END Y -->\n\n"
            "Footer\n"
        )
        f = tmp_path / "test.md"
        f.write_text(content)
        report = ResetReport()
        _strip_managed_sections(f, report, dry_run=True)

        assert f.read_text() == content  # Unchanged
        assert len(report.deleted) == 1  # But still reported

    def test_strip_multiple_sections(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text(
            "A\n\n"
            "<!-- LINTGATE:BEGIN S1 v1 -->\nC1\n<!-- LINTGATE:END S1 -->\n\n"
            "B\n\n"
            "<!-- LINTGATE:BEGIN S2 v1 -->\nC2\n<!-- LINTGATE:END S2 -->\n\n"
            "C\n"
        )
        report = ResetReport()
        _strip_managed_sections(f, report, dry_run=False)

        result = f.read_text()
        assert "LINTGATE" not in result
        assert "A" in result
        assert "B" in result
        assert "C" in result
        assert len(report.deleted) == 2

    def test_strip_nonexistent_file(self, tmp_path):
        f = tmp_path / "missing.md"
        report = ResetReport()
        _strip_managed_sections(f, report, dry_run=False)
        assert len(report.deleted) == 0
        assert len(report.errors) == 0

    def test_strip_file_without_sections(self, tmp_path):
        f = tmp_path / "clean.md"
        f.write_text("No managed sections here\n")
        report = ResetReport()
        _strip_managed_sections(f, report, dry_run=False)
        assert f.read_text() == "No managed sections here\n"
        assert len(report.deleted) == 0


# ── ResetReport ──────────────────────────────────────────────────────


class TestResetReport:
    def test_to_dict(self):
        report = ResetReport()
        report.deleted.append({"path": "/tmp/x", "type": "session"})
        report.preserved.append({"path": "/tmp/y", "reason": "protected"})
        report.errors.append("Failed to delete Z")
        d = report.to_dict()
        assert len(d["deleted"]) == 1
        assert len(d["preserved"]) == 1
        assert len(d["errors"]) == 1


# ── Helper functions ─────────────────────────────────────────────────


class TestHelpers:
    def test_project_hash_is_stable(self):
        h1 = _project_hash("/some/path")
        h2 = _project_hash("/some/path")
        assert h1 == h2
        assert len(h1) == 16

    def test_project_hash_differs_for_different_paths(self):
        h1 = _project_hash("/path/a")
        h2 = _project_hash("/path/b")
        assert h1 != h2

    def test_lintgate_home_default(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove LINTGATE_HOME if set
            os.environ.pop("LINTGATE_HOME", None)
            home = _lintgate_home()
            assert home == Path.home() / ".lintgate"

    def test_lintgate_home_custom(self):
        with patch.dict(os.environ, {"LINTGATE_HOME": "/custom/path"}):
            home = _lintgate_home()
            assert str(home).endswith("custom/path")


# ── enumerate_project_state ──────────────────────────────────────────


class TestEnumerateProjectState:
    def test_empty_project(self, tmp_path):
        entries = enumerate_project_state(str(tmp_path))
        assert entries == []

    def test_with_config_file(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "lintgate.yaml").write_text("enabled: true\n")
        entries = enumerate_project_state(str(tmp_path))
        config_entries = [e for e in entries if e["type"] == "config"]
        assert len(config_entries) == 1
        assert config_entries[0]["deletable"] is False

    def test_with_protected_files(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "CLAUDE.md").write_text("# Claude\n")
        (tmp_path / ".claude" / "AGENTS.md").write_text("# Agents\n")
        entries = enumerate_project_state(str(tmp_path))
        protected = [e for e in entries if e["type"] == "protected"]
        assert len(protected) == 2

    def test_with_managed_sections_in_claude_md(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "CLAUDE.md").write_text(
            "# Claude\n\n"
            "<!-- LINTGATE:BEGIN SIGNALS v1 -->\nSignals\n<!-- LINTGATE:END SIGNALS -->\n\n"
            "Footer\n"
        )
        entries = enumerate_project_state(str(tmp_path))
        section_entries = [e for e in entries if e["type"] == "managed_section"]
        assert len(section_entries) == 1
        assert section_entries[0]["section_id"] == "SIGNALS"

    def test_with_compass(self, tmp_path):
        from lintgate.compass import COMPASS_PATH

        compass_path = tmp_path / COMPASS_PATH
        compass_path.parent.mkdir(parents=True, exist_ok=True)
        compass_path.write_text("axes: {}\n")
        entries = enumerate_project_state(str(tmp_path))
        compass_entries = [e for e in entries if e["type"] == "compass"]
        assert len(compass_entries) == 1


# ── reset_* functions ────────────────────────────────────────────────


class TestResetFunctions:
    def test_reset_compass_only_dry_run(self, tmp_path):
        from lintgate.compass import COMPASS_PATH

        compass = tmp_path / COMPASS_PATH
        compass.parent.mkdir(parents=True, exist_ok=True)
        compass.write_text("test\n")

        report = reset_compass_only(str(tmp_path), dry_run=True)
        assert compass.exists()  # Not deleted in dry run
        assert len(report.deleted) == 1

    def test_reset_compass_only_real(self, tmp_path):
        from lintgate.compass import COMPASS_PATH

        compass = tmp_path / COMPASS_PATH
        compass.parent.mkdir(parents=True, exist_ok=True)
        compass.write_text("test\n")

        report = reset_compass_only(str(tmp_path), dry_run=False)
        assert not compass.exists()
        assert len(report.deleted) == 1

    def test_reset_session_only_no_session(self, tmp_path):
        report = reset_session_only(str(tmp_path), dry_run=True)
        assert len(report.deleted) == 0

    @patch("lintgate.reset._SESSION_DIR")
    def test_reset_session_only(self, mock_session_dir, tmp_path):
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        mock_session_dir.__truediv__ = lambda self, key: session_dir / key

        phash = _project_hash(os.path.realpath(str(tmp_path)))
        session_file = session_dir / f"{phash}.json"
        session_file.write_text("{}")

        report = reset_session_only(str(tmp_path), dry_run=True)
        assert len(report.deleted) == 1
        assert session_file.exists()  # Dry run

    def test_reset_project_dry_run(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "CLAUDE.md").write_text(
            "# Claude\n\n"
            "<!-- LINTGATE:BEGIN X v1 -->\nStuff\n<!-- LINTGATE:END X -->\n\n"
        )
        report = reset_project(str(tmp_path), dry_run=True)
        # Should report the managed section
        section_entries = [d for d in report.deleted if d.get("type") == "managed_section"]
        assert len(section_entries) == 1
        # File still has section in dry run
        assert "LINTGATE:BEGIN" in (tmp_path / ".claude" / "CLAUDE.md").read_text()

    def test_reset_project_real(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "CLAUDE.md").write_text(
            "# Claude\n\n"
            "<!-- LINTGATE:BEGIN X v1 -->\nStuff\n<!-- LINTGATE:END X -->\n\n"
            "Footer\n"
        )
        report = reset_project(str(tmp_path), dry_run=False)
        content = (tmp_path / ".claude" / "CLAUDE.md").read_text()
        assert "LINTGATE:BEGIN" not in content
        assert "Footer" in content

    @patch("lintgate.reset._SESSION_DIR")
    @patch("lintgate.reset._HABIT_STATE_DIR")
    def test_reset_global_dry_run(self, mock_habit_dir, mock_session_dir, tmp_path):
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        (session_dir / "abc123.json").write_text("{}")

        habit_dir = tmp_path / "habit"
        habit_dir.mkdir()
        (habit_dir / "def456.json").write_text("{}")

        # Patch the Path objects to act like our tmp dirs
        type(mock_session_dir).is_dir = lambda self: session_dir.is_dir()
        mock_session_dir.iterdir = lambda: session_dir.iterdir()
        type(mock_habit_dir).is_dir = lambda self: habit_dir.is_dir()
        mock_habit_dir.iterdir = lambda: habit_dir.iterdir()

        report = reset_global(dry_run=True)
        assert len(report.deleted) >= 2  # Session + habit
        assert (session_dir / "abc123.json").exists()  # Not deleted in dry run
