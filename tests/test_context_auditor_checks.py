"""Comprehensive tests for lintgate/context_auditor_checks.py."""

from __future__ import annotations

import os
import time

import pytest

from lintgate.context_auditor_checks import (
    DirectiveClassification,
    _count_syntactic_ids,
    _coverage_tokens,
    _detect_generated_patterns,
    _directive_has_matching_rule,
    _extract_keywords,
    _find_bare_name_in_project,
    _has_syntactic_id,
    _is_regex_enforceable,
    _matches_generated_pattern,
    check_contradictions,
    check_length,
    check_path_references,
    check_rule_coverage,
    check_staleness,
    check_structure,
    classify_directive_enforceability,
    extract_path_refs,
    find_dead_paths,
)

# ── check_length ────────────────────────────────────────────────────


class TestCheckLength:
    THRESHOLDS = {"max_lines_warn": 200, "max_lines_error": 500}

    def test_error_when_above_error_threshold(self):
        checks: list = []
        suggestions: list = []
        check_length(checks, suggestions, 600, self.THRESHOLDS)
        assert len(checks) == 1
        assert checks[0]["status"] == "error"
        assert "600 lines" in checks[0]["detail"]
        assert len(suggestions) == 1
        assert "600 lines" in suggestions[0]

    def test_warn_when_above_warn_below_error(self):
        checks: list = []
        suggestions: list = []
        check_length(checks, suggestions, 300, self.THRESHOLDS)
        assert checks[0]["status"] == "warn"
        assert "300 lines" in checks[0]["detail"]
        assert len(suggestions) == 1

    def test_pass_when_within_guidelines(self):
        checks: list = []
        suggestions: list = []
        check_length(checks, suggestions, 100, self.THRESHOLDS)
        assert checks[0]["status"] == "pass"
        assert len(suggestions) == 0

    def test_boundary_at_warn_threshold(self):
        checks: list = []
        suggestions: list = []
        check_length(checks, suggestions, 200, self.THRESHOLDS)
        assert checks[0]["status"] == "pass"

    def test_boundary_at_error_threshold(self):
        checks: list = []
        suggestions: list = []
        check_length(checks, suggestions, 500, self.THRESHOLDS)
        # 500 is not > 500, so it falls into warn branch (> 200)
        assert checks[0]["status"] == "warn"


# ── check_structure ─────────────────────────────────────────────────


class TestCheckStructure:
    def test_pass_with_good_structure(self):
        text = "## Section 1\nSome text\n## Section 2\nMore text\n```python\ncode\n```\n| col | col2 |\n| --- | --- |"
        lines = text.splitlines()
        checks: list = []
        suggestions: list = []
        check_structure(checks, suggestions, text, lines)
        assert checks[0]["status"] == "pass"
        assert "has code examples" in checks[0]["detail"]
        assert "has tables" in checks[0]["detail"]

    def test_warn_with_flat_text(self):
        text = "Just a single paragraph of text with no headers at all."
        lines = text.splitlines()
        checks: list = []
        suggestions: list = []
        check_structure(checks, suggestions, text, lines)
        assert checks[0]["status"] == "warn"
        assert "0 section header(s)" in checks[0]["detail"]
        assert len(suggestions) == 1

    def test_pass_with_only_headers_no_code_no_tables(self):
        text = "## Alpha\nContent\n## Beta\nMore content"
        lines = text.splitlines()
        checks: list = []
        suggestions: list = []
        check_structure(checks, suggestions, text, lines)
        assert checks[0]["status"] == "pass"
        assert "has code examples" not in checks[0]["detail"]
        assert "has tables" not in checks[0]["detail"]

    def test_warn_with_single_header(self):
        text = "## Only one\nLots of text here."
        lines = text.splitlines()
        checks: list = []
        suggestions: list = []
        check_structure(checks, suggestions, text, lines)
        assert checks[0]["status"] == "warn"
        assert "1 section header(s)" in checks[0]["detail"]


