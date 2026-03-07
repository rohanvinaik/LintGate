"""Tests for the GitHub Pages static site publisher."""

from __future__ import annotations

import os

from lintgate.wiki.composer import compose_all_pages
from lintgate.wiki.manifest import SourceRef, WikiManifest, WikiPage
from lintgate.wiki.pages_publisher import publish_pages


def _make_manifest_and_sources(tmp_path):
    """Create manifest + source files for publisher tests."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "intro.md").write_text("# Intro\n\n## Getting Started\n\nStart here.\n")
    (docs / "arch.md").write_text("# Architecture\n\n## Pipeline\n\nPipeline details.\n")
    (docs / "ref.md").write_text("Reference content.\n")

    pages = [
        WikiPage(
            name="Getting-Started",
            title="Getting Started",
            pillar="usage",
            order=1,
            rail="getting_value",
            chapter=1,
            sources=[SourceRef(file="docs/intro.md", kind="section", heading="Getting Started", level=2)],
            tags=["intro"],
        ),
        WikiPage(
            name="Pipeline",
            title="Pipeline Architecture",
            pillar="architecture",
            order=1,
            rail="how_it_works",
            chapter=1,
            prerequisites=["Getting-Started"],
            sources=[SourceRef(file="docs/arch.md", kind="section", heading="Pipeline", level=2)],
            tags=["pipeline"],
        ),
        WikiPage(
            name="Reference",
            title="Reference Guide",
            pillar="reference",
            order=1,
            rail="reference",
            chapter=1,
            sources=[SourceRef(file="docs/ref.md", kind="file")],
            tags=["reference"],
        ),
    ]
    return WikiManifest(version=1, pages=pages)


def test_publish_creates_index_files(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    composed = compose_all_pages(manifest, str(tmp_path))
    out_dir = str(tmp_path / "_site")

    result = publish_pages(manifest, composed, str(tmp_path), out_dir, check_links=False)

    assert len(result.pages) == 4  # 3 declared + Home
    # Home at root
    assert os.path.isfile(os.path.join(out_dir, "index.html"))
    # Subpages in slug dirs
    assert os.path.isfile(os.path.join(out_dir, "getting-started", "index.html"))
    assert os.path.isfile(os.path.join(out_dir, "pipeline", "index.html"))
    assert os.path.isfile(os.path.join(out_dir, "reference", "index.html"))


def test_publish_creates_static_assets(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    composed = compose_all_pages(manifest, str(tmp_path))
    out_dir = str(tmp_path / "_site")

    publish_pages(manifest, composed, str(tmp_path), out_dir, check_links=False)

    assert os.path.isfile(os.path.join(out_dir, "style.css"))
    assert os.path.isfile(os.path.join(out_dir, "script.js"))
    assert os.path.isfile(os.path.join(out_dir, "sitemap.xml"))
    assert os.path.isfile(os.path.join(out_dir, "robots.txt"))
    assert os.path.isfile(os.path.join(out_dir, ".nojekyll"))


def test_publish_html_contains_title(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    composed = compose_all_pages(manifest, str(tmp_path))
    out_dir = str(tmp_path / "_site")

    publish_pages(manifest, composed, str(tmp_path), out_dir, check_links=False, site_title="TestSite")

    with open(os.path.join(out_dir, "getting-started", "index.html")) as f:
        html = f.read()
    assert "Getting Started" in html
    assert "TestSite" in html


def test_publish_sidebar_has_rails(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    composed = compose_all_pages(manifest, str(tmp_path))
    out_dir = str(tmp_path / "_site")

    publish_pages(manifest, composed, str(tmp_path), out_dir, check_links=False)

    with open(os.path.join(out_dir, "getting-started", "index.html")) as f:
        html = f.read()
    assert "Getting Value Fast" in html
    assert "How It Works" in html


def test_publish_prev_next_navigation(tmp_path):
    """Pages in the same rail get prev/next links."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("## Part1\n\nFirst.\n")
    (docs / "b.md").write_text("## Part2\n\nSecond.\n")

    pages = [
        WikiPage(
            name="Part-1", title="Part One", pillar="guide",
            rail="how_it_works", chapter=1,
            sources=[SourceRef(file="docs/a.md", kind="section", heading="Part1", level=2)],
        ),
        WikiPage(
            name="Part-2", title="Part Two", pillar="guide",
            rail="how_it_works", chapter=2,
            sources=[SourceRef(file="docs/b.md", kind="section", heading="Part2", level=2)],
        ),
    ]
    manifest = WikiManifest(version=1, pages=pages)
    composed = compose_all_pages(manifest, str(tmp_path))
    out_dir = str(tmp_path / "_site")

    publish_pages(manifest, composed, str(tmp_path), out_dir, check_links=False)

    with open(os.path.join(out_dir, "part-1", "index.html")) as f:
        html = f.read()
    assert "Part Two" in html  # next link
    assert "&rarr;" in html


