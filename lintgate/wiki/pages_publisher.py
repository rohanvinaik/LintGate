"""GitHub Pages static site publisher.

Generates a static site from composed wiki pages:
- ``page-id/index.html`` per page (clean URLs)
- Sidebar with active state and rail grouping
- Prev/next navigation within rails
- SEO meta tags, sitemap.xml, robots.txt
- Link integrity checking
"""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .manifest import WikiManifest, WikiPage, load_metrics

if TYPE_CHECKING:
    from .composer import ComposedPage
from .transforms import apply_common_transforms, make_pages_link_fn

_WIKI_LINK_RE = re.compile(r"\[([^\]]+)\]\(([A-Z][A-Za-z0-9_-]+)\)")


def _page_slug(name: str) -> str:
    """Convert a page name/id to a URL slug."""
    return name.lower().replace(" ", "-")


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
    page_lookup: dict[str, WikiPage] = {p.name: p for p in manifest.pages}

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


# ─── HTML rendering ──────────────────────────────────────────────────────


def _render_page(
    title: str,
    site_title: str,
    content_html: str,
    sidebar_html: str,
    prev_next_html: str,
    active_page: str,
    description: str,
    base_url: str,
    slug: str,
) -> str:
    """Render a full HTML page."""
    base = base_url.rstrip("/")
    canonical = f"{base}/" if slug == "home" or active_page.lower() == "home" else f"{base}/{slug}/"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)} — {html.escape(site_title)}</title>
  <meta name="description" content="{html.escape(description)}">
  <meta name="generator" content="LintGate Wiki Publisher">
  <link rel="canonical" href="{canonical}">
  <link rel="stylesheet" href="{_asset_prefix(active_page)}style.css">
</head>
<body>
  <header class="site-header">
    <button class="sidebar-toggle" aria-label="Toggle sidebar">&#9776;</button>
    <a class="site-title" href="{_asset_prefix(active_page)}">{html.escape(site_title)}</a>
    <button class="theme-toggle" aria-label="Toggle dark mode" title="Toggle dark mode">&#9790;</button>
  </header>
  <div class="layout">
    <nav class="sidebar" role="navigation" aria-label="Wiki navigation">
      <div class="sidebar-header">
        <a href="{_asset_prefix(active_page)}">{html.escape(site_title)}</a>
      </div>
      {sidebar_html}
    </nav>
    <main class="content" role="main">
      <article>
        {content_html}
      </article>
      {prev_next_html}
    </main>
  </div>
  <footer class="site-footer">
    <p>Generated by <a href="https://github.com/rohanvinaik/lintgate">LintGate</a></p>
  </footer>
  <script src="{_asset_prefix(active_page)}script.js"></script>
