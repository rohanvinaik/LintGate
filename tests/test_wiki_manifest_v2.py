"""Tests for manifest v2 features: rails, chapters, prerequisites, metrics."""

from __future__ import annotations

from lintgate.wiki.manifest import (
    WikiManifest,
    WikiPage,
    load_manifest,
    load_metrics,
)


def _manifest_with_rails():
    return WikiManifest(
        version=1,
        pages=[
            WikiPage(
                name="Quick-Start",
                title="Quick Start",
                pillar="guide",
                rail="getting_value",
                chapter=1,
                order=1,
            ),
            WikiPage(
                name="First-Steps",
                title="First Steps",
                pillar="guide",
                rail="getting_value",
                chapter=2,
                order=2,
                prerequisites=["Quick-Start"],
            ),
            WikiPage(
                name="Architecture",
                title="Architecture",
                pillar="theory",
                rail="how_it_works",
                chapter=1,
                order=1,
            ),
            WikiPage(
                name="Glossary",
                title="Glossary",
                pillar="reference",
                rail="reference",
                chapter=1,
                order=1,
            ),
            WikiPage(
                name="Legacy",
                title="Legacy Page",
                pillar="misc",
                order=1,  # No rail
            ),
        ],
    )


def test_pages_by_rail():
    m = _manifest_with_rails()
    gv = m.pages_by_rail("getting_value")
    assert len(gv) == 2
    assert gv[0].name == "Quick-Start"
    assert gv[1].name == "First-Steps"


def test_pages_by_rail_sorted_by_chapter():
    m = _manifest_with_rails()
    # Reverse the page list to ensure sorting works
    m.pages.reverse()
    m._build_tag_index()
    gv = m.pages_by_rail("getting_value")
    assert gv[0].chapter <= gv[1].chapter


def test_pages_by_rail_empty():
    m = _manifest_with_rails()
    assert m.pages_by_rail("nonexistent") == []


def test_rails_property():
    m = _manifest_with_rails()
    rails = m.rails
    assert "getting_value" in rails
    assert "how_it_works" in rails
    assert "reference" in rails


def test_prev_next_in_rail():
    m = _manifest_with_rails()
    qs = next(p for p in m.pages if p.name == "Quick-Start")
    fs = next(p for p in m.pages if p.name == "First-Steps")

    prev_qs, next_qs = m.prev_next_in_rail(qs)
    assert prev_qs is None
    assert next_qs is not None
    assert next_qs.name == "First-Steps"

    prev_fs, next_fs = m.prev_next_in_rail(fs)
    assert prev_fs is not None
    assert prev_fs.name == "Quick-Start"
    assert next_fs is None


def test_prev_next_no_rail():
    m = _manifest_with_rails()
    legacy = next(p for p in m.pages if p.name == "Legacy")
    prev_l, next_l = m.prev_next_in_rail(legacy)
    assert prev_l is None
    assert next_l is None


def test_prerequisites_in_page():
    m = _manifest_with_rails()
    fs = next(p for p in m.pages if p.name == "First-Steps")
    assert fs.prerequisites == ["Quick-Start"]


def test_manifest_hash_includes_rail():
    m = _manifest_with_rails()
    qs = next(p for p in m.pages if p.name == "Quick-Start")
    h1 = m.manifest_hash_for_page(qs)
    # Change rail — hash should change
    qs.rail = "how_it_works"
    h2 = m.manifest_hash_for_page(qs)
    assert h1 != h2


def test_estimate_read_time(tmp_path):
    (tmp_path / "doc.md").write_text(" ".join(["word"] * 400))
    page = WikiPage(
        name="Test",
        title="Test",
        pillar="x",
        sources=[{"file": "doc.md", "kind": "file"}],
    )
    # Need SourceRef, not dict
    from lintgate.wiki.manifest import SourceRef

    page.sources = [SourceRef(file="doc.md", kind="file")]
    m = WikiManifest(version=1, pages=[page])
    rt = m.estimate_read_time(page, str(tmp_path))
    assert rt == 2  # 400 words / 200 wpm = 2 min


def test_estimate_read_time_minimum(tmp_path):
    (tmp_path / "short.md").write_text("hello")
    from lintgate.wiki.manifest import SourceRef

    page = WikiPage(
        name="T",
        title="T",
        pillar="x",
        sources=[SourceRef(file="short.md", kind="file")],
    )
    m = WikiManifest(version=1, pages=[page])
    assert m.estimate_read_time(page, str(tmp_path)) == 1


