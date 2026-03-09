"""Tests for shared wiki transforms."""

from __future__ import annotations

from lintgate.wiki.manifest import WikiPage
from lintgate.wiki.transforms import (
    apply_common_transforms,
    build_breadcrumb,
    interpolate_metrics,
    make_pages_link_fn,
    rewrite_links,
    strip_frontmatter,
    strip_leading_h1,
    wiki_link_fn,
)


def _page(**kwargs) -> WikiPage:
    defaults = {"name": "Test", "title": "Test Page", "pillar": "theory"}
    defaults.update(kwargs)
    return WikiPage(**defaults)


# ─── strip_frontmatter ───────────────────────────────────────────────────


def test_strip_frontmatter_removes_yaml():
    text = "---\ntitle: foo\n---\n\nContent here."
    assert strip_frontmatter(text) == "Content here."


def test_strip_frontmatter_no_frontmatter():
    text = "Just content."
    assert strip_frontmatter(text) == "Just content."


def test_strip_frontmatter_preserves_later_dashes():
    text = "---\nk: v\n---\n\nContent\n\n---\n\nMore."
    result = strip_frontmatter(text)
    assert "Content" in result
    assert "---" in result  # The later --- is preserved


# ─── strip_leading_h1 ────────────────────────────────────────────────────


def test_strip_leading_h1():
    text = "# My Title\n\nContent."
    assert strip_leading_h1(text) == "Content."


def test_strip_leading_h1_only_first():
    text = "# First\n\n# Second\n\nContent."
    result = strip_leading_h1(text)
    assert "# Second" in result
    assert "# First" not in result


def test_strip_leading_h1_no_h1():
    text = "## Subtitle\n\nContent."
    assert strip_leading_h1(text) == text


# ─── interpolate_metrics ─────────────────────────────────────────────────


def test_interpolate_metrics():
    text = "We have {{tool_count}} tools and {{model_count}} models."
    metrics = {"tool_count": "49", "model_count": "3"}
    result = interpolate_metrics(text, metrics)
    assert result == "We have 49 tools and 3 models."


def test_interpolate_metrics_missing_key():
    text = "Value: {{unknown_key}}"
    result = interpolate_metrics(text, {"other": "1"})
    assert result == "Value: {{unknown_key}}"


def test_interpolate_metrics_empty():
    text = "No metrics here."
    assert interpolate_metrics(text, {}) == text


# ─── build_breadcrumb ────────────────────────────────────────────────────


def test_breadcrumb_basic_pillar():
    page = _page(pillar="architecture")
    bc = build_breadcrumb(page)
    assert "**Architecture**" in bc
    assert "[Home](Home)" in bc


def test_breadcrumb_with_rail():
    page = _page(rail="getting_value", chapter=2)
    bc = build_breadcrumb(page)
    assert "**Getting Value Fast**" in bc
    assert "Chapter 2" in bc


def test_breadcrumb_with_prerequisites():
    page = _page(prerequisites=["Intro", "Setup"])
    bc = build_breadcrumb(page)
    assert "Prerequisites:" in bc
    assert "[Intro](Intro)" in bc
    assert "[Setup](Setup)" in bc


def test_breadcrumb_with_link_fn():
    page = _page(prerequisites=["Intro"])
    bc = build_breadcrumb(page, link_fn=wiki_link_fn)
    assert "[Intro](Intro)" in bc
    assert "[Home](Home)" in bc


def test_breadcrumb_with_read_time():
    page = _page()
    bc = build_breadcrumb(page, read_time_min=5)
    assert "5 min read" in bc


# ─── rewrite_links ───────────────────────────────────────────────────────


def test_rewrite_links_wiki_fn():
    text = "See [the intro](Theory-Core) for details."
    result = rewrite_links(text, wiki_link_fn)
    assert result == "See [the intro](Theory-Core) for details."


def test_rewrite_links_pages_fn():
    link_fn = make_pages_link_fn(is_root=False)
    text = "See [the intro](Theory-Core) for details."
    result = rewrite_links(text, link_fn)
    assert result == "See [the intro](../theory-core/) for details."


def test_rewrite_links_preserves_urls():
    link_fn = make_pages_link_fn()
    text = "Visit [site](https://example.com) or [file](docs/foo.md)"
    result = rewrite_links(text, link_fn)
    # URLs and file paths should not be rewritten
    assert "(https://example.com)" in result
    assert "docs/foo.md" in result


def test_rewrite_links_root_prefix():
    link_fn = make_pages_link_fn(is_root=True)
    text = "Go to [Setup](Setup-Guide)."
    result = rewrite_links(text, link_fn)
    assert "./setup-guide/" in result


# ─── apply_common_transforms ─────────────────────────────────────────────


def test_apply_common_transforms_full():
    page = _page(rail="how_it_works", chapter=1, prerequisites=["Intro"])
    text = "---\nk: v\n---\n# Title\n\nWe have {{tools}} tools.\n\nSee [more](Theory-Core)."
    metrics = {"tools": "49"}
    link_fn = make_pages_link_fn(is_root=False)

    result = apply_common_transforms(
        text,
        page,
        metrics=metrics,
        link_fn=link_fn,
        read_time_min=3,
    )

    assert "**How It Works**" in result
    assert "Chapter 1" in result
    assert "3 min read" in result
    assert "49 tools" in result
    assert "../theory-core/" in result
    # Frontmatter and leading H1 stripped
    assert "---" not in result.split("\n")[0]
    assert "# Title" not in result


def test_apply_common_transforms_no_breadcrumb():
    page = _page()
    result = apply_common_transforms("Content.", page, include_breadcrumb=False)
    assert result == "Content."


# ─── anchor links ────────────────────────────────────────────────────────


def test_rewrite_links_with_anchor():
    link_fn = make_pages_link_fn(is_root=False)
    text = "See [zero state](Glossary#zero-state) for details."
    result = rewrite_links(text, link_fn)
    assert "../glossary/#zero-state" in result


def test_rewrite_links_anchor_preserved_wiki_fn():
    text = "See [zero state](Glossary#zero-state) for details."
    result = rewrite_links(text, wiki_link_fn)
    assert "Glossary#zero-state" in result


def test_rewrite_links_no_anchor():
    link_fn = make_pages_link_fn(is_root=False)
    text = "See [glossary](Glossary) for details."
    result = rewrite_links(text, link_fn)
    assert "../glossary/" in result
    assert "#" not in result


def test_rewrite_links_anchor_with_known_pages():
    link_fn = make_pages_link_fn(is_root=False)
    known = {"Glossary", "Home"}
    text = "See [zero state](Glossary#zero-state) and [unknown](Other#thing)."
    result = rewrite_links(text, link_fn, known_pages=known)
    assert "../glossary/#zero-state" in result
    assert "Other#thing" in result  # Not rewritten — not a known page