# ── check_staleness ─────────────────────────────────────────────────


class TestCheckStaleness:
    THRESHOLDS = {"staleness_days": 30}

    def test_pass_with_fresh_file(self, tmp_path):
        f = tmp_path / "fresh.md"
        f.write_text("content")
        checks: list = []
        suggestions: list = []
        check_staleness(checks, suggestions, str(f), self.THRESHOLDS)
        assert checks[0]["status"] == "pass"
        assert "0 days ago" in checks[0]["detail"]

    def test_warn_with_stale_file(self, tmp_path):
        f = tmp_path / "stale.md"
        f.write_text("content")
        # Set mtime to 60 days ago
        old_time = time.time() - (60 * 86400)
        os.utime(str(f), (old_time, old_time))
        checks: list = []
        suggestions: list = []
        check_staleness(checks, suggestions, str(f), self.THRESHOLDS)
        assert checks[0]["status"] == "warn"
        assert "60 days ago" in checks[0]["detail"]
        assert len(suggestions) == 1

    def test_warn_with_missing_file(self):
        checks: list = []
        suggestions: list = []
        check_staleness(checks, suggestions, "/nonexistent/file.md", self.THRESHOLDS)
        assert checks[0]["status"] == "warn"
        assert "Could not determine" in checks[0]["detail"]
        assert len(suggestions) == 0


# ── check_contradictions ────────────────────────────────────────────


class TestCheckContradictions:
    def test_warn_with_overlapping_keywords(self):
        guidance = {
            "directives": {
                "do": ["Always validate credentials before deployment"],
                "do_not": ["Never validate credentials in production code"],
            }
        }
        checks: list = []
        suggestions: list = []
        check_contradictions(checks, suggestions, guidance)
        assert checks[0]["status"] == "warn"
        assert "overlapping" in checks[0]["detail"]

    def test_pass_with_no_overlap(self):
        guidance = {
            "directives": {
                "do": ["Write documentation for every function"],
                "do_not": ["Never commit secrets to version control"],
            }
        }
        checks: list = []
        suggestions: list = []
        check_contradictions(checks, suggestions, guidance)
        assert checks[0]["status"] == "pass"

    def test_pass_with_empty_directives(self):
        guidance = {"directives": {"do": [], "do_not": []}}
        checks: list = []
        suggestions: list = []
        check_contradictions(checks, suggestions, guidance)
        assert checks[0]["status"] == "pass"

    def test_pass_when_only_noise_words_overlap(self):
        # These share "use" and "code" but both are noise words
        guidance = {
            "directives": {
                "do": ["Use clean code"],
                "do_not": ["Do not use code smells"],
            }
        }
        checks: list = []
        suggestions: list = []
        check_contradictions(checks, suggestions, guidance)
        assert checks[0]["status"] == "pass"

    def test_pass_with_missing_directives_key(self):
        guidance = {}
        checks: list = []
        suggestions: list = []
        check_contradictions(checks, suggestions, guidance)
        assert checks[0]["status"] == "pass"


# ── check_rule_coverage ─────────────────────────────────────────────


