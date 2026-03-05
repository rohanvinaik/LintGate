"""Tests for wiki freshness tracking."""

from __future__ import annotations

from lintgate.wiki.freshness import (
    GENERATOR_VERSION,
    PageFreshnessState,
    WikiFreshnessState,
    build_page_freshness,
    check_page_staleness,
    content_hash,
    load_freshness_state,
    save_freshness_state,
)


def test_content_hash_deterministic():
    h1 = content_hash("hello world")
    h2 = content_hash("hello world")
    assert h1 == h2
    assert len(h1) == 16


def test_content_hash_differs():
    h1 = content_hash("hello")
    h2 = content_hash("world")
    assert h1 != h2


def test_build_page_freshness():
    state = build_page_freshness(
        "Test-Page",
        {"docs/a.md::Overview": "content here"},
        "manifest123",
        "full page content",
    )
    assert state.page_name == "Test-Page"
    assert state.manifest_hash == "manifest123"
    assert state.generator_version == GENERATOR_VERSION
    assert "docs/a.md::Overview" in state.section_hashes
    assert state.generated_at > 0


def test_check_staleness_no_previous():
    current = build_page_freshness("P", {"a::b": "content"}, "mh", "page")
    result = check_page_staleness(current, None)
    assert result["stale"] is True
    assert "no previous state" in result["reasons"]


def test_check_staleness_fresh():
    state = build_page_freshness("P", {"a::b": "content"}, "mh", "page")
    result = check_page_staleness(state, state)
    assert result["stale"] is False
    assert result["reasons"] == []
    assert result["changed_sections"] == []


def test_check_staleness_section_changed():
    old = build_page_freshness("P", {"a::b": "old content"}, "mh", "page")
    new = build_page_freshness("P", {"a::b": "new content"}, "mh", "page")
    result = check_page_staleness(new, old)
    assert result["stale"] is True
    assert "a::b" in result["changed_sections"]


def test_check_staleness_unrelated_section_no_change():
    """Changing a section not tracked by this page doesn't trigger staleness."""
    state = build_page_freshness("P", {"a::Overview": "content"}, "mh", "page")
    # Same section hashes — page stays fresh
    result = check_page_staleness(state, state)
    assert result["stale"] is False


def test_check_staleness_manifest_changed():
    old = build_page_freshness("P", {"a::b": "content"}, "old_hash", "page")
    new = build_page_freshness("P", {"a::b": "content"}, "new_hash", "page")
    result = check_page_staleness(new, old)
    assert result["stale"] is True
    assert any("manifest" in r for r in result["reasons"])


def test_check_staleness_generator_version():
    old = PageFreshnessState(
        page_name="P",
        section_hashes={"a::b": "abc"},
        manifest_hash="mh",
        generator_version="0",
        page_content_hash="ph",
        generated_at=1.0,
    )
    new = PageFreshnessState(
        page_name="P",
        section_hashes={"a::b": "abc"},
        manifest_hash="mh",
        generator_version="1",
        page_content_hash="ph",
        generated_at=2.0,
    )
    result = check_page_staleness(new, old)
    assert result["stale"] is True
    assert any("generator version" in r for r in result["reasons"])


def test_check_staleness_new_section():
    old = build_page_freshness("P", {"a::b": "content"}, "mh", "page")
    new = build_page_freshness("P", {"a::b": "content", "a::c": "extra"}, "mh", "page")
    result = check_page_staleness(new, old)
    assert result["stale"] is True
    assert "a::c" in result["changed_sections"]


def test_save_and_load_state(tmp_path):
    wiki_dir = tmp_path / ".lintgate" / "wiki"
    wiki_dir.mkdir(parents=True)

    state = WikiFreshnessState()
    state.pages["TestPage"] = build_page_freshness(
        "TestPage", {"f::h": "content"}, "mh", "page"
    )

    save_freshness_state(str(tmp_path), state)

    loaded = load_freshness_state(str(tmp_path))
    assert "TestPage" in loaded.pages
    assert loaded.pages["TestPage"].manifest_hash == "mh"
    assert loaded.pages["TestPage"].section_hashes == state.pages["TestPage"].section_hashes


def test_load_state_missing(tmp_path):
    state = load_freshness_state(str(tmp_path))
    assert state.pages == {}


def test_load_state_corrupt(tmp_path):
    wiki_dir = tmp_path / ".lintgate" / "wiki"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "_wiki_state.json").write_text("not json{{{")

    state = load_freshness_state(str(tmp_path))
    assert state.pages == {}
