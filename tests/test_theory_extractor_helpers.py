"""Unit tests for pure helper functions in lintgate/theory_extractor.py.

Tests are organized by function in priority order (low-sigma first).
Each function has happy-path, edge-case, and return-type assertions.
"""

from __future__ import annotations

import re

import pytest

from lintgate.theory_extractor import (
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
    _has_substantial_docstring,
    _is_covered_by_existing,
    _Section,
    _strip_markdown,
    _words_to_pattern,
    check_theory_staleness,
    get_theory_context_from_profile,
)


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


# ─── _strip_markdown (sigma=2) ─────────────────────────────────────────


class TestStripMarkdown:
    def test_strips_bold(self):
        assert _strip_markdown("**bold text**") == "bold text"

    def test_strips_italic(self):
        assert _strip_markdown("*italic text*") == "italic text"

    def test_strips_bold_italic(self):
        assert _strip_markdown("***bold italic***") == "bold italic"

    def test_strips_inline_code(self):
        assert _strip_markdown("use `eval()` here") == "use eval() here"

    def test_strips_numbered_list_prefix(self):
        assert _strip_markdown("1. First item") == "First item"

    def test_combined_formatting(self):
        result = _strip_markdown("1. **DO NOT** use `eval()`")
        assert result == "DO NOT use eval()"

    def test_empty_string(self):
        assert _strip_markdown("") == ""

    def test_plain_text_unchanged(self):
        assert _strip_markdown("plain text") == "plain text"

    def test_strips_surrounding_whitespace(self):
        assert _strip_markdown("  hello  ") == "hello"

    def test_multiple_inline_code_spans(self):
        result = _strip_markdown("use `foo` and `bar`")
        assert result == "use foo and bar"

    def test_return_type(self):
        assert isinstance(_strip_markdown("**test**"), str)


# ─── _words_to_pattern (sigma=2) ───────────────────────────────────────


class TestWordsToPattern:
    def test_single_word(self):
        pattern = _words_to_pattern("helper")
        assert pattern == "helper"

    def test_multi_word_space(self):
        pattern = _words_to_pattern("helper function")
        assert re.search(pattern, "helper_function")
        assert re.search(pattern, "helper-function")
        assert re.search(pattern, "helper function")

    def test_multi_word_hyphen(self):
        pattern = _words_to_pattern("task-specific")
        assert re.search(pattern, "task_specific")
        assert re.search(pattern, "task specific")

    def test_empty_parts_after_split(self):
        pattern = _words_to_pattern("a")
        assert pattern == "a"

    def test_special_regex_chars_escaped(self):
        pattern = _words_to_pattern("foo.bar")
        assert r"\." in pattern
        assert not re.search(pattern, "fooxbar")
        assert re.search(pattern, "foo.bar")

    def test_leading_trailing_whitespace(self):
        pattern = _words_to_pattern("  hello world  ")
        assert re.search(pattern, "hello_world")

    def test_return_type(self):
        assert isinstance(_words_to_pattern("test word"), str)


# ─── _filter_uncommitted_py_files (sigma=2) ────────────────────────────


class TestFilterUncommittedPyFiles:
    def test_basic_filtering(self):
        git_ctx = {
            "modified_files": ["src/main.py", "README.md"],
            "untracked_files": ["src/utils.py"],
        }
        result = _filter_uncommitted_py_files(git_ctx)
        assert result == ["src/main.py", "src/utils.py"]

    def test_excludes_tests(self):
        git_ctx = {
            "modified_files": ["tests/test_foo.py", "test_bar.py"],
            "untracked_files": [],
        }
        result = _filter_uncommitted_py_files(git_ctx)
        assert result == []

    def test_excludes_pycache(self):
        git_ctx = {
            "modified_files": ["src/__pycache__/foo.py"],
            "untracked_files": [],
        }
        result = _filter_uncommitted_py_files(git_ctx)
        assert result == []

    def test_excludes_non_python(self):
        git_ctx = {
            "modified_files": ["doc.md", "config.yaml", "style.css"],
            "untracked_files": [],
        }
        result = _filter_uncommitted_py_files(git_ctx)
        assert result == []

    def test_empty_context(self):
        result = _filter_uncommitted_py_files({})
        assert result == []

    def test_both_modified_and_untracked(self):
        git_ctx = {
            "modified_files": ["a.py"],
            "untracked_files": ["b.py"],
        }
        result = _filter_uncommitted_py_files(git_ctx)
        assert result == ["a.py", "b.py"]

    def test_return_type(self):
        result = _filter_uncommitted_py_files({"modified_files": [], "untracked_files": []})
        assert isinstance(result, list)

    def test_nested_pycache_excluded(self):
        git_ctx = {
            "modified_files": ["src/pkg/__pycache__/mod.py"],
            "untracked_files": [],
        }
        assert _filter_uncommitted_py_files(git_ctx) == []


# ─── _build_theory_profile (sigma=7) ──────────────────────────────────


