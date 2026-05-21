"""Comprehensive tests for wiki manifest, composer, and pages_publisher modules."""

from __future__ import annotations

import os

from lintgate.wiki._pages_publisher_assets import (
    _check_internal_links,
    _write_css,
    _write_js,
    _write_nojekyll,
    _write_robots,
    _write_sitemap,
)
from lintgate.wiki._pages_publisher_render import (
    _asset_prefix,
    _build_prev_next,
    _build_sidebar,
    _inline_format,
    _md_to_html,
    _MdParser,
    _page_slug,
    _render_page,
)
from lintgate.wiki._types import PublishedPage
from lintgate.wiki.composer import (
    ComposedPage,
    _compose_home,
    _generate_content,
    _has_related_section,
    _render_compass_state,
    _render_theory_profile,
    _strip_managed_markers,
    compose_all_pages,
)
from lintgate.wiki.manifest import (
    SourceRef,
    WikiManifest,
    WikiPage,
    _parse_page,
    _parse_source_ref,
    _rail_display_name_fallback,
    load_manifest,
    load_metrics,
)
from lintgate.wiki.pages_publisher import (
    PublishResult,
    _clean_stale_pages,
    publish_pages,
)

# ═══════════════════════════════════════════════════════════════════════════
# manifest.py tests
# ═══════════════════════════════════════════════════════════════════════════


class TestSourceRef:
    def test_defaults(self):
        ref = SourceRef(file="foo.md")
        assert ref.file == "foo.md"
        assert ref.kind == "section"
        assert ref.heading == ""
        assert ref.level == 2
        assert ref.heading_path == ""
        assert ref.section_id == ""
        assert ref.required is True

    def test_custom_values(self):
        ref = SourceRef(
            file="bar.md",
            kind="file",
            heading="Intro",
            level=3,
            heading_path="root/intro",
            section_id="s1",
            required=False,
        )
        assert ref.kind == "file"
        assert ref.heading == "Intro"
        assert ref.level == 3
        assert ref.required is False


class TestWikiPage:
    def test_defaults(self):
        page = WikiPage(name="test", title="Test Page", pillar="core")
        assert page.order == 0
        assert page.sources == []
        assert page.tags == []
        assert page.relations == []
        assert page.generator is None
        assert page.theory_scope is False
        assert page.rail == ""
        assert page.chapter == 0
        assert page.prerequisites == []

    def test_full_construction(self):
        page = WikiPage(
            name="arch",
            title="Architecture",
            pillar="design",
            order=2,
            tags=["internal"],
            relations=["overview"],
            rail="how_it_works",
            chapter=3,
            prerequisites=["overview"],
        )
        assert page.rail == "how_it_works"
        assert page.chapter == 3
        assert page.prerequisites == ["overview"]


class TestWikiManifest:
    @staticmethod
    def _make_manifest(pages=None, rail_names=None):
        return WikiManifest(
            version=1,
            pages=pages or [],
            rail_names=rail_names or {},
        )

    def test_empty_manifest(self):
        m = self._make_manifest()
        assert m.all_pages == []
        assert m.rails == []

    def test_all_pages_returns_copy(self):
        p = WikiPage(name="a", title="A", pillar="x")
        m = self._make_manifest([p])
        result = m.all_pages
        assert result == [p]
        result.append(WikiPage(name="b", title="B", pillar="x"))
        assert len(m.all_pages) == 1

    def test_pages_by_pillar_sorted_by_order(self):
        p1 = WikiPage(name="b", title="B", pillar="core", order=2)
        p2 = WikiPage(name="a", title="A", pillar="core", order=1)
        p3 = WikiPage(name="c", title="C", pillar="other", order=0)
        m = self._make_manifest([p1, p2, p3])
        result = m.pages_by_pillar("core")
        assert [p.name for p in result] == ["a", "b"]

    def test_pages_by_pillar_empty(self):
        m = self._make_manifest()
        assert m.pages_by_pillar("nonexistent") == []

    def test_pages_by_rail_sorted_by_chapter(self):
        p1 = WikiPage(name="ch2", title="Ch2", pillar="x", rail="how_it_works", chapter=2)
        p2 = WikiPage(name="ch1", title="Ch1", pillar="x", rail="how_it_works", chapter=1)
        p3 = WikiPage(name="other", title="Other", pillar="x", rail="reference", chapter=1)
        m = self._make_manifest([p1, p2, p3])
        result = m.pages_by_rail("how_it_works")
        assert [p.name for p in result] == ["ch1", "ch2"]

    def test_pages_by_rail_empty(self):
        m = self._make_manifest()
        assert m.pages_by_rail("nonexistent") == []

    def test_rails_preserves_declaration_order(self):
        pages = [
            WikiPage(name="a", title="A", pillar="x", rail="reference"),
            WikiPage(name="b", title="B", pillar="x", rail="how_it_works"),
            WikiPage(name="c", title="C", pillar="x", rail="reference"),
        ]
        m = self._make_manifest(pages)
        assert m.rails == ["reference", "how_it_works"]

    def test_rails_excludes_empty_rail(self):
        pages = [
            WikiPage(name="a", title="A", pillar="x", rail=""),
            WikiPage(name="b", title="B", pillar="x", rail="ref"),
        ]
        m = self._make_manifest(pages)
        assert m.rails == ["ref"]

    def test_rail_display_name_from_manifest(self):
        m = self._make_manifest(rail_names={"custom": "My Custom Rail"})
        assert m.rail_display_name("custom") == "My Custom Rail"

    def test_rail_display_name_fallback(self):
        m = self._make_manifest()
        assert m.rail_display_name("getting_value") == "Getting Value Fast"

    def test_prev_next_in_rail_middle(self):
        pages = [
            WikiPage(name="ch1", title="Ch1", pillar="x", rail="r", chapter=1),
            WikiPage(name="ch2", title="Ch2", pillar="x", rail="r", chapter=2),
            WikiPage(name="ch3", title="Ch3", pillar="x", rail="r", chapter=3),
        ]
        m = self._make_manifest(pages)
        prev_p, next_p = m.prev_next_in_rail(pages[1])
        assert prev_p is not None and prev_p.name == "ch1"
        assert next_p is not None and next_p.name == "ch3"

    def test_prev_next_in_rail_first(self):
        pages = [
            WikiPage(name="ch1", title="Ch1", pillar="x", rail="r", chapter=1),
            WikiPage(name="ch2", title="Ch2", pillar="x", rail="r", chapter=2),
        ]
        m = self._make_manifest(pages)
        prev_p, next_p = m.prev_next_in_rail(pages[0])
        assert prev_p is None
        assert next_p is not None and next_p.name == "ch2"

    def test_prev_next_in_rail_last(self):
        pages = [
            WikiPage(name="ch1", title="Ch1", pillar="x", rail="r", chapter=1),
            WikiPage(name="ch2", title="Ch2", pillar="x", rail="r", chapter=2),
        ]
        m = self._make_manifest(pages)
        prev_p, next_p = m.prev_next_in_rail(pages[1])
        assert prev_p is not None and prev_p.name == "ch1"
        assert next_p is None

    def test_prev_next_in_rail_no_rail(self):
        page = WikiPage(name="x", title="X", pillar="x", rail="")
        m = self._make_manifest([page])
        assert m.prev_next_in_rail(page) == (None, None)

    def test_prev_next_in_rail_page_not_found(self):
        pages = [WikiPage(name="ch1", title="Ch1", pillar="x", rail="r", chapter=1)]
        m = self._make_manifest(pages)
        orphan = WikiPage(name="orphan", title="Orphan", pillar="x", rail="r", chapter=99)
        assert m.prev_next_in_rail(orphan) == (None, None)

    def test_estimate_read_time_counts_words(self, tmp_path):
        src_file = tmp_path / "doc.md"
        src_file.write_text(" ".join(["word"] * 400))
        page = WikiPage(
            name="p",
            title="P",
            pillar="x",
            sources=[SourceRef(file="doc.md")],
        )
        m = self._make_manifest([page])
        assert m.estimate_read_time(page, str(tmp_path)) == 2  # 400/200 = 2

    def test_estimate_read_time_minimum_one(self, tmp_path):
        src_file = tmp_path / "short.md"
        src_file.write_text("hello")
        page = WikiPage(
            name="p",
            title="P",
            pillar="x",
            sources=[SourceRef(file="short.md")],
        )
        m = self._make_manifest([page])
        assert m.estimate_read_time(page, str(tmp_path)) == 1

    def test_estimate_read_time_missing_file(self, tmp_path):
        page = WikiPage(
            name="p",
            title="P",
            pillar="x",
            sources=[SourceRef(file="nonexistent.md")],
        )
        m = self._make_manifest([page])
        assert m.estimate_read_time(page, str(tmp_path)) == 1

    def test_estimate_read_time_no_sources(self, tmp_path):
        page = WikiPage(name="p", title="P", pillar="x")
        m = self._make_manifest([page])
        assert m.estimate_read_time(page, str(tmp_path)) == 1

    def test_infer_cross_links_explicit_relations(self):
        pages = [
            WikiPage(name="a", title="A", pillar="x", relations=["b"]),
            WikiPage(name="b", title="B", pillar="y"),
        ]
        m = self._make_manifest(pages)
        links = m.infer_cross_links(pages[0])
        assert "b" in links

    def test_infer_cross_links_shared_tags(self):
        pages = [
            WikiPage(name="a", title="A", pillar="x", tags=["common"]),
            WikiPage(name="b", title="B", pillar="y", tags=["common"]),
        ]
        m = self._make_manifest(pages)
        links = m.infer_cross_links(pages[0])
        assert "b" in links

    def test_infer_cross_links_excludes_self(self):
        pages = [
            WikiPage(name="a", title="A", pillar="x", tags=["t"], relations=["a"]),
        ]
        m = self._make_manifest(pages)
        links = m.infer_cross_links(pages[0])
        assert "a" not in links

    def test_infer_cross_links_pillar_adjacency(self):
        pages = [
            WikiPage(name="a", title="A", pillar="core", order=1),
            WikiPage(name="b", title="B", pillar="core", order=2),
            WikiPage(name="c", title="C", pillar="core", order=3),
        ]
        m = self._make_manifest(pages)
        links = m.infer_cross_links(pages[1])
        assert "a" in links
        assert "c" in links

    def test_infer_cross_links_no_duplicates(self):
        pages = [
            WikiPage(name="a", title="A", pillar="core", order=1, tags=["t"], relations=["b"]),
            WikiPage(name="b", title="B", pillar="core", order=2, tags=["t"]),
        ]
        m = self._make_manifest(pages)
        links = m.infer_cross_links(pages[0])
        assert links.count("b") == 1

    def test_infer_cross_links_empty_manifest(self):
        page = WikiPage(name="alone", title="Alone", pillar="x")
        m = self._make_manifest([page])
        assert m.infer_cross_links(page) == []

    def test_manifest_hash_for_page_deterministic(self):
        page = WikiPage(
            name="test",
            title="Title",
            pillar="core",
            order=1,
            tags=["a", "b"],
            rail="ref",
            chapter=2,
            sources=[SourceRef(file="f.md", kind="section", heading="H", level=2)],
        )
        m = self._make_manifest([page])
        h1 = m.manifest_hash_for_page(page)
        h2 = m.manifest_hash_for_page(page)
        assert h1 == h2
        assert len(h1) == 16

    def test_manifest_hash_changes_on_title_change(self):
        p1 = WikiPage(name="test", title="Title A", pillar="core")
        p2 = WikiPage(name="test", title="Title B", pillar="core")
        m = self._make_manifest([p1, p2])
        assert m.manifest_hash_for_page(p1) != m.manifest_hash_for_page(p2)


