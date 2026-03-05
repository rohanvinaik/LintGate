"""Section extraction from markdown files.

Reuses the heading-detection pattern from theory_extractor._parse_document()
but exposes a public API for targeted section extraction by heading name,
level, or whole-file ingestion.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_ANCHOR_RE = re.compile(r"\s*\{#[^}]*\}\s*$")


def _strip_anchor(heading: str) -> str:
    """Strip trailing ``{#anchor-id}`` from a heading."""
    return _ANCHOR_RE.sub("", heading).strip()


@dataclass
class ExtractedSection:
    """A section extracted from a markdown file."""

    file_path: str
    heading: str
    heading_level: int
    content: str
    start_line: int
    end_line: int


def extract_section(
    file_path: str,
    heading_pattern: str,
    heading_level: int = 2,
) -> ExtractedSection | None:
    """Extract a single section matching *heading_pattern* at *heading_level*.

    Matching is exact (case-insensitive, anchor-stripped).  Markdown anchors
    like ``{#some-id}`` at the end of headings are stripped before comparison.
    Returns the first match, or ``None``.
    """
    sections = extract_all_sections(file_path, heading_level)
    pat = _strip_anchor(heading_pattern).lower()
    for sec in sections:
        if _strip_anchor(sec.heading).lower() == pat:
            return sec
    return None


def extract_whole_file(file_path: str) -> ExtractedSection | None:
    """Return the entire file content as a single ExtractedSection."""
    try:
        with open(file_path, errors="replace") as f:
            text = f.read()
    except OSError:
        return None

    lines = text.splitlines()
    # Strip YAML frontmatter if present
    start = 0
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                start = i + 1
                break

    content = "\n".join(lines[start:]).strip()
    if not content:
        return None

    return ExtractedSection(
        file_path=file_path,
        heading=os.path.basename(file_path).replace(".md", ""),
        heading_level=0,
        content=content,
        start_line=start + 1,
        end_line=len(lines),
    )


def extract_all_sections(
    file_path: str,
    heading_level: int = 2,
) -> list[ExtractedSection]:
    """Extract all sections at exactly *heading_level* from *file_path*.

    Each section runs from its heading to the next heading at the same or
    higher level (lower number), or end-of-file.
    """
    try:
        with open(file_path, errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []

    # Find all headings at or above the requested level
    headings: list[tuple[int, int, str]] = []  # (line_idx, level, text)
    for idx, line in enumerate(lines):
        m = _HEADING_RE.match(line.rstrip("\n"))
        if m:
            level = len(m.group(1))
            headings.append((idx, level, _strip_anchor(m.group(2).strip())))

    # Filter to sections at exactly the target level, bounded by same-or-higher
    sections: list[ExtractedSection] = []
    for i, (start_idx, level, heading_text) in enumerate(headings):
        if level != heading_level:
            continue

        # Find end: next heading at same or higher level
        end_idx = len(lines)
        for j in range(i + 1, len(headings)):
            if headings[j][1] <= heading_level:
                end_idx = headings[j][0]
                break

        body_lines = lines[start_idx + 1 : end_idx]
        content = "".join(body_lines).strip()
        if content:
            sections.append(
                ExtractedSection(
                    file_path=file_path,
                    heading=heading_text,
                    heading_level=level,
                    content=content,
                    start_line=start_idx + 1,  # 1-indexed
                    end_line=end_idx,  # exclusive, 0-indexed → last content line
                )
            )

    return sections