class TestBuildTheoryProfile:
    def test_empty_sections(self):
        profile = _build_theory_profile([])
        assert set(profile.keys()) == {
            "core_theory",
            "problem_solving",
            "alignment",
            "architecture",
            "anti_patterns",
            "abstractions",
        }
        for entries in profile.values():
            assert entries == []

    def test_produces_all_six_facets(self):
        section = _make_section(
            heading="Core Theory",
            body=(
                "The system is designed because compositional architectures "
                "enable modular evolution rather than monolithic replacement."
            ),
        )
        profile = _build_theory_profile([section])
        assert isinstance(profile, dict)
        assert len(profile) == 6

    def test_core_theory_section_classified(self):
        section = _make_section(
            heading="Core Theory",
            body=(
                "The fundamental insight is that constraint-based reasoning "
                "enables faster convergence because it prunes the search space."
            ),
        )
        profile = _build_theory_profile([section])
        assert len(profile["core_theory"]) > 0

    def test_anti_patterns_section_classified(self):
        section = _make_section(
            heading="Anti-Patterns",
            body=(
                "Hard-coded workarounds would break the learning system. "
                "Task-specific hacks bypass the architecture."
            ),
        )
        profile = _build_theory_profile([section])
        assert len(profile["anti_patterns"]) > 0

    def test_entries_have_expected_keys(self):
        section = _make_section(
            heading="Architecture",
            body=(
                "The system chose decomposition over monolith because modular "
                "boundaries enable independent testing."
            ),
        )
        profile = _build_theory_profile([section])
        for facet, entries in profile.items():
            for entry in entries:
                assert "heading" in entry
                assert "source" in entry
                assert "claims" in entry
                assert isinstance(entry["claims"], list)

    def test_source_includes_rel_path_and_line(self):
        section = _make_section(
            heading="Core Theory",
            body=(
                "The key insight is that deterministic extraction because "
                "it avoids token cost rather than LLM-based approaches."
            ),
            rel_path="docs/design.md",
            line_no=42,
        )
        profile = _build_theory_profile([section])
        for entries in profile.values():
            for entry in entries:
                assert entry["source"] == "docs/design.md:42"


# ─── _dedupe_facet_entries (sigma=7) ──────────────────────────────────


class TestDedupeFacetEntries:
    def test_no_duplicates_unchanged(self):
        entries = [
            {"heading": "A", "source": "a.md:1", "claims": ["claim one"]},
            {"heading": "B", "source": "b.md:2", "claims": ["claim two"]},
        ]
        result = _dedupe_facet_entries(entries)
        assert len(result) == 2
        assert result[0]["claims"] == ["claim one"]
        assert result[1]["claims"] == ["claim two"]

    def test_exact_duplicates_removed(self):
        entries = [
            {"heading": "A", "source": "a.md:1", "claims": ["same claim"]},
            {"heading": "B", "source": "b.md:2", "claims": ["same claim"]},
        ]
        result = _dedupe_facet_entries(entries)
        assert len(result) == 1
        assert result[0]["claims"] == ["same claim"]

    def test_case_insensitive_dedup(self):
        entries = [
            {"heading": "A", "source": "a.md:1", "claims": ["The Claim"]},
            {"heading": "B", "source": "b.md:2", "claims": ["the claim"]},
        ]
        result = _dedupe_facet_entries(entries)
        assert len(result) == 1

    def test_whitespace_normalized_dedup(self):
        entries = [
            {"heading": "A", "source": "a.md:1", "claims": ["a  claim   here"]},
            {"heading": "B", "source": "b.md:2", "claims": ["a claim here"]},
        ]
        result = _dedupe_facet_entries(entries)
        assert len(result) == 1

    def test_empty_entries(self):
        assert _dedupe_facet_entries([]) == []

    def test_entry_with_all_duplicate_claims_removed(self):
        entries = [
            {"heading": "A", "source": "a.md:1", "claims": ["unique claim"]},
            {"heading": "B", "source": "b.md:2", "claims": ["unique claim"]},
        ]
        result = _dedupe_facet_entries(entries)
        assert len(result) == 1
        assert result[0]["heading"] == "A"

    def test_preserves_non_claims_fields(self):
        entries = [
            {"heading": "Title", "source": "x.md:10", "claims": ["a claim"], "extra": "data"},
        ]
        result = _dedupe_facet_entries(entries)
        assert result[0]["heading"] == "Title"
        assert result[0]["source"] == "x.md:10"
        assert result[0]["extra"] == "data"

    def test_return_type(self):
        result = _dedupe_facet_entries([])
        assert isinstance(result, list)


# ─── _build_summary (sigma=7) ─────────────────────────────────────────


