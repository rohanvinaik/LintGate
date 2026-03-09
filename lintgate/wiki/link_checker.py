"""Wiki link integrity checker.

Validates that all internal page links in composed wiki pages resolve to
actual pages in the manifest. Used both by the pages publisher (post-gen
HTML check) and as a standalone lint-shaped check for controlplane.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from .manifest import load_manifest

# Matches [text](page-name) or [text](page-name#anchor) — Wiki-Case and kebab-case
_WIKI_LINK_RE = re.compile(r"\[([^\]]+)\]\(([A-Za-z][A-Za-z0-9_-]+)(#[A-Za-z0-9_-]+)?\)")


@dataclass
class LinkError:
    """A broken or problematic link."""

    source_page: str
    target: str
    kind: str  # "broken" | "orphan" | "missing_config"
    message: str


@dataclass
class LinkCheckResult:
    """Result of a full link integrity check."""

    errors: list[LinkError] = field(default_factory=list)
    pages_checked: int = 0
    links_checked: int = 0

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "pages_checked": self.pages_checked,
            "links_checked": self.links_checked,
            "error_count": len(self.errors),
            "errors": [
                {
                    "source": e.source_page,
                    "target": e.target,
                    "kind": e.kind,
                    "message": e.message,
                }
                for e in self.errors
            ],
        }


def check_wiki_links(project_root: str) -> LinkCheckResult:
    """Check link integrity across all materialized wiki pages.

    Validates:
    1. All ``[text](Page-Name)`` links point to pages that exist in the manifest
    2. All markdown files in the wiki dir have a manifest entry (orphan detection)
    3. All manifest pages have a corresponding markdown file (missing detection)
    """
    result = LinkCheckResult()

    manifest = load_manifest(project_root)
    if manifest is None:
        result.errors.append(
            LinkError(
                source_page="",
                target="",
                kind="missing_config",
                message="No wiki manifest found",
            )
        )
        return result

    manifest_names = {p.name for p in manifest.pages}
    manifest_names.add("Home")  # Home is always valid
    # Case-insensitive lookup for link validation
    manifest_names_lower = {n.lower() for n in manifest_names}

    # Check materialized wiki pages
    wiki_dir = os.path.join(project_root, ".lintgate", "wiki")
    if not os.path.isdir(wiki_dir):
        return result

    found_files: set[str] = set()
    for fname in sorted(os.listdir(wiki_dir)):
        if not fname.endswith(".md"):
            continue
        page_name = fname[:-3]
        found_files.add(page_name)
        result.pages_checked += 1

        fpath = os.path.join(wiki_dir, fname)
        try:
            with open(fpath, errors="replace") as f:
                content = f.read()
        except OSError:
            continue

        for match in _WIKI_LINK_RE.finditer(content):
            target = match.group(2)
            result.links_checked += 1
            if target.lower() not in manifest_names_lower:
                result.errors.append(
                    LinkError(
                        source_page=page_name,
                        target=target,
                        kind="broken",
                        message=f"Link to '{target}' but no such page in manifest",
                    )
                )

    # Orphan detection: files not in manifest (case-insensitive)
    for fname in found_files:
        if fname.lower() in ("home",):
            continue
        if fname.lower() not in manifest_names_lower:
            result.errors.append(
                LinkError(
                    source_page=fname,
                    target="",
                    kind="orphan",
                    message=f"Wiki file '{fname}.md' has no manifest entry",
                )
            )

    # Missing detection: manifest pages without files (case-insensitive)
    found_lower = {f.lower() for f in found_files}
    for name in manifest_names:
        if name.lower() == "home":
            continue
        if name.lower() not in found_lower:
            result.errors.append(
                LinkError(
                    source_page="",
                    target=name,
                    kind="missing_config",
                    message=f"Manifest page '{name}' has no materialized file",
                )
            )

    return result


def check_config_completeness(project_root: str) -> list[dict[str, str]]:
    """Check that every .md in docs/wiki/ has a manifest entry.

    Returns list of dicts with 'file' and 'message' for unregistered files.
    """
    manifest = load_manifest(project_root)
    if manifest is None:
        return []

    # Collect source files referenced by manifest
    manifest_files: set[str] = set()
    for page in manifest.pages:
        for src in page.sources:
            manifest_files.add(src.file)

    # Scan docs/wiki/ for .md files
    wiki_source_dir = os.path.join(project_root, "docs", "wiki")
    if not os.path.isdir(wiki_source_dir):
        return []

    issues: list[dict[str, str]] = []
    for fname in sorted(os.listdir(wiki_source_dir)):
        if not fname.endswith(".md"):
            continue
        if fname.startswith("_"):
            continue  # Skip _Sidebar.md, _Footer.md, _template.html
        rel_path = os.path.join("docs", "wiki", fname)
        if rel_path not in manifest_files:
            issues.append(
                {
                    "file": rel_path,
                    "message": f"'{fname}' in docs/wiki/ is not referenced by any manifest page",
                }
            )

    return issues
