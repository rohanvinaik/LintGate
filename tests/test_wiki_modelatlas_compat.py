"""Tests for ModelAtlas wiki.yaml format compatibility.

Validates that LintGate correctly parses the ModelAtlas manifest format:
- ``id`` instead of ``name``
- ``path`` instead of ``file`` in sources
- ``sections: all`` instead of ``kind: file``
- ``audience`` instead of ``pillar``
- Top-level ``rails:`` with display names
- kebab-case rail IDs
"""

from __future__ import annotations

import os

from lintgate.wiki.composer import compose_all_pages
from lintgate.wiki.manifest import load_manifest
from lintgate.wiki.pages_publisher import publish_pages

MODELATLAS_WIKI_YAML = """\
materializer_version: "0.2.0"

defaults:
  theory_scope: false
  audience: user

rails:
  getting-value-fast:
    name: "Getting Value Fast"
  how-it-works:
    name: "How It Works"
  why-designed-this-way:
    name: "Why It Is Designed This Way"
  reference:
    name: "Reference"

pages:
  - id: home
    title: "Home"
    audience: user
    sources:
      - path: docs/wiki/home.md
        sections: all

  - id: getting-started
    title: "Getting Started"
    audience: user
    rail: getting-value-fast
    chapter: 1
    prerequisites: []
    sources:
      - path: docs/wiki/getting-started.md
        sections: all

  - id: query-examples
    title: "Query Examples"
    audience: user
    rail: getting-value-fast
    chapter: 2
    prerequisites: [getting-started]
    sources:
      - path: docs/wiki/query-examples.md
        sections: all

  - id: system-overview
    title: "System Overview"
    audience: operator
    rail: how-it-works
    chapter: 1
    prerequisites: []
    sources:
      - path: docs/wiki/system-overview.md
        sections: all

  - id: glossary
    title: "Glossary"
    audience: user
    rail: reference
    chapter: 1
    prerequisites: []
    sources:
      - path: docs/wiki/glossary.md
        sections: all

promotions: []
"""


def _setup_project(tmp_path):
    """Create project with ModelAtlas format wiki.yaml and source files."""
    (tmp_path / "wiki.yaml").write_text(MODELATLAS_WIKI_YAML)

    wiki_dir = tmp_path / "docs" / "wiki"
    wiki_dir.mkdir(parents=True)

    (wiki_dir / "home.md").write_text(
        "# ModelAtlas\n\n"
        "**Welcome.** See [Getting Started](Getting-Started) to begin.\n\n"
        "---\n\n"
        "## Choose Your Path\n\n"
        "- [Getting Started](getting-started)\n"
        "- [System Overview](system-overview)\n"
        "- [Glossary](glossary)\n"
    )
    (wiki_dir / "getting-started.md").write_text(
        "# Getting Started\n\n"
        "**In five minutes you will have {{model_count}} models.**\n\n"
        "---\n\n"
        "## Install\n\n"
        "```bash\ngit clone ...\n```\n\n"
        "See [Query Examples](Query-Examples) next.\n"
    )
    (wiki_dir / "query-examples.md").write_text("# Query Examples\n\nExample content.\n")
    (wiki_dir / "system-overview.md").write_text("# System Overview\n\nArchitecture content.\n")
    (wiki_dir / "glossary.md").write_text("# Glossary\n\n**Anchor**: A semantic label.\n")

    # Metrics file
    (wiki_dir / "_metrics.yaml").write_text("model_count: '19,498'\nanchor_count: '170'\n")

    return str(tmp_path)


# ─── Manifest parsing ────────────────────────────────────────────────────


def test_load_modelatlas_manifest(tmp_path):
    root = _setup_project(tmp_path)
    m = load_manifest(root)
    assert m is not None
    assert len(m.pages) == 5


def test_id_becomes_name(tmp_path):
    root = _setup_project(tmp_path)
    m = load_manifest(root)
    names = [p.name for p in m.pages]
    assert "home" in names
    assert "getting-started" in names
    assert "glossary" in names
    # No empty names
    assert "" not in names


def test_path_becomes_file(tmp_path):
    root = _setup_project(tmp_path)
    m = load_manifest(root)
    gs = next(p for p in m.pages if p.name == "getting-started")
    assert gs.sources[0].file == "docs/wiki/getting-started.md"
    assert gs.sources[0].kind == "file"


def test_audience_becomes_pillar(tmp_path):
    root = _setup_project(tmp_path)
    m = load_manifest(root)
    gs = next(p for p in m.pages if p.name == "getting-started")
    assert gs.pillar == "user"
    so = next(p for p in m.pages if p.name == "system-overview")
    assert so.pillar == "operator"


def test_rail_names_parsed(tmp_path):
    root = _setup_project(tmp_path)
    m = load_manifest(root)
    assert m.rail_names["getting-value-fast"] == "Getting Value Fast"
    assert m.rail_names["how-it-works"] == "How It Works"
    assert m.rail_display_name("getting-value-fast") == "Getting Value Fast"


def test_rails_detected(tmp_path):
    root = _setup_project(tmp_path)
    m = load_manifest(root)
    rails = m.rails
    assert "getting-value-fast" in rails
    assert "how-it-works" in rails
    assert "reference" in rails


def test_prerequisites_parsed(tmp_path):
    root = _setup_project(tmp_path)
    m = load_manifest(root)
    qe = next(p for p in m.pages if p.name == "query-examples")
    assert qe.prerequisites == ["getting-started"]


