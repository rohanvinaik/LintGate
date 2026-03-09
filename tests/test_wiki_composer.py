"""Tests for wiki page composition."""

from __future__ import annotations

from lintgate.wiki.composer import compose_all_pages
from lintgate.wiki.manifest import SourceRef, WikiManifest, WikiPage


def _make_manifest_and_sources(tmp_path):
    """Create a minimal manifest + source files for testing."""
    docs = tmp_path / "docs"
    docs.mkdir()

    (docs / "design.md").write_text(
        "# Design\n\n## Overview\n\nOverview content.\n\n## Pipeline\n\nPipeline content.\n"
    )
    (docs / "research.md").write_text("Research file content.\n")

    pages = [
        WikiPage(
            name="Theory-Core",
            title="Core Thesis",
            pillar="theory",
            order=1,
            sources=[
                SourceRef(file="docs/design.md", kind="section", heading="Overview", level=2),
            ],
            tags=["drift", "discipline"],
        ),
        WikiPage(
            name="Arch-Pipeline",
            title="Pipeline",
            pillar="architecture",
            order=1,
            sources=[
                SourceRef(file="docs/design.md", kind="section", heading="Pipeline", level=2),
            ],
            tags=["pipeline", "drift"],
        ),
        WikiPage(
            name="Theory-Research",
            title="Research",
            pillar="theory",
            order=2,
            sources=[
                SourceRef(file="docs/research.md", kind="file"),
            ],
            tags=["research"],
        ),
        WikiPage(
            name="Theory-Profile",
            title="Theory Profile",
            pillar="theory",
            order=3,
            generator="theory_profile",
            tags=["theory", "auto"],
        ),
    ]
    manifest = WikiManifest(version=1, pages=pages)
    return manifest


def test_compose_all_produces_home(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    pages = compose_all_pages(manifest, str(tmp_path))

    names = [p.name for p in pages]
    assert "Home" in names
    assert names[0] == "Home"  # Home is first


def test_compose_all_page_count(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    pages = compose_all_pages(manifest, str(tmp_path))

    # 4 declared pages + 1 Home = 5
    assert len(pages) == 5


def test_frontmatter_theory_scope_false(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    pages = compose_all_pages(manifest, str(tmp_path))

    core = next(p for p in pages if p.name == "Theory-Core")
    assert "theory_scope: false" in core.content


def test_frontmatter_theory_scope_true(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    # Promote one page
    manifest.pages[0].theory_scope = True

    pages = compose_all_pages(manifest, str(tmp_path))
    core = next(p for p in pages if p.name == "Theory-Core")
    assert "theory_scope: true" in core.content


def test_section_content_extracted(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    pages = compose_all_pages(manifest, str(tmp_path))

    core = next(p for p in pages if p.name == "Theory-Core")
    assert "Overview content." in core.content


def test_file_content_extracted(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    pages = compose_all_pages(manifest, str(tmp_path))

    research = next(p for p in pages if p.name == "Theory-Research")
    assert "Research file content." in research.content


def test_cross_links_inferred(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    pages = compose_all_pages(manifest, str(tmp_path))

    # Theory-Core (tags: drift) and Arch-Pipeline (tags: drift) should cross-link
    core = next(p for p in pages if p.name == "Theory-Core")
    assert "Arch-Pipeline" in core.content
    assert "See Also" in core.content


def test_no_composer_breadcrumb(tmp_path):
    """Composer should NOT add breadcrumb — transforms layer handles it."""
    manifest = _make_manifest_and_sources(tmp_path)
    pages = compose_all_pages(manifest, str(tmp_path))

    core = next(p for p in pages if p.name == "Theory-Core")
    # Breadcrumb is added by apply_common_transforms in the publisher,
    # not by the composer. Verify no duplicate breadcrumb line.
    assert "**Theory** |" not in core.content


def test_managed_section_markers(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    pages = compose_all_pages(manifest, str(tmp_path))

    core = next(p for p in pages if p.name == "Theory-Core")
    assert "<!-- LINTGATE_WIKI:BEGIN" in core.content
    assert "<!-- LINTGATE_WIKI:END" in core.content


def test_source_attribution(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    pages = compose_all_pages(manifest, str(tmp_path))

    core = next(p for p in pages if p.name == "Theory-Core")
    assert "Sources:" in core.content
    assert "docs/design.md" in core.content


def test_home_page_navigation(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    pages = compose_all_pages(manifest, str(tmp_path))

    home = next(p for p in pages if p.name == "Home")
    assert "## Theory" in home.content
    assert "## Architecture" in home.content
    assert "[Theory-Core]" in home.content
    assert "[Arch-Pipeline]" in home.content


def test_generated_page_theory_profile(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    theory = {
        "core_theory": [
            {
                "heading": "Core",
                "source": "design.md:1",
                "claims": [{"text": "Drift is the enemy"}],
            }
        ]
    }
    pages = compose_all_pages(manifest, str(tmp_path), theory=theory)

    profile = next(p for p in pages if p.name == "Theory-Profile")
    assert "Drift is the enemy" in profile.content


def test_generated_page_no_theory(tmp_path):
    manifest = _make_manifest_and_sources(tmp_path)
    pages = compose_all_pages(manifest, str(tmp_path))

    profile = next(p for p in pages if p.name == "Theory-Profile")
    assert "No theory profile available" in profile.content


def test_missing_section_placeholder(tmp_path):
    """Missing required section gets a placeholder."""
    manifest = _make_manifest_and_sources(tmp_path)
    # Add a source ref to a nonexistent section
    manifest.pages[0].sources.append(
        SourceRef(file="docs/design.md", kind="section", heading="Nonexistent", level=2)
    )

    pages = compose_all_pages(manifest, str(tmp_path))
    core = next(p for p in pages if p.name == "Theory-Core")
    assert "Missing section" in core.content


def test_home_page_dynamic_pillars(tmp_path):
    """Home page discovers all pillars dynamically, not just theory/architecture."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "ops.md").write_text("## Config\n\nConfig content.\n")

    pages = [
        WikiPage(
            name="Ops-Config",
            title="Configuration",
            pillar="ops",
            order=1,
            sources=[SourceRef(file="docs/ops.md", kind="section", heading="Config", level=2)],
            tags=["config"],
        ),
    ]
    manifest = WikiManifest(version=1, pages=pages)
    composed = compose_all_pages(manifest, str(tmp_path))

    home = next(p for p in composed if p.name == "Home")
    assert "## Ops" in home.content
    assert "[Ops-Config]" in home.content


def test_partial_regen_preserves_freshness(tmp_path):
    """Materializing a subset of pages must not wipe freshness for other pages."""
    from lintgate.wiki.freshness import (
        WikiFreshnessState,
        build_page_freshness,
        load_freshness_state,
        save_freshness_state,
    )

    # Seed state with a pre-existing page
    state = WikiFreshnessState()
    state.pages["Other-Page"] = build_page_freshness(
        "Other-Page", {"f::h": "content"}, "mh", "page"
    )
    save_freshness_state(str(tmp_path), state)

    # Now load and merge (simulating what wiki_materialize does after fix)
    loaded = load_freshness_state(str(tmp_path))
    loaded.pages["New-Page"] = build_page_freshness("New-Page", {"g::h": "other"}, "mh2", "page2")
    save_freshness_state(str(tmp_path), loaded)

    # Both pages should be present
    final = load_freshness_state(str(tmp_path))
    assert "Other-Page" in final.pages
    assert "New-Page" in final.pages