</body>
</html>"""


def _asset_prefix(page_name: str) -> str:
    """Return relative path prefix for assets (../ for subpages, ./ for home)."""
    if page_name.lower() == "home":
        return "./"
    return "../"


def _build_sidebar(
    manifest: WikiManifest,
    composed: list[ComposedPage],
    is_root: bool = False,
) -> str:
    """Build sidebar HTML grouped by rail (or pillar if no rails).

    Args:
        is_root: True when rendering the Home/root page (links use ./ prefix).
    """
    prefix = "./" if is_root else "../"
    lines: list[str] = []
    composed_names = {p.name for p in composed}

    rails = manifest.rails
    if rails:
        for rail in rails:
            rail_pages = manifest.pages_by_rail(rail)
            if not rail_pages:
                continue
            label = manifest.rail_display_name(rail)
            lines.append('<div class="sidebar-group">')
            lines.append(f"  <h3>{html.escape(label)}</h3>")
            lines.append("  <ul>")
            for wp in rail_pages:
                if wp.name in composed_names:
                    slug = _page_slug(wp.name)
                    lines.append(
                        f'    <li data-page="{html.escape(wp.name)}">'
                        f'<a href="{prefix}{slug}/">{html.escape(wp.title)}</a></li>'
                    )
            lines.append("  </ul>")
            lines.append("</div>")

    # Pages without rails, grouped by pillar
    no_rail = [p for p in manifest.pages if not p.rail]
    if no_rail:
        seen_pillars: list[str] = []
        for page in no_rail:
            if page.pillar and page.pillar not in seen_pillars:
                seen_pillars.append(page.pillar)
        for pillar in seen_pillars:
            pillar_pages = [p for p in no_rail if p.pillar == pillar]
            pillar_pages.sort(key=lambda p: p.order)
            lines.append('<div class="sidebar-group">')
            lines.append(f"  <h3>{html.escape(pillar.title())}</h3>")
            lines.append("  <ul>")
            for wp in pillar_pages:
                if wp.name in composed_names:
                    slug = _page_slug(wp.name)
                    lines.append(
                        f'    <li data-page="{html.escape(wp.name)}">'
                        f'<a href="{prefix}{slug}/">{html.escape(wp.title)}</a></li>'
                    )
            lines.append("  </ul>")
            lines.append("</div>")

    return "\n".join(lines)


def _build_prev_next(
    prev_p: WikiPage | None,
    next_p: WikiPage | None,
    is_root: bool,
) -> str:
    """Build prev/next navigation HTML."""
    if not prev_p and not next_p:
        return ""

    prefix = "./" if is_root else "../"
    parts: list[str] = ['<nav class="prev-next" aria-label="Page navigation">']
    if prev_p:
        slug = _page_slug(prev_p.name)
        parts.append(
            f'  <a class="prev" href="{prefix}{slug}/">&larr; {html.escape(prev_p.title)}</a>'
        )
    else:
        parts.append('  <span class="prev"></span>')
    if next_p:
        slug = _page_slug(next_p.name)
        parts.append(
            f'  <a class="next" href="{prefix}{slug}/">{html.escape(next_p.title)} &rarr;</a>'
        )
    else:
        parts.append('  <span class="next"></span>')
    parts.append("</nav>")
    return "\n".join(parts)


def _md_to_html(text: str, link_fn: Any = None) -> str:
    """Minimal markdown-to-HTML conversion.

    Handles headings, paragraphs, lists, code blocks, bold, italic, links,
    tables, and horizontal rules. Not a full markdown parser — sufficient
    for wiki pages that follow our chapter grammar.
    """
    # Strip managed-section markers before rendering
    lines = [
        line for line in text.split("\n") if not line.strip().startswith("<!-- LINTGATE_WIKI:")
    ]
    html_parts: list[str] = []
    in_code_block = False
    in_list = False
    in_table = False
    paragraph_lines: list[str] = []

    def _flush_paragraph() -> None:
        if paragraph_lines:
            text = " ".join(paragraph_lines)
            text = _inline_format(text)
            html_parts.append(f"<p>{text}</p>")
            paragraph_lines.clear()

    def _flush_list() -> None:
        nonlocal in_list
        if in_list:
            html_parts.append("</ul>")
            in_list = False

    def _flush_table() -> None:
        nonlocal in_table
        if in_table:
            html_parts.append("</tbody></table>")
            in_table = False

    for line in lines:
        # Code blocks
        if line.startswith("```"):
            if in_code_block:
                html_parts.append("</code></pre>")
                in_code_block = False
            else:
                _flush_paragraph()
                _flush_list()
                _flush_table()
                lang = line[3:].strip()
                cls = f' class="language-{html.escape(lang)}"' if lang else ""
                html_parts.append(f"<pre><code{cls}>")
                in_code_block = True
            continue

        if in_code_block:
            html_parts.append(html.escape(line))
            continue

        stripped = line.strip()

        # Empty line
        if not stripped:
            _flush_paragraph()
            _flush_list()
            _flush_table()
            continue

        # Horizontal rule
        if stripped in ("---", "***", "___"):
            _flush_paragraph()
            _flush_list()
            _flush_table()
            html_parts.append("<hr>")
            continue

        # Headings
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match:
            _flush_paragraph()
            _flush_list()
            _flush_table()
            level = len(heading_match.group(1))
            text = _inline_format(heading_match.group(2))
            anchor = re.sub(r"[^a-z0-9]+", "-", heading_match.group(2).lower()).strip("-")
            html_parts.append(f'<h{level} id="{anchor}">{text}</h{level}>')
            continue

        # Table rows
        if stripped.startswith("|"):
            _flush_paragraph()
            _flush_list()
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            # Separator row (---|---) — skip but don't close table
            if all(re.match(r"^[-:]+$", c) for c in cells if c):
                continue
            if not in_table:
                html_parts.append("<table><tbody>")
                in_table = True
            row_html = "".join(f"<td>{_inline_format(c)}</td>" for c in cells)
            html_parts.append(f"<tr>{row_html}</tr>")
            continue

        # List items
        list_match = re.match(r"^[-*+]\s+(.+)$", stripped)
        if list_match:
            _flush_paragraph()
            _flush_table()
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            item_text = _inline_format(list_match.group(1))
            html_parts.append(f"<li>{item_text}</li>")
            continue

        # Regular text → accumulate paragraph
        _flush_list()
        _flush_table()
        paragraph_lines.append(stripped)

    _flush_paragraph()
    _flush_list()
    _flush_table()
    if in_code_block:
        html_parts.append("</code></pre>")

    return "\n".join(html_parts)


def _inline_format(text: str) -> str:
    """Apply inline markdown formatting: bold, italic, code, links."""
    # Inline code (before bold/italic to avoid conflicts)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Bold
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    # Italic
    text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)
    # Links
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2">\1</a>',
        text,
    )
    return text


# ─── Static assets ───────────────────────────────────────────────────────


def _write_css(out_dir: str) -> None:
    """Write the site stylesheet."""
    css = """\
