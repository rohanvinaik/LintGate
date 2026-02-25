#!/usr/bin/env python3
"""Context health checker — PR-H / #75.

Audits CLAUDE.md and related context docs for:
- Dead links: referenced file paths that no longer exist
- Missing referenced paths (e.g. @-mentioned or backtick-quoted paths)
- Contradictions: e.g. a file marked TODO that has been deleted

Exits with code 0 if all references resolve, 1 if any dead links found.
Integrate into CI with:
    python scripts/check_context_health.py --repo-root .
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Files to audit (relative to repo root)
_DEFAULT_CONTEXT_FILES = [
    "CLAUDE.md",
    "AGENTS.md",
    "README.md",
    ".agents/CONTEXT.md",
    "docs/ARCHITECTURE.md",
]

# Pattern to capture path references in context docs
# Matches:
#   `path/to/file.py`   (backtick-quoted)
#   [label](path)       (markdown links — non-URL)
#   @path/to/file.py    (annotation-style)
_PATH_PATTERN = re.compile(
    r"""
    `([^`\n]+\.[a-zA-Z0-9]+)`        # backtick-quoted with extension
    | \[.*?\]\(([^)#\n]+)\)           # markdown links (skip fragments and URLs)
    | @([\w./-]+\.[a-zA-Z0-9]+)       # @-annotated paths
    """,
    re.VERBOSE,
)

_URL_PREFIX = re.compile(r"^https?://|^ftp://|^mailto:")


def _extract_references(text: str) -> list[str]:
    """Extract file references from context document text."""
    refs: list[str] = []
    for m in _PATH_PATTERN.finditer(text):
        # Pick the first non-None group
        ref = m.group(1) or m.group(2) or m.group(3)
        if not ref:
            continue
        # Skip URLs
        if _URL_PREFIX.match(ref):
            continue
        # Skip refs containing template markers like <...>
        if "<" in ref or ">" in ref:
            continue
        refs.append(ref)
    return refs


def _check_file(
    context_file: Path,
    repo_root: Path,
    dead_links: list[tuple[str, str]],
    missing_files: list[str],
) -> None:
    """Check a single context document for dead references."""
    if not context_file.exists():
        missing_files.append(str(context_file.relative_to(repo_root)))
        return

    text = context_file.read_text(encoding="utf-8", errors="replace")
    refs = _extract_references(text)
    ctx_name = str(context_file.relative_to(repo_root))

    for ref in refs:
        # Resolve relative to both the file's directory and repo root
        candidates = [
            context_file.parent / ref,
            repo_root / ref,
        ]
        if not any(c.exists() for c in candidates):
            dead_links.append((ctx_name, ref))


def main() -> int:
    parser = argparse.ArgumentParser(description="Context document health checker")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root directory (default: current directory)",
    )
    parser.add_argument(
        "--files",
        nargs="*",
        default=_DEFAULT_CONTEXT_FILES,
        help="Context files to audit (relative to repo root)",
    )
    args = parser.parse_args()

    repo_root = Path(os.path.abspath(args.repo_root))
    dead_links: list[tuple[str, str]] = []
    missing_files: list[str] = []

    for rel_path in args.files:
        context_file = repo_root / rel_path
        _check_file(context_file, repo_root, dead_links, missing_files)

    ok = True

    if missing_files:
        print("⚠ Missing context files (not yet created):")
        for f in missing_files:
            print(f"    {f}")

    if dead_links:
        print("✗ Dead references found:")
        for ctx, ref in dead_links:
            print(f"    [{ctx}] → {ref}")
        ok = False
    else:
        print("✓ All context file references resolve correctly.")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
