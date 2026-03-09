"""Shared wiki transforms — used by both Wiki and Pages publishers.

Each transform is a pure function operating on markdown text. The
``apply_common_transforms`` orchestrator runs them in the correct order.
The ``link_fn`` parameter lets each publisher format links for its target
(Wiki-Case for GitHub Wiki, relative paths for Pages).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .manifest import WikiPage

# Type for link rewriting: (page_name) -> formatted_link
LinkFn = Callable[[str], str]

_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n?", re.DOTALL)
_LEADING_H1_RE = re.compile(r"^#\s+[^\n]+\n?")
_METRIC_RE = re.compile(r"\{\{(\w+)\}\}")
# Match wiki-style page links: [text](Page-Name) or [text](Page-Name#anchor)
# Supports both Wiki-Case (Getting-Started) and kebab-case (getting-started).
# Excludes URLs (contains ://) and file paths (contains . before last segment).
_WIKI_LINK_RE = re.compile(r"\[([^\]]+)\]\(([A-Za-z][A-Za-z0-9_-]+)(#[A-Za-z0-9_-]+)?\)")


def strip_frontmatter(text: str) -> str:
    """Remove YAML ``---`` frontmatter blocks from the start of text."""
    return _FRONTMATTER_RE.sub("", text).lstrip("\n")


def strip_leading_h1(text: str) -> str:
    """Remove a duplicate leading H1 heading.

    The materializer and source files may both contribute an H1 — this
    removes the first one to prevent duplication.
    """
    return _LEADING_H1_RE.sub("", text, count=1).lstrip("\n")


def interpolate_metrics(text: str, metrics: dict[str, str]) -> str:
    """Replace ``{{key}}`` placeholders with values from metrics dict."""
    if not metrics:
        return text

    def _replace(m: re.Match[str]) -> str:
        key: str = m.group(1)
        return metrics.get(key, m.group(0))

    return _METRIC_RE.sub(_replace, text)


def build_breadcrumb(
    page: WikiPage,
    read_time_min: int = 0,
    link_fn: LinkFn | None = None,
    manifest: Any | None = None,
) -> str:
    """Generate a navigation breadcrumb header line.

    Format: ``Rail / Chapter N | Prerequisites: ... | N min read``
    """
    parts: list[str] = []

    # Rail label — use manifest rail_display_name if available
    if page.rail:
        if manifest is not None and hasattr(manifest, "rail_display_name"):
            rail_label = manifest.rail_display_name(page.rail)
        else:
            rail_label = _rail_display_name(page.rail)
    else:
        rail_label = page.pillar.title() if page.pillar else "Wiki"
    parts.append(f"**{rail_label}**")

    # Chapter
    if page.chapter:
        parts.append(f"Chapter {page.chapter}")

    # Prerequisites
    if page.prerequisites:
        prereq_links: list[str] = []
        for prereq in page.prerequisites:
            if link_fn:
                prereq_links.append(f"[{prereq}]({link_fn(prereq)})")
            else:
                prereq_links.append(f"[{prereq}]({prereq})")
        parts.append(f"Prerequisites: {', '.join(prereq_links)}")

    # Read time
    if read_time_min:
        parts.append(f"{read_time_min} min read")

    # Home link (skip if we're already on Home)
    if page.name.lower() != "home":
        home = f"[Home]({link_fn('Home')})" if link_fn else "[Home](Home)"
        parts.append(home)

    return " | ".join(parts)


def rewrite_links(
    text: str,
    link_fn: LinkFn,
    known_pages: set[str] | None = None,
) -> str:
    """Rewrite wiki-style page links using the provided link formatter.

    Matches ``[text](page-name)`` patterns. If ``known_pages`` is provided,
    only rewrites links whose target is a known page name (case-insensitive).
    Otherwise rewrites all matches that look like page links (no ``/``, ``:``,
    or ``.`` in the target).
    """
    known_lower = {p.lower() for p in known_pages} if known_pages else None

    def _rewrite(m: re.Match[str]) -> str:
        display: str = m.group(1)
        target: str = m.group(2)
        anchor: str = m.group(3) or ""  # e.g. "#zero-state"
        # Skip anything that looks like a URL scheme or file path
        # (the regex already excludes / and . but check surrounding context)
        if known_lower is not None and target.lower() not in known_lower:
            return m.group(0)
        return f"[{display}]({link_fn(target)}{anchor})"

    return _WIKI_LINK_RE.sub(_rewrite, text)


def apply_common_transforms(
    text: str,
    page: WikiPage,
    metrics: dict[str, str] | None = None,
    link_fn: LinkFn | None = None,
    read_time_min: int = 0,
    include_breadcrumb: bool = True,
    manifest: Any | None = None,
) -> str:
    """Apply all transforms in order.

    1. Strip frontmatter
    2. Strip leading H1
    3. Interpolate metrics
    4. Prepend breadcrumb
    5. Rewrite links
    """
    result = strip_frontmatter(text)
    result = strip_leading_h1(result)
    if metrics:
        result = interpolate_metrics(result, metrics)
    if include_breadcrumb:
        breadcrumb = build_breadcrumb(page, read_time_min, link_fn, manifest=manifest)
        result = breadcrumb + "\n\n" + result
    if link_fn:
        known_pages: set[str] | None = None
        if manifest is not None and hasattr(manifest, "pages"):
            known_pages = {p.name for p in manifest.pages}
            known_pages.add("Home")
            known_pages.add("home")
        result = rewrite_links(result, link_fn, known_pages=known_pages)
    return result


# ─── Link format helpers ─────────────────────────────────────────────────


def wiki_link_fn(page_name: str) -> str:
    """Format link for GitHub Wiki tab (Wiki-Case)."""
    return page_name


def pages_link_fn(page_name: str, is_root: bool = False) -> str:
    """Format link for GitHub Pages (relative clean URLs)."""
    slug = page_name.lower().replace(" ", "-")
    if is_root:
        return f"./{slug}/"
    return f"../{slug}/"


def make_pages_link_fn(is_root: bool = False) -> LinkFn:
    """Create a pages link function with the correct prefix."""

    def _fn(page_name: str) -> str:
        return pages_link_fn(page_name, is_root)

    return _fn


# ─── Internal helpers ────────────────────────────────────────────────────

_RAIL_NAMES = {
    "getting_value": "Getting Value Fast",
    "how_it_works": "How It Works",
    "why_designed": "Why It Is Designed This Way",
    "reference": "Reference",
}


def _rail_display_name(rail: str) -> str:
    """Human-readable rail name."""
    return _RAIL_NAMES.get(rail, rail.replace("_", " ").title())