:root {
  --bg: #fafafa;
  --fg: #1a1a1a;
  --sidebar-bg: #f5f5f5;
  --sidebar-fg: #333;
  --accent: #333;
  --accent-hover: #555;
  --border: #e0e0e0;
  --code-bg: #f5f5f5;
  --max-width: 860px;
  --sidebar-width: 220px;
}

[data-theme="dark"] {
  --bg: #141414;
  --fg: #fafafa;
  --sidebar-bg: #1a1a1a;
  --sidebar-fg: #ccc;
  --accent: #ccc;
  --accent-hover: #fff;
  --border: #2a2a2a;
  --code-bg: #1e1e1e;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: "SF Mono", "Fira Code", "Cascadia Code", monospace;
  background: var(--bg);
  color: var(--fg);
  line-height: 1.6;
  font-size: 14px;
}

.site-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.5rem 1rem;
  background: var(--sidebar-bg);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 60;
}

.site-header .site-title {
  font-weight: 700;
  font-size: 0.9rem;
  color: var(--fg);
  text-decoration: none;
  letter-spacing: -0.02em;
}

.theme-toggle {
  margin-left: auto;
  background: none;
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 0.2rem 0.5rem;
  font-size: 1rem;
  cursor: pointer;
  color: var(--fg);
}
.theme-toggle:hover { background: var(--border); }

.layout {
  display: flex;
  min-height: calc(100vh - 40px);
}

.sidebar {
  width: var(--sidebar-width);
  background: var(--sidebar-bg);
  color: var(--sidebar-fg);
  padding: 1.5rem 1rem;
  border-right: 1px solid var(--border);
  overflow-y: auto;
  position: sticky;
  top: 40px;
  height: calc(100vh - 40px);
  flex-shrink: 0;
}

.sidebar-header {
  font-weight: 700;
  font-size: 1rem;
  margin-bottom: 1.5rem;
  letter-spacing: -0.02em;
}

.sidebar-header a { color: inherit; text-decoration: none; }