class TestBuildSummary:
    def test_empty_profile(self):
        profile = {
            "core_theory": [],
            "problem_solving": [],
            "alignment": [],
            "architecture": [],
            "anti_patterns": [],
            "abstractions": [],
        }
        summary = _build_summary(profile)
        for facet_summary in summary.values():
            assert facet_summary["claim_count"] == 0
            assert facet_summary["source_count"] == 0
            assert facet_summary["top_claims"] == []

    def test_claim_count_correct(self):
        profile = {
            "core_theory": [
                {"heading": "A", "source": "a.md:1", "claims": ["c1", "c2"]},
                {"heading": "B", "source": "b.md:2", "claims": ["c3"]},
            ],
        }
        summary = _build_summary(profile)
        assert summary["core_theory"]["claim_count"] == 3
        assert summary["core_theory"]["source_count"] == 2

    def test_top_claims_capped_at_3(self):
        profile = {
            "facet": [
                {
                    "heading": "H",
                    "source": "s.md:1",
                    "claims": ["a", "b", "c", "d", "e"],
                },
            ],
        }
        summary = _build_summary(profile)
        assert len(summary["facet"]["top_claims"]) == 3

    def test_top_claims_from_largest_entry_first(self):
        profile = {
            "facet": [
                {"heading": "Small", "source": "s.md:1", "claims": ["x"]},
                {"heading": "Large", "source": "l.md:2", "claims": ["a", "b", "c"]},
            ],
        }
        summary = _build_summary(profile)
        assert summary["facet"]["top_claims"][0] == "a"

    def test_return_type(self):
        profile = {"facet": []}
        summary = _build_summary(profile)
        assert isinstance(summary, dict)


# ─── _extract_claims (sigma=10) ──────────────────────────────────────


class TestExtractClaims:
    def test_extracts_causal_claim(self):
        section = _make_section(
            heading="Core Theory",
            body=(
                "The system uses constraint propagation because it prunes "
                "the search space exponentially."
            ),
        )
        claims = _extract_claims(section, "core_theory")
        assert len(claims) >= 1
        assert any("constraint propagation" in c for c in claims)

    def test_skips_short_sentences(self):
        section = _make_section(heading="Test", body="Too short.")
        claims = _extract_claims(section, "core_theory")
        assert claims == []

    def test_skips_code_blocks(self):
        section = _make_section(
            heading="Test",
            body=(
                "```python\ndef foo(): pass\n```\n\n"
                "The fundamental design is because deterministic extraction avoids token cost."
            ),
        )
        claims = _extract_claims(section, "core_theory")
        assert not any("def foo" in c for c in claims)

    def test_skips_camelcase_heavy_sentences(self):
        section = _make_section(
            heading="Test",
            body=(
                "This mentions MyClassOne and MyClassTwo and "
                "MyClassThree and MyClassFour because they are important components."
            ),
        )
        claims = _extract_claims(section, "core_theory")
        assert claims == []

    def test_skips_path_heavy_sentences(self):
        section = _make_section(
            heading="Test",
            body=(
                "The files are at src/a/b/c/d.py because they organize "
                "the core modules of the system."
            ),
        )
        claims = _extract_claims(section, "core_theory")
        assert claims == []

    def test_skips_code_like_fragments(self):
        section = _make_section(
            heading="Test",
            body="python -m pytest tests/ because it verifies the entire system.",
        )
        claims = _extract_claims(section, "core_theory")
        assert claims == []

    def test_capped_at_8_claims(self):
        body_parts = []
        for i in range(15):
            body_parts.append(
                f"The principle {i} is fundamental because it enables "
                f"compositional reasoning rather than monolithic approach {i}."
            )
        section = _make_section(heading="Core Theory", body="\n\n".join(body_parts))
        claims = _extract_claims(section, "core_theory")
        assert len(claims) <= 8

    def test_strips_inline_code_preserving_content(self):
        section = _make_section(
            heading="Core Theory",
            body=(
                "The `extract_theory` function is fundamental because it "
                "enables deterministic theory extraction rather than LLM calls."
            ),
        )
        claims = _extract_claims(section, "core_theory")
        if claims:
            assert not any("`" in c for c in claims)

    def test_strips_markdown_tables(self):
        section = _make_section(
            heading="Test",
            body=(
                "| Header | Value |\n|--------|-------|\n| A | B |\n\n"
                "The key insight is that deterministic extraction because "
                "it avoids token cost rather than LLM-based approaches."
            ),
        )
        claims = _extract_claims(section, "core_theory")
        assert not any("|" in c for c in claims)

    def test_return_type(self):
        section = _make_section(heading="Test", body="")
        claims = _extract_claims(section, "core_theory")
        assert isinstance(claims, list)


# ─── _build_facet_summaries (sigma=10) ────────────────────────────────