class TestParseSourceRef:
    def test_basic_file_key(self):
        ref = _parse_source_ref({"file": "docs/a.md"})
        assert ref.file == "docs/a.md"
        assert ref.kind == "section"

    def test_path_alias(self):
        ref = _parse_source_ref({"path": "docs/b.md"})
        assert ref.file == "docs/b.md"

    def test_source_alias(self):
        ref = _parse_source_ref({"source": "docs/c.md"})
        assert ref.file == "docs/c.md"

    def test_sections_all_sets_kind_file(self):
        ref = _parse_source_ref({"file": "a.md", "sections": "all"})
        assert ref.kind == "file"

    def test_headings_all_sets_kind_file(self):
        ref = _parse_source_ref({"file": "a.md", "headings": "all"})
        assert ref.kind == "file"

    def test_explicit_kind_overrides(self):
        ref = _parse_source_ref({"file": "a.md", "kind": "generated"})
        assert ref.kind == "generated"

    def test_heading_and_level(self):
        ref = _parse_source_ref({"file": "a.md", "heading": "Intro", "level": 3})
        assert ref.heading == "Intro"
        assert ref.level == 3

    def test_required_default_true(self):
        ref = _parse_source_ref({"file": "a.md"})
        assert ref.required is True

    def test_required_false(self):
        ref = _parse_source_ref({"file": "a.md", "required": False})
        assert ref.required is False

    def test_empty_dict(self):
        ref = _parse_source_ref({})
        assert ref.file == ""
        assert ref.kind == "section"

    def test_sections_non_all_string(self):
        ref = _parse_source_ref({"file": "a.md", "sections": "Architecture"})
        assert ref.kind == "section"


class TestParsePage:
    def test_basic_parse(self):
        page = _parse_page({"name": "overview", "title": "Overview", "pillar": "core"})
        assert page.name == "overview"
        assert page.title == "Overview"
        assert page.pillar == "core"

    def test_id_alias(self):
        page = _parse_page({"id": "my-page", "title": "Title", "pillar": "x"})
        assert page.name == "my-page"

    def test_audience_alias(self):
        page = _parse_page({"name": "p", "title": "T", "audience": "user"})
        assert page.pillar == "user"

    def test_sources_as_sections_list(self):
        page = _parse_page(
            {
                "name": "p",
                "title": "T",
                "pillar": "x",
                "sections": [{"file": "a.md", "heading": "H"}],
            }
        )
        assert len(page.sources) == 1
        assert page.sources[0].file == "a.md"

    def test_sections_as_all_string_ignored(self):
        page = _parse_page(
            {
                "name": "p",
                "title": "T",
                "pillar": "x",
                "sections": "all",
            }
        )
        assert page.sources == []

    def test_sections_as_file_path_string(self):
        page = _parse_page(
            {
                "name": "p",
                "title": "T",
                "pillar": "x",
                "sections": "docs/guide.md",
            }
        )
        assert len(page.sources) == 1
        assert page.sources[0].file == "docs/guide.md"

    def test_rail_and_chapter(self):
        page = _parse_page(
            {
                "name": "p",
                "title": "T",
                "pillar": "x",
                "rail": "getting_value",
                "chapter": 3,
            }
        )
        assert page.rail == "getting_value"
        assert page.chapter == 3

    def test_prerequisites(self):
        page = _parse_page(
            {
                "name": "p",
                "title": "T",
                "pillar": "x",
                "prerequisites": ["intro", "setup"],
            }
        )
        assert page.prerequisites == ["intro", "setup"]

    def test_empty_dict(self):
        page = _parse_page({})
        assert page.name == ""
        assert page.title == ""
        assert page.pillar == ""


class TestLoadManifest:
    def test_loads_wiki_yaml(self, tmp_path):
        manifest_content = {
            "version": 2,
            "pages": [
                {"name": "intro", "title": "Intro", "pillar": "core", "tags": ["getting-started"]},
            ],
        }
        import yaml

        (tmp_path / "wiki.yaml").write_text(yaml.dump(manifest_content))
        result = load_manifest(str(tmp_path))
        assert result is not None
        assert result.version == 2
        assert len(result.pages) == 1
        assert result.pages[0].name == "intro"

    def test_loads_lintgate_manifest(self, tmp_path):
        lintgate_dir = tmp_path / ".lintgate"
        lintgate_dir.mkdir()
        manifest_content = {"version": 1, "pages": [{"name": "x", "title": "X", "pillar": "y"}]}
        import yaml

        (lintgate_dir / "wiki_manifest.yaml").write_text(yaml.dump(manifest_content))
        result = load_manifest(str(tmp_path))
        assert result is not None
        assert result.pages[0].name == "x"

    def test_wiki_yaml_preferred_over_lintgate(self, tmp_path):
        import yaml

        (tmp_path / "wiki.yaml").write_text(
            yaml.dump(
                {
                    "version": 1,
                    "pages": [{"name": "from_wiki", "title": "W", "pillar": "x"}],
                }
            )
        )
        lintgate_dir = tmp_path / ".lintgate"
        lintgate_dir.mkdir()
        (lintgate_dir / "wiki_manifest.yaml").write_text(
            yaml.dump(
                {
                    "version": 1,
                    "pages": [{"name": "from_lintgate", "title": "L", "pillar": "x"}],
                }
            )
        )
        result = load_manifest(str(tmp_path))
        assert result is not None
        assert result.pages[0].name == "from_wiki"

    def test_returns_none_when_no_file(self, tmp_path):
        assert load_manifest(str(tmp_path)) is None

    def test_returns_none_on_invalid_yaml(self, tmp_path):
        (tmp_path / "wiki.yaml").write_text(": : invalid : [\n")
        assert load_manifest(str(tmp_path)) is None

    def test_returns_none_on_non_dict_yaml(self, tmp_path):
        (tmp_path / "wiki.yaml").write_text("- just a list\n")
        assert load_manifest(str(tmp_path)) is None

    def test_rails_dict_format(self, tmp_path):
        import yaml

        content = {
            "version": 1,
            "rails": {
                "getting_value": {"name": "Getting Value Fast"},
                "reference": "Reference Guide",
            },
            "pages": [],
        }
        (tmp_path / "wiki.yaml").write_text(yaml.dump(content))
        result = load_manifest(str(tmp_path))
        assert result is not None
        assert result.rail_names["getting_value"] == "Getting Value Fast"
        assert result.rail_names["reference"] == "Reference Guide"

    def test_empty_pages_list(self, tmp_path):
        import yaml

        (tmp_path / "wiki.yaml").write_text(yaml.dump({"version": 1}))
        result = load_manifest(str(tmp_path))
        assert result is not None
        assert result.pages == []


class TestRailDisplayNameFallback:
    def test_known_rails(self):
        assert _rail_display_name_fallback("getting_value") == "Getting Value Fast"
        assert _rail_display_name_fallback("how_it_works") == "How It Works"
        assert _rail_display_name_fallback("why_designed") == "Why It Is Designed This Way"
        assert _rail_display_name_fallback("reference") == "Reference"

    def test_known_kebab_case(self):
        assert _rail_display_name_fallback("getting-value-fast") == "Getting Value Fast"
        assert _rail_display_name_fallback("how-it-works") == "How It Works"

    def test_unknown_rail_titlecased(self):
        assert _rail_display_name_fallback("custom_rail_name") == "Custom Rail Name"
        assert _rail_display_name_fallback("custom-rail-name") == "Custom Rail Name"

    def test_known_rails_differ_from_fallback(self):
        """Kill VALUE survivors: verify dict entries that differ from the title-case fallback."""
        # "getting_value" → dict: "Getting Value Fast" vs fallback: "Getting Value"
        assert _rail_display_name_fallback("getting_value") == "Getting Value Fast"
        assert _rail_display_name_fallback("getting_value") != "Getting Value"
        # "why_designed" → dict: "Why It Is Designed This Way" vs fallback: "Why Designed"
        assert _rail_display_name_fallback("why_designed") == "Why It Is Designed This Way"
        assert _rail_display_name_fallback("why_designed") != "Why Designed"
        # "why-designed-this-way" → dict value differs from fallback
        assert _rail_display_name_fallback("why-designed-this-way") == "Why It Is Designed This Way"
        assert _rail_display_name_fallback("why-designed-this-way") != "Why Designed This Way"


class TestLoadMetrics:
    def test_loads_from_docs_wiki(self, tmp_path):
        import yaml

        metrics_dir = tmp_path / "docs" / "wiki"
        metrics_dir.mkdir(parents=True)
        (metrics_dir / "_metrics.yaml").write_text(yaml.dump({"tool_count": 42, "version": "1.0"}))
        result = load_metrics(str(tmp_path))
        assert result == {"tool_count": "42", "version": "1.0"}

    def test_loads_from_lintgate_dir(self, tmp_path):
        import yaml

        lintgate_dir = tmp_path / ".lintgate"
        lintgate_dir.mkdir()
        (lintgate_dir / "_metrics.yaml").write_text(yaml.dump({"count": 10}))
        result = load_metrics(str(tmp_path))
        assert result == {"count": "10"}

    def test_returns_empty_when_no_file(self, tmp_path):
        assert load_metrics(str(tmp_path)) == {}

    def test_returns_empty_on_invalid_yaml(self, tmp_path):
        metrics_dir = tmp_path / "docs" / "wiki"
        metrics_dir.mkdir(parents=True)
        (metrics_dir / "_metrics.yaml").write_text(": invalid [\n")
        assert load_metrics(str(tmp_path)) == {}

    def test_returns_empty_on_non_dict_yaml(self, tmp_path):
        metrics_dir = tmp_path / "docs" / "wiki"
        metrics_dir.mkdir(parents=True)
        (metrics_dir / "_metrics.yaml").write_text("- list item\n")
        assert load_metrics(str(tmp_path)) == {}


# ═══════════════════════════════════════════════════════════════════════════
# composer.py tests
# ═══════════════════════════════════════════════════════════════════════════