def test_prev_next_in_rail(tmp_path):
    root = _setup_project(tmp_path)
    m = load_manifest(root)
    gs = next(p for p in m.pages if p.name == "getting-started")
    prev_p, next_p = m.prev_next_in_rail(gs)
    assert prev_p is None
    assert next_p is not None
    assert next_p.name == "query-examples"


# ─── Composition ─────────────────────────────────────────────────────────


def test_compose_reads_source_content(tmp_path):
    root = _setup_project(tmp_path)
    m = load_manifest(root)
    composed = compose_all_pages(m, root)
    gs = next(p for p in composed if p.name == "getting-started")
    assert "five minutes" in gs.content


def test_compose_page_names_not_empty(tmp_path):
    root = _setup_project(tmp_path)
    m = load_manifest(root)
    composed = compose_all_pages(m, root)
    for page in composed:
        assert page.name, "Empty page name in composed pages"


# ─── Pages publisher ─────────────────────────────────────────────────────


def test_publish_creates_subdirectories(tmp_path):
    root = _setup_project(tmp_path)
    m = load_manifest(root)
    composed = compose_all_pages(m, root)
    out_dir = str(tmp_path / "_site")

    result = publish_pages(m, composed, root, out_dir, check_links=False)

    # All pages should have non-empty slugs
    for p in result.pages:
        assert p.slug, f"Empty slug for page {p.name}"
        assert p.name, "Empty name for published page"

    # Subdirectories should exist
    assert os.path.isfile(os.path.join(out_dir, "index.html"))  # home
    assert os.path.isfile(os.path.join(out_dir, "getting-started", "index.html"))
    assert os.path.isfile(os.path.join(out_dir, "glossary", "index.html"))
    assert os.path.isfile(os.path.join(out_dir, "system-overview", "index.html"))


def test_publish_sidebar_has_rail_display_names(tmp_path):
    root = _setup_project(tmp_path)
    m = load_manifest(root)
    composed = compose_all_pages(m, root)
    out_dir = str(tmp_path / "_site")

    publish_pages(m, composed, root, out_dir, check_links=False)

    with open(os.path.join(out_dir, "getting-started", "index.html")) as f:
        html = f.read()
    assert "Getting Value Fast" in html
    assert "How It Works" in html


def test_publish_sidebar_links_have_slugs(tmp_path):
    root = _setup_project(tmp_path)
    m = load_manifest(root)
    composed = compose_all_pages(m, root)
    out_dir = str(tmp_path / "_site")

    publish_pages(m, composed, root, out_dir, check_links=False)

    with open(os.path.join(out_dir, "getting-started", "index.html")) as f:
        html = f.read()
    # Links should go to ../slug/, not ../
    assert "../getting-started/" in html or "../query-examples/" in html
    assert "..//" not in html  # No double-slash empty slugs


def test_publish_content_not_stub(tmp_path):
    root = _setup_project(tmp_path)
    m = load_manifest(root)
    composed = compose_all_pages(m, root)
    out_dir = str(tmp_path / "_site")

    publish_pages(m, composed, root, out_dir, check_links=False)

    with open(os.path.join(out_dir, "getting-started", "index.html")) as f:
        html = f.read()
    # Should have actual content, not just template chrome
    assert "five minutes" in html
    assert "Install" in html  # Section heading from source
    assert len(html) > 2000  # Real content, not a tiny stub


def test_publish_metrics_interpolated(tmp_path):
    root = _setup_project(tmp_path)
    m = load_manifest(root)
    composed = compose_all_pages(m, root)
    out_dir = str(tmp_path / "_site")

    publish_pages(m, composed, root, out_dir, check_links=False)

    with open(os.path.join(out_dir, "getting-started", "index.html")) as f:
        html = f.read()
    assert "19,498" in html
    assert "{{model_count}}" not in html


def test_publish_link_check_catches_empty_slug(tmp_path):
    """Regression: empty slugs should be caught by link checker."""
    root = _setup_project(tmp_path)
    m = load_manifest(root)
    composed = compose_all_pages(m, root)
    out_dir = str(tmp_path / "_site")

    result = publish_pages(m, composed, root, out_dir, check_links=True)

    # With correct parsing, all links should be valid
    assert result.link_errors == [], f"Unexpected link errors: {result.link_errors}"


def test_publish_sitemap_has_slugs(tmp_path):
    root = _setup_project(tmp_path)
    m = load_manifest(root)
    composed = compose_all_pages(m, root)
    out_dir = str(tmp_path / "_site")

    publish_pages(
        m,
        composed,
        root,
        out_dir,
        check_links=False,
        base_url="https://rohanv.me/ModelAtlas",
    )

    with open(os.path.join(out_dir, "sitemap.xml")) as f:
        sitemap = f.read()
    assert "getting-started" in sitemap
    assert "///" not in sitemap  # No empty slug artifacts


def test_publish_css_monospace_font(tmp_path):
    root = _setup_project(tmp_path)
    m = load_manifest(root)
    composed = compose_all_pages(m, root)
    out_dir = str(tmp_path / "_site")

    publish_pages(m, composed, root, out_dir, check_links=False)

    with open(os.path.join(out_dir, "style.css")) as f:
        css = f.read()
    assert "SF Mono" in css
    assert "monospace" in css
    assert "--bg: #141414" in css  # Dark mode matches rohanv.me
