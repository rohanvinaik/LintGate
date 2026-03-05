"""Hash-based staleness tracking for generated wiki pages.

Hashes extracted section content (not whole source files) plus the manifest
entry hash and a generator version constant.  Any change in these triggers
staleness for the affected page only.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .manifest import WikiManifest

GENERATOR_VERSION = "1"

_STATE_FILE = "_wiki_state.json"


@dataclass
class PageFreshnessState:
    """Freshness state for a single wiki page."""

    page_name: str
    section_hashes: dict[str, str] = field(default_factory=dict)
    manifest_hash: str = ""
    generator_version: str = ""
    page_content_hash: str = ""
    generated_at: float = 0.0


@dataclass
class WikiFreshnessState:
    """Aggregate freshness state for all wiki pages."""

    pages: dict[str, PageFreshnessState] = field(default_factory=dict)


def content_hash(text: str) -> str:
    """Compute sha256[:16] of text content."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def build_page_freshness(
    page_name: str,
    section_contents: dict[str, str],
    manifest_hash: str,
    page_content: str,
) -> PageFreshnessState:
    """Build freshness state for a page from its current extracted content."""
    return PageFreshnessState(
        page_name=page_name,
        section_hashes={key: content_hash(val) for key, val in section_contents.items()},
        manifest_hash=manifest_hash,
        generator_version=GENERATOR_VERSION,
        page_content_hash=content_hash(page_content),
        generated_at=time.time(),
    )


def check_page_staleness(
    current: PageFreshnessState,
    stored: PageFreshnessState | None,
) -> dict[str, Any]:
    """Check whether a page is stale compared to stored state.

    Returns a dict with:
      - stale: bool
      - reasons: list of human-readable reasons
      - changed_sections: list of section keys whose hashes differ
    """
    if stored is None:
        return {"stale": True, "reasons": ["no previous state"], "changed_sections": []}

    reasons: list[str] = []
    changed: list[str] = []

    # Generator version change
    if current.generator_version != stored.generator_version:
        reasons.append(
            f"generator version changed: {stored.generator_version} → {current.generator_version}"
        )

    # Manifest hash change
    if current.manifest_hash != stored.manifest_hash:
        reasons.append("manifest entry changed")

    # Section content hashes
    all_keys = set(current.section_hashes) | set(stored.section_hashes)
    for key in sorted(all_keys):
        cur = current.section_hashes.get(key)
        old = stored.section_hashes.get(key)
        if cur != old:
            changed.append(key)
            if old is None:
                reasons.append(f"new section: {key}")
            elif cur is None:
                reasons.append(f"removed section: {key}")
            else:
                reasons.append(f"section changed: {key}")

    return {
        "stale": len(reasons) > 0,
        "reasons": reasons,
        "changed_sections": changed,
    }


def check_wiki_freshness(
    project_root: str,
) -> dict[str, Any]:
    """Check freshness of all wiki pages.

    Returns a summary dict with per-page staleness info.
    Requires the manifest and current source content to compute current hashes.
    """
    from .composer import compose_all_pages
    from .manifest import load_manifest

    manifest = load_manifest(project_root)
    if manifest is None:
        return {"error": "no manifest found", "fresh": 0, "stale": 0, "missing": 0}

    stored = load_freshness_state(project_root)
    composed = compose_all_pages(manifest, project_root)

    fresh_count = 0
    stale_count = 0
    missing_count = 0
    page_details: dict[str, Any] = {}

    for page in composed:
        # Build current freshness from composed content
        section_contents = _section_contents_for_page(page.name, manifest, project_root)
        manifest_page = next((p for p in manifest.pages if p.name == page.name), None)
        m_hash = manifest.manifest_hash_for_page(manifest_page) if manifest_page else ""

        current = build_page_freshness(page.name, section_contents, m_hash, page.content)
        stored_page = stored.pages.get(page.name)

        staleness = check_page_staleness(current, stored_page)

        if stored_page is None:
            missing_count += 1
        elif staleness["stale"]:
            stale_count += 1
        else:
            fresh_count += 1

        page_details[page.name] = staleness

    total = fresh_count + stale_count + missing_count
    return {
        "fresh": fresh_count,
        "stale": stale_count,
        "missing": missing_count,
        "total": total,
        "freshness_score": fresh_count / total if total > 0 else 0.0,
        "pages": page_details,
    }


def _section_contents_for_page(
    page_name: str,
    manifest: WikiManifest,
    project_root: str,
) -> dict[str, str]:
    """Extract section content keyed by 'file::heading' for hash computation."""
    from .extractor import extract_section, extract_whole_file

    page = next((p for p in manifest.pages if p.name == page_name), None)
    if page is None:
        return {}

    contents: dict[str, str] = {}
    for src in page.sources:
        abs_path = os.path.join(project_root, src.file)
        key = f"{src.file}::{src.heading}" if src.heading else src.file

        if src.kind == "file":
            sec = extract_whole_file(abs_path)
            if sec:
                contents[key] = sec.content
        elif src.kind == "section":
            sec = extract_section(abs_path, src.heading, src.level)
            if sec:
                contents[key] = sec.content

    return contents


def load_freshness_state(project_root: str) -> WikiFreshnessState:
    """Load freshness state from ``.lintgate/wiki/_wiki_state.json``."""
    state_path = os.path.join(project_root, ".lintgate", "wiki", _STATE_FILE)
    if not os.path.isfile(state_path):
        return WikiFreshnessState()

    try:
        with open(state_path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return WikiFreshnessState()

    pages: dict[str, PageFreshnessState] = {}
    for name, pdata in data.get("pages", {}).items():
        pages[name] = PageFreshnessState(
            page_name=name,
            section_hashes=pdata.get("section_hashes", {}),
            manifest_hash=pdata.get("manifest_hash", ""),
            generator_version=pdata.get("generator_version", ""),
            page_content_hash=pdata.get("page_content_hash", ""),
            generated_at=pdata.get("generated_at", 0.0),
        )
    return WikiFreshnessState(pages=pages)


def save_freshness_state(project_root: str, state: WikiFreshnessState) -> None:
    """Save freshness state to ``.lintgate/wiki/_wiki_state.json``."""
    state_path = os.path.join(project_root, ".lintgate", "wiki", _STATE_FILE)
    os.makedirs(os.path.dirname(state_path), exist_ok=True)

    data: dict[str, Any] = {"pages": {}}
    for name, pstate in state.pages.items():
        data["pages"][name] = {
            "section_hashes": pstate.section_hashes,
            "manifest_hash": pstate.manifest_hash,
            "generator_version": pstate.generator_version,
            "page_content_hash": pstate.page_content_hash,
            "generated_at": pstate.generated_at,
        }

    with open(state_path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