.sidebar-group { margin-bottom: 1.2rem; }
.sidebar-group h3 {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--accent);
  margin-bottom: 0.4rem;
  opacity: 0.7;
}

.sidebar ul { list-style: none; }
.sidebar li { margin-bottom: 0.1rem; }
.sidebar a {
  color: var(--sidebar-fg);
  text-decoration: none;
  font-size: 0.85rem;
  display: block;
  padding: 0.2rem 0.5rem;
  border-radius: 3px;
}
.sidebar a:hover { background: var(--border); }
.sidebar li.active a {
  background: var(--fg);
  color: var(--bg);
}

.sidebar-toggle {
  display: none;
  background: none;
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 0.2rem 0.5rem;
  font-size: 1.2rem;
  cursor: pointer;
  color: var(--fg);
}

.content {
  flex: 1;
  max-width: var(--max-width);
  margin: 0 auto;
  padding: 2rem 2.5rem 4rem;
}

article h1 {
  font-size: 1.6rem;
  margin-bottom: 1rem;
  letter-spacing: -0.03em;
  font-weight: 700;
}
article h2 {
  font-size: 1.2rem;
  margin: 2rem 0 0.75rem;
  border-bottom: 1px solid var(--border);
  padding-bottom: 0.3rem;
  letter-spacing: -0.02em;
}
article h3 { font-size: 1rem; margin: 1.5rem 0 0.5rem; }
article p { margin-bottom: 1rem; }
article ul, article ol { margin: 0.5rem 0 1rem 1.5rem; }
article li { margin-bottom: 0.3rem; }
article a { color: var(--accent); }
article a:hover { color: var(--accent-hover); }

article pre {
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1rem;
  overflow-x: auto;
  margin-bottom: 1rem;
  font-size: 0.85rem;
  line-height: 1.5;
}

article code {
  font-family: inherit;
  font-size: 0.9em;
}

article p code, article li code {
  background: var(--code-bg);
  padding: 0.1em 0.3em;
  border-radius: 2px;
}

article table {
  border-collapse: collapse;
  width: 100%;
  margin-bottom: 1rem;
  font-size: 0.9rem;
}
article td, article th {
  border: 1px solid var(--border);
  padding: 0.4rem 0.6rem;
  text-align: left;
}

article hr {
  border: none;
  border-top: 1px solid var(--border);
  margin: 2rem 0;
}

.breadcrumb {
  font-size: 0.8rem;
  color: var(--accent);
  opacity: 0.7;
  margin-bottom: 1rem;
}

.prev-next {
  display: flex;
  justify-content: space-between;
  margin-top: 3rem;
  padding-top: 1rem;
  border-top: 1px solid var(--border);
}
.prev-next a {
  color: var(--accent);
  text-decoration: none;
  font-size: 0.85rem;
}
.prev-next a:hover { color: var(--accent-hover); }

.site-footer {
  text-align: center;
  padding: 1rem;
  font-size: 0.75rem;
  color: var(--accent);
  opacity: 0.5;
  border-top: 1px solid var(--border);
}

@media (max-width: 768px) {
  .sidebar {
    position: fixed;
    left: calc(-1 * var(--sidebar-width) - 20px);
    top: 0;
    z-index: 50;
    transition: left 0.3s;
  }
  .sidebar.open { left: 0; }
  .sidebar-toggle { display: block; }
  .content { padding: 3rem 1rem 2rem; }
}
"""
    with open(os.path.join(out_dir, "style.css"), "w") as f:
        f.write(css)


def _write_js(out_dir: str) -> None:
    """Write the site JavaScript (dark mode + mobile sidebar)."""
    js = """\
