"""Tests for PASS 2 bug fixes: Fix 7, Fix 5.

Fix 7: Bigram + negation pair detection in contradiction checker
Fix 5: Structured dry_run changes in lint_fixer
"""

from __future__ import annotations

from lintgate.context_auditor_checks import (
    _detect_negation_pairs,
    _extract_keywords,
    check_contradictions,
)

# ── Fix 7: Contradiction detection ──────────────────────────────────────


class TestBigramExtraction:
    """_extract_keywords should extract both unigrams and bigrams."""

    def test_bigrams_extracted(self) -> None:
        directives = {"always validate credentials before access"}
        keywords = _extract_keywords(directives)
        assert "validate credentials" in keywords
        assert "credentials" in keywords

    def test_unigrams_still_extracted(self) -> None:
        directives = {"always validate inputs"}
        keywords = _extract_keywords(directives)
        assert "always" in keywords
        assert "validate" in keywords
        assert "inputs" in keywords


class TestNegationPairs:
    """_detect_negation_pairs should find always/never conflicts."""

    def test_always_vs_never_detected(self) -> None:
        do_directives = {"always use caching for expensive calls"}
        do_not_directives = {"never use caching in test environments"}
        pairs = _detect_negation_pairs(do_directives, do_not_directives)
        assert "caching" in pairs

    def test_no_false_positive_different_terms(self) -> None:
        do_directives = {"always write tests"}
        do_not_directives = {"never commit secrets"}
        pairs = _detect_negation_pairs(do_directives, do_not_directives)
        assert len(pairs) == 0


class TestContradictionDetection:
    """check_contradictions should detect semantic conflicts."""

    def _run_check(self, do: list[str], do_not: list[str]) -> list[dict]:
        checks: list[dict] = []
        suggestions: list[str] = []
        guidance = {
            "directives": {
                "do": do,
                "do_not": do_not,
            }
        }
        check_contradictions(checks, suggestions, guidance)
        return checks

    def test_always_vs_never_triggers_warning(self) -> None:
        checks = self._run_check(
            do=["Always use caching for database queries"],
            do_not=["Never use caching in this project"],
        )
        assert any(c["status"] == "warn" for c in checks)

    def test_always_validate_vs_never_validate(self) -> None:
        checks = self._run_check(
            do=["Always validate credentials before access"],
            do_not=["Never validate credentials in test mode"],
        )
        assert any(c["status"] == "warn" for c in checks)

    def test_no_false_positive_unrelated_directives(self) -> None:
        checks = self._run_check(
            do=["Always write tests"],
            do_not=["Never commit secrets"],
        )
        # Should pass (no contradiction)
        assert any(c["status"] == "pass" for c in checks)