class TestBuildFacetSummaries:
    def test_empty_profile(self):
        profile = {
            "core_theory": [],
            "problem_solving": [],
            "alignment": [],
            "architecture": [],
            "anti_patterns": [],
            "abstractions": [],
        }
        summaries, anti_patterns = _build_facet_summaries(profile)
        for s in summaries.values():
            assert s == "(no theory content found)"
        assert anti_patterns == []

    def test_produces_summary_per_facet(self):
        profile = {
            "core_theory": [
                {
                    "heading": "Core",
                    "source": "c.md:1",
                    "claims": [
                        "The system enables compositional reasoning because it prunes the space."
                    ],
                }
            ],
            "problem_solving": [],
        }
        summaries, _ = _build_facet_summaries(profile)
        assert summaries["core_theory"] != "(no theory content found)"
        assert summaries["problem_solving"] == "(no theory content found)"

    def test_anti_patterns_extracted(self):
        profile = {
            "anti_patterns": [
                {
                    "heading": "Bad",
                    "source": "b.md:1",
                    "claims": ["Hard-coded workarounds break the system."],
                }
            ],
        }
        _, anti_patterns = _build_facet_summaries(profile)
        assert len(anti_patterns) == 1
        assert "Hard-coded" in anti_patterns[0]

    def test_anti_patterns_capped_at_10(self):
        claims = [f"Anti-pattern {i} will break things." for i in range(15)]
        profile = {
            "anti_patterns": [{"heading": "Bad", "source": "b.md:1", "claims": claims}],
        }
        _, anti_patterns = _build_facet_summaries(profile)
        assert len(anti_patterns) == 10

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

    def test_return_types(self):
        profile = {"x": []}
        summaries, anti_patterns = _build_facet_summaries(profile)
        assert isinstance(summaries, dict)
        assert isinstance(anti_patterns, list)


# ─── _is_covered_by_existing (sigma=10) ──────────────────────────────


class TestIsCoveredByExisting:
    def test_exact_match_in_patterns(self):
        assert _is_covered_by_existing(r"\beval\b", {r"\beval\b"}, []) is True

    def test_no_match(self):
        assert _is_covered_by_existing(r"\beval\b", set(), []) is False

    def test_regex_match_from_existing_rule(self):
        rules = [{"pattern": r"eval"}]
        assert _is_covered_by_existing(r"eval()", set(), rules) is True

    def test_existing_rule_without_pattern_key(self):
        rules = [{"kind": "forbid"}]
        assert _is_covered_by_existing(r"\beval\b", set(), rules) is False

    def test_existing_rule_empty_pattern(self):
        rules = [{"pattern": ""}]
        assert _is_covered_by_existing(r"\beval\b", set(), rules) is False

    def test_invalid_regex_in_existing_rule(self):
        rules = [{"pattern": r"[invalid"}]
        assert _is_covered_by_existing(r"\beval\b", set(), rules) is False

    def test_empty_inputs(self):
        assert _is_covered_by_existing("anything", set(), []) is False

    def test_return_type(self):
        result = _is_covered_by_existing("x", set(), [])
        assert isinstance(result, bool)


# ─── _build_validity_report (sigma=11) ────────────────────────────────


class TestBuildValidityReport:
    def _empty_profile(self):
        return {
            "core_theory": [],
            "problem_solving": [],
            "alignment": [],
            "architecture": [],
            "anti_patterns": [],
            "abstractions": [],
        }

    def _empty_enforceable(self):
        return {
            "proposed_rules": [],
            "existing_rule_count": 0,
            "directives_analyzed": 0,
            "already_covered": 0,
        }

    def test_empty_profile_is_weak(self):
        report = _build_validity_report(
            self._empty_profile(),
            docs_scanned=1,
            sections_scanned=0,
            enforceable=self._empty_enforceable(),
        )
        assert report["status"] == "weak"
        assert report["total_claims"] == 0
        assert "core_theory" in report["missing_required_facets"]
        assert "problem_solving" in report["missing_required_facets"]
        assert "alignment" in report["missing_required_facets"]

    def test_strong_profile(self):
        profile = {
            "core_theory": [
                {"heading": "A", "source": "a.md:1", "claims": ["c1", "c2", "c3"]}
            ],
            "problem_solving": [
                {"heading": "B", "source": "b.md:2", "claims": ["c4", "c5"]}
            ],
            "alignment": [
                {"heading": "C", "source": "c.md:3", "claims": ["c6", "c7"]}
            ],
            "architecture": [],
            "anti_patterns": [],
            "abstractions": [],
        }
        enforceable = {
            "proposed_rules": [{"add_line": "LINTGATE_FORBID_REGEX: eval"}],
            "existing_rule_count": 1,
            "directives_analyzed": 2,
            "already_covered": 0,
        }
        report = _build_validity_report(
            profile, docs_scanned=3, sections_scanned=10, enforceable=enforceable
        )
        assert report["status"] == "strong"
        assert report["total_claims"] == 7
        assert report["missing_required_facets"] == []

    def test_partial_status_when_low_density(self):
        profile = {
            "core_theory": [
                {"heading": "A", "source": "a.md:1", "claims": ["c1", "c2", "c3"]}
            ],
            "problem_solving": [
                {"heading": "B", "source": "b.md:2", "claims": ["c4", "c5"]}
            ],
            "alignment": [
                {"heading": "C", "source": "c.md:3", "claims": ["c6", "c7"]}
            ],
            "architecture": [],
            "anti_patterns": [],
            "abstractions": [],
        }
        enforceable = self._empty_enforceable()
        report = _build_validity_report(
            profile, docs_scanned=10, sections_scanned=20, enforceable=enforceable
        )
        assert report["status"] == "partial"

    def test_claims_per_doc_zero_docs(self):
        report = _build_validity_report(
            self._empty_profile(),
            docs_scanned=0,
            sections_scanned=0,
            enforceable=self._empty_enforceable(),
        )
        assert report["claims_per_doc"] == 0.0

    def test_traceability_all_traced(self):
        profile = {
            "facet": [
                {"heading": "H", "source": "file.md:1", "claims": ["c1"]},
            ],
        }
        report = _build_validity_report(
            profile, docs_scanned=1, sections_scanned=1, enforceable=self._empty_enforceable()
        )
        assert report["traceability_pct"] == 100.0

    def test_traceability_no_entries(self):
        report = _build_validity_report(
            self._empty_profile(),
            docs_scanned=1,
            sections_scanned=0,
            enforceable=self._empty_enforceable(),
        )
        assert report["traceability_pct"] == 0.0

    def test_warnings_and_recommendations_populated(self):
        report = _build_validity_report(
            self._empty_profile(),
            docs_scanned=1,
            sections_scanned=0,
            enforceable=self._empty_enforceable(),
        )
        assert len(report["warnings"]) > 0
        assert len(report["recommendations"]) > 0

    def test_return_keys(self):
        report = _build_validity_report(
            self._empty_profile(),
            docs_scanned=0,
            sections_scanned=0,
            enforceable=self._empty_enforceable(),
        )
        expected_keys = {
            "status",
            "docs_scanned",
            "sections_scanned",
            "total_claims",
            "claims_per_doc",
            "facets_with_claims",
            "missing_required_facets",
            "traceability_pct",
            "existing_rules",
            "proposed_rules",
            "warnings",
            "recommendations",
        }
        assert set(report.keys()) == expected_keys