# ─── YAML loading with new fields ────────────────────────────────────────


def test_load_manifest_with_rails(tmp_path):
    lg_dir = tmp_path / ".lintgate"
    lg_dir.mkdir()
    (lg_dir / "wiki_manifest.yaml").write_text("""\
version: 1
pages:
  - name: Quick-Start
    title: Quick Start
    pillar: guide
    rail: getting_value
    chapter: 1
    prerequisites: []
    sources: []
  - name: Advanced
    title: Advanced
    pillar: guide
    rail: getting_value
    chapter: 2
    prerequisites: [Quick-Start]
    sources: []
""")
    m = load_manifest(str(tmp_path))
    assert m is not None
    assert m.pages[0].rail == "getting_value"
    assert m.pages[0].chapter == 1
    assert m.pages[1].prerequisites == ["Quick-Start"]


def test_load_manifest_wiki_yaml(tmp_path):
    """wiki.yaml at project root takes precedence."""
    (tmp_path / "wiki.yaml").write_text("""\
version: 1
pages:
  - name: FromRoot
    title: From Root
    pillar: docs
    sources: []
""")
    # Also create .lintgate version (should be ignored)
    lg_dir = tmp_path / ".lintgate"
    lg_dir.mkdir()
    (lg_dir / "wiki_manifest.yaml").write_text("""\
version: 1
pages:
  - name: FromLintgate
    title: From Lintgate
    pillar: docs
    sources: []
""")
    m = load_manifest(str(tmp_path))
    assert m is not None
    assert m.pages[0].name == "FromRoot"


# ─── Metrics ─────────────────────────────────────────────────────────────


def test_load_metrics(tmp_path):
    wiki_dir = tmp_path / "docs" / "wiki"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "_metrics.yaml").write_text("tool_count: 49\nmodel_count: 3\n")

    metrics = load_metrics(str(tmp_path))
    assert metrics == {"tool_count": "49", "model_count": "3"}


def test_load_metrics_lintgate_fallback(tmp_path):
    lg_dir = tmp_path / ".lintgate"
    lg_dir.mkdir()
    (lg_dir / "_metrics.yaml").write_text("version: 2\n")

    metrics = load_metrics(str(tmp_path))
    assert metrics == {"version": "2"}


def test_load_metrics_missing(tmp_path):
    assert load_metrics(str(tmp_path)) == {}


def test_load_metrics_invalid(tmp_path):
    wiki_dir = tmp_path / "docs" / "wiki"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "_metrics.yaml").write_text("not: [valid: yaml: {{")

    # Should not crash
    metrics = load_metrics(str(tmp_path))
    assert isinstance(metrics, dict)


# ─── Key aliases ─────────────────────────────────────────────────────────


def test_sections_key_alias_for_sources(tmp_path):
    """'sections:' at page level should be accepted as alias for 'sources:'."""
    (tmp_path / "wiki.yaml").write_text("""\
version: 1
pages:
  - id: Research
    title: Research Paper
    pillar: docs
    sections:
      - source: docs/research.md
        headings: all
""")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "research.md").write_text("Research content.\n")

    m = load_manifest(str(tmp_path))
    assert m is not None
    page = m.pages[0]
    assert page.name == "Research"
    assert len(page.sources) == 1
    assert page.sources[0].file == "docs/research.md"
    assert page.sources[0].kind == "file"


def test_source_key_alias_for_file(tmp_path):
    """'source:' should be accepted as alias for 'file:' in source refs."""
    (tmp_path / "wiki.yaml").write_text("""\
version: 1
pages:
  - name: Intro
    title: Introduction
    pillar: docs
    sources:
      - source: docs/intro.md
        sections: all
""")
    m = load_manifest(str(tmp_path))
    assert m is not None
    assert m.pages[0].sources[0].file == "docs/intro.md"


def test_headings_all_alias_for_sections_all(tmp_path):
    """'headings: all' should be treated same as 'sections: all'."""
    (tmp_path / "wiki.yaml").write_text("""\
version: 1
pages:
  - name: Guide
    title: Guide
    pillar: docs
    sources:
      - file: docs/guide.md
        headings: all
""")
    m = load_manifest(str(tmp_path))
    assert m is not None
    assert m.pages[0].sources[0].kind == "file"
