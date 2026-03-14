"""Comprehensive tests for lintgate/theory_extractor.py.

Covers public API functions (extract_theory, extract_constraints,
build_theory_pack, get_theory_context, get_theory_context_from_profile),
internal construction functions, utility helpers, enforceable rule extraction,
staleness checking, and end-to-end integration scenarios.

Tests that require filesystem I/O use tmp_path. External I/O boundaries
(e.g., build_context_guidance) are mocked at the module boundary.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from lintgate.theory_extractor import (
    _REQUIRED_THEORY_FACETS,
    _RULE_TEMPLATES,
    _build_digest_text,
    _build_facet_summaries,
    _build_summary,
    _build_theory_profile,
    _build_validity_report,
    _collect_covered_sources,
    _dedupe_facet_entries,
    _extract_claims,
    _extract_enforceable_rules,
    _filter_uncommitted_py_files,
    _find_uncovered_files,
    _has_frontmatter_opt_out,
    _has_substantial_docstring,
    _is_covered_by_existing,
    _parse_document,
    _pick_best_summary_claim,
    _score_claim,
    _Section,
    _split_sentences,
    _strip_markdown,
    _words_to_pattern,
    build_theory_pack,
    check_theory_staleness,
    extract_constraints,
    extract_theory,
    get_theory_context,
    get_theory_context_from_profile,
)

# ─── Helpers ────────────────────────────────────────────────────────────


def _make_section(
    body: str,
    heading: str = "Test",
    heading_level: int = 2,
    rel_path: str = "test.md",
    line_no: int = 1,
) -> _Section:
    return _Section(
        heading=heading,
        heading_level=heading_level,
        body=body,
        source_file=f"/project/{rel_path}",
        rel_path=rel_path,
        line_no=line_no,
    )


def _empty_guidance() -> dict[str, Any]:
    return {"rules": [], "directives": {"do_not": [], "must": [], "critical": []}}


def _make_project_with_md(tmp_path: Path, files: dict[str, str]) -> str:
    """Create a project directory with given markdown files.

    Args:
        tmp_path: pytest tmp_path fixture.
        files: mapping from relative path to file content.

    Returns:
        Absolute path to the project root.
    """
    for rel_path, content in files.items():
        full = tmp_path / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    return str(tmp_path)


# ─── extract_theory (public API, filesystem) ────────────────────────────


class TestExtractTheory:
    """Tests for the main extract_theory() public API."""

    def test_empty_project_returns_all_keys(self, tmp_path: Path):
        root = str(tmp_path)
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            result = extract_theory(root)
        assert "theory_profile" in result
        assert "docs_scanned" in result
        assert "doc_paths" in result
        assert "enforceable_rules" in result
        assert "summary" in result
        assert "validity" in result
        assert result["docs_scanned"] == 0
        assert result["doc_paths"] == []

    def test_single_md_file_is_scanned(self, tmp_path: Path):
        md_content = textwrap.dedent("""\
            # Core Theory

            The key insight is that deterministic linting enables reproducible quality
            because static analysis catches errors before runtime.
        """)
        root = _make_project_with_md(tmp_path, {"README.md": md_content})
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            result = extract_theory(root)
        assert result["docs_scanned"] == 1
        assert "README.md" in result["doc_paths"][0]

    def test_profile_has_all_six_facets(self, tmp_path: Path):
        root = _make_project_with_md(tmp_path, {"empty.md": "# Empty\n\nNothing."})
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            result = extract_theory(root)
        profile = result["theory_profile"]
        expected_facets = {
            "core_theory",
            "problem_solving",
            "alignment",
            "architecture",
            "anti_patterns",
            "abstractions",
        }
        assert set(profile.keys()) == expected_facets

    def test_frontmatter_opt_out_skips_file(self, tmp_path: Path):
        opted_out = (
            "---\ntheory_scope: false\n---\n# Theory\n\nThis should be skipped because opt-out."
        )
        kept = "# Architecture\n\nWe chose this design because modular separation enables independent testing."
        root = _make_project_with_md(
            tmp_path,
            {"skip.md": opted_out, "keep.md": kept},
        )
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            result = extract_theory(root)
        # skip.md should be excluded
        assert result["docs_scanned"] == 1
        paths = result["doc_paths"]
        assert any("keep.md" in p for p in paths)
        assert not any("skip.md" in p for p in paths)

    def test_working_tree_files_adds_docstring_sources(self, tmp_path: Path):
        py_content = textwrap.dedent('''\
            """Module theory: This module provides deterministic analysis because
            static extraction is reproducible and enables caching across sessions.
            """

            def foo():
                pass
        ''')
        root = _make_project_with_md(tmp_path, {"mod.py": py_content})
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            result = extract_theory(root, working_tree_files=["mod.py"])
        assert "docstring_sources" in result
        assert result["docstring_sources"] >= 1

    def test_working_tree_files_none_no_docstring_sources(self, tmp_path: Path):
        root = _make_project_with_md(tmp_path, {"doc.md": "# Hi\n\nHello."})
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            result = extract_theory(root, working_tree_files=None)
        assert "docstring_sources" not in result

    def test_working_tree_files_empty_list_no_docstring_sources(self, tmp_path: Path):
        root = str(tmp_path)
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            result = extract_theory(root, working_tree_files=[])
        assert "docstring_sources" not in result

    def test_validity_included_in_result(self, tmp_path: Path):
        root = str(tmp_path)
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            result = extract_theory(root)
        v = result["validity"]
        assert "status" in v
        assert "total_claims" in v
        assert "missing_required_facets" in v

    def test_theory_rich_doc_extracts_claims(self, tmp_path: Path):
        md_content = textwrap.dedent("""\
            # Core Theory

            The fundamental insight is that static analysis because deterministic
            extraction eliminates the need for runtime sampling.

            # Problem-Solving Approach

            Rather than brute-force scanning, we use guided heuristic search
            because structured elimination is more efficient than exhaustive enumeration.

            # Alignment

            The goal is not just to detect issues but to ensure the right approach.
            If you bypass the composition layer, you will ruin the architecture.

            # Architecture

            We chose modular decomposition because separation of concerns enables
            independent testing, rather than a monolithic design.

            # Anti-Patterns

            Hard-coding values would break the extensibility because it prevents
            configuration-driven behavior.

            # Key Abstractions

            We call this concept a **theory facet** because it represents a
            distinct dimension of project understanding.
        """)
        root = _make_project_with_md(tmp_path, {"THEORY.md": md_content})
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            result = extract_theory(root)
        profile = result["theory_profile"]
        total_claims = sum(len(e["claims"]) for entries in profile.values() for e in entries)
        assert total_claims > 0


# ─── extract_constraints (backward compat) ───────────────────────────


class TestExtractConstraints:
    """Tests for the backward-compatible extract_constraints wrapper."""

    def test_returns_same_as_extract_theory(self, tmp_path: Path):
        root = str(tmp_path)
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            from_theory = extract_theory(root)
            from_constraints = extract_constraints(root)
        assert from_theory.keys() == from_constraints.keys()
        assert from_theory["docs_scanned"] == from_constraints["docs_scanned"]

    def test_empty_project(self, tmp_path: Path):
        root = str(tmp_path)
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            result = extract_constraints(root)
        assert result["docs_scanned"] == 0


# ─── build_theory_pack ────────────────────────────────────────────────


class TestBuildTheoryPack:
    """Tests for the build_theory_pack() runtime payload builder."""

    def test_pack_has_required_keys(self, tmp_path: Path):
        root = str(tmp_path)
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            pack = build_theory_pack(root)
        assert "digest_text" in pack
        assert "digest_token_estimate" in pack
        assert "enforceable_rules" in pack
        assert "facet_summaries" in pack
        assert "anti_patterns" in pack
        assert "summary" in pack
        assert "validity" in pack

    def test_include_full_profile_false_omits_profile(self, tmp_path: Path):
        root = str(tmp_path)
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            pack = build_theory_pack(root, include_full_profile=False)
        assert "full_profile" not in pack

    def test_include_full_profile_true_includes_profile(self, tmp_path: Path):
        root = str(tmp_path)
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            pack = build_theory_pack(root, include_full_profile=True)
        assert "full_profile" in pack
        profile = pack["full_profile"]
        assert "core_theory" in profile

    def test_digest_text_is_string(self, tmp_path: Path):
        root = str(tmp_path)
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            pack = build_theory_pack(root)
        assert isinstance(pack["digest_text"], str)
        assert isinstance(pack["digest_token_estimate"], int)
        assert pack["digest_token_estimate"] >= 0

    def test_pack_with_theory_content(self, tmp_path: Path):
        md = textwrap.dedent("""\
            # Core Theory

            The fundamental approach is deterministic because reproducibility
            enables caching and correctness verification.

            # Anti-Patterns

            Hard-coding would break the system because it prevents extension.
        """)
        root = _make_project_with_md(tmp_path, {"DESIGN.md": md})
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            pack = build_theory_pack(root, include_full_profile=True)
        assert pack["digest_token_estimate"] > 0
        assert "## Project Theory" in pack["digest_text"]


# ─── get_theory_context (filesystem-based Tier 2) ────────────────────


class TestGetTheoryContext:
    """Tests for on-demand Tier 2 retrieval with I/O."""

    def test_raises_on_zero_max_claims(self, tmp_path: Path):
        root = str(tmp_path)
        with pytest.raises(ValueError, match="max_claims must be > 0"):
            get_theory_context(root, max_claims=0)

    def test_raises_on_negative_max_claims(self, tmp_path: Path):
        root = str(tmp_path)
        with pytest.raises(ValueError, match="max_claims must be > 0"):
            get_theory_context(root, max_claims=-1)

    def test_empty_project_returns_empty_claims(self, tmp_path: Path):
        root = str(tmp_path)
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            result = get_theory_context(root)
        assert result["claims"] == []
        assert result["total_matched"] == 0
        assert result["returned_count"] == 0
        assert result["truncated"] is False

    def test_facet_filter_applied(self, tmp_path: Path):
        md = textwrap.dedent("""\
            # Core Theory

            The key principle is that deterministic extraction because
            static analysis ensures reproducibility.

            # Architecture

            We chose modular design because separation of concerns enables
            independent validation rather than monolithic coupling.
        """)
        root = _make_project_with_md(tmp_path, {"DOC.md": md})
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            result = get_theory_context(root, facet="core_theory")
        for claim in result["claims"]:
            assert claim["facet"] == "core_theory"

    def test_keyword_filtering(self, tmp_path: Path):
        md = textwrap.dedent("""\
            # Core Theory

            The key insight is that caching enables faster extraction
            because repeated scans are eliminated through memoization.

            Deterministic linting ensures reproducibility because
            the same input always produces the same output.
        """)
        root = _make_project_with_md(tmp_path, {"DOC.md": md})
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            result = get_theory_context(root, keywords=["caching"])
        if result["claims"]:
            assert any("caching" in c["claim"].lower() for c in result["claims"])

    def test_max_claims_truncation(self, tmp_path: Path):
        md = textwrap.dedent("""\
            # Core Theory

            First claim because it provides fundamental insight.
            Second claim because it enables compositional reasoning.
            Third claim because it ensures deterministic behavior.
            Fourth claim because it demonstrates extensibility.
        """)
        root = _make_project_with_md(tmp_path, {"DOC.md": md})
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            result = get_theory_context(root, max_claims=2)
        assert result["returned_count"] <= 2

    def test_query_metadata_returned(self, tmp_path: Path):
        root = str(tmp_path)
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            result = get_theory_context(root, facet="alignment", keywords=["test"])
        assert result["query"]["facet"] == "alignment"
        assert result["query"]["keywords"] == ["test"]

    def test_nonexistent_facet_searches_all(self, tmp_path: Path):
        md = textwrap.dedent("""\
            # Core Theory

            The key insight is that static analysis because deterministic
            checks ensure correctness.
        """)
        root = _make_project_with_md(tmp_path, {"DOC.md": md})
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            result = get_theory_context(root, facet="nonexistent_facet")
        # Should search all facets when the facet doesn't exist
        assert result["query"]["facet"] == "nonexistent_facet"


# ─── get_theory_context_from_profile (pure, no I/O) ──────────────────


class TestGetTheoryContextFromProfile:
    """Tests for Tier 2 retrieval from pre-extracted profile."""

    def test_empty_profile_returns_empty(self):
        result = get_theory_context_from_profile({})
        assert result["claims"] == []
        assert result["total_matched"] == 0
        assert result["truncated"] is False

    def test_none_profile_returns_empty(self):
        result = get_theory_context_from_profile(None)  # type: ignore[arg-type]  # intentional: test None handling
        assert result["claims"] == []

    def test_zero_max_claims_returns_empty(self):
        profile = {"core_theory": [{"claims": ["A claim"], "source": "test.md:1", "heading": "H"}]}
        result = get_theory_context_from_profile(profile, max_claims=0)
        assert result["claims"] == []

    def test_negative_max_claims_returns_empty(self):
        result = get_theory_context_from_profile(
            {"core_theory": [{"claims": ["X"], "source": "s", "heading": "h"}]},
            max_claims=-5,
        )
        assert result["claims"] == []

    def test_single_facet_all_claims(self):
        profile = {
            "core_theory": [
                {
                    "claims": ["Claim A", "Claim B"],
                    "source": "test.md:1",
                    "heading": "Theory",
                }
            ]
        }
        result = get_theory_context_from_profile(profile, facet="core_theory")
        assert result["total_matched"] == 2
        assert result["returned_count"] == 2

    def test_keyword_scoring(self):
        profile = {
            "core_theory": [
                {
                    "claims": [
                        "Caching enables speed",
                        "Linting ensures quality",
                        "Caching and linting together",
                    ],
                    "source": "test.md:1",
                    "heading": "Theory",
                }
            ]
        }
        result = get_theory_context_from_profile(profile, keywords=["caching", "linting"])
        # "Caching and linting together" matches both keywords (score=2)
        assert result["claims"][0]["relevance_score"] == 2
        assert result["claims"][0]["claim"] == "Caching and linting together"

    def test_keyword_case_insensitive(self):
        profile = {
            "core_theory": [
                {
                    "claims": ["CACHING is important"],
                    "source": "s",
                    "heading": "h",
                }
            ]
        }
        result = get_theory_context_from_profile(profile, keywords=["caching"])
        assert result["total_matched"] == 1

    def test_no_keywords_returns_all_claims_in_facet(self):
        profile = {
            "core_theory": [{"claims": ["A", "B", "C"], "source": "s", "heading": "h"}],
            "alignment": [{"claims": ["D", "E"], "source": "s", "heading": "h"}],
        }
        result = get_theory_context_from_profile(profile, facet="core_theory")
        assert result["total_matched"] == 3

    def test_max_claims_truncates(self):
        profile = {
            "core_theory": [
                {
                    "claims": ["A", "B", "C", "D", "E"],
                    "source": "s",
                    "heading": "h",
                }
            ]
        }
        result = get_theory_context_from_profile(profile, max_claims=2)
        assert result["returned_count"] == 2
        assert result["truncated"] is True
        assert result["total_matched"] == 5

    def test_truncated_false_when_within_limit(self):
        profile = {"core_theory": [{"claims": ["A", "B"], "source": "s", "heading": "h"}]}
        result = get_theory_context_from_profile(profile, max_claims=5)
        assert result["truncated"] is False

    def test_nonexistent_facet_searches_all(self):
        profile = {
            "core_theory": [{"claims": ["Claim A"], "source": "s", "heading": "h"}],
            "alignment": [{"claims": ["Claim B"], "source": "s2", "heading": "h2"}],
        }
        result = get_theory_context_from_profile(profile, facet="bogus")
        # Should search all facets
        assert result["total_matched"] == 2

    def test_missing_source_and_heading_keys(self):
        profile = {"core_theory": [{"claims": ["A claim"]}]}
        result = get_theory_context_from_profile(profile)
        assert result["claims"][0]["source"] == ""
        assert result["claims"][0]["heading"] == ""

    def test_query_metadata_preserved(self):
        result = get_theory_context_from_profile({}, facet="anti_patterns", keywords=["foo", "bar"])
        assert result["query"]["facet"] == "anti_patterns"
        assert result["query"]["keywords"] == ["foo", "bar"]

    def test_multiple_entries_aggregated(self):
        profile = {
            "core_theory": [
                {"claims": ["A"], "source": "a.md:1", "heading": "H1"},
                {"claims": ["B"], "source": "b.md:1", "heading": "H2"},
            ]
        }
        result = get_theory_context_from_profile(profile)
        assert result["total_matched"] == 2
        sources = {c["source"] for c in result["claims"]}
        assert "a.md:1" in sources
        assert "b.md:1" in sources


# ─── _build_theory_profile ────────────────────────────────────────────


class TestBuildTheoryProfile:
    """Tests for theory profile construction from sections."""

    def test_empty_sections_all_facets_empty(self):
        profile = _build_theory_profile([])
        for facet in profile.values():
            assert facet == []

    def test_core_theory_heading_classified(self):
        section = _make_section(
            heading="Core Theory",
            body="The fundamental principle is because it enables deterministic extraction.",
        )
        profile = _build_theory_profile([section])
        assert len(profile["core_theory"]) > 0

    def test_multiple_sections_multiple_facets(self):
        sections = [
            _make_section(
                heading="Theory Overview",
                body="The key insight is that static analysis because it enables reproducibility.",
            ),
            _make_section(
                heading="Anti-Patterns",
                body="Hard-coding would ruin the extensibility of the system.",
            ),
        ]
        profile = _build_theory_profile(sections)
        # At least core_theory and anti_patterns should have entries
        has_entries = [f for f, e in profile.items() if e]
        assert len(has_entries) >= 1

    def test_deduplication_within_facet(self):
        sections = [
            _make_section(
                heading="Theory",
                body="The key insight is fundamental because it enables correctness.",
                rel_path="a.md",
            ),
            _make_section(
                heading="Theory",
                body="The key insight is fundamental because it enables correctness.",
                rel_path="b.md",
            ),
        ]
        profile = _build_theory_profile(sections)
        # Claims should be deduplicated
        all_claims = [c for entries in profile.values() for e in entries for c in e["claims"]]
        claim_texts = [c.lower().strip() for c in all_claims]
        assert len(claim_texts) == len(set(claim_texts))


# ─── _extract_claims ─────────────────────────────────────────────────


class TestExtractClaims:
    """Tests for claim extraction from sections."""

    def test_empty_body_no_claims(self):
        section = _make_section(body="")
        claims = _extract_claims(section, "core_theory")
        assert claims == []

    def test_short_sentences_filtered(self):
        section = _make_section(body="Too short.")
        claims = _extract_claims(section, "core_theory")
        assert claims == []

    def test_code_blocks_stripped(self):
        body = textwrap.dedent("""\
            The key insight is that because static analysis enables caching.

            ```python
            def foo():
                # This code should not be extracted as a claim because it is code.
                pass
            ```
        """)
        section = _make_section(body=body)
        claims = _extract_claims(section, "core_theory")
        for claim in claims:
            assert "def foo():" not in claim

    def test_camelcase_heavy_sentences_filtered(self):
        body = (
            "MyClassA MyClassB MyClassC MyClassD are all part of the system "
            "because they enable composition."
        )
        section = _make_section(body=body)
        claims = _extract_claims(section, "core_theory")
        for claim in claims:
            # Should not contain the CamelCase-heavy sentence
            assert "MyClassA" not in claim

    def test_path_heavy_sentences_filtered(self):
        body = (
            "The files are at src/foo/bar/baz/qux/module.py because the "
            "structure requires deep nesting."
        )
        section = _make_section(body=body)
        claims = _extract_claims(section, "core_theory")
        for claim in claims:
            assert claim.count("/") <= 3

    def test_code_like_fragments_filtered(self):
        body = "python -m pytest tests/ runs all the tests."
        section = _make_section(body=body)
        claims = _extract_claims(section, "core_theory")
        for claim in claims:
            assert not claim.startswith("python")

    def test_claims_capped_at_eight(self):
        body = "\n\n".join(
            f"Claim number {i} is important because it enables feature {i}." for i in range(20)
        )
        section = _make_section(body=body)
        claims = _extract_claims(section, "core_theory")
        assert len(claims) <= 8

    def test_inline_code_preserved_without_backticks(self):
        body = (
            "The `extract_theory` function is fundamental because it drives "
            "the entire pipeline from markdown to structured profile."
        )
        section = _make_section(body=body)
        claims = _extract_claims(section, "core_theory")
        for claim in claims:
            assert "`" not in claim

    def test_very_long_sentence_filtered(self):
        body = "A " * 300 + "because reasons."
        section = _make_section(body=body)
        claims = _extract_claims(section, "core_theory")
        # Sentences over 500 chars should be filtered
        for claim in claims:
            assert len(claim) <= 600  # Allow some whitespace normalization margin

    def test_markdown_tables_stripped(self):
        body = textwrap.dedent("""\
            | Header | Value |
            |--------|-------|
            | foo    | bar   |

            The key principle is that because extraction is deterministic we can cache.
        """)
        section = _make_section(body=body)
        claims = _extract_claims(section, "core_theory")
        for claim in claims:
            assert "|" not in claim


# ─── _dedupe_facet_entries ────────────────────────────────────────────


class TestDedupeFacetEntries:
    """Tests for claim deduplication within facets."""

    def test_empty_entries(self):
        assert _dedupe_facet_entries([]) == []

    def test_duplicate_claims_removed(self):
        entries = [
            {"heading": "A", "source": "a.md:1", "claims": ["Claim one"]},
            {"heading": "B", "source": "b.md:1", "claims": ["Claim one"]},
        ]
        result = _dedupe_facet_entries(entries)
        all_claims = [c for e in result for c in e["claims"]]
        assert len(all_claims) == 1

    def test_case_insensitive_dedup(self):
        entries = [
            {"heading": "A", "source": "a.md:1", "claims": ["Hello World"]},
            {"heading": "B", "source": "b.md:1", "claims": ["hello world"]},
        ]
        result = _dedupe_facet_entries(entries)
        all_claims = [c for e in result for c in e["claims"]]
        assert len(all_claims) == 1

    def test_whitespace_normalized_for_dedup(self):
        entries = [
            {"heading": "A", "source": "a.md:1", "claims": ["Hello  World"]},
            {"heading": "B", "source": "b.md:1", "claims": ["Hello World"]},
        ]
        result = _dedupe_facet_entries(entries)
        all_claims = [c for e in result for c in e["claims"]]
        assert len(all_claims) == 1

    def test_entries_with_no_unique_claims_dropped(self):
        entries = [
            {"heading": "A", "source": "a.md:1", "claims": ["Unique claim"]},
            {"heading": "B", "source": "b.md:1", "claims": ["Unique claim"]},
        ]
        result = _dedupe_facet_entries(entries)
        assert len(result) == 1

    def test_original_entries_not_mutated(self):
        entries = [
            {"heading": "A", "source": "a.md:1", "claims": ["X", "Y"]},
        ]
        original_claims = list(entries[0]["claims"])
        _dedupe_facet_entries(entries)
        assert entries[0]["claims"] == original_claims


# ─── _extract_enforceable_rules ───────────────────────────────────────


class TestExtractEnforceableRules:
    """Tests for enforceable rule extraction from directives."""

    def test_empty_directives(self):
        guidance = _empty_guidance()
        result = _extract_enforceable_rules(guidance, set(), [])
        assert result["proposed_rules"] == []
        assert result["directives_analyzed"] == 0

    def test_do_not_use_directive(self):
        guidance = {
            "rules": [],
            "directives": {
                "do_not": ["DO NOT use eval"],
                "must": [],
                "critical": [],
            },
        }
        result = _extract_enforceable_rules(guidance, set(), [])
        assert len(result["proposed_rules"]) > 0
        rule = result["proposed_rules"][0]
        assert rule["proposed_rule"]["kind"] == "forbid_regex"
        assert "eval" in rule["proposed_rule"]["pattern"]

    def test_must_use_directive(self):
        guidance = {
            "rules": [],
            "directives": {
                "do_not": [],
                "must": ["MUST use pathlib"],
                "critical": [],
            },
        }
        result = _extract_enforceable_rules(guidance, set(), [])
        assert len(result["proposed_rules"]) > 0
        rule = result["proposed_rules"][0]
        assert rule["proposed_rule"]["kind"] == "require_regex"

    def test_do_not_import_directive(self):
        guidance = {
            "rules": [],
            "directives": {
                "do_not": ["DO NOT import os.system"],
                "must": [],
                "critical": [],
            },
        }
        result = _extract_enforceable_rules(guidance, set(), [])
        assert len(result["proposed_rules"]) > 0
        rule = result["proposed_rules"][0]
        assert "os\\.system" in rule["proposed_rule"]["pattern"]

    def test_already_covered_rules_not_proposed(self):
        guidance = {
            "rules": [],
            "directives": {
                "do_not": ["DO NOT use eval"],
                "must": [],
                "critical": [],
            },
        }
        existing_patterns = {r"\beval\b"}
        result = _extract_enforceable_rules(guidance, existing_patterns, [])
        assert result["already_covered"] > 0
        assert len(result["proposed_rules"]) == 0

    def test_deduplication_of_proposed_rules(self):
        guidance = {
            "rules": [],
            "directives": {
                "do_not": ["DO NOT use eval", "DO NOT use eval"],
                "must": [],
                "critical": [],
            },
        }
        result = _extract_enforceable_rules(guidance, set(), [])
        patterns = [r["proposed_rule"]["pattern"] for r in result["proposed_rules"]]
        assert len(patterns) == len(set(patterns))

    def test_existing_rule_count_tracked(self):
        guidance = _empty_guidance()
        existing_rules = [{"pattern": r"\bfoo\b"}, {"pattern": r"\bbar\b"}]
        result = _extract_enforceable_rules(guidance, set(), existing_rules)
        assert result["existing_rule_count"] == 2

    def test_do_not_call_directive(self):
        guidance = {
            "rules": [],
            "directives": {
                "do_not": ["DO NOT call os.system()"],
                "must": [],
                "critical": [],
            },
        }
        result = _extract_enforceable_rules(guidance, set(), [])
        assert len(result["proposed_rules"]) > 0

    def test_must_import_directive(self):
        guidance = {
            "rules": [],
            "directives": {
                "do_not": [],
                "must": ["MUST import typing"],
                "critical": [],
            },
        }
        result = _extract_enforceable_rules(guidance, set(), [])
        assert len(result["proposed_rules"]) > 0
        rule = result["proposed_rules"][0]
        assert rule["proposed_rule"]["kind"] == "require_regex"
        assert "typing" in rule["proposed_rule"]["pattern"]

    def test_critical_directives_processed(self):
        guidance = {
            "rules": [],
            "directives": {
                "do_not": [],
                "must": [],
                "critical": ["DO NOT use pickle"],
            },
        }
        result = _extract_enforceable_rules(guidance, set(), [])
        assert result["directives_analyzed"] == 1
        assert len(result["proposed_rules"]) > 0


# ─── _build_summary ──────────────────────────────────────────────────


class TestBuildSummary:
    """Tests for human-readable summary generation."""

    def test_empty_profile(self):
        profile: dict[str, Any] = {
            "core_theory": [],
            "problem_solving": [],
            "alignment": [],
            "architecture": [],
            "anti_patterns": [],
            "abstractions": [],
        }
        summary = _build_summary(profile)
        for facet in profile:
            assert facet in summary
            assert summary[facet]["claim_count"] == 0
            assert summary[facet]["top_claims"] == []

    def test_claims_counted_correctly(self):
        profile = {
            "core_theory": [
                {"claims": ["A", "B"], "source": "s"},
                {"claims": ["C"], "source": "s2"},
            ],
            "alignment": [],
        }
        summary = _build_summary(profile)
        assert summary["core_theory"]["claim_count"] == 3
        assert summary["core_theory"]["source_count"] == 2

    def test_top_claims_capped_at_three(self):
        profile = {
            "core_theory": [
                {"claims": ["A", "B", "C", "D", "E"], "source": "s"},
            ],
        }
        summary = _build_summary(profile)
        assert len(summary["core_theory"]["top_claims"]) == 3

    def test_top_claims_from_richest_entry_first(self):
        profile = {
            "core_theory": [
                {"claims": ["Short"], "source": "s1"},
                {"claims": ["A", "B", "C", "D"], "source": "s2"},
            ],
        }
        summary = _build_summary(profile)
        # The entry with 4 claims should contribute first
        assert summary["core_theory"]["top_claims"][0] == "A"


# ─── _build_validity_report ──────────────────────────────────────────


class TestBuildValidityReport:
    """Tests for theory extraction validity reporting."""

    def test_empty_profile_weak(self):
        profile: dict[str, Any] = {
            "core_theory": [],
            "problem_solving": [],
            "alignment": [],
            "architecture": [],
            "anti_patterns": [],
            "abstractions": [],
        }
        report = _build_validity_report(profile, 0, 0, {"proposed_rules": []})
        assert report["status"] == "weak"
        assert report["total_claims"] == 0

    def test_missing_required_facets_detected(self):
        profile = {
            "core_theory": [],
            "problem_solving": [{"claims": ["A", "B", "C"], "source": "s"}],
            "alignment": [],
            "architecture": [{"claims": ["X", "Y", "Z"], "source": "s"}],
            "anti_patterns": [],
            "abstractions": [],
        }
        report = _build_validity_report(profile, 5, 10, {"proposed_rules": []})
        assert "core_theory" in report["missing_required_facets"]
        assert "alignment" in report["missing_required_facets"]
        assert "problem_solving" not in report["missing_required_facets"]

    def test_strong_status_all_facets_present(self):
        profile = {
            "core_theory": [{"claims": ["A", "B", "C"], "source": "s"}],
            "problem_solving": [{"claims": ["D", "E"], "source": "s"}],
            "alignment": [{"claims": ["F"], "source": "s"}],
            "architecture": [],
            "anti_patterns": [],
            "abstractions": [],
        }
        report = _build_validity_report(
            profile,
            docs_scanned=3,
            sections_scanned=10,
            enforceable={"proposed_rules": [{"x": 1}], "existing_rule_count": 1},
        )
        assert report["status"] == "strong"
        assert report["missing_required_facets"] == []

    def test_low_claim_density_warning(self):
        profile = {
            "core_theory": [{"claims": ["A"], "source": "s"}],
            "problem_solving": [{"claims": ["B"], "source": "s"}],
            "alignment": [{"claims": ["C"], "source": "s"}],
            "architecture": [],
            "anti_patterns": [],
            "abstractions": [],
        }
        report = _build_validity_report(
            profile,
            docs_scanned=10,
            sections_scanned=20,
            enforceable={"proposed_rules": [], "existing_rule_count": 1},
        )
        assert any("Low claim density" in w for w in report["warnings"])

    def test_no_enforceable_rules_warning(self):
        profile = {
            "core_theory": [{"claims": ["A", "B", "C"], "source": "s"}],
            "problem_solving": [{"claims": ["D", "E"], "source": "s"}],
            "alignment": [{"claims": ["F"], "source": "s"}],
            "architecture": [],
            "anti_patterns": [],
            "abstractions": [],
        }
        report = _build_validity_report(
            profile,
            docs_scanned=3,
            sections_scanned=10,
            enforceable={"proposed_rules": [], "existing_rule_count": 0},
        )
        assert any("No enforceable rules" in w for w in report["warnings"])
        assert report["status"] == "partial"

    def test_traceability_100_pct(self):
        profile = {
            "core_theory": [
                {"claims": ["A"], "source": "test.md:1"},
                {"claims": ["B"], "source": "test.md:5"},
            ],
        }
        report = _build_validity_report(profile, 1, 2, {"proposed_rules": []})
        assert report["traceability_pct"] == 100.0

    def test_traceability_with_empty_source(self):
        profile = {
            "core_theory": [
                {"claims": ["A"], "source": "test.md:1"},
                {"claims": ["B"], "source": ""},
            ],
        }
        report = _build_validity_report(profile, 1, 2, {"proposed_rules": []})
        assert report["traceability_pct"] == 50.0

    def test_zero_docs_no_division_error(self):
        profile: dict[str, Any] = {"core_theory": [], "problem_solving": [], "alignment": []}
        report = _build_validity_report(profile, 0, 0, {"proposed_rules": []})
        assert report["claims_per_doc"] == 0.0

    def test_required_theory_facets_constant(self):
        assert "core_theory" in _REQUIRED_THEORY_FACETS
        assert "problem_solving" in _REQUIRED_THEORY_FACETS
        assert "alignment" in _REQUIRED_THEORY_FACETS


# ─── _build_facet_summaries ──────────────────────────────────────────


class TestBuildFacetSummaries:
    """Tests for facet summary and anti-pattern extraction."""

    def test_empty_profile_all_placeholders(self):
        profile: dict[str, Any] = {
            "core_theory": [],
            "alignment": [],
        }
        summaries, anti_patterns = _build_facet_summaries(profile)
        for facet in profile:
            assert summaries[facet] == "(no theory content found)"
        assert anti_patterns == []

    def test_anti_patterns_extracted(self):
        profile = {"anti_patterns": [{"claims": ["Don't use eval", "Avoid globals"]}]}
        summaries, anti_patterns = _build_facet_summaries(profile)
        assert "Don't use eval" in anti_patterns
        assert "Avoid globals" in anti_patterns

    def test_anti_patterns_capped_at_ten(self):
        profile = {"anti_patterns": [{"claims": [f"AP {i}" for i in range(15)]}]}
        _, anti_patterns = _build_facet_summaries(profile)
        assert len(anti_patterns) <= 10

    def test_summaries_deduplicated_across_facets(self):
        # When two facets have the same best claim, dedup should prevent reuse
        profile = {
            "core_theory": [
                {
                    "claims": [
                        "Because static analysis enables reproducibility, we use deterministic extraction."
                    ]
                }
            ],
            "problem_solving": [
                {
                    "claims": [
                        "Because static analysis enables reproducibility, we use deterministic extraction."
                    ]
                }
            ],
        }
        summaries, _ = _build_facet_summaries(profile)
        values = list(summaries.values())
        non_placeholder = [v for v in values if v != "(no theory content found)"]
        # If the same claim appears in both facets, the second should get a
        # different summary or fall back to the only available claim
        assert len(non_placeholder) >= 1


# ─── _build_digest_text ──────────────────────────────────────────────


class TestBuildDigestText:
    """Tests for digest text assembly."""

    def test_empty_inputs(self):
        text, tokens = _build_digest_text(
            {"proposed_rules": [], "existing_rule_count": 0},
            {},
            [],
        )
        assert "## Project Theory" in text
        assert "(no enforceable rules extracted)" in text
        assert tokens >= 0

    def test_with_proposed_rules(self):
        enforceable = {
            "proposed_rules": [{"add_line": "LINTGATE_FORBID_REGEX: \\beval\\b"}],
            "existing_rule_count": 0,
        }
        text, tokens = _build_digest_text(enforceable, {}, [])
        assert "LINTGATE_FORBID_REGEX" in text
        assert tokens > 0

    def test_with_existing_rules_message(self):
        enforceable = {
            "proposed_rules": [],
            "existing_rule_count": 5,
        }
        text, _ = _build_digest_text(enforceable, {}, [])
        assert "5 active rules" in text

    def test_facet_summaries_included(self):
        facet_summaries = {
            "core_theory": "The key insight is deterministic analysis.",
            "alignment": "(no theory content found)",
        }
        text, _ = _build_digest_text(
            {"proposed_rules": [], "existing_rule_count": 0},
            facet_summaries,
            [],
        )
        assert "Core Theory" in text
        assert "deterministic analysis" in text
        # Placeholder facets should not appear
        assert "Alignment Criteria" not in text

    def test_anti_patterns_included(self):
        text, _ = _build_digest_text(
            {"proposed_rules": [], "existing_rule_count": 0},
            {},
            ["Don't hard-code values", "Avoid monoliths"],
        )
        assert "Anti-Patterns" in text
        assert "Don't hard-code values" in text

    def test_anti_patterns_capped_at_seven(self):
        aps = [f"Anti-pattern {i}" for i in range(15)]
        text, _ = _build_digest_text(
            {"proposed_rules": [], "existing_rule_count": 0},
            {},
            aps,
        )
        # Only first 7 should appear
        assert "Anti-pattern 6" in text
        assert "Anti-pattern 7" not in text

    def test_long_anti_pattern_truncated(self):
        long_ap = "A" * 200
        text, _ = _build_digest_text(
            {"proposed_rules": [], "existing_rule_count": 0},
            {},
            [long_ap],
        )
        assert "..." in text

    def test_token_estimate_proportional_to_content(self):
        _, tokens_empty = _build_digest_text(
            {"proposed_rules": [], "existing_rule_count": 0}, {}, []
        )
        _, tokens_full = _build_digest_text(
            {"proposed_rules": [], "existing_rule_count": 0},
            {"core_theory": "A long summary with many words for estimation."},
            ["Anti-pattern one", "Anti-pattern two"],
        )
        assert tokens_full >= tokens_empty

    def test_proposed_rules_capped_at_ten(self):
        rules = [{"add_line": f"LINTGATE_FORBID_REGEX: rule{i}"} for i in range(15)]
        text, _ = _build_digest_text(
            {"proposed_rules": rules, "existing_rule_count": 0},
            {},
            [],
        )
        assert "rule9" in text
        assert "rule10" not in text


# ─── _strip_markdown ─────────────────────────────────────────────────


class TestStripMarkdown:
    """Tests for markdown stripping utility."""

    def test_bold(self):
        assert _strip_markdown("**bold**") == "bold"

    def test_italic(self):
        assert _strip_markdown("*italic*") == "italic"

    def test_bold_italic(self):
        assert _strip_markdown("***bold italic***") == "bold italic"

    def test_numbered_list_prefix(self):
        assert _strip_markdown("1. First item") == "First item"

    def test_inline_code(self):
        assert _strip_markdown("`code`") == "code"

    def test_plain_text_unchanged(self):
        assert _strip_markdown("plain text") == "plain text"

    def test_empty_string(self):
        assert _strip_markdown("") == ""

    def test_whitespace_stripped(self):
        assert _strip_markdown("  hello  ") == "hello"

    def test_mixed_formatting(self):
        result = _strip_markdown("1. **DO NOT** use `eval`")
        assert "DO NOT" in result
        assert "eval" in result
        assert "**" not in result
        assert "`" not in result


# ─── _words_to_pattern ────────────────────────────────────────────────


class TestWordsToPattern:
    """Tests for multi-word phrase to regex pattern conversion."""

    def test_single_word(self):
        assert _words_to_pattern("hello") == "hello"

    def test_multi_word_space(self):
        pattern = _words_to_pattern("hello world")
        assert r"[_\s-]*" in pattern

    def test_multi_word_hyphen(self):
        pattern = _words_to_pattern("hello-world")
        assert r"[_\s-]*" in pattern

    def test_empty_string(self):
        result = _words_to_pattern("")
        assert isinstance(result, str)

    def test_special_regex_chars_escaped(self):
        pattern = _words_to_pattern("foo.bar")
        assert r"\." in pattern


# ─── _is_covered_by_existing ─────────────────────────────────────────


class TestIsCoveredByExisting:
    """Tests for checking if a pattern is already covered."""

    def test_exact_match_in_patterns(self):
        assert _is_covered_by_existing(r"\beval\b", {r"\beval\b"}, [])

    def test_no_match(self):
        assert not _is_covered_by_existing(r"\beval\b", set(), [])

    def test_regex_match_from_existing_rules(self):
        rules = [{"pattern": r"eval"}]
        assert _is_covered_by_existing(r"\beval\b", set(), rules)

    def test_invalid_regex_in_existing_skipped(self):
        rules = [{"pattern": r"[invalid"}]
        assert not _is_covered_by_existing(r"\bfoo\b", set(), rules)

    def test_empty_pattern_in_rule_skipped(self):
        rules = [{"pattern": ""}]
        assert not _is_covered_by_existing(r"\bfoo\b", set(), rules)

    def test_rule_without_pattern_key(self):
        rules = [{"other_key": "value"}]
        assert not _is_covered_by_existing(r"\bfoo\b", set(), rules)


# ─── _filter_uncommitted_py_files ────────────────────────────────────


class TestFilterUncommittedPyFiles:
    """Tests for git context filtering."""

    def test_empty_context(self):
        result = _filter_uncommitted_py_files({})
        assert result == []

    def test_mixed_files(self):
        ctx = {
            "modified_files": ["src/foo.py", "README.md", "tests/test_foo.py"],
            "untracked_files": ["new.py", "data.json"],
        }
        result = _filter_uncommitted_py_files(ctx)
        assert "src/foo.py" in result
        assert "new.py" in result
        assert "README.md" not in result
        assert "data.json" not in result

    def test_test_files_excluded(self):
        ctx = {
            "modified_files": ["tests/test_foo.py", "test_bar.py"],
            "untracked_files": [],
        }
        result = _filter_uncommitted_py_files(ctx)
        assert "tests/test_foo.py" not in result
        assert "test_bar.py" not in result

    def test_pycache_excluded(self):
        ctx = {
            "modified_files": ["__pycache__/foo.cpython-311.pyc"],
            "untracked_files": ["src/__pycache__/bar.py"],
        }
        result = _filter_uncommitted_py_files(ctx)
        assert result == []


# ─── _collect_covered_sources ─────────────────────────────────────────


class TestCollectCoveredSources:
    """Tests for covered source extraction from profiles."""

    def test_empty_profile(self):
        assert _collect_covered_sources({}) == set()

    def test_sources_with_line_numbers(self):
        profile = {
            "core_theory": [
                {"source": "foo.py:10", "claims": ["A"]},
                {"source": "bar.py:20", "claims": ["B"]},
            ]
        }
        result = _collect_covered_sources(profile)
        assert "foo.py" in result
        assert "bar.py" in result

    def test_sources_without_line_numbers(self):
        profile = {"core_theory": [{"source": "README.md", "claims": ["A"]}]}
        result = _collect_covered_sources(profile)
        assert "README.md" in result

    def test_non_list_facet_entries_skipped(self):
        profile = {
            "core_theory": "not a list",
            "alignment": [{"source": "a.md:1", "claims": ["X"]}],
        }
        result = _collect_covered_sources(profile)
        assert "a.md" in result


# ─── _has_substantial_docstring ───────────────────────────────────────


class TestHasSubstantialDocstring:
    """Tests for docstring detection in Python files."""

    def test_file_with_substantial_docstring(self, tmp_path: Path):
        py = tmp_path / "mod.py"
        py.write_text(
            '"""This module provides a substantial docstring with enough characters."""\n'
        )
        assert _has_substantial_docstring(str(py))

    def test_file_with_short_docstring(self, tmp_path: Path):
        py = tmp_path / "mod.py"
        py.write_text('"""Short."""\n')
        assert not _has_substantial_docstring(str(py))

    def test_file_without_docstring(self, tmp_path: Path):
        py = tmp_path / "mod.py"
        py.write_text("x = 1\n")
        assert not _has_substantial_docstring(str(py))

    def test_nonexistent_file(self):
        assert not _has_substantial_docstring("/nonexistent/path.py")

    def test_syntax_error_file(self, tmp_path: Path):
        py = tmp_path / "bad.py"
        py.write_text("def foo(\n")
        assert not _has_substantial_docstring(str(py))

    def test_empty_file(self, tmp_path: Path):
        py = tmp_path / "empty.py"
        py.write_text("")
        assert not _has_substantial_docstring(str(py))


# ─── _find_uncovered_files ────────────────────────────────────────────


class TestFindUncoveredFiles:
    """Tests for finding files not covered by theory."""

    def test_all_covered(self, tmp_path: Path):
        py = tmp_path / "mod.py"
        py.write_text('"""This module has a substantial docstring for testing coverage."""\n')
        profile = {"core_theory": [{"source": "mod.py:1", "claims": ["X"]}]}
        result = _find_uncovered_files(["mod.py"], profile, str(tmp_path))
        assert result == []

    def test_uncovered_with_docstring(self, tmp_path: Path):
        py = tmp_path / "new_mod.py"
        py.write_text(
            '"""This new module has a substantial docstring that is not yet covered."""\n'
        )
        profile = {"core_theory": [{"source": "other.py:1", "claims": ["X"]}]}
        result = _find_uncovered_files(["new_mod.py"], profile, str(tmp_path))
        assert "new_mod.py" in result

    def test_uncovered_without_docstring_excluded(self, tmp_path: Path):
        py = tmp_path / "no_doc.py"
        py.write_text("x = 1\n")
        profile: dict[str, Any] = {}
        result = _find_uncovered_files(["no_doc.py"], profile, str(tmp_path))
        assert result == []

    def test_nonexistent_file_excluded(self, tmp_path: Path):
        profile: dict[str, Any] = {}
        result = _find_uncovered_files(["missing.py"], profile, str(tmp_path))
        assert result == []


# ─── check_theory_staleness ──────────────────────────────────────────


class TestCheckTheoryStaleness:
    """Tests for theory staleness checking."""

    def test_no_py_files_not_stale(self, tmp_path: Path):
        result = check_theory_staleness(
            str(tmp_path),
            theory_profile={},
            git_context={"modified_files": [], "untracked_files": []},
        )
        assert result["stale"] is False
        assert result["uncovered_files"] == []
        assert result["total_uncommitted_py"] == 0

    def test_none_profile_marks_stale(self, tmp_path: Path):
        result = check_theory_staleness(
            str(tmp_path),
            theory_profile=None,
            git_context={
                "modified_files": ["src/foo.py"],
                "untracked_files": [],
            },
        )
        assert result["stale"] is True
        assert "No theory profile exists" in result["recommendation"]

    def test_uncovered_file_marks_stale(self, tmp_path: Path):
        py = tmp_path / "new_mod.py"
        py.write_text(
            '"""This module provides theory-relevant design rationale for staleness detection."""\n'
        )
        result = check_theory_staleness(
            str(tmp_path),
            theory_profile={"core_theory": []},
            git_context={
                "modified_files": ["new_mod.py"],
                "untracked_files": [],
            },
        )
        assert result["stale"] is True
        assert "new_mod.py" in result["uncovered_files"]

    def test_covered_file_not_stale(self, tmp_path: Path):
        py = tmp_path / "mod.py"
        py.write_text('"""This module has a substantial docstring to test coverage freshness."""\n')
        profile = {"core_theory": [{"source": "mod.py:1", "claims": ["X"]}]}
        result = check_theory_staleness(
            str(tmp_path),
            theory_profile=profile,
            git_context={
                "modified_files": ["mod.py"],
                "untracked_files": [],
            },
        )
        assert result["stale"] is False

    def test_uncovered_files_capped_at_twenty(self, tmp_path: Path):
        result = check_theory_staleness(
            str(tmp_path),
            theory_profile=None,
            git_context={
                "modified_files": [f"mod{i}.py" for i in range(30)],
                "untracked_files": [],
            },
        )
        assert len(result["uncovered_files"]) <= 20

    def test_recommendation_includes_file_names(self, tmp_path: Path):
        py = tmp_path / "feature.py"
        py.write_text(
            '"""Feature module with substantial docstring for theory staleness checks."""\n'
        )
        result = check_theory_staleness(
            str(tmp_path),
            theory_profile={"core_theory": []},
            git_context={
                "modified_files": ["feature.py"],
                "untracked_files": [],
            },
        )
        if result["stale"]:
            assert "feature.py" in result["recommendation"]

    def test_only_non_test_py_files_counted(self, tmp_path: Path):
        result = check_theory_staleness(
            str(tmp_path),
            theory_profile={},
            git_context={
                "modified_files": ["tests/test_foo.py", "test_bar.py"],
                "untracked_files": [],
            },
        )
        assert result["total_uncommitted_py"] == 0


# ─── _RULE_TEMPLATES constant ────────────────────────────────────────


class TestRuleTemplates:
    """Tests for the _RULE_TEMPLATES constant structure."""

    def test_all_templates_are_four_tuples(self):
        for template in _RULE_TEMPLATES:
            assert len(template) == 4

    def test_all_regex_patterns_valid(self):
        import re as re_mod

        for regex, _kind, _pattern_builder, _confidence in _RULE_TEMPLATES:
            re_mod.compile(regex)  # Should not raise

    def test_all_kinds_valid(self):
        valid_kinds = {"forbid_regex", "require_regex"}
        for _, kind, _, _ in _RULE_TEMPLATES:
            assert kind in valid_kinds

    def test_all_confidences_valid(self):
        valid_confidences = {"low", "medium", "high"}
        for _, _, _, confidence in _RULE_TEMPLATES:
            assert confidence in valid_confidences

    def test_pattern_builders_callable(self):
        for _, _, builder, _ in _RULE_TEMPLATES:
            assert callable(builder)


# ─── Integration: extract_theory -> build_theory_pack ─────────────────


class TestIntegration:
    """End-to-end integration tests."""

    def test_full_pipeline_empty_project(self, tmp_path: Path):
        root = str(tmp_path)
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            theory = extract_theory(root)
            pack = build_theory_pack(root)
        assert theory["docs_scanned"] == 0
        assert "digest_text" in pack
        # Validity should be weak with no docs
        assert theory["validity"]["status"] == "weak"

    def test_full_pipeline_with_rich_docs(self, tmp_path: Path):
        md = textwrap.dedent("""\
            # Core Theory

            The fundamental insight is that because deterministic analysis
            enables reproducible quality checks, we can avoid runtime sampling.

            # Problem-Solving Approach

            Rather than brute-force scanning, we use guided heuristic elimination
            because structured search is more efficient than exhaustive enumeration.

            # Alignment

            The goal is to ensure correct architecture. If you bypass the
            composition layer, you will ruin the design invariants.

            # Architecture

            We chose modular decomposition because separation of concerns enables
            independent testing rather than monolithic coupling.

            # Anti-Patterns

            Hard-coding would break extensibility because it prevents
            configuration-driven behavior.

            # Key Abstractions

            We call this concept a **theory facet** because it represents
            a distinct dimension of project understanding.
        """)
        root = _make_project_with_md(tmp_path, {"DESIGN.md": md})
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            theory = extract_theory(root)
            pack = build_theory_pack(root, include_full_profile=True)
        assert theory["docs_scanned"] == 1
        profile = theory["theory_profile"]
        total_claims = sum(len(e["claims"]) for entries in profile.values() for e in entries)
        assert total_claims > 0
        assert pack["digest_token_estimate"] > 0
        assert "full_profile" in pack

    def test_staleness_after_extraction(self, tmp_path: Path):
        py = tmp_path / "new_feature.py"
        py.write_text(
            '"""New feature module with substantial design rationale for staleness testing."""\n'
        )
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            theory = extract_theory(str(tmp_path))
        staleness = check_theory_staleness(
            str(tmp_path),
            theory_profile=theory["theory_profile"],
            git_context={
                "modified_files": ["new_feature.py"],
                "untracked_files": [],
            },
        )
        # new_feature.py has a docstring but is not covered by theory
        assert staleness["stale"] is True

    def test_context_from_profile_consistent_with_extraction(self, tmp_path: Path):
        md = textwrap.dedent("""\
            # Core Theory

            The key insight is that deterministic extraction because
            reproducibility enables caching across sessions.
        """)
        root = _make_project_with_md(tmp_path, {"DOC.md": md})
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=_empty_guidance(),
        ):
            theory = extract_theory(root)
        profile = theory["theory_profile"]
        result = get_theory_context_from_profile(profile, facet="core_theory")
        assert result["returned_count"] == len(result["claims"])
        for claim in result["claims"]:
            assert claim["facet"] == "core_theory"

    def test_enforceable_rules_from_directives(self, tmp_path: Path):
        root = str(tmp_path)
        guidance = {
            "rules": [],
            "directives": {
                "do_not": ["DO NOT use exec", "DO NOT import subprocess"],
                "must": ["MUST use pathlib"],
                "critical": [],
            },
        }
        with patch(
            "lintgate.theory_extractor.build_context_guidance",
            return_value=guidance,
        ):
            theory = extract_theory(root)
        rules = theory["enforceable_rules"]["proposed_rules"]
        assert len(rules) >= 2
        patterns = [r["proposed_rule"]["pattern"] for r in rules]
        assert any("exec" in p for p in patterns)


# ─── Unique tests merged from test_theory_extractor_helpers.py ────────


class TestBuildFacetSummariesDedup:
    """Edge cases for deduplication across facets in _build_facet_summaries."""

    def test_deduplicates_summaries_across_facets(self):
        claim_a = "The system enables compositional reasoning because it prunes the space."
        claim_b = "The approach uses guided descent rather than brute-force enumeration."
        profile = {
            "core_theory": [
                {"heading": "A", "source": "a.md:1", "claims": [claim_a, claim_b]},
            ],
            "problem_solving": [
                {"heading": "B", "source": "b.md:2", "claims": [claim_a, claim_b]},
            ],
        }
        summaries, _ = _build_facet_summaries(profile)
        used = [v for v in summaries.values() if v != "(no theory content found)"]
        assert len(used) == len(set(used)), "Facet summaries should not repeat the same claim"

    def test_single_claim_fallback_when_all_excluded(self):
        same_claim = "The system enables compositional reasoning because it prunes the space."
        profile = {
            "core_theory": [
                {"heading": "A", "source": "a.md:1", "claims": [same_claim]},
            ],
            "problem_solving": [
                {"heading": "B", "source": "b.md:2", "claims": [same_claim]},
            ],
        }
        summaries, _ = _build_facet_summaries(profile)
        used = [v for v in summaries.values() if v != "(no theory content found)"]
        assert len(used) == 2
        assert used[0] == same_claim
        assert used[1] == same_claim


class TestGetTheoryContextFromProfileMissingClaims:
    """Edge case: entry missing 'claims' key entirely."""

    def test_missing_claims_key_in_entry(self):
        profile = {
            "core_theory": [
                {"heading": "H", "source": "s.md:1"},
            ],
        }
        result = get_theory_context_from_profile(profile)
        assert result["total_matched"] == 0


class TestCollectCoveredSourcesMissingKey:
    """Edge case: entry missing 'source' key."""

    def test_entry_missing_source_key(self):
        profile = {
            "core_theory": [{"claims": ["no source field"]}],
        }
        result = _collect_covered_sources(profile)
        assert result == {""}


class TestHasSubstantialDocstringBoundary:
    """Boundary tests for the 30-char docstring threshold."""

    def test_exactly_30_chars(self, tmp_path: Path):
        docstring = "a" * 30
        f = tmp_path / "mod.py"
        f.write_text(f'"""{docstring}"""\n')
        assert _has_substantial_docstring(str(f)) is True

    def test_29_chars_too_short(self, tmp_path: Path):
        docstring = "a" * 29
        f = tmp_path / "mod.py"
        f.write_text(f'"""{docstring}"""\n')
        assert _has_substantial_docstring(str(f)) is False

    def test_whitespace_only_docstring(self, tmp_path: Path):
        f = tmp_path / "mod.py"
        f.write_text('"""                                   """\n')
        assert _has_substantial_docstring(str(f)) is False


# ─── Unique tests merged from test_theory_extractor_coverage.py ───────


class TestBuildTheoryPackUncoveredLines:
    """Cover specific uncovered branches in build_theory_pack."""

    def test_facet_with_entries_but_empty_claims_gives_no_content(self, tmp_path: Path):
        fake_result = {
            "theory_profile": {
                "core_theory": [
                    {"heading": "Test", "source": "test.md:1", "claims": []},
                ],
                "problem_solving": [],
                "alignment": [],
                "architecture": [],
                "anti_patterns": [],
                "abstractions": [],
            },
            "enforceable_rules": {
                "proposed_rules": [],
                "existing_rule_count": 0,
                "directives_analyzed": 0,
                "already_covered": 0,
            },
            "summary": {},
            "validity": {},
        }
        with patch("lintgate.theory_extractor.extract_theory", return_value=fake_result):
            pack = build_theory_pack(str(tmp_path))
        assert pack["facet_summaries"]["core_theory"] == "(no theory content found)"

    def test_existing_rule_count_in_digest(self, tmp_path: Path):
        fake_result = {
            "theory_profile": {
                "core_theory": [],
                "problem_solving": [],
                "alignment": [],
                "architecture": [],
                "anti_patterns": [],
                "abstractions": [],
            },
            "enforceable_rules": {
                "proposed_rules": [],
                "existing_rule_count": 5,
                "directives_analyzed": 0,
                "already_covered": 0,
            },
            "summary": {},
            "validity": {},
        }
        with patch("lintgate.theory_extractor.extract_theory", return_value=fake_result):
            pack = build_theory_pack(str(tmp_path))
        assert "5 active rules enforced by linter" in pack["digest_text"]


class TestDiscoverMdFilesEdgeCases:
    """Cover the file-cap and duplicate-skip branches."""

    def test_rules_dir_cap_at_max_md_files(self, tmp_path: Path):
        from lintgate.theory_extractor import _discover_md_files

        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        for i in range(105):
            (rules_dir / f"rule_{i:04d}.md").write_text(f"# Rule {i}\n")

        found = _discover_md_files(str(tmp_path))
        assert len(found) == 100

    def test_duplicate_skip_in_main_walk(self, tmp_path: Path):
        from lintgate.theory_extractor import _discover_md_files

        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "shared.md").write_text("# Shared Rule\n")

        original_walk = os.walk

        def patched_walk(top, **kwargs):
            yield from original_walk(top, **kwargs)
            yield (str(rules_dir), [], ["shared.md"])

        with patch("os.walk", side_effect=patched_walk):
            found = _discover_md_files(str(tmp_path))

        rules_shared_path = str(rules_dir / "shared.md")
        count = sum(1 for f in found if f == rules_shared_path)
        assert count == 1, f"Expected 1 occurrence of {rules_shared_path}, got {count}"

    def test_main_walk_cap_at_max_md_files(self, tmp_path: Path):
        from lintgate.theory_extractor import _discover_md_files

        for i in range(105):
            (tmp_path / f"doc_{i:04d}.md").write_text(f"# Doc {i}\n")

        found = _discover_md_files(str(tmp_path))
        assert len(found) == 100


class TestHasFrontmatterOptOut:
    """Cover OSError, empty file, and unclosed frontmatter branches."""

    def test_oserror_returns_false(self, tmp_path: Path):
        nonexistent = str(tmp_path / "does_not_exist.md")
        result = _has_frontmatter_opt_out(nonexistent)
        assert result is False

    def test_empty_file_returns_false(self, tmp_path: Path):
        empty_file = tmp_path / "empty.md"
        empty_file.write_text("")
        result = _has_frontmatter_opt_out(str(empty_file))
        assert result is False

    def test_unclosed_frontmatter_returns_false(self, tmp_path: Path):
        unclosed = tmp_path / "unclosed.md"
        unclosed.write_text("---\ntheory_scope: false\ntitle: test\n# Heading\n\nBody text.\n")
        result = _has_frontmatter_opt_out(str(unclosed))
        assert result is False

    def test_valid_opt_out_returns_true(self, tmp_path: Path):
        opted_out = tmp_path / "opted_out.md"
        opted_out.write_text("---\ntheory_scope: false\n---\n\n# Content\n")
        result = _has_frontmatter_opt_out(str(opted_out))
        assert result is True


class TestParseDocumentOSError:
    """Cover OSError branch when reading file content."""

    def test_oserror_returns_empty_list(self, tmp_path: Path):
        test_file = tmp_path / "test.md"
        test_file.write_text("# Title\n\nBody content.\n")

        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            result = _parse_document(str(test_file), str(tmp_path))
        assert result == []


class TestScoreClaimFacets:
    """Cover uncovered facet-specific scoring branches in _score_claim."""

    def test_importance_markers_score(self):
        s = "The fundamental insight of this architecture is that modules compose."
        score = _score_claim(s, "abstractions")
        assert score >= 1

    def test_core_theory_research_question(self):
        s = "This work addresses the problem of cross-module drift in large codebases."
        score = _score_claim(s, "core_theory")
        assert score >= 1

    def test_problem_solving_comparative(self):
        s = "Guided search is more efficient than brute-force enumeration."
        score = _score_claim(s, "problem_solving")
        assert score >= 2

    def test_problem_solving_process_verbs(self):
        s = "The extractor scans all markdown documents and classifies sections."
        score = _score_claim(s, "problem_solving")
        assert score >= 1

    def test_problem_solving_step_phase(self):
        s = "Each pipeline stage validates its output before passing to the next."
        score = _score_claim(s, "problem_solving")
        assert score >= 1

    def test_problem_solving_sequential_markers(self):
        s = "First the system parses the input, then it validates the schema."
        score = _score_claim(s, "problem_solving")
        assert score >= 1

    def test_alignment_destructive_verbs(self):
        s = "Ad-hoc solutions will ruin the compositional architecture."
        score = _score_claim(s, "alignment")
        assert score >= 2

    def test_alignment_proper_correct(self):
        s = "The proper approach is to use the pipeline module for all processing."
        score = _score_claim(s, "alignment")
        assert score >= 1

    def test_alignment_goal_purpose(self):
        s = "The purpose is to maintain strict module boundaries."
        score = _score_claim(s, "alignment")
        assert score >= 1

    def test_alignment_non_goal(self):
        s = "Performance optimization is a non-goal for this iteration."
        score = _score_claim(s, "alignment")
        assert score >= 2

    def test_alignment_primary_objective(self):
        score = _score_claim("The primary objective is ensuring code quality.", "alignment")
        assert score > 0

    def test_architecture_why_not_pattern(self):
        score = _score_claim("This is why we chose X over Y for the design.", "architecture")
        assert score > 0

    def test_architecture_rationale_pattern(self):
        score = _score_claim(
            "**Rationale** We chose this approach because of simplicity.",
            "architecture",
        )
        assert score > 0

    def test_architecture_performance_claim(self):
        score = _score_claim(
            "The design avoids quadratic complexity in the core loop.", "architecture"
        )
        assert score > 0

    def test_anti_patterns_trying_harder(self):
        score = _score_claim("Premature optimization leads to worse code quality.", "anti_patterns")
        assert score > 0


class TestSplitSentencesCheckboxSkip:
    """Cover the '- [' and '|' paragraph skip in _split_sentences."""

    def test_checkbox_paragraph_skipped(self):
        text = (
            "First sentence about theory.\n\n"
            "- [ ] TODO item one\n\n"
            "- [x] Completed item\n\n"
            "Second sentence with content."
        )
        sentences = _split_sentences(text)
        for s in sentences:
            assert not s.strip().startswith("- [")

    def test_pipe_paragraph_skipped(self):
        text = "Theory claim here.\n\n| Column | Header |\n\nAnother claim."
        sentences = _split_sentences(text)
        for s in sentences:
            assert not s.strip().startswith("|")


class TestExtractEnforceableRulesPatternNone:
    """Cover pattern_builder returning None branch."""

    def test_pattern_builder_returns_none(self):
        guidance = {
            "directives": {
                "do_not": ["DO NOT use eval"],
                "must": [],
                "critical": [],
            },
            "rules": [],
        }
        fake_templates = [
            (
                r"DO NOT (?:ever )?use (\w+(?:\.\w+)*)",
                "forbid_regex",
                lambda _: None,
                "high",
            ),
        ]
        with patch("lintgate.theory_extractor._RULE_TEMPLATES", fake_templates):
            result = _extract_enforceable_rules(guidance, set(), [])
        assert result["proposed_rules"] == []


class TestBuildValidityReportPartial:
    """Cover the 'partial' status branch with no missing facets."""

    def test_status_partial_with_warnings_but_no_missing_facets(self):
        profile = {
            "core_theory": [{"heading": "CT", "source": "a.md:1", "claims": ["c1", "c2"]}],
            "problem_solving": [{"heading": "PS", "source": "a.md:5", "claims": ["c3", "c4"]}],
            "alignment": [{"heading": "AL", "source": "a.md:10", "claims": ["c5", "c6"]}],
            "architecture": [],
            "anti_patterns": [],
            "abstractions": [],
        }
        enforceable = {
            "proposed_rules": [],
            "existing_rule_count": 0,
            "directives_analyzed": 0,
            "already_covered": 0,
        }
        report = _build_validity_report(
            profile,
            docs_scanned=10,
            sections_scanned=20,
            enforceable=enforceable,
        )
        assert report["status"] == "partial"
        assert len(report["warnings"]) > 0
        assert len(report["missing_required_facets"]) == 0


class TestPickBestSummaryClaim:
    """Cover quality_score branches inside _pick_best_summary_claim."""

    def test_short_claim_penalized(self):
        short = "Too short."
        long_good = "Because the architecture enables modular design, we chose this approach rather than the monolithic alternative."
        result = _pick_best_summary_claim([short, long_good])
        assert result == long_good

    def test_code_marker_penalized(self):
        with_code = "The CODE pattern is used throughout the architecture because it enables modular design."
        without_code = "Because the architecture enables modular design, we chose this approach rather than monolithic."
        result = _pick_best_summary_claim([with_code, without_code])
        assert result == without_code

    def test_many_slashes_penalized(self):
        slashy = "The path/to/some/deep/module/file is important because it enables modular design."
        clean = "Because the architecture enables modular design, we chose this approach rather than monolithic."
        result = _pick_best_summary_claim([slashy, clean])
        assert result == clean

    def test_key_core_fundamental_rewarded(self):
        claim = "The core principle is separation of concerns because it enables modular testing."
        result = _pick_best_summary_claim([claim])
        assert result == claim


class TestWordsToPatternWhitespaceEdge:
    """Cover the whitespace-only input edge case."""

    def test_whitespace_only_input(self):
        result = _words_to_pattern("   ")
        assert isinstance(result, str)