(function() {
  // Dark mode — respect saved preference, then system preference
  var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  var saved = localStorage.getItem('theme');
  var isDark = saved === 'dark' || (!saved && prefersDark);
  if (isDark) {
    document.documentElement.setAttribute('data-theme', 'dark');
  }

  // Dark mode toggle button
  var themeBtn = document.querySelector('.theme-toggle');
  if (themeBtn) {
    function updateIcon() {
      var dark = document.documentElement.getAttribute('data-theme') === 'dark';
      themeBtn.textContent = dark ? '\\u2600' : '\\u263E';
      themeBtn.setAttribute('title', dark ? 'Switch to light mode' : 'Switch to dark mode');
    }
    updateIcon();
    themeBtn.addEventListener('click', function() {
      var dark = document.documentElement.getAttribute('data-theme') === 'dark';
      if (dark) {
        document.documentElement.removeAttribute('data-theme');
        localStorage.setItem('theme', 'light');
      } else {
        document.documentElement.setAttribute('data-theme', 'dark');
        localStorage.setItem('theme', 'dark');
      }
      updateIcon();
    });
  }

  // Sidebar toggle
  var toggle = document.querySelector('.sidebar-toggle');
  var sidebar = document.querySelector('.sidebar');
  if (toggle && sidebar) {
    toggle.addEventListener('click', function() {
      sidebar.classList.toggle('open');
    });
  }

  // Active page highlighting
  var currentPath = window.location.pathname.replace(/\\/index\\.html$/, '/');
  document.querySelectorAll('.sidebar li[data-page]').forEach(function(li) {
    var link = li.querySelector('a');
    if (link && link.pathname && currentPath.endsWith(link.pathname.replace(/\\/index\\.html$/, '/'))) {
      li.classList.add('active');
    }
  });
})();
"""
    with open(os.path.join(out_dir, "script.js"), "w") as f:
        f.write(js)


# ─── SEO / deploy files ─────────────────────────────────────────────────


def _write_sitemap(pages: list[PublishedPage], out_dir: str, base_url: str) -> None:
    """Write sitemap.xml."""
    base = base_url.rstrip("/")
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for page in pages:
        if page.slug == "home" or page.name.lower() == "home":
            url = f"{base}/"
        else:
            url = f"{base}/{page.slug}/"
        lines.append(f"  <url><loc>{html.escape(url)}</loc></url>")
    lines.append("</urlset>")

    with open(os.path.join(out_dir, "sitemap.xml"), "w") as f:
        f.write("\n".join(lines))


def _write_robots(out_dir: str, base_url: str) -> None:
    """Write robots.txt."""
    with open(os.path.join(out_dir, "robots.txt"), "w") as f:
        f.write(f"User-agent: *\nAllow: /\nSitemap: {base_url}/sitemap.xml\n")


def _write_nojekyll(out_dir: str) -> None:
    """Write .nojekyll to prevent Jekyll processing."""
    open(os.path.join(out_dir, ".nojekyll"), "w").close()  # noqa: SIM115


# ─── Link checking ──────────────────────────────────────────────────────


def _check_internal_links(pages: list[PublishedPage], out_dir: str) -> list[str]:
    """Validate internal links in generated HTML files.

    Returns a list of error strings for broken links.
    """
    valid_slugs = {p.slug for p in pages if p.slug}
    errors: list[str] = []

    # Match relative hrefs: ../slug/ or ./slug/
    href_re = re.compile(r'href="(\.\./([^"/]+)/|\.\/([^"/]+)/)"')

    for page in pages:
        try:
            with open(page.path, encoding="utf-8") as f:
                content = f.read()
        except OSError:
            errors.append(f"{page.name}: could not read {page.path}")
            continue

        for match in href_re.finditer(content):
            # Group 2 is slug from ../<slug>/, group 3 from ./<slug>/
            slug = match.group(2) or match.group(3) or ""
            if not slug:
                errors.append(f"{page.name}: empty slug in href '{match.group(1)}'")
                continue
            # Skip asset files
            if slug.endswith((".css", ".js")):
                continue
            if slug not in valid_slugs:
                errors.append(f"{page.name}: broken link to '{slug}'")

    return errors
