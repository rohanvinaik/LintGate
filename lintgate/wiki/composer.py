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

    If the manifest includes a page named "home" (case-insensitive), it is
    used as the Home page.  Otherwise an auto-generated Home is prepended.
    """
    pages: list[ComposedPage] = []
    has_home = False

    for wiki_page in manifest.pages:
        composed = _compose_page(wiki_page, manifest, project_root, theory, compass)
        if composed is not None:
            if composed.name.lower() == "home":
                has_home = True
            pages.append(composed)

    # Only generate a Home page if the manifest doesn't declare one
    if not has_home:
        home = _compose_home(manifest, pages)
        pages.insert(0, home)

    return pages


def _has_related_section(content: str) -> bool:
    """Check if content already has a Related/See Also section."""
    lower = content.lower()
    return any(
        marker in lower
        for marker in [
            "## related",
            "## see also",
            "## related concepts",
            "## further reading",
            "## references",
        ]
    )


def _strip_managed_markers(text: str) -> str:
    """Remove LINTGATE_WIKI:BEGIN/END markers from output."""
    lines = text.split("\n")
    return "\n".join(line for line in lines if not line.strip().startswith("<!-- LINTGATE_WIKI:"))


def _compose_generated(
    page: WikiPage,
    theory: dict[str, Any] | None,
    compass: dict[str, Any] | None,
) -> tuple[list[str], list[str]]:
    """Build parts and source_files for a generator-based page."""
    parts = [_frontmatter(page), f"# {page.title}", ""]
    gen_content = _generate_content(page.generator, theory, compass)
    if gen_content:
        parts.append(gen_content)
        parts.append("")
    return parts, []


def _compose_whole_file(
    page: WikiPage,
    project_root: str,
) -> tuple[list[str], list[str]]:
    """Build parts and source_files for a whole-file source page."""
    parts: list[str] = []
    source_files: list[str] = []
    src = page.sources[0]
    abs_path = os.path.join(project_root, src.file)
    sec = extract_whole_file(abs_path)
    if sec:
        source_files.append(abs_path)
        parts.append(sec.content)
    elif src.required:
        parts.append(f"*Missing source: `{src.file}`*")
    return parts, source_files


def _compose_sections(
    page: WikiPage,
    project_root: str,
) -> tuple[list[str], list[str]]:
    """Build parts and source_files for a section-based page."""
    parts = [_frontmatter(page), f"# {page.title}", ""]
    source_files: list[str] = []
    sections_content = _extract_sources(page, project_root)
    for i, (src_file, heading, content) in enumerate(sections_content):
        if src_file not in source_files:
            source_files.append(src_file)
        section_id = f"{page.name}:{heading or f'section_{i}'}"
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
    return parts, source_files


def _append_cross_links(full_content: str, manifest: WikiManifest, page: WikiPage) -> str:
    """Append inferred cross-links if no related section exists."""
    if _has_related_section(full_content):
        return full_content
    cross_links = manifest.infer_cross_links(page)
    if not cross_links:
        return full_content
    full_content += "\n## See Also\n\n"
    for link_name in cross_links:
        full_content += f"- [{link_name}]({link_name})\n"
    return full_content + "\n"


def _compose_page(
    page: WikiPage,
    manifest: WikiManifest,
    project_root: str,
    theory: dict[str, Any] | None,
    compass: dict[str, Any] | None,
) -> ComposedPage | None:
    """Compose a single wiki page.

    For pages with file sources (``kind: file``), the source content is used
    directly — the composer does not add frontmatter, breadcrumbs, or H1
    (the publisher's ``apply_common_transforms`` handles those).

    For pages with section sources, content is assembled from extracted
    sections with headings.  Generated pages use their generator function.
    """
    is_whole_file = len(page.sources) == 1 and page.sources[0].kind == "file" and not page.generator

    if page.generator:
        parts, source_files = _compose_generated(page, theory, compass)
    elif is_whole_file:
        parts, source_files = _compose_whole_file(page, project_root)
    else:
        parts, source_files = _compose_sections(page, project_root)

    # Source attribution footer (section-based and generated pages only)
    if not is_whole_file and source_files:
        parts.append("")
        parts.append("---")
        rel_files = [os.path.relpath(f, project_root) for f in source_files]
        parts.append(f"*Sources: {', '.join(f'`{f}`' for f in rel_files)}*")

    full_content = _append_cross_links("\n".join(parts), manifest, page)

    return ComposedPage(
        name=page.name,
        title=page.title,
        content=full_content,
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
    from .transforms import _rail_display_name

    parts: list[str] = []

    if page.rail:
        parts.append(f"**{_rail_display_name(page.rail)}**")
    else:
        parts.append(f"**{page.pillar.title()}**")

    if page.chapter:
        parts.append(f"Chapter {page.chapter}")

    if page.prerequisites:
        prereqs = ", ".join(f"[{p}]({p})" for p in page.prerequisites)
        parts.append(f"Prerequisites: {prereqs}")

    parts.append("[Home](Home)")
    return " | ".join(parts)


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