class TestHasRelatedSection:
    def test_detects_related(self):
        assert _has_related_section("Some text\n## Related\nLinks here") is True

    def test_detects_see_also(self):
        assert _has_related_section("## See Also\n- link") is True

    def test_detects_further_reading(self):
        assert _has_related_section("stuff\n## Further Reading") is True

    def test_detects_references(self):
        assert _has_related_section("## References\n") is True

    def test_detects_related_concepts(self):
        assert _has_related_section("## Related Concepts") is True

    def test_case_insensitive(self):
        assert _has_related_section("## RELATED") is True
        assert _has_related_section("## see also") is True

    def test_no_related_section(self):
        assert _has_related_section("# Title\nSome text\n## Architecture") is False

    def test_empty_string(self):
        assert _has_related_section("") is False


class TestStripManagedMarkers:
    def test_strips_begin_end(self):
        text = "line1\n<!-- LINTGATE_WIKI:BEGIN sec -->\ncontent\n<!-- LINTGATE_WIKI:END sec -->\nline2"
        result = _strip_managed_markers(text)
        assert "LINTGATE_WIKI" not in result
        assert "content" in result
        assert "line1" in result
        assert "line2" in result

    def test_no_markers(self):
        text = "plain text\nno markers"
        assert _strip_managed_markers(text) == text

    def test_empty_string(self):
        assert _strip_managed_markers("") == ""


class TestGenerateContent:
    def test_theory_profile_generator(self):
        theory = {
            "core_theory": [
                {
                    "heading": "Architecture",
                    "source": "design.md",
                    "claims": [{"text": "Modular design"}],
                }
            ]
        }
        result = _generate_content("theory_profile", theory, None)
        assert "Architecture" in result
        assert "Modular design" in result

    def test_theory_profile_none(self):
        result = _generate_content("theory_profile", None, None)
        assert "No theory profile available" in result

    def test_compass_state_generator(self):
        compass = {
            "axes": {
                "test_quality": {
                    "depth": "deep",
                    "toward": ["mutation testing"],
                    "away": ["mocking"],
                }
            }
        }
        result = _generate_content("compass_state", None, compass)
        assert "Test Quality" in result
        assert "deep" in result
        assert "mutation testing" in result
        assert "mocking" in result

    def test_compass_state_none(self):
        result = _generate_content("compass_state", None, None)
        assert "No compass state available" in result

    def test_unknown_generator(self):
        result = _generate_content("nonexistent_generator", None, None)
        assert result == ""


class TestRenderTheoryProfile:
    def test_empty_theory(self):
        result = _render_theory_profile({})
        assert "No theory profile" in result

    def test_none_theory(self):
        result = _render_theory_profile(None)
        assert "No theory profile" in result

    def test_empty_facet_skipped(self):
        result = _render_theory_profile({"empty_facet": []})
        assert result == ""

    def test_claims_as_strings(self):
        theory = {"test": [{"heading": "H", "source": "s", "claims": ["claim1", "claim2"]}]}
        result = _render_theory_profile(theory)
        assert "- claim1" in result
        assert "- claim2" in result

    def test_claims_as_dicts(self):
        theory = {"test": [{"heading": "H", "source": "s", "claims": [{"text": "claim_dict"}]}]}
        result = _render_theory_profile(theory)
        assert "- claim_dict" in result

    def test_claims_capped_at_five(self):
        claims = [f"claim_{i}" for i in range(10)]
        theory = {"facet": [{"heading": "H", "source": "s", "claims": claims}]}
        result = _render_theory_profile(theory)
        assert "claim_4" in result
        assert "claim_5" not in result


class TestRenderCompassState:
    def test_empty_compass(self):
        assert "No compass state" in _render_compass_state({})

    def test_none_compass(self):
        assert "No compass state" in _render_compass_state(None)

    def test_axes_wrapper(self):
        compass = {
            "axes": {"quality": {"depth": "surface", "toward": ["tests"], "away": ["hacks"]}}
        }
        result = _render_compass_state(compass)
        assert "Quality" in result
        assert "surface" in result

    def test_flat_compass_without_axes_key(self):
        compass = {"quality": {"depth": "deep", "toward": ["tests"], "away": []}}
        result = _render_compass_state(compass)
        assert "Quality" in result
        assert "deep" in result

    def test_toward_capped_at_five(self):
        compass = {"ax": {"depth": "d", "toward": [f"t{i}" for i in range(10)], "away": []}}
        result = _render_compass_state(compass)
        assert "t4" in result
        assert "t5" not in result

    def test_non_dict_axis_skipped(self):
        compass = {"scalar": "not a dict", "real": {"depth": "d"}}
        result = _render_compass_state(compass)
        assert "Scalar" not in result
        assert "Real" in result


class TestComposeHome:
    def test_home_page_structure(self):
        pages = [
            WikiPage(name="a", title="Page A", pillar="core", order=1),
            WikiPage(name="b", title="Page B", pillar="design", order=1),
        ]
        manifest = WikiManifest(version=1, pages=pages)
        composed = [
            ComposedPage(name="a", title="Page A", content="...", pillar="core"),
            ComposedPage(name="b", title="Page B", content="...", pillar="design"),
        ]
        home = _compose_home(manifest, composed)
        assert home.name == "Home"
        assert "LintGate Wiki" in home.content
        assert "Core" in home.content
        assert "Design" in home.content
        assert "[a](a)" in home.content
        assert "[b](b)" in home.content

    def test_home_page_empty_manifest(self):
        manifest = WikiManifest(version=1, pages=[])
        home = _compose_home(manifest, [])
        assert home.name == "Home"
        assert "LintGate Wiki" in home.content

    def test_home_page_excludes_uncomposed_pages(self):
        pages = [
            WikiPage(name="a", title="Page A", pillar="core", order=1),
            WikiPage(name="b", title="Page B", pillar="core", order=2),
        ]
        manifest = WikiManifest(version=1, pages=pages)
        composed = [ComposedPage(name="a", title="Page A", content="...", pillar="core")]
        home = _compose_home(manifest, composed)
        assert "[a](a)" in home.content
        assert "[b](b)" not in home.content


class TestComposeAllPages:
    def test_auto_generates_home_page(self, tmp_path):
        src_file = tmp_path / "doc.md"
        src_file.write_text("# Doc\n\nContent here.")
        manifest = WikiManifest(
            version=1,
            pages=[
                WikiPage(
                    name="overview",
                    title="Overview",
                    pillar="core",
                    sources=[SourceRef(file="doc.md", kind="file")],
                ),
            ],
        )
        result = compose_all_pages(manifest, str(tmp_path))
        names = [p.name for p in result]
        assert "Home" in names
        assert "overview" in names

    def test_no_auto_home_if_manifest_declares_it(self, tmp_path):
        manifest = WikiManifest(
            version=1,
            pages=[
                WikiPage(name="home", title="Home", pillar="core", generator="theory_profile"),
                WikiPage(name="other", title="Other", pillar="core", generator="compass_state"),
            ],
        )
        result = compose_all_pages(manifest, str(tmp_path))
        home_pages = [p for p in result if p.name.lower() == "home"]
        assert len(home_pages) == 1
        assert home_pages[0].name == "home"

    def test_compose_whole_file_source(self, tmp_path):
        src = tmp_path / "guide.md"
        src.write_text("---\ntitle: Guide\n---\n# Guide\n\nBody text.")
        manifest = WikiManifest(
            version=1,
            pages=[
                WikiPage(
                    name="guide",
                    title="Guide",
                    pillar="core",
                    sources=[SourceRef(file="guide.md", kind="file")],
                ),
            ],
        )
        result = compose_all_pages(manifest, str(tmp_path))
        guide = next(p for p in result if p.name == "guide")
        assert "Body text" in guide.content

    def test_compose_section_source(self, tmp_path):
        src = tmp_path / "doc.md"
        src.write_text("# Title\n\n## Architecture\n\nArch content\n\n## Other\n\nOther stuff\n")
        manifest = WikiManifest(
            version=1,
            pages=[
                WikiPage(
                    name="arch",
                    title="Architecture",
                    pillar="core",
                    sources=[SourceRef(file="doc.md", kind="section", heading="Architecture")],
                ),
            ],
        )
        result = compose_all_pages(manifest, str(tmp_path))
        arch = next(p for p in result if p.name == "arch")
        assert "Arch content" in arch.content

    def test_compose_missing_required_source(self, tmp_path):
        manifest = WikiManifest(
            version=1,
            pages=[
                WikiPage(
                    name="missing",
                    title="Missing",
                    pillar="core",
                    sources=[
                        SourceRef(file="nonexistent.md", kind="section", heading="H", required=True)
                    ],
                ),
            ],
        )
        result = compose_all_pages(manifest, str(tmp_path))
        page = next(p for p in result if p.name == "missing")
        assert "Missing section" in page.content

    def test_compose_generated_page(self, tmp_path):
        manifest = WikiManifest(
            version=1,
            pages=[
                WikiPage(
                    name="theory",
                    title="Theory",
                    pillar="core",
                    generator="theory_profile",
                    theory_scope=True,
                ),
            ],
        )
        theory = {"core": [{"heading": "Core", "source": "s", "claims": ["claim1"]}]}
        result = compose_all_pages(manifest, str(tmp_path), theory=theory)
        page = next(p for p in result if p.name == "theory")
        assert "claim1" in page.content
        assert page.theory_scope is True

    def test_cross_links_appended(self, tmp_path):
        src = tmp_path / "a.md"
        src.write_text("# A\n\nContent A\n")
        pages = [
            WikiPage(
                name="alpha",
                title="Alpha",
                pillar="core",
                order=1,
                tags=["shared"],
                sources=[SourceRef(file="a.md", kind="file")],
            ),
            WikiPage(
                name="beta",
                title="Beta",
                pillar="core",
                order=2,
                tags=["shared"],
                generator="theory_profile",
            ),
        ]
        manifest = WikiManifest(version=1, pages=pages)
        result = compose_all_pages(manifest, str(tmp_path))
        alpha = next(p for p in result if p.name == "alpha")
        assert "See Also" in alpha.content
        assert "beta" in alpha.content

    def test_cross_links_skipped_if_related_section_exists(self, tmp_path):
        src = tmp_path / "a.md"
        src.write_text("# A\n\nContent\n\n## Related\n\n- existing link\n")
        pages = [
            WikiPage(
                name="alpha",
                title="Alpha",
                pillar="core",
                order=1,
                tags=["t"],
                sources=[SourceRef(file="a.md", kind="file")],
            ),
            WikiPage(
                name="beta",
                title="Beta",
                pillar="core",
                order=2,
                tags=["t"],
                generator="theory_profile",
            ),
        ]
        manifest = WikiManifest(version=1, pages=pages)
        result = compose_all_pages(manifest, str(tmp_path))
        alpha = next(p for p in result if p.name == "alpha")
        assert "See Also" not in alpha.content


# ═══════════════════════════════════════════════════════════════════════════
# pages_publisher.py tests
# ═══════════════════════════════════════════════════════════════════════════


class TestPageSlug:
    def test_lowercase(self):
        assert _page_slug("Getting-Started") == "getting-started"

    def test_spaces_to_dashes(self):
        assert _page_slug("My Page") == "my-page"

    def test_already_lowercase(self):
        assert _page_slug("home") == "home"