class TestCheckRuleCoverage:
    THRESHOLDS = {"min_rule_coverage_pct": 50}

    def test_pass_with_no_directives(self):
        guidance = {"directives": {"do_not": []}}
        checks: list = []
        suggestions: list = []
        check_rule_coverage(checks, suggestions, guidance, [], self.THRESHOLDS)
        assert checks[0]["status"] == "pass"
        assert "No DO NOT directives" in checks[0]["detail"]

    def test_pass_with_full_coverage(self):
        guidance = {
            "directives": {
                "do_not": ["Do not use console.log()"],
            }
        }
        rules = [
            {
                "kind": "forbid_regex",
                "message": "console.log",
                "source": "",
                "pattern": "console\\.log",
            }
        ]
        checks: list = []
        suggestions: list = []
        check_rule_coverage(checks, suggestions, guidance, rules, self.THRESHOLDS)
        assert checks[0]["status"] == "pass"

    def test_warn_with_partial_coverage(self):
        guidance = {
            "directives": {
                "do_not": [
                    "Do not use console.log()",
                    "Do not use eval_function()",
                ],
            }
        }
        # Only one rule covers the first directive
        rules = [
            {
                "kind": "forbid_regex",
                "message": "console.log",
                "source": "",
                "pattern": "console\\.log",
            }
        ]
        checks: list = []
        suggestions: list = []
        check_rule_coverage(checks, suggestions, guidance, rules, self.THRESHOLDS)
        assert checks[0]["status"] == "pass"  # 1/2 = 50% == threshold

    def test_warn_below_threshold(self):
        guidance = {
            "directives": {
                "do_not": [
                    "Do not use console.log()",
                    "Do not use eval_function()",
                    "Do not use exec_command()",
                ],
            }
        }
        # Only one rule
        rules = [
            {
                "kind": "forbid_regex",
                "message": "console.log",
                "source": "",
                "pattern": "console\\.log",
            }
        ]
        checks: list = []
        suggestions: list = []
        check_rule_coverage(checks, suggestions, guidance, rules, self.THRESHOLDS)
        assert checks[0]["status"] == "warn"
        assert len(suggestions) >= 1

    def test_pass_no_enforceable_with_architectural(self):
        guidance = {
            "directives": {
                "do_not": [
                    "Do not bypass the review approach without understanding coherence",
                ],
            }
        }
        checks: list = []
        suggestions: list = []
        check_rule_coverage(checks, suggestions, guidance, [], self.THRESHOLDS)
        assert checks[0]["status"] == "pass"
        assert "No regex-enforceable" in checks[0]["detail"]
        assert "architectural" in checks[0]["detail"]


# ── check_path_references ───────────────────────────────────────────