def test_publish_link_check_passes_valid(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    composed = compose_all_pages(manifest, str(tmp_path))
    out_dir = str(tmp_path / "_site")

    result = publish_pages(manifest, composed, str(tmp_path), out_dir, check_links=True)

    assert result.link_errors == []


def test_publish_sitemap_content(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    composed = compose_all_pages(manifest, str(tmp_path))
    out_dir = str(tmp_path / "_site")

    publish_pages(
        manifest, composed, str(tmp_path), out_dir,
        check_links=False, base_url="https://example.com",
    )

    with open(os.path.join(out_dir, "sitemap.xml")) as f:
        sitemap = f.read()
    assert "<loc>https://example.com/" in sitemap
    assert "getting-started" in sitemap


def test_publish_dark_mode_support(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    composed = compose_all_pages(manifest, str(tmp_path))
    out_dir = str(tmp_path / "_site")

    publish_pages(manifest, composed, str(tmp_path), out_dir, check_links=False)

    with open(os.path.join(out_dir, "style.css")) as f:
        css = f.read()
    assert "data-theme" in css
    assert "dark" in css


def test_publish_deterministic(tmp_path):
    """Two runs produce identical output."""
    manifest = _make_manifest_and_sources(tmp_path)
    composed = compose_all_pages(manifest, str(tmp_path))

    out1 = str(tmp_path / "_site1")
    out2 = str(tmp_path / "_site2")

    publish_pages(manifest, composed, str(tmp_path), out1, check_links=False)
    publish_pages(manifest, composed, str(tmp_path), out2, check_links=False)

    for page_name in ["index.html", "getting-started/index.html", "pipeline/index.html"]:
        path1 = os.path.join(out1, page_name)
        path2 = os.path.join(out2, page_name)
        with open(path1) as f1, open(path2) as f2:
            assert f1.read() == f2.read(), f"Mismatch in {page_name}"


def test_publish_has_header_element(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    composed = compose_all_pages(manifest, str(tmp_path))
    out_dir = str(tmp_path / "_site")

    publish_pages(manifest, composed, str(tmp_path), out_dir, check_links=False, site_title="TestWiki")

    with open(os.path.join(out_dir, "getting-started", "index.html")) as f:
        page_html = f.read()
    assert '<header class="site-header">' in page_html
    assert "TestWiki" in page_html


def test_publish_has_dark_mode_toggle(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    composed = compose_all_pages(manifest, str(tmp_path))
    out_dir = str(tmp_path / "_site")

    publish_pages(manifest, composed, str(tmp_path), out_dir, check_links=False)

    with open(os.path.join(out_dir, "getting-started", "index.html")) as f:
        page_html = f.read()
    assert 'class="theme-toggle"' in page_html

    with open(os.path.join(out_dir, "script.js")) as f:
        js = f.read()
    assert "theme-toggle" in js
    assert "localStorage" in js

    with open(os.path.join(out_dir, "style.css")) as f:
        css = f.read()
    assert ".theme-toggle" in css
