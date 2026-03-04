"""Tests for wiki section extraction."""

from __future__ import annotations

from lintgate.wiki.extractor import (
    extract_all_sections,
    extract_section,
    extract_whole_file,
)

SAMPLE_MD = """\
# Top Level

Intro text.

## Overview

Overview content here.
More overview.

## Design Philosophy

Design content here.

### Subsection

Sub content.

## Pipeline

Pipeline content.
"""


def test_extract_section_by_heading(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text(SAMPLE_MD)

    sec = extract_section(str(md), "Overview", heading_level=2)
    assert sec is not None
    assert sec.heading == "Overview"
    assert sec.heading_level == 2
    assert "Overview content here." in sec.content
    assert "More overview." in sec.content


def test_extract_section_case_insensitive(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text(SAMPLE_MD)

    sec = extract_section(str(md), "overview", heading_level=2)
    assert sec is not None
    assert sec.heading == "Overview"


def test_extract_section_exact_match_required(tmp_path):
    """Substring no longer matches — must be exact."""
    md = tmp_path / "doc.md"
    md.write_text(SAMPLE_MD)

    sec = extract_section(str(md), "Design", heading_level=2)
    assert sec is None  # "Design" != "Design Philosophy"

    sec = extract_section(str(md), "Design Philosophy", heading_level=2)
    assert sec is not None
    assert sec.heading == "Design Philosophy"


def test_extract_section_strips_anchor(tmp_path):
    """Anchors like {#some-id} are stripped before comparison."""
    md = tmp_path / "doc.md"
    md.write_text("## My Heading {#my-anchor}\n\nContent.\n")

    sec = extract_section(str(md), "My Heading", heading_level=2)
    assert sec is not None
    assert "Content." in sec.content


def test_extract_section_missing(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text(SAMPLE_MD)

    sec = extract_section(str(md), "Nonexistent", heading_level=2)
    assert sec is None


def test_extract_section_wrong_level(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text(SAMPLE_MD)

    # "Subsection" is ### (level 3), not level 2
    sec = extract_section(str(md), "Subsection", heading_level=2)
    assert sec is None


def test_extract_section_level_boundary(tmp_path):
    """Section content ends at next same-or-higher level heading."""
    md = tmp_path / "doc.md"
    md.write_text(SAMPLE_MD)

    sec = extract_section(str(md), "Design Philosophy", heading_level=2)
    assert sec is not None
    # Should include subsection content (### is lower level than ##)
    assert "Sub content." in sec.content
    # Should NOT include Pipeline content (next ## heading)
    assert "Pipeline content." not in sec.content


def test_extract_all_sections(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text(SAMPLE_MD)

    sections = extract_all_sections(str(md), heading_level=2)
    assert len(sections) == 3
    assert sections[0].heading == "Overview"
    assert sections[1].heading == "Design Philosophy"
    assert sections[2].heading == "Pipeline"


def test_extract_all_sections_level_3(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text(SAMPLE_MD)

    sections = extract_all_sections(str(md), heading_level=3)
    assert len(sections) == 1
    assert sections[0].heading == "Subsection"


def test_extract_whole_file(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text("Some content\nMore content\n")

    sec = extract_whole_file(str(md))
    assert sec is not None
    assert sec.heading == "doc"
    assert sec.heading_level == 0
    assert "Some content" in sec.content


def test_extract_whole_file_strips_frontmatter(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text("---\ntheory_scope: false\n---\n\nActual content.\n")

    sec = extract_whole_file(str(md))
    assert sec is not None
    assert "theory_scope" not in sec.content
    assert "Actual content." in sec.content


def test_extract_whole_file_missing(tmp_path):
    sec = extract_whole_file(str(tmp_path / "missing.md"))
    assert sec is None


def test_extract_section_missing_file(tmp_path):
    sec = extract_section(str(tmp_path / "missing.md"), "Overview")
    assert sec is None


def test_extract_all_empty_file(tmp_path):
    md = tmp_path / "empty.md"
    md.write_text("")

    sections = extract_all_sections(str(md), heading_level=2)
    assert sections == []
