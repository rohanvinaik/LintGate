"""Wiki manifest schema and YAML loader.

The manifest declares page structure — which source sections compose each
wiki page, pillar/rail grouping, tag-based cross-link inference, chapter
grammar, prerequisite graph, and generation metadata.

Stored at ``.lintgate/wiki_manifest.yaml`` (or ``wiki.yaml`` at project root).
"""

from __future__ import annotations

import hashlib
import math
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SourceRef:
    """Reference to a source section or file for wiki composition."""

    file: str
    kind: str = "section"  # "section" | "file" | "generated"
    heading: str = ""
    level: int = 2
    heading_path: str = ""
    section_id: str = ""
    required: bool = True


@dataclass
class WikiPage:
    """A single wiki page declaration."""

    name: str
    title: str
    pillar: str
    order: int = 0
    sources: list[SourceRef] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)
    generator: str | None = None
    theory_scope: bool = False
    # Reading rail: "getting_value" | "how_it_works" | "why_designed" | "reference"
    rail: str = ""
    # Chapter number within the rail (for ordering and breadcrumb)
    chapter: int = 0
    # Prerequisite page names — reader should read these first
    prerequisites: list[str] = field(default_factory=list)


@dataclass
class WikiManifest:
    """Parsed wiki manifest with cross-link inference."""

    version: int
    pages: list[WikiPage]
    # Rail ID → display name (from top-level ``rails:`` section)
    rail_names: dict[str, str] = field(default_factory=dict)
    _tag_index: dict[str, list[str]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._build_tag_index()

    def _build_tag_index(self) -> None:
        """Build tag → page-name index for cross-link inference."""
        idx: dict[str, list[str]] = {}
        for page in self.pages:
            for tag in page.tags:
                idx.setdefault(tag, []).append(page.name)
        self._tag_index = idx

    @property
    def all_pages(self) -> list[WikiPage]:
        return list(self.pages)

    def pages_by_pillar(self, pillar: str) -> list[WikiPage]:
        return sorted(
            [p for p in self.pages if p.pillar == pillar],
            key=lambda p: p.order,
        )

    def pages_by_rail(self, rail: str) -> list[WikiPage]:
        """Return pages in a reading rail, sorted by chapter number."""
        return sorted(
            [p for p in self.pages if p.rail == rail],
            key=lambda p: p.chapter,
        )

    @property
    def rails(self) -> list[str]:
        """Return all distinct rails in declaration order."""
        seen: list[str] = []
        for page in self.pages:
            if page.rail and page.rail not in seen:
                seen.append(page.rail)
        return seen

    def rail_display_name(self, rail: str) -> str:
        """Human-readable rail name from manifest or fallback."""
        if rail in self.rail_names:
            return self.rail_names[rail]
        return _rail_display_name_fallback(rail)

    def prev_next_in_rail(self, page: WikiPage) -> tuple[WikiPage | None, WikiPage | None]:
        """Return (prev, next) pages in the same rail."""
        if not page.rail:
            return None, None
        rail_pages = self.pages_by_rail(page.rail)
        for i, p in enumerate(rail_pages):
            if p.name == page.name:
                prev_p = rail_pages[i - 1] if i > 0 else None
                next_p = rail_pages[i + 1] if i < len(rail_pages) - 1 else None
                return prev_p, next_p
        return None, None

    def estimate_read_time(self, page: WikiPage, project_root: str) -> int:
        """Estimate reading time in minutes based on source word count."""
        words = 0
        for src in page.sources:
            abs_path = os.path.join(project_root, src.file)
            try:
                with open(abs_path, errors="replace") as f:
                    words += len(f.read().split())
            except OSError:
                pass
        return max(1, math.ceil(words / 200))

    def infer_cross_links(self, page: WikiPage) -> list[str]:
        """Infer cross-links for a page from tags + pillar adjacency + explicit relations."""
        links: dict[str, None] = {}  # ordered set

        # 1. Explicit relations first
        for rel in page.relations:
            if rel != page.name:
                links[rel] = None

        # 2. Shared tags
        for tag in page.tags:
            for linked_name in self._tag_index.get(tag, []):
                if linked_name != page.name:
                    links[linked_name] = None

        # 3. Pillar adjacency (prev/next in order)
        pillar_pages = self.pages_by_pillar(page.pillar)
        for i, p in enumerate(pillar_pages):
            if p.name == page.name:
                if i > 0:
                    links[pillar_pages[i - 1].name] = None
                if i < len(pillar_pages) - 1:
                    links[pillar_pages[i + 1].name] = None
                break

        return list(links)

    def manifest_hash_for_page(self, page: WikiPage) -> str:
        """Compute a stable hash of a page's manifest entry."""
        parts = [
            page.name,
            page.title,
            page.pillar,
            str(page.order),
            str(page.theory_scope),
            page.generator or "",
            ",".join(page.tags),
            ",".join(page.relations),
            page.rail,
            str(page.chapter),
            ",".join(page.prerequisites),
        ]
        for src in page.sources:
            parts.append(f"{src.file}:{src.kind}:{src.heading}:{src.level}")
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _parse_source_ref(data: dict[str, Any]) -> SourceRef:
    """Parse a single source reference from manifest YAML.

    Accepts key aliases across formats:
    - File path: ``file``, ``path``, ``source``
    - Whole-file marker: ``sections: all``, ``headings: all``
    - Explicit kind: ``kind: file|section|generated``
    """
    # Accept "file", "path", or "source" as the file path key
    file_path = data.get("file", "") or data.get("path", "") or data.get("source", "")

    # "sections: all" or "headings: all" means whole-file
    kind = data.get("kind", "")
    if not kind:
        sections = data.get("sections", "") or data.get("headings", "")
        if sections == "all":
            kind = "file"
        elif sections:
            kind = "section"
        else:
            kind = "section"

    return SourceRef(
        file=file_path,
        kind=kind,
        heading=data.get("heading", ""),
        level=data.get("level", 2),
        heading_path=data.get("heading_path", ""),
        section_id=data.get("section_id", ""),
        required=data.get("required", True),
    )


def _parse_page(data: dict[str, Any]) -> WikiPage:
    """Parse a single page entry from manifest YAML.

    Accepts key aliases across formats:
    - Name: ``name``, ``id``
    - Pillar: ``pillar``, ``audience``
    - Source list: ``sources``, ``sections`` (when a list of dicts)
    """
    # Accept "sources" or "sections" as the source list key
    raw_sources = data.get("sources", []) or data.get("sections", [])
    # If "sections" is a string like "all", it's a whole-file marker, not a source list
    if isinstance(raw_sources, str):
        raw_sources = [{"file": raw_sources}] if raw_sources != "all" else []
    sources = [_parse_source_ref(s) for s in raw_sources]

    # ModelAtlas uses "id", LintGate uses "name"
    name = data.get("name", "") or data.get("id", "")

    # ModelAtlas uses "audience" where LintGate uses "pillar"
    pillar = data.get("pillar", "") or data.get("audience", "")

    return WikiPage(
        name=name,
        title=data.get("title", ""),
        pillar=pillar,
        order=data.get("order", 0),
        sources=sources,
        tags=data.get("tags", []),
        relations=data.get("relations", []),
        generator=data.get("generator"),
        theory_scope=data.get("theory_scope", False),
        rail=data.get("rail", ""),
        chapter=data.get("chapter", 0),
        prerequisites=data.get("prerequisites", []),
    )


def load_manifest(project_root: str) -> WikiManifest | None:
    """Load wiki manifest from ``wiki.yaml`` (project root) or ``.lintgate/wiki_manifest.yaml``.

    Checks ``wiki.yaml`` first (ModelAtlas convention), falls back to
    ``.lintgate/wiki_manifest.yaml``.  Returns ``None`` if neither exists
    or ``yaml`` is unavailable.
    """
    try:
        import yaml
    except ImportError:
        return None

    # Try wiki.yaml at project root first, then .lintgate/wiki_manifest.yaml
    candidates = [
        os.path.join(project_root, "wiki.yaml"),
        os.path.join(project_root, ".lintgate", "wiki_manifest.yaml"),
    ]
    manifest_path = None
    for candidate in candidates:
        if os.path.isfile(candidate):
            manifest_path = candidate
            break
    if manifest_path is None:
        return None

    try:
        with open(manifest_path) as f:
            data = yaml.safe_load(f)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    # Parse top-level rails section (ModelAtlas format)
    rail_names: dict[str, str] = {}
    raw_rails = data.get("rails", {})
    if isinstance(raw_rails, dict):
        for rail_id, rail_data in raw_rails.items():
            if isinstance(rail_data, dict):
                rail_names[rail_id] = str(rail_data.get("name", rail_id))
            elif isinstance(rail_data, str):
                rail_names[rail_id] = rail_data

    pages = [_parse_page(p) for p in data.get("pages", [])]
    return WikiManifest(
        version=data.get("version", 1),
        pages=pages,
        rail_names=rail_names,
    )


def _rail_display_name_fallback(rail: str) -> str:
    """Fallback: convert rail ID to display name via heuristics."""
    known = {
        "getting_value": "Getting Value Fast",
        "getting-value-fast": "Getting Value Fast",
        "how_it_works": "How It Works",
        "how-it-works": "How It Works",
        "why_designed": "Why It Is Designed This Way",
        "why-designed-this-way": "Why It Is Designed This Way",
        "reference": "Reference",
    }
    if rail in known:
        return known[rail]
    return rail.replace("-", " ").replace("_", " ").title()


def load_metrics(project_root: str) -> dict[str, str]:
    """Load volatile metrics from ``_metrics.yaml`` for template interpolation.

    Checks ``docs/wiki/_metrics.yaml`` then ``.lintgate/_metrics.yaml``.
    Returns empty dict if not found.
    """
    try:
        import yaml
    except ImportError:
        return {}

    candidates = [
        os.path.join(project_root, "docs", "wiki", "_metrics.yaml"),
        os.path.join(project_root, ".lintgate", "_metrics.yaml"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            try:
                with open(candidate) as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items()}
            except Exception:
                pass
    return {}