# ─── _extract_enforceable_rules (sigma=13) ────────────────────────────


class TestExtractEnforceableRules:
    def test_no_directives(self):
        guidance = {"directives": {"do_not": [], "must": [], "critical": []}}
        result = _extract_enforceable_rules(guidance, set(), [])
        assert result["proposed_rules"] == []
        assert result["directives_analyzed"] == 0

    def test_extracts_do_not_use_rule(self):
        guidance = {"directives": {"do_not": ["DO NOT use eval"], "must": [], "critical": []}}
        result = _extract_enforceable_rules(guidance, set(), [])
        assert len(result["proposed_rules"]) == 1
        rule = result["proposed_rules"][0]
        assert rule["proposed_rule"]["kind"] == "forbid_regex"
        assert "eval" in rule["proposed_rule"]["pattern"]
        assert "LINTGATE_FORBID_REGEX" in rule["add_line"]

    def test_extracts_must_use_rule(self):
        guidance = {
            "directives": {"do_not": [], "must": ["MUST use the pipeline module"], "critical": []}
        }
        result = _extract_enforceable_rules(guidance, set(), [])
        assert len(result["proposed_rules"]) == 1
        rule = result["proposed_rules"][0]
        assert rule["proposed_rule"]["kind"] == "require_regex"
        assert "LINTGATE_REQUIRE_REGEX" in rule["add_line"]

    def test_skips_covered_patterns(self):
        guidance = {"directives": {"do_not": ["DO NOT use eval"], "must": [], "critical": []}}
        existing_patterns = {r"\beval\b"}
        result = _extract_enforceable_rules(guidance, existing_patterns, [])
        assert result["already_covered"] == 1
        assert result["proposed_rules"] == []

    def test_deduplicates_proposed_rules(self):
        guidance = {
            "directives": {
                "do_not": ["DO NOT use eval", "DO NOT use eval"],
                "must": [],
                "critical": [],
            }
        }
        result = _extract_enforceable_rules(guidance, set(), [])
        assert len(result["proposed_rules"]) == 1

    def test_missing_directives_key(self):
        guidance = {}
        result = _extract_enforceable_rules(guidance, set(), [])
        assert result["proposed_rules"] == []
        assert result["directives_analyzed"] == 0

    def test_existing_rule_count(self):
        guidance = {"directives": {"do_not": [], "must": [], "critical": []}}
        existing_rules = [{"pattern": "x"}, {"pattern": "y"}]
        result = _extract_enforceable_rules(guidance, set(), existing_rules)
        assert result["existing_rule_count"] == 2

    def test_do_not_import_rule(self):
        guidance = {
            "directives": {"do_not": ["DO NOT import os.system"], "must": [], "critical": []}
        }
        result = _extract_enforceable_rules(guidance, set(), [])
        assert len(result["proposed_rules"]) == 1
        assert result["proposed_rules"][0]["proposed_rule"]["kind"] == "forbid_regex"

    def test_return_type_and_keys(self):
        guidance = {"directives": {"do_not": [], "must": [], "critical": []}}
        result = _extract_enforceable_rules(guidance, set(), [])
        assert isinstance(result, dict)
        assert set(result.keys()) == {
            "proposed_rules",
            "existing_rule_count",
            "directives_analyzed",
            "already_covered",
        }


# ─── _build_digest_text (sigma=14) ───────────────────────────────────


