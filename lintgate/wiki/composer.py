"""Page composition — assembles wiki pages from extracted sections.

Produces complete markdown pages with frontmatter, navigation, managed-section
markers, source content, inferred cross-links, and attribution footers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .extractor import extract_section, extract_whole_file

if TYPE_CHECKING:
    from .manifest import WikiManifest, WikiPage

_MANAGED_BEGIN = "<!-- LINTGATE_WIKI:BEGIN {section_id} -->"
_MANAGED_END = "<!-- LINTGATE_WIKI:END {section_id} -->"


@dataclass
class ComposedPage:
    """A fully composed wiki page ready to write."""

    name: str
    title: str
    content: str
    pillar: str
    theory_scope: bool = False
    source_files: list[str] = field(default_factory=list)


def compose_all_pages(
    manifest: WikiManifest,
    project_root: str,
    theory: dict[str, Any] | None = None,
    compass: dict[str, Any] | None = None,
) -> list[ComposedPage]:
    """Compose all pages declared in the manifest.

    Returns a list of ComposedPage objects including an auto-generated Home page.
    """
    pages: list[ComposedPage] = []

    for wiki_page in manifest.pages:
        composed = _compose_page(wiki_page, manifest, project_root, theory, compass)
        if composed is not None:
            pages.append(composed)

    # Generate Home page
    home = _compose_home(manifest, pages)
    pages.insert(0, home)

    return pages


def _compose_page(
    page: WikiPage,
    manifest: WikiManifest,
    project_root: str,
    theory: dict[str, Any] | None,
    compass: dict[str, Any] | None,
) -> ComposedPage | None:
    """Compose a single wiki page."""
    parts: list[str] = []
    source_files: list[str] = []

    # Frontmatter
    parts.append(_frontmatter(page))

    # Navigation breadcrumb
    parts.append(_breadcrumb(page))
    parts.append("")

    # Title
    parts.append(f"# {page.title}")
    parts.append("")

    # Handle generated pages
    if page.generator:
        gen_content = _generate_content(page.generator, theory, compass)
        if gen_content:
            parts.append(_MANAGED_BEGIN.format(section_id=page.generator))
            parts.append(gen_content)
            parts.append(_MANAGED_END.format(section_id=page.generator))
            parts.append("")
    else:
        # Extract and compose source sections
        sections_content = _extract_sources(page, project_root)
        for i, (src_file, heading, content) in enumerate(sections_content):
            if src_file not in source_files:
                source_files.append(src_file)
            section_id = f"{page.name}_{i}"
            parts.append(_MANAGED_BEGIN.format(section_id=section_id))
            if heading:
                parts.append(f"## {heading}")
                parts.append("")
            parts.append(content)
            parts.append(_MANAGED_END.format(section_id=section_id))
            if i < len(sections_content) - 1:
                parts.append("")
                parts.append("---")
            parts.append("")

    # Inferred cross-links
    cross_links = manifest.infer_cross_links(page)
    if cross_links:
        parts.append("## See Also")
        parts.append("")
        for link_name in cross_links:
            parts.append(f"- [{link_name}]({link_name})")
        parts.append("")

    # Source attribution
    if source_files:
        parts.append("---")
        rel_paths = [os.path.relpath(f, project_root) for f in source_files]
        parts.append(f"*Sources: {', '.join(f'`{p}`' for p in rel_paths)}*")
        parts.append("")

    return ComposedPage(
        name=page.name,
        title=page.title,
        content="\n".join(parts),
        pillar=page.pillar,
        theory_scope=page.theory_scope,
        source_files=source_files,
    )


def _frontmatter(page: WikiPage) -> str:
    """Generate YAML frontmatter."""
    lines = [
        "---",
        f"theory_scope: {'true' if page.theory_scope else 'false'}",
        f"pillar: {page.pillar}",
        "generated_by: lintgate_wiki",
        "---",
    ]
    return "\n".join(lines)


def _breadcrumb(page: WikiPage) -> str:
    """Generate navigation breadcrumb."""
    pillar_label = page.pillar.title()
    return f"**{pillar_label}** | [Home](Home)"


def _extract_sources(
    page: WikiPage,
    project_root: str,
) -> list[tuple[str, str, str]]:
    """Extract content from all source references.

    Returns list of (abs_file_path, heading_text, content).
    """
    results: list[tuple[str, str, str]] = []
    for src in page.sources:
        abs_path = os.path.join(project_root, src.file)

        if src.kind == "file":
            sec = extract_whole_file(abs_path)
            if sec:
                results.append((abs_path, "", sec.content))
            elif src.required:
                results.append((abs_path, "", f"*Missing source: `{src.file}`*"))

        elif src.kind == "section":
            sec = extract_section(abs_path, src.heading, src.level)
            if sec:
                results.append((abs_path, sec.heading, sec.content))
            elif src.required:
                results.append(
                    (abs_path, src.heading, f"*Missing section: `{src.heading}` in `{src.file}`*")
                )

        # kind == "generated" is handled by _generate_content, not here

    return results


def _generate_content(
    generator: str,
    theory: dict[str, Any] | None,
    compass: dict[str, Any] | None,
) -> str:
    """Generate content for auto-generated pages."""
    if generator == "theory_profile":
        return _render_theory_profile(theory)
    elif generator == "compass_state":
        return _render_compass_state(compass)
    return ""


def _render_theory_profile(theory: dict[str, Any] | None) -> str:
    """Render theory profile as markdown."""
    if not theory:
        return "*No theory profile available. Run `extract_project_theory` first.*"

    parts: list[str] = []
    for facet_name, entries in theory.items():
        if not entries:
            continue
        parts.append(f"### {facet_name.replace('_', ' ').title()}")
        parts.append("")
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    heading = entry.get("heading", "")
                    source = entry.get("source", "")
                    claims = entry.get("claims", [])
                    if heading:
                        parts.append(f"**{heading}** ({source})")
                    for claim in claims[:5]:
                        if isinstance(claim, dict):
                            parts.append(f"- {claim.get('text', str(claim))}")
                        else:
                            parts.append(f"- {claim}")
                    parts.append("")
        parts.append("")
    return "\n".join(parts).strip()


def _render_compass_state(compass: dict[str, Any] | None) -> str:
    """Render compass state as markdown."""
    if not compass:
        return "*No compass state available. Run `compass_update` first.*"

    parts: list[str] = []
    axes = compass.get("axes", compass)
    for axis_name, axis_data in axes.items():
        if not isinstance(axis_data, dict):
            continue
        parts.append(f"### {axis_name.replace('_', ' ').title()}")
        parts.append("")
        depth = axis_data.get("depth", "unknown")
        parts.append(f"**Depth**: {depth}")
        toward = axis_data.get("toward", [])
        if toward:
            parts.append("")
            parts.append("**Toward**:")
            for item in toward[:5]:
                parts.append(f"- {item}")
        away = axis_data.get("away", [])
        if away:
            parts.append("")
            parts.append("**Away from**:")
            for item in away[:5]:
                parts.append(f"- {item}")
        parts.append("")
    return "\n".join(parts).strip()


def _compose_home(
    manifest: WikiManifest,
    composed_pages: list[ComposedPage],
) -> ComposedPage:
    """Generate the Home navigation page."""
    parts: list[str] = []

    # Frontmatter
    parts.append("---")
    parts.append("theory_scope: false")
    parts.append("generated_by: lintgate_wiki")
    parts.append("---")

    parts.append("# LintGate Wiki")
    parts.append("")
    parts.append("Auto-generated from source documentation via wiki manifest.")
    parts.append("")

    # Group by pillar — discover all pillars dynamically
    page_names = {p.name for p in composed_pages}
    seen_pillars: list[str] = []
    for page in manifest.pages:
        if page.pillar and page.pillar not in seen_pillars:
            seen_pillars.append(page.pillar)

    for pillar in seen_pillars:
        pillar_pages = manifest.pages_by_pillar(pillar)
        if not pillar_pages:
            continue

        parts.append(f"## {pillar.title()}")
        parts.append("")
        parts.append("| Page | Title |")
        parts.append("|------|-------|")
        for wp in pillar_pages:
            if wp.name in page_names:
                parts.append(f"| [{wp.name}]({wp.name}) | {wp.title} |")
        parts.append("")

    return ComposedPage(
        name="Home",
        title="LintGate Wiki",
        content="\n".join(parts),
        pillar="",
        theory_scope=False,
    )
