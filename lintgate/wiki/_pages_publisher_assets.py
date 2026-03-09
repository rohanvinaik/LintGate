"""Static asset writers and link checking for the pages publisher.

Contains: _write_css, _write_js, _write_sitemap, _write_robots,
_write_nojekyll, _check_internal_links.
"""

from __future__ import annotations

import html
import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .pages_publisher import PublishedPage


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
