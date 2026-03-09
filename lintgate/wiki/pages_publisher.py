"""GitHub Pages static site publisher.

Generates a static site from composed wiki pages:
- ``page-id/index.html`` per page (clean URLs)
- Sidebar with active state and rail grouping
- Prev/next navigation within rails
- SEO meta tags, sitemap.xml, robots.txt
- Link integrity checking

Implementation is split across sub-modules:
- ``_pages_publisher_render``: HTML rendering, markdown parser, sidebar/nav
- ``_pages_publisher_assets``: CSS/JS writers, sitemap, robots, link checker
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .manifest import WikiManifest, load_metrics

if TYPE_CHECKING:
    from .composer import ComposedPage
# Re-export asset/SEO helpers for backward compatibility
from ._pages_publisher_assets import (  # noqa: F401
    _check_internal_links,
    _write_css,
    _write_js,
    _write_nojekyll,
    _write_robots,
    _write_sitemap,
)

# Re-export render helpers for backward compatibility
from ._pages_publisher_render import (  # noqa: F401
    _asset_prefix,
    _build_prev_next,
    _build_sidebar,
    _inline_format,
    _md_to_html,
    _MdParser,
    _page_slug,
    _render_page,
)
from .transforms import apply_common_transforms, make_pages_link_fn

_WIKI_LINK_RE = re.compile(r"\[([^\]]+)\]\(([A-Z][A-Za-z0-9_-]+)\)")


@dataclass
class PublishedPage:
    """A page written to the output directory."""

    name: str
    path: str
    slug: str
    html_size: int


@dataclass
class PublishResult:
    """Result of a pages publish run."""

    pages: list[PublishedPage] = field(default_factory=list)
    out_dir: str = ""
    sitemap_written: bool = False
    link_errors: list[str] = field(default_factory=list)


def publish_pages(
    manifest: WikiManifest,
    composed: list[ComposedPage],
    project_root: str,
    out_dir: str,
    check_links: bool = True,
    site_title: str = "Project Wiki",
    base_url: str = "",
) -> PublishResult:
    """Generate the full static site from composed pages."""
    # Clean stale page directories from previous runs
    _clean_stale_pages(out_dir, {_page_slug(p.name) for p in composed})
    os.makedirs(out_dir, exist_ok=True)
    metrics = load_metrics(project_root)
    result = PublishResult(out_dir=out_dir)

    # Build sidebar HTML per context (root vs subpage need different link prefixes)
    sidebar_root = _build_sidebar(manifest, composed, is_root=True)
    sidebar_sub = _build_sidebar(manifest, composed, is_root=False)

    # Build page lookup for prev/next
    page_lookup = {p.name: p for p in manifest.pages}

    for page in composed:
        is_home = page.name.lower() == "home"
        slug = _page_slug(page.name)
        sidebar_html = sidebar_root if is_home else sidebar_sub
        link_fn = make_pages_link_fn(is_root=is_home)

        # Apply transforms to content
        wiki_page = page_lookup.get(page.name)
        if wiki_page:
            read_time = manifest.estimate_read_time(wiki_page, project_root)
            transformed = apply_common_transforms(
                page.content,
                wiki_page,
                metrics=metrics,
                link_fn=link_fn,
                read_time_min=read_time,
                manifest=manifest,
            )
        else:
            # Home page or pages without manifest entry
            transformed = page.content

        # Convert markdown to simple HTML
        content_html = _md_to_html(transformed, link_fn if wiki_page else None)

        # Prev/next nav
        prev_next_html = ""
        if wiki_page:
            prev_p, next_p = manifest.prev_next_in_rail(wiki_page)
            prev_next_html = _build_prev_next(prev_p, next_p, is_home)

        # SEO meta
        description = page.title
        if wiki_page and wiki_page.rail:
            description = f"{page.title} — {manifest.rail_display_name(wiki_page.rail)}"

        # Full HTML page
        page_html = _render_page(
            title=page.title,
            site_title=site_title,
            content_html=content_html,
            sidebar_html=sidebar_html,
            prev_next_html=prev_next_html,
            active_page=page.name,
            description=description,
            base_url=base_url,
            slug=slug,
        )

        # Write page-id/index.html
        page_dir = out_dir if is_home else os.path.join(out_dir, slug)
        os.makedirs(page_dir, exist_ok=True)
        out_path = os.path.join(page_dir, "index.html")

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(page_html)

        result.pages.append(
            PublishedPage(
                name=page.name,
                path=out_path,
                slug=slug,
                html_size=len(page_html),
            )
        )

    # Write static assets
    _write_css(out_dir)
    _write_js(out_dir)

    # Write sitemap.xml + robots.txt + .nojekyll
    _write_sitemap(result.pages, out_dir, base_url)
    result.sitemap_written = True
    _write_robots(out_dir, base_url)
    _write_nojekyll(out_dir)

    # Link integrity check
    if check_links:
        result.link_errors = _check_internal_links(result.pages, out_dir)

    return result


def _clean_stale_pages(out_dir: str, current_slugs: set[str]) -> None:
    """Remove page directories from previous runs that are no longer in the manifest."""
    import shutil

    if not os.path.isdir(out_dir):
        return
    # Only remove subdirectories (not asset files)
    for entry in os.listdir(out_dir):
        path = os.path.join(out_dir, entry)
        if not os.path.isdir(path):
            continue
        if entry not in current_slugs:
            shutil.rmtree(path)