class TestBuildDigestText:
    def test_empty_inputs(self):
        text, tokens = _build_digest_text(
            {"proposed_rules": [], "existing_rule_count": 0},
            {},
            [],
        )
        assert "## Project Theory (Enforceable Rules)" in text
        assert "(no enforceable rules extracted)" in text
        assert isinstance(tokens, int)
        assert tokens > 0

    def test_includes_proposed_rules(self):
        enforceable = {
            "proposed_rules": [{"add_line": "LINTGATE_FORBID_REGEX: eval"}],
            "existing_rule_count": 0,
        }
        text, _ = _build_digest_text(enforceable, {}, [])
        assert "LINTGATE_FORBID_REGEX: eval" in text

    def test_includes_existing_rule_count(self):
        enforceable = {"proposed_rules": [], "existing_rule_count": 5}
        text, _ = _build_digest_text(enforceable, {}, [])
        assert "5 active rules enforced by linter" in text
        assert "(no enforceable rules extracted)" not in text

    def test_includes_facet_summaries(self):
        summaries = {
            "core_theory": "The system enables compositional reasoning.",
            "problem_solving": "(no theory content found)",
        }
        text, _ = _build_digest_text(
            {"proposed_rules": [], "existing_rule_count": 0}, summaries, []
        )
        assert "Core Theory" in text
        assert "compositional reasoning" in text
        assert "Problem-Solving" not in text

    def test_includes_anti_patterns(self):
        text, _ = _build_digest_text(
            {"proposed_rules": [], "existing_rule_count": 0},
            {},
            ["Hard-coded workarounds break the system."],
        )
        assert "## Anti-Patterns (Conceptual Violations)" in text
        assert "Hard-coded" in text

    def test_anti_patterns_capped_at_7(self):
        anti_patterns = [f"Anti-pattern {i}." for i in range(15)]
        text, _ = _build_digest_text(
            {"proposed_rules": [], "existing_rule_count": 0}, {}, anti_patterns
        )
        displayed = text.count("Anti-pattern")
        assert displayed == 7

    def test_long_anti_pattern_truncated(self):
        long_ap = "x" * 200
        text, _ = _build_digest_text(
            {"proposed_rules": [], "existing_rule_count": 0}, {}, [long_ap]
        )
        assert "..." in text

    def test_token_estimate_positive(self):
        _, tokens = _build_digest_text(
            {"proposed_rules": [], "existing_rule_count": 0},
            {"core_theory": "Some theory claim."},
            [],
        )
        assert tokens > 0

    def test_proposed_rules_capped_at_10(self):
        rules = [{"add_line": f"LINTGATE_FORBID_REGEX: pattern_{i}"} for i in range(15)]
        enforceable = {"proposed_rules": rules, "existing_rule_count": 0}
        text, _ = _build_digest_text(enforceable, {}, [])
        assert text.count("LINTGATE_FORBID_REGEX") == 10

    def test_return_types(self):
        text, tokens = _build_digest_text(
            {"proposed_rules": [], "existing_rule_count": 0}, {}, []
        )
        assert isinstance(text, str)
        assert isinstance(tokens, int)


# ─── get_theory_context_from_profile (sigma=16) ──────────────────────