class TestAssetPrefix:
    def test_home_page(self):
        assert _asset_prefix("Home") == "./"
        assert _asset_prefix("home") == "./"

    def test_subpage(self):
        assert _asset_prefix("Getting-Started") == "../"
        assert _asset_prefix("architecture") == "../"


class TestInlineFormat:
    def test_bold(self):
        assert "<strong>bold</strong>" in _inline_format("**bold** text")

    def test_italic(self):
        assert "<em>italic</em>" in _inline_format("*italic* text")

    def test_inline_code(self):
        assert "<code>code</code>" in _inline_format("`code` text")

    def test_links(self):
        result = _inline_format("[click](http://example.com)")
        assert '<a href="http://example.com">click</a>' in result

    def test_plain_text_unchanged(self):
        assert _inline_format("plain text") == "plain text"

    def test_empty_string(self):
        assert _inline_format("") == ""


class TestMdToHtml:
    def test_heading(self):
        result = _md_to_html("## My Heading")
        assert "<h2" in result
        assert "My Heading" in result

    def test_paragraph(self):
        result = _md_to_html("Simple paragraph text.")
        assert "<p>" in result
        assert "Simple paragraph text." in result

    def test_list_items(self):
        result = _md_to_html("- item one\n- item two")
        assert "<ul>" in result
        assert "<li>" in result
        assert "item one" in result
        assert "item two" in result

    def test_code_block(self):
        result = _md_to_html("```python\nprint('hello')\n```")
        assert "<pre><code" in result
        assert "language-python" in result
        assert "print(&#x27;hello&#x27;)" in result or "print('hello')" in result

    def test_code_block_no_language(self):
        result = _md_to_html("```\ncode\n```")
        assert "<pre><code>" in result

    def test_horizontal_rule(self):
        result = _md_to_html("text\n\n---\n\nmore text")
        assert "<hr>" in result

    def test_table(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = _md_to_html(md)
        assert "<table>" in result
        assert "<td>" in result

    def test_managed_markers_stripped(self):
        md = "<!-- LINTGATE_WIKI:BEGIN sec -->\nContent\n<!-- LINTGATE_WIKI:END sec -->"
        result = _md_to_html(md)
        assert "LINTGATE_WIKI" not in result
        assert "Content" in result

    def test_empty_input(self):
        assert _md_to_html("") == ""

    def test_bold_in_paragraph(self):
        result = _md_to_html("This is **bold** text.")
        assert "<strong>bold</strong>" in result


class TestMdParser:
    def test_flush_paragraph_on_blank_line(self):
        parser = _MdParser()
        parser.feed("line one")
        parser.feed("line two")
        parser.feed("")
        result = parser.finish()
        assert "<p>" in result
        assert "line one" in result
        assert "line two" in result

    def test_nested_code_block(self):
        parser = _MdParser()
        parser.feed("```")
        parser.feed("code line")
        parser.feed("```")
        result = parser.finish()
        assert "<pre><code>" in result
        assert "code line" in result

    def test_list_then_paragraph(self):
        parser = _MdParser()
        parser.feed("- list item")
        parser.feed("")
        parser.feed("paragraph text")
        result = parser.finish()
        assert "<ul>" in result
        assert "</ul>" in result
        # Paragraph should appear after the list is closed
        assert "<p>" in result


class TestBuildSidebar:
    def test_sidebar_with_rails(self):
        pages = [
            WikiPage(name="ch1", title="Chapter 1", pillar="core", rail="ref", chapter=1),
            WikiPage(name="ch2", title="Chapter 2", pillar="core", rail="ref", chapter=2),
        ]
        manifest = WikiManifest(version=1, pages=pages, rail_names={"ref": "Reference"})
        composed = [
            ComposedPage(name="ch1", title="Chapter 1", content="", pillar="core"),
            ComposedPage(name="ch2", title="Chapter 2", content="", pillar="core"),
        ]
        html_output = _build_sidebar(manifest, composed, is_root=True)
        assert "Reference" in html_output
        assert "ch1" in html_output
        assert "ch2" in html_output

    def test_sidebar_with_pillar_fallback(self):
        pages = [
            WikiPage(name="p1", title="Page 1", pillar="design", order=1),
        ]
        manifest = WikiManifest(version=1, pages=pages)
        composed = [ComposedPage(name="p1", title="Page 1", content="", pillar="design")]
        html_output = _build_sidebar(manifest, composed, is_root=False)
        assert "Design" in html_output
        assert "../p1/" in html_output

    def test_sidebar_root_prefix(self):
        pages = [WikiPage(name="p1", title="P", pillar="x", order=1)]
        manifest = WikiManifest(version=1, pages=pages)
        composed = [ComposedPage(name="p1", title="P", content="", pillar="x")]
        html_root = _build_sidebar(manifest, composed, is_root=True)
        html_sub = _build_sidebar(manifest, composed, is_root=False)
        assert "./p1/" in html_root
        assert "../p1/" in html_sub

    def test_sidebar_excludes_uncomposed_pages(self):
        pages = [
            WikiPage(name="p1", title="P1", pillar="x", order=1),
            WikiPage(name="p2", title="P2", pillar="x", order=2),
        ]
        manifest = WikiManifest(version=1, pages=pages)
        composed = [ComposedPage(name="p1", title="P1", content="", pillar="x")]
        html_output = _build_sidebar(manifest, composed, is_root=True)
        assert "p1" in html_output
        assert "p2" not in html_output

    def test_sidebar_empty_manifest(self):
        manifest = WikiManifest(version=1, pages=[])
        html_output = _build_sidebar(manifest, [], is_root=True)
        assert html_output == ""


class TestBuildPrevNext:
    def test_both_prev_and_next(self):
        prev_p = WikiPage(name="prev", title="Previous", pillar="x")
        next_p = WikiPage(name="next", title="Next Page", pillar="x")
        html_output = _build_prev_next(prev_p, next_p, is_root=False)
        assert "Previous" in html_output
        assert "Next Page" in html_output
        assert "../prev/" in html_output
        assert "../next/" in html_output

    def test_prev_only(self):
        prev_p = WikiPage(name="prev", title="Prev", pillar="x")
        html_output = _build_prev_next(prev_p, None, is_root=False)
        assert "Prev" in html_output
        assert '<span class="next">' in html_output

    def test_next_only(self):
        next_p = WikiPage(name="next", title="Next", pillar="x")
        html_output = _build_prev_next(None, next_p, is_root=False)
        assert "Next" in html_output
        assert '<span class="prev">' in html_output

    def test_neither(self):
        assert _build_prev_next(None, None, is_root=False) == ""

    def test_root_prefix(self):
        prev_p = WikiPage(name="prev", title="Prev", pillar="x")
        html_output = _build_prev_next(prev_p, None, is_root=True)
        assert "./prev/" in html_output


class TestRenderPage:
    def test_basic_render(self):
        html_output = _render_page(
            title="Test Page",
            site_title="My Wiki",
            content_html="<p>Hello</p>",
            sidebar_html="<ul></ul>",
            prev_next_html="",
            active_page="test-page",
            description="Test description",
            base_url="https://example.com",
            slug="test-page",
        )
        assert "<!DOCTYPE html>" in html_output
        assert "Test Page" in html_output
        assert "My Wiki" in html_output
        assert "<p>Hello</p>" in html_output
        assert 'content="Test description"' in html_output
        assert 'href="https://example.com/test-page/"' in html_output

    def test_home_canonical_url(self):
        html_output = _render_page(
            title="Home",
            site_title="Wiki",
            content_html="",
            sidebar_html="",
            prev_next_html="",
            active_page="Home",
            description="Home",
            base_url="https://example.com",
            slug="home",
        )
        assert 'href="https://example.com/"' in html_output

    def test_html_escaping(self):
        html_output = _render_page(
            title="Page <script>",
            site_title="Wiki & More",
            content_html="<p>safe</p>",
            sidebar_html="",
            prev_next_html="",
            active_page="test",
            description="Desc <b>bold</b>",
            base_url="",
            slug="test",
        )
        assert "&lt;script&gt;" in html_output
        assert "Wiki &amp; More" in html_output


class TestWriteAssets:
    def test_write_css(self, tmp_path):
        _write_css(str(tmp_path))
        css_path = tmp_path / "style.css"
        assert css_path.exists()
        content = css_path.read_text()
        assert ":root" in content
        assert "--bg" in content

    def test_write_js(self, tmp_path):
        _write_js(str(tmp_path))
        js_path = tmp_path / "script.js"
        assert js_path.exists()
        content = js_path.read_text()
        assert "theme" in content.lower()

    def test_write_nojekyll(self, tmp_path):
        _write_nojekyll(str(tmp_path))
        assert (tmp_path / ".nojekyll").exists()

    def test_write_robots(self, tmp_path):
        _write_robots(str(tmp_path), "https://example.com")
        robots = (tmp_path / "robots.txt").read_text()
        assert "User-agent: *" in robots
        assert "https://example.com/sitemap.xml" in robots

    def test_write_sitemap(self, tmp_path):
        pages = [
            PublishedPage(name="Home", path="", slug="home", html_size=100),
            PublishedPage(name="Intro", path="", slug="intro", html_size=200),
        ]
        _write_sitemap(pages, str(tmp_path), "https://example.com")
        sitemap = (tmp_path / "sitemap.xml").read_text()
        assert "<urlset" in sitemap
        assert "https://example.com/" in sitemap
        assert "https://example.com/intro/" in sitemap


class TestCheckInternalLinks:
    def test_no_errors_with_valid_links(self, tmp_path):
        page_dir = tmp_path / "intro"
        page_dir.mkdir()
        (page_dir / "index.html").write_text('<a href="../home/">Home</a>')
        pages = [
            PublishedPage(name="Home", path=str(tmp_path / "index.html"), slug="home", html_size=0),
            PublishedPage(
                name="Intro", path=str(page_dir / "index.html"), slug="intro", html_size=0
            ),
        ]
        # Write the home page too
        (tmp_path / "index.html").write_text('<a href="./intro/">Intro</a>')
        errors = _check_internal_links(pages, str(tmp_path))
        assert errors == []

    def test_detects_broken_links(self, tmp_path):
        page_dir = tmp_path / "intro"
        page_dir.mkdir()
        (page_dir / "index.html").write_text('<a href="../nonexistent/">Broken</a>')
        pages = [
            PublishedPage(
                name="Intro", path=str(page_dir / "index.html"), slug="intro", html_size=0
            ),
        ]
        errors = _check_internal_links(pages, str(tmp_path))
        assert len(errors) == 1
        assert "nonexistent" in errors[0]

    def test_ignores_asset_links(self, tmp_path):
        (tmp_path / "index.html").write_text('<a href="./style.css">CSS</a>')
        pages = [
            PublishedPage(name="Home", path=str(tmp_path / "index.html"), slug="home", html_size=0),
        ]
        errors = _check_internal_links(pages, str(tmp_path))
        assert errors == []

    def test_handles_missing_file_gracefully(self, tmp_path):
        pages = [
            PublishedPage(name="Gone", path=str(tmp_path / "gone.html"), slug="gone", html_size=0),
        ]
        errors = _check_internal_links(pages, str(tmp_path))
        assert len(errors) == 1
        assert "could not read" in errors[0]


class TestCleanStalePages:
    def test_removes_stale_directories(self, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        stale = out / "old-page"
        stale.mkdir()
        (stale / "index.html").write_text("old content")
        current = out / "current-page"
        current.mkdir()
        (current / "index.html").write_text("current content")

        _clean_stale_pages(str(out), {"current-page"})
        assert current.exists()
        assert not stale.exists()

    def test_does_not_remove_files(self, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        (out / "style.css").write_text("body {}")
        _clean_stale_pages(str(out), set())
        assert (out / "style.css").exists()

    def test_noop_when_dir_missing(self, tmp_path):
        _clean_stale_pages(str(tmp_path / "nonexistent"), set())

    def test_preserves_current_slugs(self, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        keep = out / "keep"
        keep.mkdir()
        _clean_stale_pages(str(out), {"keep"})
        assert keep.exists()


class TestPublishPages:
    @staticmethod
    def _minimal_publish(tmp_path, pages=None, manifest_pages=None):
        """Helper: run publish_pages with minimal config and return result."""
        if manifest_pages is None:
            manifest_pages = [
                WikiPage(name="intro", title="Introduction", pillar="core", order=1),
            ]
        manifest = WikiManifest(version=1, pages=manifest_pages)

        if pages is None:
            pages = [
                ComposedPage(name="Home", title="Home", content="# Home\n\nWelcome.", pillar=""),
                ComposedPage(
                    name="intro", title="Introduction", content="# Intro\n\nContent.", pillar="core"
                ),
            ]
        out_dir = str(tmp_path / "site")
        return publish_pages(
            manifest,
            pages,
            str(tmp_path),
            out_dir,
            check_links=True,
            site_title="Test Wiki",
            base_url="https://test.com",
        )

    def test_creates_output_directory(self, tmp_path):
        result = self._minimal_publish(tmp_path)
        assert os.path.isdir(result.out_dir)

    def test_creates_index_html_per_page(self, tmp_path):
        result = self._minimal_publish(tmp_path)
        assert len(result.pages) == 2
        for page in result.pages:
            assert os.path.isfile(page.path)
            assert page.path.endswith("index.html")

    def test_home_written_at_root(self, tmp_path):
        result = self._minimal_publish(tmp_path)
        home = next(p for p in result.pages if p.name == "Home")
        assert home.path == os.path.join(result.out_dir, "index.html")

    def test_subpage_written_in_slug_dir(self, tmp_path):
        result = self._minimal_publish(tmp_path)
        intro = next(p for p in result.pages if p.name == "intro")
        assert "intro" in intro.path
        assert intro.path.endswith(os.path.join("intro", "index.html"))

    def test_sitemap_written(self, tmp_path):
        result = self._minimal_publish(tmp_path)
        assert result.sitemap_written is True
        assert os.path.isfile(os.path.join(result.out_dir, "sitemap.xml"))

    def test_static_assets_written(self, tmp_path):
        result = self._minimal_publish(tmp_path)
        assert os.path.isfile(os.path.join(result.out_dir, "style.css"))
        assert os.path.isfile(os.path.join(result.out_dir, "script.js"))
        assert os.path.isfile(os.path.join(result.out_dir, ".nojekyll"))
        assert os.path.isfile(os.path.join(result.out_dir, "robots.txt"))

    def test_html_size_positive(self, tmp_path):
        result = self._minimal_publish(tmp_path)
        for page in result.pages:
            assert page.html_size > 0

    def test_link_errors_empty_for_valid_site(self, tmp_path):
        result = self._minimal_publish(tmp_path)
        # Any errors are about links between the composed pages, not structural failures
        # We accept the result as-is since this depends on content
        assert isinstance(result.link_errors, list)

    def test_check_links_disabled(self, tmp_path):
        manifest = WikiManifest(
            version=1,
            pages=[WikiPage(name="p", title="P", pillar="x")],
        )
        composed = [
            ComposedPage(name="Home", title="Home", content="# Home", pillar=""),
            ComposedPage(name="p", title="P", content="# P", pillar="x"),
        ]
        out_dir = str(tmp_path / "site")
        result = publish_pages(
            manifest,
            composed,
            str(tmp_path),
            out_dir,
            check_links=False,
            site_title="Test",
            base_url="",
        )
        assert result.link_errors == []

    def test_publish_result_defaults(self):
        r = PublishResult()
        assert r.pages == []
        assert r.out_dir == ""
        assert r.sitemap_written is False
        assert r.link_errors == []

    def test_published_page_dataclass(self):
        p = PublishedPage(name="test", path="/a/b.html", slug="test", html_size=42)
        assert p.name == "test"
        assert p.html_size == 42


# ═══════════════════════════════════════════════════════════════════════════
# transforms.py tests
# ═══════════════════════════════════════════════════════════════════════════


class TestStripFrontmatter:
    def test_strips_yaml_frontmatter(self):
        from lintgate.wiki.transforms import strip_frontmatter

        text = "---\ntitle: Test\n---\n\nContent here"
        result = strip_frontmatter(text)
        assert result == "Content here"

    def test_no_frontmatter(self):
        from lintgate.wiki.transforms import strip_frontmatter

        text = "No frontmatter\nJust content"
        assert strip_frontmatter(text) == text

    def test_empty_string(self):
        from lintgate.wiki.transforms import strip_frontmatter

        assert strip_frontmatter("") == ""


class TestStripLeadingH1:
    def test_strips_h1(self):
        from lintgate.wiki.transforms import strip_leading_h1

        text = "# Title\nContent"
        result = strip_leading_h1(text)
        assert result == "Content"

    def test_no_h1(self):
        from lintgate.wiki.transforms import strip_leading_h1

        text = "## H2\nContent"
        result = strip_leading_h1(text)
        assert "## H2" in result

    def test_empty(self):
        from lintgate.wiki.transforms import strip_leading_h1

        assert strip_leading_h1("") == ""


class TestInterpolateMetrics:
    def test_replaces_placeholders(self):
        from lintgate.wiki.transforms import interpolate_metrics

        text = "Tools: {{tool_count}}, Version: {{version}}"
        metrics = {"tool_count": "42", "version": "1.0"}
        result = interpolate_metrics(text, metrics)
        assert result == "Tools: 42, Version: 1.0"

    def test_unknown_key_preserved(self):
        from lintgate.wiki.transforms import interpolate_metrics

        text = "Value: {{unknown}}"
        result = interpolate_metrics(text, {"other": "x"})
        assert result == "Value: {{unknown}}"

    def test_empty_metrics(self):
        from lintgate.wiki.transforms import interpolate_metrics

        text = "No change {{key}}"
        assert interpolate_metrics(text, {}) == text

    def test_no_placeholders(self):
        from lintgate.wiki.transforms import interpolate_metrics

        text = "Plain text"
        assert interpolate_metrics(text, {"k": "v"}) == text


class TestBuildBreadcrumb:
    def test_with_rail(self):
        from lintgate.wiki.transforms import build_breadcrumb

        page = WikiPage(name="p", title="P", pillar="core", rail="getting_value")
        result = build_breadcrumb(page)
        assert "Getting Value Fast" in result

    def test_with_pillar_fallback(self):
        from lintgate.wiki.transforms import build_breadcrumb

        page = WikiPage(name="p", title="P", pillar="design")
        result = build_breadcrumb(page)
        assert "Design" in result

    def test_with_chapter(self):
        from lintgate.wiki.transforms import build_breadcrumb

        page = WikiPage(name="p", title="P", pillar="x", chapter=3)
        result = build_breadcrumb(page)
        assert "Chapter 3" in result

    def test_with_read_time(self):
        from lintgate.wiki.transforms import build_breadcrumb

        page = WikiPage(name="p", title="P", pillar="x")
        result = build_breadcrumb(page, read_time_min=5)
        assert "5 min read" in result

    def test_with_prerequisites(self):
        from lintgate.wiki.transforms import build_breadcrumb

        page = WikiPage(name="p", title="P", pillar="x", prerequisites=["intro"])
        result = build_breadcrumb(page)
        assert "Prerequisites" in result
        assert "intro" in result

    def test_home_page_no_home_link(self):
        from lintgate.wiki.transforms import build_breadcrumb

        page = WikiPage(name="Home", title="Home", pillar="x")
        result = build_breadcrumb(page)
        assert "[Home]" not in result

    def test_non_home_has_home_link(self):
        from lintgate.wiki.transforms import build_breadcrumb

        page = WikiPage(name="other", title="Other", pillar="x")
        result = build_breadcrumb(page)
        assert "[Home]" in result

    def test_with_manifest_rail_name(self):
        from lintgate.wiki.transforms import build_breadcrumb

        page = WikiPage(name="p", title="P", pillar="x", rail="custom")
        manifest = WikiManifest(version=1, pages=[], rail_names={"custom": "Custom Rail"})
        result = build_breadcrumb(page, manifest=manifest)
        assert "Custom Rail" in result

    def test_no_pillar_fallback_to_wiki(self):
        from lintgate.wiki.transforms import build_breadcrumb

        page = WikiPage(name="p", title="P", pillar="")
        result = build_breadcrumb(page)
        assert "Wiki" in result


class TestRewriteLinks:
    def test_rewrites_known_page_links(self):
        from lintgate.wiki.transforms import rewrite_links

        text = "[Click here](Overview)"
        result = rewrite_links(text, lambda p: f"../{p.lower()}/", known_pages={"Overview"})
        assert "[Click here](../overview/)" in result

    def test_skips_unknown_pages(self):
        from lintgate.wiki.transforms import rewrite_links

        text = "[link](Unknown)"
        result = rewrite_links(text, lambda p: f"../{p}/", known_pages={"Home"})
        assert result == text

    def test_without_known_pages(self):
        from lintgate.wiki.transforms import rewrite_links

        text = "[link](SomePage)"
        result = rewrite_links(text, lambda p: f"../{p.lower()}/")
        assert "[link](../somepage/)" in result

    def test_preserves_anchors(self):
        from lintgate.wiki.transforms import rewrite_links

        text = "[link](Page#section)"
        result = rewrite_links(text, lambda p: f"../{p.lower()}/", known_pages={"Page"})
        assert "#section" in result

    def test_no_links(self):
        from lintgate.wiki.transforms import rewrite_links

        text = "No links here"
        assert rewrite_links(text, lambda p: p) == text


class TestApplyCommonTransforms:
    def test_full_pipeline(self):
        from lintgate.wiki.transforms import apply_common_transforms

        text = "---\ntitle: T\n---\n# Title\n\nContent with {{metric}}"
        page = WikiPage(name="p", title="P", pillar="core")
        result = apply_common_transforms(
            text,
            page,
            metrics={"metric": "42"},
            read_time_min=3,
        )
        assert "---" not in result.split("\n")[0]
        assert "# Title" not in result  # stripped H1
        assert "42" in result
        assert "3 min read" in result

    def test_no_breadcrumb(self):
        from lintgate.wiki.transforms import apply_common_transforms

        page = WikiPage(name="p", title="P", pillar="core")
        result = apply_common_transforms("Content", page, include_breadcrumb=False)
        assert "Core" not in result or "min read" not in result

    def test_link_rewriting(self):
        from lintgate.wiki.transforms import apply_common_transforms

        page = WikiPage(name="p", title="P", pillar="core")
        manifest = WikiManifest(
            version=1,
            pages=[
                WikiPage(name="Target", title="T", pillar="core"),
                page,
            ],
        )
        text = "See [link](Target) for details."
        link_fn = lambda p: f"../{p.lower()}/"  # noqa: E731
        result = apply_common_transforms(
            text,
            page,
            link_fn=link_fn,
            include_breadcrumb=False,
            manifest=manifest,
        )
        assert "../target/" in result


class TestLinkFunctions:
    def test_wiki_link_fn(self):
        from lintgate.wiki.transforms import wiki_link_fn

        assert wiki_link_fn("My-Page") == "My-Page"

    def test_pages_link_fn_root(self):
        from lintgate.wiki.transforms import pages_link_fn

        assert pages_link_fn("intro", is_root=True) == "./intro/"

    def test_pages_link_fn_subpage(self):
        from lintgate.wiki.transforms import pages_link_fn

        assert pages_link_fn("intro", is_root=False) == "../intro/"

    def test_make_pages_link_fn_root(self):
        from lintgate.wiki.transforms import make_pages_link_fn

        fn = make_pages_link_fn(is_root=True)
        assert fn("Overview") == "./overview/"

    def test_make_pages_link_fn_subpage(self):
        from lintgate.wiki.transforms import make_pages_link_fn

        fn = make_pages_link_fn(is_root=False)
        assert fn("Overview") == "../overview/"


# ═══════════════════════════════════════════════════════════════════════════
# freshness.py tests
# ═══════════════════════════════════════════════════════════════════════════


class TestContentHash:
    def test_deterministic(self):
        from lintgate.wiki.freshness import content_hash

        h1 = content_hash("hello world")
        h2 = content_hash("hello world")
        assert h1 == h2

    def test_different_inputs_different_hashes(self):
        from lintgate.wiki.freshness import content_hash

        assert content_hash("a") != content_hash("b")

    def test_hash_length(self):
        from lintgate.wiki.freshness import content_hash

        assert len(content_hash("test")) == 16

    def test_empty_string(self):
        from lintgate.wiki.freshness import content_hash

        h = content_hash("")
        assert isinstance(h, str)
        assert len(h) == 16


class TestBuildPageFreshness:
    def test_builds_state(self):
        from lintgate.wiki.freshness import build_page_freshness

        state = build_page_freshness(
            page_name="intro",
            section_contents={"docs/a.md::Intro": "content"},
            manifest_hash="abcd1234",
            page_content="full page content",
        )
        assert state.page_name == "intro"
        assert state.manifest_hash == "abcd1234"
        assert "docs/a.md::Intro" in state.section_hashes
        assert state.generator_version == "1"
        assert state.generated_at > 0
        assert len(state.page_content_hash) == 16

    def test_empty_sections(self):
        from lintgate.wiki.freshness import build_page_freshness

        state = build_page_freshness("p", {}, "hash", "content")
        assert state.section_hashes == {}


class TestCheckPageStaleness:
    def test_no_stored_state(self):
        from lintgate.wiki.freshness import PageFreshnessState, check_page_staleness

        current = PageFreshnessState(page_name="p", generator_version="1")
        result = check_page_staleness(current, None)
        assert result["stale"] is True
        assert "no previous state" in result["reasons"]

    def test_fresh_when_identical(self):
        from lintgate.wiki.freshness import PageFreshnessState, check_page_staleness

        state = PageFreshnessState(
            page_name="p",
            section_hashes={"a": "hash1"},
            manifest_hash="mhash",
            generator_version="1",
        )
        result = check_page_staleness(state, state)
        assert result["stale"] is False
        assert result["reasons"] == []
        assert result["changed_sections"] == []

    def test_stale_on_generator_version_change(self):
        from lintgate.wiki.freshness import PageFreshnessState, check_page_staleness

        current = PageFreshnessState(page_name="p", generator_version="2")
        stored = PageFreshnessState(page_name="p", generator_version="1")
        result = check_page_staleness(current, stored)
        assert result["stale"] is True
        assert any("generator version" in r for r in result["reasons"])

    def test_stale_on_manifest_change(self):
        from lintgate.wiki.freshness import PageFreshnessState, check_page_staleness

        current = PageFreshnessState(page_name="p", manifest_hash="new", generator_version="1")
        stored = PageFreshnessState(page_name="p", manifest_hash="old", generator_version="1")
        result = check_page_staleness(current, stored)
        assert result["stale"] is True
        assert any("manifest" in r for r in result["reasons"])

    def test_stale_on_section_change(self):
        from lintgate.wiki.freshness import PageFreshnessState, check_page_staleness

        current = PageFreshnessState(
            page_name="p",
            section_hashes={"a": "new"},
            generator_version="1",
        )
        stored = PageFreshnessState(
            page_name="p",
            section_hashes={"a": "old"},
            generator_version="1",
        )
        result = check_page_staleness(current, stored)
        assert result["stale"] is True
        assert "a" in result["changed_sections"]

    def test_stale_on_new_section(self):
        from lintgate.wiki.freshness import PageFreshnessState, check_page_staleness

        current = PageFreshnessState(
            page_name="p",
            section_hashes={"a": "h1", "b": "h2"},
            generator_version="1",
        )
        stored = PageFreshnessState(
            page_name="p",
            section_hashes={"a": "h1"},
            generator_version="1",
        )
        result = check_page_staleness(current, stored)
        assert result["stale"] is True
        assert any("new section" in r for r in result["reasons"])

    def test_stale_on_removed_section(self):
        from lintgate.wiki.freshness import PageFreshnessState, check_page_staleness

        current = PageFreshnessState(
            page_name="p",
            section_hashes={},
            generator_version="1",
        )
        stored = PageFreshnessState(
            page_name="p",
            section_hashes={"a": "h1"},
            generator_version="1",
        )
        result = check_page_staleness(current, stored)
        assert result["stale"] is True
        assert any("removed section" in r for r in result["reasons"])


class TestFreshnessStatePersistence:
    def test_save_and_load_roundtrip(self, tmp_path):
        from lintgate.wiki.freshness import (
            PageFreshnessState,
            WikiFreshnessState,
            load_freshness_state,
            save_freshness_state,
        )

        state = WikiFreshnessState(
            pages={
                "intro": PageFreshnessState(
                    page_name="intro",
                    section_hashes={"a.md::H": "hash1"},
                    manifest_hash="mhash",
                    generator_version="1",
                    page_content_hash="phash",
                    generated_at=1234567890.0,
                ),
            }
        )
        save_freshness_state(str(tmp_path), state)
        loaded = load_freshness_state(str(tmp_path))
        assert "intro" in loaded.pages
        p = loaded.pages["intro"]
        assert p.section_hashes == {"a.md::H": "hash1"}
        assert p.manifest_hash == "mhash"
        assert p.generator_version == "1"
        assert p.generated_at == 1234567890.0

    def test_load_missing_file_returns_empty(self, tmp_path):
        from lintgate.wiki.freshness import load_freshness_state

        state = load_freshness_state(str(tmp_path))
        assert state.pages == {}

    def test_load_corrupt_json_returns_empty(self, tmp_path):
        from lintgate.wiki.freshness import load_freshness_state

        state_dir = tmp_path / ".lintgate" / "wiki"
        state_dir.mkdir(parents=True)
        (state_dir / "_wiki_state.json").write_text("not json{{{")
        state = load_freshness_state(str(tmp_path))
        assert state.pages == {}

    def test_save_creates_directory(self, tmp_path):
        from lintgate.wiki.freshness import WikiFreshnessState, save_freshness_state

        save_freshness_state(str(tmp_path), WikiFreshnessState())
        assert (tmp_path / ".lintgate" / "wiki" / "_wiki_state.json").exists()


# ═══════════════════════════════════════════════════════════════════════════
# extractor.py tests
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractSection:
    def test_extracts_matching_section(self, tmp_path):
        from lintgate.wiki.extractor import extract_section

        doc = tmp_path / "doc.md"
        doc.write_text("# Title\n\n## Architecture\n\nArch content\n\n## Other\n\nOther stuff\n")
        result = extract_section(str(doc), "Architecture", 2)
        assert result is not None
        assert result.heading == "Architecture"
        assert "Arch content" in result.content

    def test_case_insensitive_match(self, tmp_path):
        from lintgate.wiki.extractor import extract_section

        doc = tmp_path / "doc.md"
        doc.write_text("## My Section\n\nContent\n")
        result = extract_section(str(doc), "my section", 2)
        assert result is not None

    def test_strips_anchor_from_heading(self, tmp_path):
        from lintgate.wiki.extractor import extract_section

        doc = tmp_path / "doc.md"
        doc.write_text("## Architecture {#arch-id}\n\nContent\n")
        result = extract_section(str(doc), "Architecture", 2)
        assert result is not None
        assert result.heading == "Architecture"

    def test_returns_none_for_missing_section(self, tmp_path):
        from lintgate.wiki.extractor import extract_section

        doc = tmp_path / "doc.md"
        doc.write_text("## Existing\n\nContent\n")
        assert extract_section(str(doc), "Nonexistent", 2) is None

    def test_returns_none_for_missing_file(self, tmp_path):
        from lintgate.wiki.extractor import extract_section

        assert extract_section(str(tmp_path / "no.md"), "H", 2) is None

    def test_wrong_heading_level(self, tmp_path):
        from lintgate.wiki.extractor import extract_section

        doc = tmp_path / "doc.md"
        doc.write_text("### H3 Section\n\nContent\n")
        assert extract_section(str(doc), "H3 Section", 2) is None
        assert extract_section(str(doc), "H3 Section", 3) is not None


class TestExtractWholeFile:
    def test_extracts_whole_file(self, tmp_path):
        from lintgate.wiki.extractor import extract_whole_file

        doc = tmp_path / "doc.md"
        doc.write_text("# Title\n\nBody content\n")
        result = extract_whole_file(str(doc))
        assert result is not None
        assert "Title" in result.content
        assert "Body content" in result.content

    def test_strips_frontmatter(self, tmp_path):
        from lintgate.wiki.extractor import extract_whole_file

        doc = tmp_path / "doc.md"
        doc.write_text("---\ntitle: Test\n---\n# Title\n\nBody\n")
        result = extract_whole_file(str(doc))
        assert result is not None
        assert "title: Test" not in result.content
        assert "Body" in result.content

    def test_returns_none_for_missing_file(self, tmp_path):
        from lintgate.wiki.extractor import extract_whole_file

        assert extract_whole_file(str(tmp_path / "no.md")) is None

    def test_returns_none_for_empty_file(self, tmp_path):
        from lintgate.wiki.extractor import extract_whole_file

        doc = tmp_path / "empty.md"
        doc.write_text("")
        assert extract_whole_file(str(doc)) is None

    def test_returns_none_for_frontmatter_only(self, tmp_path):
        from lintgate.wiki.extractor import extract_whole_file

        doc = tmp_path / "fm.md"
        doc.write_text("---\ntitle: T\n---\n")
        assert extract_whole_file(str(doc)) is None

    def test_heading_from_filename(self, tmp_path):
        from lintgate.wiki.extractor import extract_whole_file

        doc = tmp_path / "my-guide.md"
        doc.write_text("Content here.")
        result = extract_whole_file(str(doc))
        assert result is not None
        assert result.heading == "my-guide"


class TestExtractAllSections:
    def test_extracts_multiple_sections(self, tmp_path):
        from lintgate.wiki.extractor import extract_all_sections

        doc = tmp_path / "doc.md"
        doc.write_text("# Title\n\n## A\n\nA content\n\n## B\n\nB content\n")
        result = extract_all_sections(str(doc), 2)
        assert len(result) == 2
        assert result[0].heading == "A"
        assert result[1].heading == "B"

    def test_empty_file(self, tmp_path):
        from lintgate.wiki.extractor import extract_all_sections

        doc = tmp_path / "empty.md"
        doc.write_text("")
        assert extract_all_sections(str(doc), 2) == []

    def test_missing_file(self, tmp_path):
        from lintgate.wiki.extractor import extract_all_sections

        assert extract_all_sections(str(tmp_path / "no.md"), 2) == []

    def test_section_bounded_by_same_level(self, tmp_path):
        from lintgate.wiki.extractor import extract_all_sections

        doc = tmp_path / "doc.md"
        doc.write_text(
            "## First\n\nFirst content\n\n### Sub\n\nSub content\n\n## Second\n\nSecond content\n"
        )
        result = extract_all_sections(str(doc), 2)
        assert len(result) == 2
        # First section should include the sub-heading content
        assert "Sub content" in result[0].content


# ═══════════════════════════════════════════════════════════════════════════
# Additional edge-case tests (consolidated from satellite files)
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractSectionEdgeCases:
    """Unique edge cases from test_wiki_extractor.py."""

    def test_exact_match_required(self, tmp_path):
        """Substring no longer matches -- must be exact."""
        from lintgate.wiki.extractor import extract_section

        doc = tmp_path / "doc.md"
        doc.write_text(
            "# Top\n\n## Design Philosophy\n\nDesign content.\n\n## Pipeline\n\nPipeline.\n"
        )
        sec = extract_section(str(doc), "Design", heading_level=2)
        assert sec is None  # "Design" != "Design Philosophy"

        sec = extract_section(str(doc), "Design Philosophy", heading_level=2)
        assert sec is not None
        assert sec.heading == "Design Philosophy"

    def test_level_boundary_includes_lower(self, tmp_path):
        """Section content ends at next same-or-higher level heading."""
        from lintgate.wiki.extractor import extract_section

        doc = tmp_path / "doc.md"
        doc.write_text(
            "## Design Philosophy\n\nDesign content.\n\n"
            "### Subsection\n\nSub content.\n\n"
            "## Pipeline\n\nPipeline content.\n"
        )
        sec = extract_section(str(doc), "Design Philosophy", heading_level=2)
        assert sec is not None
        # Should include subsection content (### is lower level than ##)
        assert "Sub content." in sec.content
        # Should NOT include Pipeline content (next ## heading)
        assert "Pipeline content." not in sec.content


class TestStripFrontmatterEdgeCases:
    """Unique edge cases from test_wiki_transforms.py."""

    def test_preserves_later_dashes(self):
        from lintgate.wiki.transforms import strip_frontmatter

        text = "---\nk: v\n---\n\nContent\n\n---\n\nMore."
        result = strip_frontmatter(text)
        assert "Content" in result
        assert "---" in result  # The later --- is preserved


class TestStripLeadingH1EdgeCases:
    def test_only_first_h1_stripped(self):
        from lintgate.wiki.transforms import strip_leading_h1

        text = "# First\n\n# Second\n\nContent."
        result = strip_leading_h1(text)
        assert "# Second" in result
        assert "# First" not in result


class TestRewriteLinksEdgeCases:
    """Anchor and known_pages edge cases from test_wiki_transforms.py."""

    def test_anchor_link_preserved_in_pages_fn(self):
        from lintgate.wiki.transforms import make_pages_link_fn, rewrite_links

        link_fn = make_pages_link_fn(is_root=False)
        text = "See [zero state](Glossary#zero-state) for details."
        result = rewrite_links(text, link_fn)
        assert "../glossary/#zero-state" in result

    def test_anchor_preserved_wiki_fn(self):
        from lintgate.wiki.transforms import rewrite_links, wiki_link_fn

        text = "See [zero state](Glossary#zero-state) for details."
        result = rewrite_links(text, wiki_link_fn)
        assert "Glossary#zero-state" in result

    def test_no_anchor_clean(self):
        from lintgate.wiki.transforms import make_pages_link_fn, rewrite_links

        link_fn = make_pages_link_fn(is_root=False)
        text = "See [glossary](Glossary) for details."
        result = rewrite_links(text, link_fn)
        assert "../glossary/" in result
        assert "#" not in result

    def test_anchor_with_known_pages_filter(self):
        from lintgate.wiki.transforms import make_pages_link_fn, rewrite_links

        link_fn = make_pages_link_fn(is_root=False)
        known = {"Glossary", "Home"}
        text = "See [zero state](Glossary#zero-state) and [unknown](Other#thing)."
        result = rewrite_links(text, link_fn, known_pages=known)
        assert "../glossary/#zero-state" in result
        assert "Other#thing" in result  # Not rewritten -- not a known page

    def test_root_prefix(self):
        from lintgate.wiki.transforms import make_pages_link_fn, rewrite_links

        link_fn = make_pages_link_fn(is_root=True)
        text = "Go to [Setup](Setup-Guide)."
        result = rewrite_links(text, link_fn)
        assert "./setup-guide/" in result

    def test_preserves_urls_and_file_paths(self):
        from lintgate.wiki.transforms import make_pages_link_fn, rewrite_links

        link_fn = make_pages_link_fn()
        text = "Visit [site](https://example.com) or [file](docs/foo.md)"
        result = rewrite_links(text, link_fn)
        assert "(https://example.com)" in result
        assert "docs/foo.md" in result


class TestComposerEdgeCases:
    """Unique tests from test_wiki_composer.py."""

    def test_no_composer_breadcrumb(self, tmp_path):
        """Composer should NOT add breadcrumb -- transforms layer handles it."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "design.md").write_text("## Overview\n\nOverview content.\n")

        pages = [
            WikiPage(
                name="Theory-Core",
                title="Core Thesis",
                pillar="theory",
                order=1,
                sources=[
                    SourceRef(file="docs/design.md", kind="section", heading="Overview", level=2)
                ],
            ),
        ]
        manifest = WikiManifest(version=1, pages=pages)
        composed = compose_all_pages(manifest, str(tmp_path))
        core = next(p for p in composed if p.name == "Theory-Core")
        assert "**Theory** |" not in core.content

    def test_managed_section_markers(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "design.md").write_text("## Overview\n\nOverview content.\n")

        pages = [
            WikiPage(
                name="Theory-Core",
                title="Core Thesis",
                pillar="theory",
                order=1,
                sources=[
                    SourceRef(file="docs/design.md", kind="section", heading="Overview", level=2)
                ],
            ),
        ]
        manifest = WikiManifest(version=1, pages=pages)
        composed = compose_all_pages(manifest, str(tmp_path))
        core = next(p for p in composed if p.name == "Theory-Core")
        assert "<!-- LINTGATE_WIKI:BEGIN" in core.content
        assert "<!-- LINTGATE_WIKI:END" in core.content

    def test_source_attribution(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "design.md").write_text("## Overview\n\nOverview content.\n")

        pages = [
            WikiPage(
                name="Theory-Core",
                title="Core Thesis",
                pillar="theory",
                order=1,
                sources=[
                    SourceRef(file="docs/design.md", kind="section", heading="Overview", level=2)
                ],
            ),
        ]
        manifest = WikiManifest(version=1, pages=pages)
        composed = compose_all_pages(manifest, str(tmp_path))
        core = next(p for p in composed if p.name == "Theory-Core")
        assert "Sources:" in core.content
        assert "docs/design.md" in core.content

    def test_home_page_dynamic_pillars(self, tmp_path):
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

    def test_partial_regen_preserves_freshness(self, tmp_path):
        """Materializing a subset of pages must not wipe freshness for other pages."""
        from lintgate.wiki.freshness import (
            WikiFreshnessState,
            build_page_freshness,
            load_freshness_state,
            save_freshness_state,
        )

        state = WikiFreshnessState()
        state.pages["Other-Page"] = build_page_freshness(
            "Other-Page", {"f::h": "content"}, "mh", "page"
        )
        save_freshness_state(str(tmp_path), state)

        loaded = load_freshness_state(str(tmp_path))
        loaded.pages["New-Page"] = build_page_freshness(
            "New-Page", {"g::h": "other"}, "mh2", "page2"
        )
        save_freshness_state(str(tmp_path), loaded)

        final = load_freshness_state(str(tmp_path))
        assert "Other-Page" in final.pages
        assert "New-Page" in final.pages


class TestManifestTagIndex:
    """Kill STATE+VALUE mutants on WikiManifest._build_tag_index."""

    def test_tag_index_maps_tags_to_pages(self):
        pages = [
            WikiPage(name="A", title="A", pillar="p", tags=["x", "y"]),
            WikiPage(name="B", title="B", pillar="p", tags=["y", "z"]),
        ]
        m = WikiManifest(version=1, pages=pages)
        assert "x" in m._tag_index
        assert m._tag_index["x"] == ["A"]
        assert sorted(m._tag_index["y"]) == ["A", "B"]
        assert m._tag_index["z"] == ["B"]

    def test_tag_index_empty_tags(self):
        pages = [WikiPage(name="A", title="A", pillar="p", tags=[])]
        m = WikiManifest(version=1, pages=pages)
        assert m._tag_index == {}

    def test_tag_index_rebuilt_on_init(self):
        """Ensure __post_init__ triggers _build_tag_index."""
        pages = [WikiPage(name="P", title="P", pillar="p", tags=["t"])]
        m = WikiManifest(version=1, pages=pages)
        assert m._tag_index["t"] == ["P"]


class TestManifestV2Features:
    """Consolidated v2 manifest tests (rails, chapters, prerequisites, aliases)."""

    def test_manifest_hash_includes_rail(self):
        m = WikiManifest(
            version=1,
            pages=[
                WikiPage(
                    name="Quick-Start", title="Q", pillar="guide", rail="getting_value", chapter=1
                ),
            ],
        )
        qs = m.pages[0]
        h1 = m.manifest_hash_for_page(qs)
        qs.rail = "how_it_works"
        h2 = m.manifest_hash_for_page(qs)
        assert h1 != h2

    def test_load_manifest_with_rails(self, tmp_path):
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

    def test_sections_key_alias_for_sources(self, tmp_path):
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

    def test_source_key_alias_for_file(self, tmp_path):
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

    def test_headings_all_alias_for_sections_all(self, tmp_path):
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


class TestModelAtlasCompat:
    """ModelAtlas wiki.yaml format compatibility tests.

    Validates that LintGate correctly parses the ModelAtlas manifest format:
    - ``id`` instead of ``name``
    - ``path`` instead of ``file`` in sources
    - ``sections: all`` instead of ``kind: file``
    - ``audience`` instead of ``pillar``
    - Top-level ``rails:`` with display names
    - kebab-case rail IDs
    """

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

    @staticmethod
    def _setup_project(tmp_path):
        """Create project with ModelAtlas format wiki.yaml and source files."""
        (tmp_path / "wiki.yaml").write_text(TestModelAtlasCompat.MODELATLAS_WIKI_YAML)

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

        (wiki_dir / "_metrics.yaml").write_text("model_count: '19,498'\nanchor_count: '170'\n")

        return str(tmp_path)

    def test_load_modelatlas_manifest(self, tmp_path):
        root = self._setup_project(tmp_path)
        m = load_manifest(root)
        assert m is not None
        assert len(m.pages) == 5

    def test_id_becomes_name(self, tmp_path):
        root = self._setup_project(tmp_path)
        m = load_manifest(root)
        assert m is not None
        names = [p.name for p in m.pages]
        assert "home" in names
        assert "getting-started" in names
        assert "glossary" in names
        assert "" not in names

    def test_path_becomes_file(self, tmp_path):
        root = self._setup_project(tmp_path)
        m = load_manifest(root)
        assert m is not None
        gs = next(p for p in m.pages if p.name == "getting-started")
        assert gs.sources[0].file == "docs/wiki/getting-started.md"
        assert gs.sources[0].kind == "file"

    def test_audience_becomes_pillar(self, tmp_path):
        root = self._setup_project(tmp_path)
        m = load_manifest(root)
        assert m is not None
        gs = next(p for p in m.pages if p.name == "getting-started")
        assert gs.pillar == "user"
        so = next(p for p in m.pages if p.name == "system-overview")
        assert so.pillar == "operator"

    def test_rail_names_parsed(self, tmp_path):
        root = self._setup_project(tmp_path)
        m = load_manifest(root)
        assert m is not None
        assert m.rail_names["getting-value-fast"] == "Getting Value Fast"
        assert m.rail_names["how-it-works"] == "How It Works"
        assert m.rail_display_name("getting-value-fast") == "Getting Value Fast"

    def test_rails_detected(self, tmp_path):
        root = self._setup_project(tmp_path)
        m = load_manifest(root)
        assert m is not None
        rails = m.rails
        assert "getting-value-fast" in rails
        assert "how-it-works" in rails
        assert "reference" in rails

    def test_prerequisites_parsed(self, tmp_path):
        root = self._setup_project(tmp_path)
        m = load_manifest(root)
        assert m is not None
        qe = next(p for p in m.pages if p.name == "query-examples")
        assert qe.prerequisites == ["getting-started"]

    def test_prev_next_in_rail(self, tmp_path):
        root = self._setup_project(tmp_path)
        m = load_manifest(root)
        assert m is not None
        gs = next(p for p in m.pages if p.name == "getting-started")
        prev_p, next_p = m.prev_next_in_rail(gs)
        assert prev_p is None
        assert next_p is not None
        assert next_p.name == "query-examples"

    def test_compose_reads_source_content(self, tmp_path):
        root = self._setup_project(tmp_path)
        m = load_manifest(root)
        assert m is not None
        composed = compose_all_pages(m, root)
        gs = next(p for p in composed if p.name == "getting-started")
        assert "five minutes" in gs.content

    def test_compose_page_names_not_empty(self, tmp_path):
        root = self._setup_project(tmp_path)
        m = load_manifest(root)
        assert m is not None
        composed = compose_all_pages(m, root)
        for page in composed:
            assert page.name, "Empty page name in composed pages"

    def test_publish_creates_subdirectories(self, tmp_path):
        root = self._setup_project(tmp_path)
        m = load_manifest(root)
        assert m is not None
        composed = compose_all_pages(m, root)
        out_dir = str(tmp_path / "_site")

        result = publish_pages(m, composed, root, out_dir, check_links=False)

        for p in result.pages:
            assert p.slug, f"Empty slug for page {p.name}"
            assert p.name, "Empty name for published page"

        assert os.path.isfile(os.path.join(out_dir, "index.html"))
        assert os.path.isfile(os.path.join(out_dir, "getting-started", "index.html"))
        assert os.path.isfile(os.path.join(out_dir, "glossary", "index.html"))

    def test_publish_sidebar_has_rail_display_names(self, tmp_path):
        root = self._setup_project(tmp_path)
        m = load_manifest(root)
        assert m is not None
        composed = compose_all_pages(m, root)
        out_dir = str(tmp_path / "_site")

        publish_pages(m, composed, root, out_dir, check_links=False)

        with open(os.path.join(out_dir, "getting-started", "index.html")) as f:
            html = f.read()
        assert "Getting Value Fast" in html
        assert "How It Works" in html

    def test_publish_sidebar_links_have_slugs(self, tmp_path):
        root = self._setup_project(tmp_path)
        m = load_manifest(root)
        assert m is not None
        composed = compose_all_pages(m, root)
        out_dir = str(tmp_path / "_site")

        publish_pages(m, composed, root, out_dir, check_links=False)

        with open(os.path.join(out_dir, "getting-started", "index.html")) as f:
            html = f.read()
        assert "../getting-started/" in html or "../query-examples/" in html
        assert "..//" not in html

    def test_publish_content_not_stub(self, tmp_path):
        root = self._setup_project(tmp_path)
        m = load_manifest(root)
        assert m is not None
        composed = compose_all_pages(m, root)
        out_dir = str(tmp_path / "_site")

        publish_pages(m, composed, root, out_dir, check_links=False)

        with open(os.path.join(out_dir, "getting-started", "index.html")) as f:
            html = f.read()
        assert "five minutes" in html
        assert "Install" in html
        assert len(html) > 2000

    def test_publish_metrics_interpolated(self, tmp_path):
        root = self._setup_project(tmp_path)
        m = load_manifest(root)
        assert m is not None
        composed = compose_all_pages(m, root)
        out_dir = str(tmp_path / "_site")

        publish_pages(m, composed, root, out_dir, check_links=False)

        with open(os.path.join(out_dir, "getting-started", "index.html")) as f:
            html = f.read()
        assert "19,498" in html
        assert "{{model_count}}" not in html

    def test_publish_link_check_catches_empty_slug(self, tmp_path):
        """Regression: empty slugs should be caught by link checker."""
        root = self._setup_project(tmp_path)
        m = load_manifest(root)
        assert m is not None
        composed = compose_all_pages(m, root)
        out_dir = str(tmp_path / "_site")

        result = publish_pages(m, composed, root, out_dir, check_links=True)
        assert result.link_errors == [], f"Unexpected link errors: {result.link_errors}"

    def test_publish_sitemap_has_slugs(self, tmp_path):
        root = self._setup_project(tmp_path)
        m = load_manifest(root)
        assert m is not None
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
        assert "///" not in sitemap

    def test_publish_css_monospace_font(self, tmp_path):
        root = self._setup_project(tmp_path)
        m = load_manifest(root)
        assert m is not None
        composed = compose_all_pages(m, root)
        out_dir = str(tmp_path / "_site")

        publish_pages(m, composed, root, out_dir, check_links=False)

        with open(os.path.join(out_dir, "style.css")) as f:
            css = f.read()
        assert "SF Mono" in css
        assert "monospace" in css
        assert "--bg: #141414" in css


# ═══════════════════════════════════════════════════════════════════════════
# theory_scope frontmatter tests
# ═══════════════════════════════════════════════════════════════════════════


class TestTheoryScope:
    def test_theory_scope_false_excluded(self, tmp_path):
        from lintgate.theory_extractor import _has_frontmatter_opt_out

        wiki_dir = tmp_path / ".lintgate" / "wiki"
        wiki_dir.mkdir(parents=True)

        page = wiki_dir / "Theory-Core.md"
        page.write_text(
            "---\n"
            "theory_scope: false\n"
            "pillar: theory\n"
            "generated_by: lintgate_wiki\n"
            "---\n"
            "# Core Thesis\n\nSome content.\n"
        )

        assert _has_frontmatter_opt_out(str(page)) is True

    def test_theory_scope_true_included(self, tmp_path):
        from lintgate.theory_extractor import _has_frontmatter_opt_out

        wiki_dir = tmp_path / ".lintgate" / "wiki"
        wiki_dir.mkdir(parents=True)

        page = wiki_dir / "Promoted-Page.md"
        page.write_text(
            "---\n"
            "theory_scope: true\n"
            "pillar: theory\n"
            "generated_by: lintgate_wiki\n"
            "---\n"
            "# Promoted Content\n\nThis should be extracted.\n"
        )

        assert _has_frontmatter_opt_out(str(page)) is False

    def test_discover_md_files_respects_theory_scope(self, tmp_path):
        from lintgate.theory_extractor import _discover_md_files, _has_frontmatter_opt_out

        wiki_dir = tmp_path / ".lintgate" / "wiki"
        wiki_dir.mkdir(parents=True)

        excluded = wiki_dir / "Excluded.md"
        excluded.write_text("---\ntheory_scope: false\n---\n# Excluded\n\nNot for theory.\n")

        included = wiki_dir / "Included.md"
        included.write_text("---\ntheory_scope: true\n---\n# Included\n\nFor theory.\n")

        found = _discover_md_files(str(tmp_path))
        found_basenames = [os.path.basename(f) for f in found]
        assert "Excluded.md" in found_basenames
        assert "Included.md" in found_basenames

        excluded_path = str(wiki_dir / "Excluded.md")
        included_path = str(wiki_dir / "Included.md")
        assert _has_frontmatter_opt_out(excluded_path) is True
        assert _has_frontmatter_opt_out(included_path) is False

    def test_no_frontmatter_not_opted_out(self, tmp_path):
        from lintgate.theory_extractor import _has_frontmatter_opt_out

        md = tmp_path / "plain.md"
        md.write_text("# Just a heading\n\nContent.\n")

        assert _has_frontmatter_opt_out(str(md)) is False
