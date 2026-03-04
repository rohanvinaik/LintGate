"""Wiki manifest schema and YAML loader.

The manifest declares page structure — which source sections compose each
wiki page, pillar grouping, tag-based cross-link inference, and generation
metadata.  Stored at ``.lintgate/wiki_manifest.yaml``.
"""

from __future__ import annotations

import hashlib
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


@dataclass
class WikiManifest:
    """Parsed wiki manifest with cross-link inference."""

    version: int
    pages: list[WikiPage]
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
        ]
        for src in page.sources:
            parts.append(f"{src.file}:{src.kind}:{src.heading}:{src.level}")
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _parse_source_ref(data: dict[str, Any]) -> SourceRef:
    """Parse a single source reference from manifest YAML."""
    return SourceRef(
        file=data.get("file", ""),
        kind=data.get("kind", "section"),
        heading=data.get("heading", ""),
        level=data.get("level", 2),
        heading_path=data.get("heading_path", ""),
        section_id=data.get("section_id", ""),
        required=data.get("required", True),
    )


def _parse_page(data: dict[str, Any]) -> WikiPage:
    """Parse a single page entry from manifest YAML."""
    sources = [_parse_source_ref(s) for s in data.get("sources", [])]
    return WikiPage(
        name=data.get("name", ""),
        title=data.get("title", ""),
        pillar=data.get("pillar", ""),
        order=data.get("order", 0),
        sources=sources,
        tags=data.get("tags", []),
        relations=data.get("relations", []),
        generator=data.get("generator"),
        theory_scope=data.get("theory_scope", False),
    )


def load_manifest(project_root: str) -> WikiManifest | None:
    """Load wiki manifest from ``.lintgate/wiki_manifest.yaml``.

    Returns ``None`` if the file doesn't exist or ``yaml`` is unavailable.
    """
    try:
        import yaml
    except ImportError:
        return None

    manifest_path = os.path.join(project_root, ".lintgate", "wiki_manifest.yaml")
    if not os.path.isfile(manifest_path):
        return None

    try:
        with open(manifest_path) as f:
            data = yaml.safe_load(f)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    pages = [_parse_page(p) for p in data.get("pages", [])]
    return WikiManifest(
        version=data.get("version", 1),
        pages=pages,
    )