class TestGetTheoryContextFromProfile:
    def test_empty_profile(self):
        result = get_theory_context_from_profile({})
        assert result["claims"] == []
        assert result["total_matched"] == 0
        assert result["returned_count"] == 0
        assert result["truncated"] is False

    def test_none_profile(self):
        result = get_theory_context_from_profile(None)
        assert result["claims"] == []
        assert result["total_matched"] == 0

    def test_max_claims_zero(self):
        profile = {
            "core_theory": [
                {"heading": "H", "source": "s.md:1", "claims": ["A claim."]},
            ],
        }
        result = get_theory_context_from_profile(profile, max_claims=0)
        assert result["claims"] == []

    def test_retrieves_all_claims_no_filter(self):
        profile = {
            "core_theory": [
                {"heading": "H", "source": "s.md:1", "claims": ["Claim one.", "Claim two."]},
            ],
        }
        result = get_theory_context_from_profile(profile)
        assert result["total_matched"] == 2
        assert result["returned_count"] == 2

    def test_facet_filter(self):
        profile = {
            "core_theory": [
                {"heading": "A", "source": "a.md:1", "claims": ["Core claim."]},
            ],
            "alignment": [
                {"heading": "B", "source": "b.md:2", "claims": ["Alignment claim."]},
            ],
        }
        result = get_theory_context_from_profile(profile, facet="alignment")
        assert result["total_matched"] == 1
        assert result["claims"][0]["facet"] == "alignment"

    def test_nonexistent_facet_returns_all(self):
        profile = {
            "core_theory": [
                {"heading": "A", "source": "a.md:1", "claims": ["A claim."]},
            ],
        }
        result = get_theory_context_from_profile(profile, facet="nonexistent")
        assert result["total_matched"] == 1

    def test_keyword_filtering(self):
        profile = {
            "core_theory": [
                {
                    "heading": "H",
                    "source": "s.md:1",
                    "claims": [
                        "Compositional architectures enable modular evolution.",
                        "The system uses brute force search.",
                    ],
                },
            ],
        }
        result = get_theory_context_from_profile(profile, keywords=["compositional"])
        assert result["total_matched"] == 1
        assert "compositional" in result["claims"][0]["claim"].lower()

    def test_keyword_case_insensitive(self):
        profile = {
            "core_theory": [
                {"heading": "H", "source": "s.md:1", "claims": ["Compositional Reasoning."]},
            ],
        }
        result = get_theory_context_from_profile(profile, keywords=["COMPOSITIONAL"])
        assert result["total_matched"] == 1

    def test_multiple_keywords_boost_score(self):
        profile = {
            "core_theory": [
                {
                    "heading": "H",
                    "source": "s.md:1",
                    "claims": [
                        "Compositional modular architecture.",
                        "Simple compositional claim.",
                    ],
                },
            ],
        }
        result = get_theory_context_from_profile(
            profile, keywords=["compositional", "modular"]
        )
        assert result["claims"][0]["relevance_score"] == 2

    def test_truncation(self):
        profile = {
            "core_theory": [
                {
                    "heading": "H",
                    "source": "s.md:1",
                    "claims": [f"Claim {i}." for i in range(10)],
                },
            ],
        }
        result = get_theory_context_from_profile(profile, max_claims=3)
        assert result["returned_count"] == 3
        assert result["total_matched"] == 10
        assert result["truncated"] is True

    def test_claim_structure(self):
        profile = {
            "core_theory": [
                {"heading": "Title", "source": "file.md:42", "claims": ["A theory claim."]},
            ],
        }
        result = get_theory_context_from_profile(profile)
        claim = result["claims"][0]
        assert claim["facet"] == "core_theory"
        assert claim["claim"] == "A theory claim."
        assert claim["source"] == "file.md:42"
        assert claim["heading"] == "Title"
        assert "relevance_score" in claim

    def test_query_echoed_back(self):
        result = get_theory_context_from_profile(
            {}, facet="core_theory", keywords=["test"]
        )
        assert result["query"]["facet"] == "core_theory"
        assert result["query"]["keywords"] == ["test"]

    def test_missing_claims_key_in_entry(self):
        profile = {
            "core_theory": [
                {"heading": "H", "source": "s.md:1"},
            ],
        }
        result = get_theory_context_from_profile(profile)
        assert result["total_matched"] == 0

    def test_missing_source_and_heading(self):
        profile = {
            "core_theory": [
                {"claims": ["A claim."]},
            ],
        }
        result = get_theory_context_from_profile(profile)
        assert result["claims"][0]["source"] == ""
        assert result["claims"][0]["heading"] == ""

    def test_return_type(self):
        result = get_theory_context_from_profile({})
        assert isinstance(result, dict)
        assert isinstance(result["claims"], list)
        assert isinstance(result["truncated"], bool)


# ─── check_theory_staleness (sigma=22) ────────────────────────────────


class TestCheckTheoryStaleness:
    def test_no_uncommitted_files_not_stale(self):
        result = check_theory_staleness(
            project_root="/tmp/proj",
            theory_profile={"core_theory": []},
            git_context={"modified_files": [], "untracked_files": []},
        )
        assert result["stale"] is False
        assert result["uncovered_files"] == []
        assert result["total_uncommitted_py"] == 0
        assert result["recommendation"] == ""

    def test_theory_profile_none_is_stale(self):
        result = check_theory_staleness(
            project_root="/tmp/proj",
            theory_profile=None,
            git_context={"modified_files": ["src/core.py"], "untracked_files": []},
        )
        assert result["stale"] is True
        assert "src/core.py" in result["uncovered_files"]
        assert "No theory profile exists" in result["recommendation"]
        assert "build_theory_pack" in result["recommendation"]

    def test_uncommitted_files_all_covered_not_stale(self, tmp_path):
        # Create a real .py file with a docstring >= 30 chars so _find_uncovered_files
        # would pick it up if not covered
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        py_file = src_dir / "core.py"
        py_file.write_text('"""This module provides core utilities for the project."""\nx = 1\n')

        theory_profile = {
            "core_theory": [
                {
                    "heading": "Core",
                    "source": "src/core.py:1",
                    "claims": ["A claim"],
                }
            ],
        }
        result = check_theory_staleness(
            project_root=str(tmp_path),
            theory_profile=theory_profile,
            git_context={"modified_files": ["src/core.py"], "untracked_files": []},
        )
        assert result["stale"] is False
        assert result["uncovered_files"] == []
        assert result["total_uncommitted_py"] == 1

    def test_uncommitted_uncovered_file_is_stale(self, tmp_path):
        # Create an uncovered .py file with a long enough docstring
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        py_file = src_dir / "new_module.py"
        py_file.write_text(
            '"""This module provides brand new functionality for the project."""\ndef foo(): pass\n'
        )

        theory_profile = {
            "core_theory": [
                {
                    "heading": "Core",
                    "source": "src/other.py:1",
                    "claims": ["A claim"],
                }
            ],
        }
        result = check_theory_staleness(
            project_root=str(tmp_path),
            theory_profile=theory_profile,
            git_context={"modified_files": ["src/new_module.py"], "untracked_files": []},
        )
        assert result["stale"] is True
        assert "src/new_module.py" in result["uncovered_files"]
        assert "build_theory_pack" in result["recommendation"]

    def test_empty_git_context_not_stale(self):
        result = check_theory_staleness(
            project_root="/tmp/proj",
            theory_profile={"core_theory": []},
            git_context={},
        )
        assert result["stale"] is False
        assert result["total_uncommitted_py"] == 0

    def test_return_keys(self):
        result = check_theory_staleness(
            project_root="/tmp/proj",
            theory_profile={},
            git_context={},
        )
        expected_keys = {"stale", "uncovered_files", "total_uncommitted_py", "recommendation"}
        assert set(result.keys()) == expected_keys

    def test_return_types(self):
        result = check_theory_staleness(
            project_root="/tmp/proj",
            theory_profile={},
            git_context={},
        )
        assert isinstance(result, dict)
        assert isinstance(result["stale"], bool)
        assert isinstance(result["uncovered_files"], list)
        assert isinstance(result["total_uncommitted_py"], int)
        assert isinstance(result["recommendation"], str)

    def test_only_test_files_excluded_not_stale(self):
        result = check_theory_staleness(
            project_root="/tmp/proj",
            theory_profile=None,
            git_context={
                "modified_files": ["tests/test_foo.py", "test_bar.py"],
                "untracked_files": [],
            },
        )
        assert result["stale"] is False
        assert result["total_uncommitted_py"] == 0