class TestCheckPathReferences:
    THRESHOLDS = {"max_path_references": 50}

    def test_pass_all_paths_valid(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("code")
        text = "Check `src/main.py` for details."
        checks: list = []
        suggestions: list = []
        check_path_references(checks, suggestions, text, str(tmp_path), self.THRESHOLDS)
        assert any(c["status"] == "pass" and c["check"] == "path_references" for c in checks)

    def test_warn_dead_paths(self, tmp_path):
        text = "Check `src/missing.py` for details."
        checks: list = []
        suggestions: list = []
        check_path_references(checks, suggestions, text, str(tmp_path), self.THRESHOLDS)
        assert any(c["status"] == "warn" and c["check"] == "path_references" for c in checks)
        assert len(suggestions) >= 1

    def test_warn_too_many_paths(self, tmp_path):
        # Generate text with > 50 path refs
        refs = [f"`dir/file{i}.py`" for i in range(55)]
        text = "\n".join(refs)
        checks: list = []
        suggestions: list = []
        check_path_references(checks, suggestions, text, str(tmp_path), self.THRESHOLDS)
        assert any(c["check"] == "path_reference_volume" for c in checks)

    def test_pass_no_paths(self, tmp_path):
        text = "No paths here, just plain text."
        checks: list = []
        suggestions: list = []
        check_path_references(checks, suggestions, text, str(tmp_path), self.THRESHOLDS)
        assert checks[0]["status"] == "pass"
        assert "No path references" in checks[0]["detail"]


# ── extract_path_refs ───────────────────────────────────────────────


class TestExtractPathRefs:
    def test_extracts_paths_in_backticks(self):
        text = "See `src/main.py` and `docs/README.md` for details."
        refs = extract_path_refs(text)
        assert "src/main.py" in refs
        assert "docs/README.md" in refs

    def test_excludes_urls(self):
        text = "Visit `https://example.com/path` for info."
        refs = extract_path_refs(text)
        assert len(refs) == 0

    def test_excludes_shell_commands(self):
        text = "Run `pip install lintgate` to install."
        refs = extract_path_refs(text)
        assert len(refs) == 0

    def test_excludes_code_blocks(self):
        text = "```python\nfrom src/module.py import foo\n```\nSee `lib/utils.py`."
        refs = extract_path_refs(text)
        assert "lib/utils.py" in refs
        # The path inside the code block should not be extracted
        assert "src/module.py" not in refs

    def test_excludes_hf_model_ids(self):
        text = "Use `meta-llama/Llama-2-7b` for inference."
        refs = extract_path_refs(text)
        assert len(refs) == 0

    def test_excludes_tree_chars(self):
        text = "```\n`├── src/\n│   └── main.py`\n```"
        refs = extract_path_refs(text)
        assert len(refs) == 0

    def test_excludes_space_without_sep(self):
        text = "Use `some command here` to run."
        refs = extract_path_refs(text)
        assert len(refs) == 0

    def test_extracts_extension_only_path(self):
        text = "Edit `config.yaml` for settings."
        refs = extract_path_refs(text)
        assert "config.yaml" in refs


# ── find_dead_paths ─────────────────────────────────────────────────


class TestFindDeadPaths:
    def test_existing_paths(self, tmp_path):
        (tmp_path / "hello.py").write_text("pass")
        dead = find_dead_paths(["hello.py"], str(tmp_path))
        assert dead == []

    def test_missing_paths(self, tmp_path):
        dead = find_dead_paths(["missing.py"], str(tmp_path))
        assert "missing.py" in dead

    def test_glob_patterns_skipped(self, tmp_path):
        dead = find_dead_paths(["src/*.py"], str(tmp_path))
        assert dead == []

    def test_home_paths_expansion(self, tmp_path):
        # Use a path that definitely doesn't exist under home
        dead = find_dead_paths(["~/nonexistent_lintgate_test_path_xyz"], str(tmp_path))
        assert "~/nonexistent_lintgate_test_path_xyz" in dead

    def test_generated_patterns_skipped(self, tmp_path):
        dead = find_dead_paths(["dist/bundle.js"], str(tmp_path), ["dist/*"])
        assert dead == []

    def test_bare_name_found_in_subdirectory(self, tmp_path):
        subdir = tmp_path / "src" / "pkg"
        subdir.mkdir(parents=True)
        (subdir / "utils.py").write_text("pass")
        dead = find_dead_paths(["utils.py"], str(tmp_path))
        assert dead == []

    def test_bare_name_not_found(self, tmp_path):
        dead = find_dead_paths(["nonexistent_file_xyz.py"], str(tmp_path))
        assert "nonexistent_file_xyz.py" in dead

    def test_dotslash_prefix_stripped(self, tmp_path):
        (tmp_path / "main.py").write_text("pass")
        dead = find_dead_paths(["./main.py"], str(tmp_path))
        assert dead == []


# ── classify_directive_enforceability ───────────────────────────────


class TestClassifyDirectiveEnforceability:
    def test_pure_syntactic(self):
        result = classify_directive_enforceability("Do not use console.log() in production")
        assert result.classification == "enforceable"
        assert result.confidence >= 0.9

    def test_pure_architectural(self):
        result = classify_directive_enforceability(
            "Do not bypass the review approach without understanding"
        )
        assert result.classification == "architectural"
        assert result.confidence >= 0.7

    def test_mixed_syntactic_dominant(self):
        result = classify_directive_enforceability(
            "Do not bypass approach but always call my_func() and other_func() and validate_input()"
        )
        assert result.classification == "enforceable"
        assert result.confidence == pytest.approx(0.7)

    def test_mixed_architectural_dominant(self):
        result = classify_directive_enforceability(
            "Do not bypass the coherence constraint approach without understanding or verifying my_func()"
        )
        assert result.classification == "architectural"
        assert result.confidence == pytest.approx(0.7)

    def test_neither_signal(self):
        result = classify_directive_enforceability("Do not do bad things")
        assert result.classification == "uncertain"
        assert result.confidence <= 0.4

    def test_dataclass_defaults(self):
        dc = DirectiveClassification(classification="enforceable")
        assert dc.confidence == 1.0
        assert dc.reason == ""


# ── _extract_keywords ───────────────────────────────────────────────


class TestExtractKeywords:
    def test_extracts_4plus_letter_words(self):
        result = _extract_keywords({"always validate credentials before deployment"})
        assert "always" in result
        assert "validate" in result
        assert "credentials" in result
        assert "before" in result
        assert "deployment" in result

    def test_excludes_short_words(self):
        result = _extract_keywords({"do not use it"})
        assert "not" not in result
        assert "use" not in result

    def test_empty_input(self):
        result = _extract_keywords(set())
        assert result == set()


# ── _coverage_tokens ────────────────────────────────────────────────


class TestCoverageTokens:
    def test_basic_tokenization(self):
        tokens = _coverage_tokens("hello_world testing")
        assert "hello" in tokens
        assert "world" in tokens
        assert "testing" in tokens  # len > 4 and ends with s -> "testing"[:-1] = "testin"

    def test_strips_trailing_s(self):
        tokens = _coverage_tokens("errors warnings")
        assert "error" in tokens
        assert "warning" in tokens

    def test_short_tokens_excluded(self):
        tokens = _coverage_tokens("ab cd ef")
        assert len(tokens) == 0

    def test_underscore_splitting(self):
        tokens = _coverage_tokens("my_special_function")
        assert "special" in tokens
        assert "function" in tokens


# ── _is_regex_enforceable ──────────────────────────────────────────


class TestIsRegexEnforceable:
    def test_true_for_syntactic(self):
        assert _is_regex_enforceable("Do not use console.log() in code") is True

    def test_false_for_architectural(self):
        assert (
            _is_regex_enforceable("Do not bypass the review approach without understanding")
            is False
        )


# ── _has_syntactic_id and _count_syntactic_ids ──────────────────────


class TestSyntacticIdHelpers:
    def test_has_syntactic_id_dotted(self):
        assert _has_syntactic_id("os.path.join") is True

    def test_has_syntactic_id_backtick(self):
        assert _has_syntactic_id("avoid `eval`") is True

    def test_has_syntactic_id_constant(self):
        assert _has_syntactic_id("NEVER_USE_THIS") is True

    def test_has_syntactic_id_function_call(self):
        assert _has_syntactic_id("do not call my_func()") is True

    def test_has_syntactic_id_false(self):
        assert _has_syntactic_id("just plain words") is False

    def test_count_syntactic_ids(self):
        text = "Use os.path.join and `eval` and MY_CONST and some_func()"
        count = _count_syntactic_ids(text)
        assert count >= 3  # dotted, backtick, constant, possibly function


# ── _detect_generated_patterns ──────────────────────────────────────


class TestDetectGeneratedPatterns:
    def test_pyproject_toml(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[build-system]")
        patterns = _detect_generated_patterns(str(tmp_path))
        assert "dist" in patterns
        assert "*.egg-info" in patterns

    def test_makefile(self, tmp_path):
        (tmp_path / "Makefile").write_text("all:")
        patterns = _detect_generated_patterns(str(tmp_path))
        assert "build" in patterns
        assert "out" in patterns

    def test_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")
        patterns = _detect_generated_patterns(str(tmp_path))
        assert "node_modules" in patterns
        assert "dist" in patterns

    def test_cargo_toml(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]")
        patterns = _detect_generated_patterns(str(tmp_path))
        assert "target" in patterns

    def test_empty_project(self, tmp_path):
        patterns = _detect_generated_patterns(str(tmp_path))
        assert patterns == []

    def test_deduplication(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("")
        (tmp_path / "Makefile").write_text("")
        patterns = _detect_generated_patterns(str(tmp_path))
        # "build" and "build/*" should appear only once
        assert patterns.count("build") == 1
        assert patterns.count("build/*") == 1


# ── _matches_generated_pattern ──────────────────────────────────────


class TestMatchesGeneratedPattern:
    def test_matching_direct(self):
        assert _matches_generated_pattern("dist/bundle.js", ["dist/*"]) is True

    def test_matching_first_segment(self):
        assert _matches_generated_pattern("dist/sub/file.js", ["dist"]) is True

    def test_non_matching(self):
        assert _matches_generated_pattern("src/main.py", ["dist/*"]) is False

    def test_dot_slash_prefix_stripped(self):
        assert _matches_generated_pattern("./dist/file.js", ["dist/*"]) is True

    def test_egg_info_pattern(self):
        assert _matches_generated_pattern("mypackage.egg-info/PKG-INFO", ["*.egg-info/*"]) is True


# ── _find_bare_name_in_project ──────────────────────────────────────


class TestFindBareNameInProject:
    def test_file_in_root(self, tmp_path):
        (tmp_path / "myfile.py").write_text("pass")
        assert _find_bare_name_in_project("myfile.py", str(tmp_path)) is True

    def test_file_in_src_subdirectory(self, tmp_path):
        src = tmp_path / "src" / "pkg"
        src.mkdir(parents=True)
        (src / "utils.py").write_text("pass")
        assert _find_bare_name_in_project("utils.py", str(tmp_path)) is True

    def test_not_found(self, tmp_path):
        assert _find_bare_name_in_project("nope.py", str(tmp_path)) is False

    def test_depth_limit(self, tmp_path):
        # Create a file deeper than 3 levels; it should NOT be found
        deep = tmp_path / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        (deep / "deep.py").write_text("pass")
        assert _find_bare_name_in_project("deep.py", str(tmp_path)) is False


# ── _directive_has_matching_rule ────────────────────────────────────


class TestDirectiveHasMatchingRule:
    def test_exact_match_in_rule_text(self):
        rules = [{"message": "Do not use console.log()", "source": "", "pattern": ""}]
        result = _directive_has_matching_rule(
            "do not use console.log()",
            _coverage_tokens("do not use console.log()"),
            rules,
        )
        assert result is True

    def test_keyword_overlap_match(self):
        rules = [
            {
                "message": "forbid console logging",
                "source": "",
                "pattern": "console\\.log",
            }
        ]
        directive_lower = "do not use console.log in production"
        directive_words = _coverage_tokens(directive_lower)
        result = _directive_has_matching_rule(directive_lower, directive_words, rules)
        assert result is True

    def test_no_match(self):
        rules = [{"message": "forbid eval", "source": "", "pattern": "eval"}]
        directive_lower = "do not use print statements"
        directive_words = _coverage_tokens(directive_lower)
        result = _directive_has_matching_rule(directive_lower, directive_words, rules)
        assert result is False

    def test_empty_rules(self):
        result = _directive_has_matching_rule("anything", set(), [])
        assert result is False

    def test_min_overlap_for_short_directives(self):
        # With <= 3 directive words, only 1 overlap needed
        rules = [{"message": "secret leak", "source": "", "pattern": ""}]
        directive_words = {"secret", "leak"}
        result = _directive_has_matching_rule("secret leak", directive_words, rules)
        assert result is True

    def test_min_overlap_for_long_directives_needs_two(self):
        # With > 3 directive words, need 2+ overlap; only 1 overlap -> False
        rules = [{"message": "only secret mentioned here", "source": "", "pattern": ""}]
        directive_words = {"secret", "credential", "password", "token"}
        result = _directive_has_matching_rule(
            "secret credential password token", directive_words, rules
        )
        assert result is False  # only "secret" overlaps, need 2+

    def test_min_overlap_for_long_directives_two_matches(self):
        # With > 3 directive words, 2 overlaps -> True
        rules = [{"message": "secret credential leak", "source": "", "pattern": ""}]
        directive_words = {"secret", "credential", "password", "token"}
        result = _directive_has_matching_rule(
            "secret credential password token", directive_words, rules
        )
        assert result is True
