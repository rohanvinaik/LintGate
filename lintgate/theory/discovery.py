"""Theory discovery — file enumeration, document parsing, and docstring extraction.

Extracted from theory_extractor.py. Contains the logic for finding markdown
files in a project, parsing them into headed sections, and extracting
theory-relevant content from Python module-level docstrings.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# ─── Constants ───────────────────────────────────────────────────────────

# Max files to scan (prevent runaway on huge repos)
_MAX_MD_FILES = 100

# Extra directories to skip when scanning for .md files (on top of canonical).
# .claude is skipped EXCEPT for .claude/rules/ which is scanned explicitly
# in _discover_md_files(). "downloaded" and "retrospectives" are theory-specific.
_EXTRA_MD_SKIP_DIRS = frozenset({"downloaded", ".claude", "retrospectives"})


# ─── Section dataclass ───────────────────────────────────────────────────


class _Section:
    """A headed section from a markdown document."""

    __slots__ = (
        "heading",
        "heading_level",
        "body",
        "source_file",
        "rel_path",
        "line_no",
    )

    def __init__(
        self,
        heading: str,
        heading_level: int,
        body: str,
        source_file: str,
        rel_path: str,
        line_no: int,
    ):
        self.heading = heading
        self.heading_level = heading_level
        self.body = body
        self.source_file = source_file
        self.rel_path = rel_path
        self.line_no = line_no


# ─── Document discovery ──────────────────────────────────────────────────


def _scan_priority_dir(directory: Path, found: list[str]) -> bool:
    """Scan a priority directory for .md files. Returns True if cap reached."""
    if not directory.is_dir():
        return False
    found_set = set(found)
    for fname in sorted(os.listdir(directory)):
        if not fname.lower().endswith(".md"):
            continue
        fpath = str(directory / fname)
        if fpath not in found_set:
            found.append(fpath)
            if len(found) >= _MAX_MD_FILES:
                return True
    return False


def _discover_md_files(project_root: str) -> list[str]:
    """Find all markdown files in the project, respecting skip dirs.

    Also explicitly scans .claude/rules/ which is a first-class location
    for project theory and constraint documentation.
    """
    root = Path(project_root)
    found: list[str] = []

    # Priority dirs: high-value theory content that should survive the global cap
    if _scan_priority_dir(root / ".claude" / "rules", found):
        return found
    if _scan_priority_dir(root / ".lintgate" / "wiki", found):
        return found

    from ..discovery import CANONICAL_EXCLUDE_DIRS, should_skip_dir

    skip_all = CANONICAL_EXCLUDE_DIRS | _EXTRA_MD_SKIP_DIRS
    found_set = set(found)

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted([d for d in dirnames if d not in skip_all and not should_skip_dir(d)])
        for fname in sorted(filenames):
            if not fname.lower().endswith(".md"):
                continue
            full_path = os.path.join(dirpath, fname)
            if full_path in found_set:
                continue
            found.append(full_path)
            found_set.add(full_path)
            if len(found) >= _MAX_MD_FILES:
                return found

    return found


# ─── Document parsing ────────────────────────────────────────────────────


def _has_frontmatter_opt_out(md_path: str) -> bool:
    """Return True when markdown frontmatter declares `theory_scope: false`."""
    try:
        with open(md_path, errors="replace") as f:
            head_lines: list[str] = []
            for _ in range(10):
                line = f.readline()
                if line == "":
                    break
                head_lines.append(line.rstrip("\n"))
    except OSError:
        return False

    if not head_lines:
        return False
    if head_lines[0].lstrip("\ufeff").strip() != "---":
        return False

    fm_end = None
    for i, line in enumerate(head_lines[1:], 1):
        if line.strip() == "---":
            fm_end = i
            break
    if fm_end is None:
        return False

    frontmatter = "\n".join(head_lines[1:fm_end])
    return bool(
        re.search(
            r"^\s*theory_scope\s*:\s*false\s*(?:#.*)?$",
            frontmatter,
            re.IGNORECASE | re.MULTILINE,
        )
    )


def _parse_document(md_path: str, project_root: str) -> list[_Section]:
    """Parse a markdown file into headed sections."""
    if _has_frontmatter_opt_out(md_path):
        return []

    try:
        text = Path(md_path).read_text(errors="replace")
    except OSError:
        return []

    rel_path = os.path.relpath(md_path, project_root)
    lines = text.splitlines()
    sections: list[_Section] = []
    current_heading = os.path.basename(md_path).replace(".md", "")
    current_level = 0
    current_body_lines: list[str] = []
    current_line_no = 1

    for i, line in enumerate(lines, 1):
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match:
            # Flush previous section
            if current_body_lines:
                body = "\n".join(current_body_lines).strip()
                if body:
                    sections.append(
                        _Section(
                            heading=current_heading,
                            heading_level=current_level,
                            body=body,
                            source_file=md_path,
                            rel_path=rel_path,
                            line_no=current_line_no,
                        )
                    )
            current_heading = heading_match.group(2).strip()
            current_level = len(heading_match.group(1))
            current_body_lines = []
            current_line_no = i
        else:
            current_body_lines.append(line)

    # Flush last section
    if current_body_lines:
        body = "\n".join(current_body_lines).strip()
        if body:
            sections.append(
                _Section(
                    heading=current_heading,
                    heading_level=current_level,
                    body=body,
                    source_file=md_path,
                    rel_path=rel_path,
                    line_no=current_line_no,
                )
            )

    return sections


# ─── Working-tree theory extraction (#182) ────────────────────────────────


def extract_docstring_claims(
    project_root: str,
    python_files: list[str],
) -> list[_Section]:
    """Extract theory-relevant sections from Python module-level docstrings.

    Scans module-level docstrings (the first expression statement in each file)
    for design intent, architectural rationale, and theory claims. Returns
    _Section objects that can flow through the standard classification pipeline.

    Args:
        project_root: Absolute path to the project root.
        python_files: List of Python file paths (absolute or relative to project_root).

    Returns:
        List of _Section objects extracted from module-level docstrings.
    """
    import ast

    sections: list[_Section] = []
    root = Path(project_root)

    for fpath in python_files:
        abs_path = fpath if os.path.isabs(fpath) else str(root / fpath)
        if not os.path.isfile(abs_path):
            continue
        if not abs_path.endswith(".py"):
            continue

        try:
            source = Path(abs_path).read_text(errors="replace")
        except OSError:
            continue

        # Extract module-level docstring via AST
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        docstring = ast.get_docstring(tree)
        if not docstring or len(docstring.strip()) < 30:
            continue

        rel_path = os.path.relpath(abs_path, project_root)
        module_name = Path(rel_path).stem

        # Create a section from the docstring
        sections.append(
            _Section(
                heading=f"Module: {module_name}",
                heading_level=1,
                body=docstring,
                source_file=abs_path,
                rel_path=rel_path,
                line_no=1,
            )
        )

    return sections