# ─── _collect_covered_sources (sigma=18) ──────────────────────────────


class TestCollectCoveredSources:
    def test_empty_profile(self):
        assert _collect_covered_sources({}) == set()

    def test_extracts_file_from_colon_source(self):
        profile = {
            "core_theory": [{"source": "src/core.py:10", "claims": ["x"]}],
        }
        result = _collect_covered_sources(profile)
        assert "src/core.py" in result

    def test_extracts_source_without_colon(self):
        profile = {
            "alignment": [{"source": "README.md", "claims": ["y"]}],
        }
        result = _collect_covered_sources(profile)
        assert "README.md" in result

    def test_multiple_facets_combined(self):
        profile = {
            "core_theory": [{"source": "a.py:1", "claims": []}],
            "alignment": [{"source": "b.py:5", "claims": []}],
            "architecture": [{"source": "c.py", "claims": []}],
        }
        result = _collect_covered_sources(profile)
        assert result == {"a.py", "b.py", "c.py"}

    def test_deduplicates_same_file(self):
        profile = {
            "core_theory": [
                {"source": "x.py:1", "claims": []},
                {"source": "x.py:20", "claims": []},
            ],
        }
        result = _collect_covered_sources(profile)
        assert result == {"x.py"}

    def test_skips_non_list_facet_values(self):
        profile = {
            "summary": "not a list",
            "core_theory": [{"source": "ok.py:1", "claims": []}],
        }
        result = _collect_covered_sources(profile)
        assert result == {"ok.py"}

    def test_entry_missing_source_key(self):
        profile = {
            "core_theory": [{"claims": ["no source field"]}],
        }
        result = _collect_covered_sources(profile)
        assert result == {""}

    def test_returns_set_type(self):
        assert isinstance(_collect_covered_sources({}), set)


# ─── _has_substantial_docstring (sigma=16) ────────────────────────────


class TestHasSubstantialDocstring:
    def test_file_with_long_docstring(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text('"""This module provides substantial functionality for the project."""\nx = 1\n')
        assert _has_substantial_docstring(str(f)) is True

    def test_file_with_short_docstring(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text('"""Short."""\nx = 1\n')
        assert _has_substantial_docstring(str(f)) is False

    def test_file_without_docstring(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("x = 1\ny = 2\n")
        assert _has_substantial_docstring(str(f)) is False

    def test_empty_file(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("")
        assert _has_substantial_docstring(str(f)) is False

    def test_syntax_error_returns_false(self, tmp_path):
        f = tmp_path / "bad.py"
        f.write_text("def broken(\n")
        assert _has_substantial_docstring(str(f)) is False

    def test_nonexistent_file_returns_false(self):
        assert _has_substantial_docstring("/no/such/file.py") is False

    def test_exactly_30_chars(self, tmp_path):
        docstring = "a" * 30
        f = tmp_path / "mod.py"
        f.write_text(f'"""{docstring}"""\n')
        assert _has_substantial_docstring(str(f)) is True

    def test_29_chars_too_short(self, tmp_path):
        docstring = "a" * 29
        f = tmp_path / "mod.py"
        f.write_text(f'"""{docstring}"""\n')
        assert _has_substantial_docstring(str(f)) is False

    def test_whitespace_only_docstring(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text('"""                                   """\n')
        assert _has_substantial_docstring(str(f)) is False

    def test_returns_bool(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("x = 1\n")
        assert isinstance(_has_substantial_docstring(str(f)), bool)
