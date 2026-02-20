"""Tests for battle-tested zero-state bootstrap defaults."""

from __future__ import annotations

import re

from lintgate.bootstrap_defaults import ZERO_STATE_ANTI_PATTERNS, ZERO_STATE_FACET_FALLBACKS

# The same regex used by context_bootstrap._select_actionable_anti_patterns
_NEGATIVE_CUE_RE = re.compile(
    r"\b(do\s+not|don['']t|never|avoid|anti[- ]?pattern|what\s+didn['']t\s+work|"
    r"what\s+went\s+wrong|failure|mistake|trap|pitfall|not\s+recommended)\b",
    re.IGNORECASE,
)


class TestAntiPatternQuality:
    def test_all_match_negative_cue_regex(self):
        """Every default anti-pattern must pass the same filter as extracted claims."""
        for item in ZERO_STATE_ANTI_PATTERNS:
            assert _NEGATIVE_CUE_RE.search(item), (
                f"Anti-pattern doesn't match _NEGATIVE_CUE_RE: {item!r}"
            )

    def test_all_under_length_limit(self):
        """Each item must be under 260 chars (the truncation threshold)."""
        for item in ZERO_STATE_ANTI_PATTERNS:
            assert len(item) <= 260, f"Anti-pattern too long ({len(item)} chars): {item!r}"

    def test_count_is_seven(self):
        """We should have exactly 7 curated anti-patterns."""
        assert len(ZERO_STATE_ANTI_PATTERNS) == 7

    def test_no_duplicates(self):
        """No duplicate items."""
        assert len(set(ZERO_STATE_ANTI_PATTERNS)) == len(ZERO_STATE_ANTI_PATTERNS)


class TestFacetFallbackQuality:
    def test_covers_all_four_facets(self):
        """All 4 facet keys must be present."""
        expected = {"core_theory", "problem_solving", "alignment", "architecture"}
        assert set(ZERO_STATE_FACET_FALLBACKS.keys()) == expected

    def test_all_non_empty(self):
        """Each fallback must be a non-empty string."""
        for key, val in ZERO_STATE_FACET_FALLBACKS.items():
            assert isinstance(val, str) and len(val) > 20, (
                f"Facet fallback '{key}' is too short or wrong type: {val!r}"
            )

    def test_all_unique(self):
        """Each fallback text must be unique."""
        values = list(ZERO_STATE_FACET_FALLBACKS.values())
        assert len(set(values)) == len(values)
