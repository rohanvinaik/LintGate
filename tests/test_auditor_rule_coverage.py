"""Tests for lintgate/context/auditor_rule_coverage.py.

Covers directive enforceability classification, coverage tokens,
rule matching, and the check_rule_coverage pipeline.
"""

from __future__ import annotations

from lintgate.context.auditor_rule_coverage import (
    DirectiveClassification,
    _count_syntactic_ids,
    _coverage_tokens,
    _directive_has_matching_rule,
    _has_syntactic_id,
    _is_regex_enforceable,
    check_rule_coverage,
    classify_directive_enforceability,
)

# ── _coverage_tokens ─────────────────────────────────────────────


class TestCoverageTokens:
    def test_basic_words(self):
        tokens = _coverage_tokens("hello world python")
        assert "hello" in tokens
        assert "world" in tokens
        assert "python" in tokens

    def test_strips_trailing_s(self):
        tokens = _coverage_tokens("tests modules")
        assert "test" in tokens
        assert "module" in tokens

    def test_short_tokens_excluded(self):
        tokens = _coverage_tokens("a do it is be")
        assert len(tokens) == 0

    def test_underscore_splitting(self):
        tokens = _coverage_tokens("some_long_name")
        assert "some" in tokens
        assert "long" in tokens
        assert "name" in tokens

    def test_empty_string(self):
        assert _coverage_tokens("") == set()


# ── _has_syntactic_id / _count_syntactic_ids ─────────────────────


class TestSyntacticIdentifiers:
    def test_dotted_path(self):
        assert _has_syntactic_id("Use foo.bar.baz")

    def test_backtick_identifier(self):
        assert _has_syntactic_id("Don't use `some_func()`")

    def test_constant_name(self):
        assert _has_syntactic_id("Avoid MAX_RETRIES usage")

    def test_function_call(self):
        assert _has_syntactic_id("Call run_tests() first")

    def test_plain_prose(self):
        assert _has_syntactic_id("Keep it simple and clear") is False
        assert _count_syntactic_ids("Keep it simple and clear") == 0

    def test_count_multiple(self):
        text = "Use `foo_bar()` and MAX_VALUE with mod.path"
        count = _count_syntactic_ids(text)
        assert count >= 3


# ── classify_directive_enforceability ────────────────────────────


class TestClassifyDirectiveEnforceability:
    def test_enforceable_syntactic_only(self):
        result = classify_directive_enforceability("DO NOT use `os.system()` for subprocess calls")
        assert result.classification == "enforceable"
        assert result.confidence >= 0.7

    def test_architectural_only(self):
        result = classify_directive_enforceability(
            "DO NOT bypass verification without understanding the constraint"
        )
        assert result.classification == "architectural"
        assert result.confidence >= 0.7

    def test_uncertain_no_signals(self):
        result = classify_directive_enforceability("DO NOT do bad things")
        assert result.classification == "uncertain"
        assert result.confidence <= 0.5

    def test_mixed_syntactic_dominant(self):
        result = classify_directive_enforceability(
            "DO NOT call `foo.bar()` or `baz_func()` without verify"
        )
        # Multiple syntactic IDs vs single architectural cue
        assert result.classification in ("enforceable", "uncertain")

    def test_dataclass_fields(self):
        dc = DirectiveClassification(classification="enforceable", confidence=0.9, reason="test")
        assert dc.classification == "enforceable"
        assert dc.confidence == 0.9
        assert dc.reason == "test"


class TestIsRegexEnforceable:
    def test_enforceable(self):
        assert _is_regex_enforceable("DO NOT use `os.system()`") is True

    def test_not_enforceable(self):
        assert _is_regex_enforceable("DO NOT take shortcuts") is False
        # Verify the inverse: a syntactic directive IS enforceable
        assert _is_regex_enforceable("DO NOT use `os.system()`") is True


# ── _directive_has_matching_rule ──────────────────────────────────


class TestDirectiveHasMatchingRule:
    def test_exact_text_match(self):
        rules = [{"message": "DO NOT use os.system", "source": "", "pattern": ""}]
        assert _directive_has_matching_rule("do not use os.system", set(), rules)

    def test_keyword_overlap(self):
        rules = [{"message": "system call forbidden", "source": "", "pattern": "os.system"}]
        tokens = _coverage_tokens("os.system calls")
        assert _directive_has_matching_rule("os.system calls", tokens, rules)

    def test_no_match(self):
        rules = [{"message": "unrelated rule", "source": "", "pattern": ""}]
        tokens = _coverage_tokens("do not use eval")
        assert not _directive_has_matching_rule("do not use eval", tokens, rules)

    def test_empty_rules(self):
        assert not _directive_has_matching_rule("anything", {"test"}, [])


# ── check_rule_coverage ──────────────────────────────────────────


class TestCheckRuleCoverage:
    def test_no_directives_passes(self):
        checks: list = []
        suggestions: list = []
        check_rule_coverage(
            checks, suggestions, {"directives": {"do_not": []}}, [], {"min_rule_coverage_pct": 50}
        )
        assert len(checks) == 1
        assert checks[0]["status"] == "pass"

    def test_all_covered_passes(self):
        checks: list = []
        suggestions: list = []
        directives = {"directives": {"do_not": ["DO NOT use `os.system()`"]}}
        rules = [
            {"kind": "forbid_regex", "message": "os.system", "source": "", "pattern": "os.system"}
        ]
        check_rule_coverage(checks, suggestions, directives, rules, {"min_rule_coverage_pct": 50})
        assert any(c["status"] == "pass" for c in checks)

    def test_uncovered_warns(self):
        checks: list = []
        suggestions: list = []
        directives = {
            "directives": {
                "do_not": [
                    "DO NOT use `eval()` anywhere",
                    "DO NOT call `exec()` directly",
                ]
            }
        }
        check_rule_coverage(checks, suggestions, directives, [], {"min_rule_coverage_pct": 50})
        warn_checks = [c for c in checks if c["status"] == "warn"]
        assert len(warn_checks) >= 1

    def test_architectural_only_passes(self):
        checks: list = []
        suggestions: list = []
        directives = {
            "directives": {
                "do_not": [
                    "DO NOT bypass verification without understanding",
                ]
            }
        }
        check_rule_coverage(checks, suggestions, directives, [], {"min_rule_coverage_pct": 50})
        assert any(c["status"] == "pass" for c in checks)

    def test_missing_directives_key(self):
        checks: list = []
        suggestions: list = []
        check_rule_coverage(checks, suggestions, {}, [], {"min_rule_coverage_pct": 50})
        assert len(checks) == 1
        assert checks[0]["status"] == "pass"
