"""Tests for lintgate.context.auditor — context health auditing and session readiness."""

from __future__ import annotations

import os
import time
from unittest.mock import patch

from lintgate.context.auditor import (
    _REQUIRED_FACETS,
    DEFAULT_THRESHOLDS,
    SessionReadiness,
    _build_recommendation,
    _check_enforceable_rules,
    _check_theory_facets,
    _check_theory_staleness,
    audit_context_health,
    check_session_readiness,
)
from lintgate.context.auditor_checks import (
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

# ── Helpers ──────────────────────────────────────────────────────────


def _make_checks_and_suggestions():
    """Return fresh mutable lists for checks and suggestions."""
    return [], []


# ── check_length ─────────────────────────────────────────────────────


class TestCheckLength:
    def test_within_guideline(self):
        checks, suggestions = _make_checks_and_suggestions()
        check_length(checks, suggestions, 100, DEFAULT_THRESHOLDS)
        assert len(checks) == 1
        assert checks[0]["status"] == "pass"
        assert checks[0]["check"] == "length"
        assert "100 lines" in checks[0]["detail"]
        assert len(suggestions) == 0

    def test_at_warn_boundary(self):
        checks, suggestions = _make_checks_and_suggestions()
        check_length(checks, suggestions, 300, DEFAULT_THRESHOLDS)
        assert checks[0]["status"] == "pass"

    def test_above_warn_below_error(self):
        checks, suggestions = _make_checks_and_suggestions()
        check_length(checks, suggestions, 350, DEFAULT_THRESHOLDS)
        assert checks[0]["status"] == "warn"
        assert "350 lines" in checks[0]["detail"]
        assert len(suggestions) == 1
        assert "splitting" in suggestions[0].lower() or "rules" in suggestions[0].lower()

    def test_above_error(self):
        checks, suggestions = _make_checks_and_suggestions()
        check_length(checks, suggestions, 600, DEFAULT_THRESHOLDS)
        assert checks[0]["status"] == "error"
        assert "600 lines" in checks[0]["detail"]
        assert len(suggestions) == 1

    def test_zero_lines(self):
        checks, suggestions = _make_checks_and_suggestions()
        check_length(checks, suggestions, 0, DEFAULT_THRESHOLDS)
        assert checks[0]["status"] == "pass"

    def test_custom_thresholds(self):
        checks, suggestions = _make_checks_and_suggestions()
        custom = {"max_lines_warn": 10, "max_lines_error": 20}
        check_length(checks, suggestions, 15, custom)
        assert checks[0]["status"] == "warn"

    def test_at_error_boundary(self):
        checks, suggestions = _make_checks_and_suggestions()
        check_length(checks, suggestions, 500, DEFAULT_THRESHOLDS)
        # Exactly at error boundary: not > 500, so should be warn (> 300)
        assert checks[0]["status"] == "warn"


# ── check_structure ──────────────────────────────────────────────────


class TestCheckStructure:
    def test_well_structured(self):
        text = "## Section One\nContent here.\n## Section Two\nMore content."
        lines = text.splitlines()
        checks, suggestions = _make_checks_and_suggestions()
        check_structure(checks, suggestions, text, lines)
        assert checks[0]["status"] == "pass"
        assert "2 sections" in checks[0]["detail"]

    def test_with_code_blocks(self):
        text = "## Section One\n```python\ncode\n```\n## Section Two\nMore."
        lines = text.splitlines()
        checks, suggestions = _make_checks_and_suggestions()
        check_structure(checks, suggestions, text, lines)
        assert checks[0]["status"] == "pass"
        assert "code examples" in checks[0]["detail"]

    def test_with_tables(self):
        text = "## Section One\n| col1 | col2 |\n| --- | --- |\n## Section Two\nMore."
        lines = text.splitlines()
        checks, suggestions = _make_checks_and_suggestions()
        check_structure(checks, suggestions, text, lines)
        assert checks[0]["status"] == "pass"
        assert "tables" in checks[0]["detail"]

    def test_no_headers_warns(self):
        text = "Just some text without any headers."
        lines = text.splitlines()
        checks, suggestions = _make_checks_and_suggestions()
        check_structure(checks, suggestions, text, lines)
        assert checks[0]["status"] == "warn"
        assert len(suggestions) == 1
        assert "headers" in suggestions[0].lower()

    def test_single_header_warns(self):
        text = "# Title\nSome content but only one header."
        lines = text.splitlines()
        checks, suggestions = _make_checks_and_suggestions()
        check_structure(checks, suggestions, text, lines)
        assert checks[0]["status"] == "warn"
        assert "1 section" in checks[0]["detail"]

    def test_empty_text(self):
        text = ""
        lines = text.splitlines()
        checks, suggestions = _make_checks_and_suggestions()
        check_structure(checks, suggestions, text, lines)
        assert checks[0]["status"] == "warn"


# ── check_staleness ──────────────────────────────────────────────────


class TestCheckStaleness:
    def test_fresh_file(self, tmp_path):
        f = tmp_path / "CLAUDE.md"
        f.write_text("# Context")
        checks, suggestions = _make_checks_and_suggestions()
        check_staleness(checks, suggestions, str(f), DEFAULT_THRESHOLDS)
        assert checks[0]["status"] == "pass"
        assert "0 days ago" in checks[0]["detail"]

    def test_stale_file(self, tmp_path):
        f = tmp_path / "CLAUDE.md"
        f.write_text("# Context")
        # Set mtime to 60 days ago
        old_time = time.time() - 60 * 86400
        os.utime(str(f), (old_time, old_time))
        checks, suggestions = _make_checks_and_suggestions()
        check_staleness(checks, suggestions, str(f), DEFAULT_THRESHOLDS)
        assert checks[0]["status"] == "warn"
        assert "60 days ago" in checks[0]["detail"]
        assert len(suggestions) == 1

    def test_nonexistent_file(self, tmp_path):
        checks, suggestions = _make_checks_and_suggestions()
        check_staleness(checks, suggestions, str(tmp_path / "missing.md"), DEFAULT_THRESHOLDS)
        assert checks[0]["status"] == "warn"
        assert "could not determine" in checks[0]["detail"].lower()

    def test_custom_staleness_days(self, tmp_path):
        f = tmp_path / "CLAUDE.md"
        f.write_text("# Context")
        old_time = time.time() - 5 * 86400
        os.utime(str(f), (old_time, old_time))
        checks, suggestions = _make_checks_and_suggestions()
        check_staleness(checks, suggestions, str(f), {"staleness_days": 3})
        assert checks[0]["status"] == "warn"


# ── check_contradictions ─────────────────────────────────────────────


class TestCheckContradictions:
    def test_no_contradictions(self):
        guidance = {
            "directives": {
                "do": ["Use pytest for testing"],
                "do_not": ["Never commit secrets"],
            }
        }
        checks, suggestions = _make_checks_and_suggestions()
        check_contradictions(checks, suggestions, guidance)
        assert checks[0]["status"] == "pass"

    def test_contradicting_keywords(self):
        guidance = {
            "directives": {
                "do": [
                    "Always use caching for performance optimization",
                    "Always enable logging everywhere",
                ],
                "do_not": [
                    "Never use caching in production code",
                    "Never enable logging in hot paths",
                ],
            }
        }
        checks, suggestions = _make_checks_and_suggestions()
        check_contradictions(checks, suggestions, guidance)
        assert checks[0]["status"] == "warn"
        assert len(suggestions) >= 1

    def test_negation_pairs(self):
        guidance = {
            "directives": {
                "do": ["always use caching"],
                "do_not": ["never use caching"],
            }
        }
        checks, suggestions = _make_checks_and_suggestions()
        check_contradictions(checks, suggestions, guidance)
        assert checks[0]["status"] == "warn"
        assert "caching" in checks[0]["detail"]

    def test_empty_directives(self):
        guidance: dict[str, dict[str, list[str]]] = {"directives": {"do": [], "do_not": []}}
        checks, suggestions = _make_checks_and_suggestions()
        check_contradictions(checks, suggestions, guidance)
        assert checks[0]["status"] == "pass"

    def test_missing_directives_key(self):
        guidance: dict[str, object] = {}
        checks, suggestions = _make_checks_and_suggestions()
        check_contradictions(checks, suggestions, guidance)
        assert checks[0]["status"] == "pass"


# ── check_rule_coverage ──────────────────────────────────────────────


class TestCheckRuleCoverage:
    def test_no_do_not_directives(self):
        guidance: dict[str, dict[str, list[str]]] = {"directives": {"do_not": []}}
        checks, suggestions = _make_checks_and_suggestions()
        check_rule_coverage(checks, suggestions, guidance, [], DEFAULT_THRESHOLDS)
        assert checks[0]["status"] == "pass"
        assert "No DO NOT" in checks[0]["detail"]

    def test_enforceable_with_matching_rule(self):
        # A directive with syntactic identifiers is enforceable
        guidance = {
            "directives": {
                "do_not": ["DO NOT use solve_task_foo() directly"],
            }
        }
        rules = [
            {
                "kind": "forbid_regex",
                "pattern": r"solve_task_\w+",
                "message": "No solve_task_ functions",
                "source": "CLAUDE.md:10",
            }
        ]
        checks, suggestions = _make_checks_and_suggestions()
        check_rule_coverage(checks, suggestions, guidance, rules, DEFAULT_THRESHOLDS)
        assert checks[0]["status"] == "pass"

    def test_enforceable_without_matching_rule(self):
        # An enforceable directive with no matching rule should warn if below threshold
        guidance = {
            "directives": {
                "do_not": ["DO NOT use global_state_dict directly"],
            }
        }
        checks, suggestions = _make_checks_and_suggestions()
        check_rule_coverage(checks, suggestions, guidance, [], DEFAULT_THRESHOLDS)
        assert checks[0]["status"] == "warn"

    def test_only_architectural_directives(self):
        guidance = {
            "directives": {
                "do_not": [
                    "DO NOT iterate without understanding the constraints",
                ],
            }
        }
        checks, suggestions = _make_checks_and_suggestions()
        check_rule_coverage(checks, suggestions, guidance, [], DEFAULT_THRESHOLDS)
        # Architectural-only: should pass (no enforceable directives found)
        assert checks[0]["status"] == "pass"
        assert "architectural" in checks[0]["detail"].lower() or "No regex" in checks[0]["detail"]


# ── extract_path_refs ────────────────────────────────────────────────


class TestExtractPathRefs:
    def test_backtick_paths(self):
        text = "See `src/main.py` and `docs/guide.md` for details."
        refs = extract_path_refs(text)
        assert "src/main.py" in refs
        assert "docs/guide.md" in refs

    def test_ignores_urls(self):
        text = "Visit `https://example.com/path` for info."
        refs = extract_path_refs(text)
        assert len(refs) == 0

    def test_ignores_code_blocks(self):
        text = "```\nsome/path.py\n```\nSee `real/path.py`."
        refs = extract_path_refs(text)
        assert "real/path.py" in refs
        # The path inside the code block should be stripped
        assert "some/path.py" not in refs

    def test_ignores_shell_commands(self):
        text = "Run `pip install package` and `git status`."
        refs = extract_path_refs(text)
        assert len(refs) == 0

    def test_file_extension_without_slash(self):
        text = "Edit `config.yaml` for settings."
        refs = extract_path_refs(text)
        assert "config.yaml" in refs

    def test_empty_text(self):
        refs = extract_path_refs("")
        assert refs == []

    def test_ignores_hf_model_ids(self):
        text = "Use `google/gemma-7b` model."
        refs = extract_path_refs(text)
        assert "google/gemma-7b" not in refs

    def test_ignores_tree_chars(self):
        text = "See `\u251c\u2500\u2500 src/main.py`."
        refs = extract_path_refs(text)
        assert len(refs) == 0

    def test_excludes_non_https_urls(self):
        text = "Legacy links: `http://example.com/path` and `ftp://example.com/pub/file`."
        refs = extract_path_refs(text)
        assert len(refs) == 0

    def test_excludes_space_without_sep(self):
        text = "Use `some command here` to run."
        refs = extract_path_refs(text)
        assert len(refs) == 0


# ── find_dead_paths ──────────────────────────────────────────────────


class TestFindDeadPaths:
    def test_all_exist(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("")
        dead = find_dead_paths(["src/main.py"], str(tmp_path))
        assert dead == []

    def test_missing_path(self, tmp_path):
        dead = find_dead_paths(["nonexistent/file.py"], str(tmp_path))
        assert "nonexistent/file.py" in dead

    # test_glob_patterns_skipped removed (byte-identical to test_auditor_path_refs.py)

    def test_home_dir_path(self, tmp_path):
        # Tilde paths are expanded and checked
        dead = find_dead_paths(["~/definitely_nonexistent_lintgate_test_path.xyz"], str(tmp_path))
        assert len(dead) == 1

    def test_generated_patterns_skipped(self, tmp_path):
        dead = find_dead_paths(
            ["dist/bundle.js"],
            str(tmp_path),
            generated_patterns=["dist", "dist/*"],
        )
        assert dead == []

    def test_empty_refs(self, tmp_path):
        dead = find_dead_paths([], str(tmp_path))
        assert dead == []

    def test_bare_name_found_in_subdir(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "utils.py").write_text("")
        dead = find_dead_paths(["utils.py"], str(tmp_path))
        # Bare name should be found via _find_bare_name_in_project
        assert dead == []

    def test_dotslash_prefix_stripped(self, tmp_path):
        (tmp_path / "main.py").write_text("pass")
        dead = find_dead_paths(["./main.py"], str(tmp_path))
        assert dead == []


# ── check_path_references ────────────────────────────────────────────


class TestCheckPathReferences:
    def test_no_refs(self):
        checks, suggestions = _make_checks_and_suggestions()
        check_path_references(
            checks, suggestions, "No backtick paths here.", "/tmp", DEFAULT_THRESHOLDS
        )
        assert checks[0]["status"] == "pass"
        assert "No path references" in checks[0]["detail"]

    def test_all_refs_valid(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("")
        text = "See `src/main.py` for the entry point."
        checks, suggestions = _make_checks_and_suggestions()
        check_path_references(checks, suggestions, text, str(tmp_path), DEFAULT_THRESHOLDS)
        assert any(c["check"] == "path_references" and c["status"] == "pass" for c in checks)

    def test_dead_refs_warns(self, tmp_path):
        text = "See `nonexistent/missing.py` for details."
        checks, suggestions = _make_checks_and_suggestions()
        check_path_references(checks, suggestions, text, str(tmp_path), DEFAULT_THRESHOLDS)
        assert any(c["check"] == "path_references" and c["status"] == "warn" for c in checks)

    def test_excessive_refs_warns(self, tmp_path):
        # Generate many path references that exist
        (tmp_path / "src").mkdir()
        refs = []
        for i in range(55):
            f = tmp_path / "src" / f"file_{i}.py"
            f.write_text("")
            refs.append(f"`src/file_{i}.py`")
        text = "\n".join(refs)
        checks, suggestions = _make_checks_and_suggestions()
        check_path_references(checks, suggestions, text, str(tmp_path), DEFAULT_THRESHOLDS)
        assert any(
            c.get("check") == "path_reference_volume" and c["status"] == "warn" for c in checks
        )


# ── classify_directive_enforceability ─────────────────────────────────


class TestClassifyDirectiveEnforceability:
    def test_enforceable_syntactic(self):
        result = classify_directive_enforceability("DO NOT use solve_task_foo() directly")
        assert result.classification == "enforceable"
        assert result.confidence >= 0.7

    def test_architectural_process(self):
        result = classify_directive_enforceability(
            "DO NOT iterate without understanding the problem first"
        )
        assert result.classification == "architectural"
        assert result.confidence >= 0.7

    def test_uncertain_no_signals(self):
        result = classify_directive_enforceability("be kind")
        assert result.classification == "uncertain"
        assert result.confidence <= 0.5

    def test_mixed_signals_syntactic_dominant(self):
        result = classify_directive_enforceability(
            "DO NOT bypass config.validate() or auth_module.check_token() constraints"
        )
        assert result.classification in ("enforceable", "uncertain")

    def test_dataclass_fields(self):
        result = classify_directive_enforceability("something")
        assert isinstance(result, DirectiveClassification)
        assert isinstance(result.classification, str)
        assert isinstance(result.confidence, float)
        assert isinstance(result.reason, str)


# ── _is_regex_enforceable ────────────────────────────────────────────


class TestIsRegexEnforceable:
    def test_enforceable(self):
        assert _is_regex_enforceable("DO NOT call solve_task_foo() directly") is True

    def test_not_enforceable_architectural(self):
        assert _is_regex_enforceable("DO NOT iterate without understanding") is False

    def test_not_enforceable_vague(self):
        assert _is_regex_enforceable("be nice") is False


# ── SessionReadiness dataclass ───────────────────────────────────────


class TestSessionReadiness:
    def test_defaults(self):
        sr = SessionReadiness()
        assert sr.ready is False
        assert sr.missing == []
        assert sr.recommendation == ""

    def test_custom_values(self):
        sr = SessionReadiness(ready=True, missing=["x"], recommendation="do y")
        assert sr.ready is True
        assert sr.missing == ["x"]
        assert sr.recommendation == "do y"


# ── _check_theory_facets ─────────────────────────────────────────────


class TestCheckTheoryFacets:
    def test_none_profile(self):
        missing: list[str] = []
        _check_theory_facets(None, missing)
        assert missing == ["no_theory_profile"]

    def test_complete_profile(self):
        profile = {
            "core_theory": [{"claims": ["claim1"]}],
            "problem_solving": [{"claims": ["claim2"]}],
            "alignment": [{"claims": ["claim3"]}],
        }
        missing: list[str] = []
        _check_theory_facets(profile, missing)
        assert missing == []

    def test_missing_one_facet(self):
        profile = {
            "core_theory": [{"claims": ["claim1"]}],
            "problem_solving": [{"claims": ["claim2"]}],
            "alignment": [],  # no entries
        }
        missing: list[str] = []
        _check_theory_facets(profile, missing)
        assert "missing_facet:alignment" in missing

    def test_facet_without_claims(self):
        profile = {
            "core_theory": [{"something": "else"}],  # no "claims" key
            "problem_solving": [{"claims": ["claim2"]}],
            "alignment": [{"claims": ["claim3"]}],
        }
        missing: list[str] = []
        _check_theory_facets(profile, missing)
        assert "missing_facet:core_theory" in missing

    def test_empty_profile(self):
        missing: list[str] = []
        _check_theory_facets({}, missing)
        # All three required facets should be missing
        assert len(missing) == 3
        for facet in _REQUIRED_FACETS:
            assert f"missing_facet:{facet}" in missing

    def test_non_dict_entries_in_facet(self):
        profile = {
            "core_theory": ["not_a_dict"],  # string, not dict
            "problem_solving": [{"claims": ["claim2"]}],
            "alignment": [{"claims": ["claim3"]}],
        }
        missing: list[str] = []
        _check_theory_facets(profile, missing)
        assert "missing_facet:core_theory" in missing


# ── _check_enforceable_rules ─────────────────────────────────────────


class TestCheckEnforceableRules:
    def test_no_claude_md(self, tmp_path):
        missing: list[str] = []
        _check_enforceable_rules(str(tmp_path), missing)
        assert "no_enforceable_rules" in missing

    def test_claude_md_without_rules(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# Context\nSome instructions.")
        missing: list[str] = []
        _check_enforceable_rules(str(tmp_path), missing)
        assert "no_enforceable_rules" in missing

    def test_claude_md_with_forbid_regex(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("LINTGATE_FORBID_REGEX: def\\s+solve_task_")
        missing: list[str] = []
        _check_enforceable_rules(str(tmp_path), missing)
        assert missing == []

    def test_claude_md_with_require_regex(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("LINTGATE_REQUIRE_REGEX: ^from __future__")
        missing: list[str] = []
        _check_enforceable_rules(str(tmp_path), missing)
        assert missing == []


# ── _check_theory_staleness ──────────────────────────────────────────


class TestCheckTheoryStaleness:
    def test_stale_theory(self, tmp_path):
        missing: list[str] = []
        staleness_result = {"stale": True, "uncovered_files": ["a.py", "b.py"]}
        with (
            patch(
                "lintgate.context.auditor.check_theory_staleness",
                return_value=staleness_result,
                create=True,
            ),
            patch(
                "lintgate.theory_extractor.check_theory_staleness",
                return_value=staleness_result,
            ),
        ):
            _check_theory_staleness(str(tmp_path), {}, {}, missing)
        assert any("theory_stale" in m for m in missing)

    def test_not_stale(self, tmp_path):
        missing: list[str] = []
        staleness_result = {"stale": False}
        with patch(
            "lintgate.theory_extractor.check_theory_staleness",
            return_value=staleness_result,
        ):
            _check_theory_staleness(str(tmp_path), {}, {}, missing)
        assert not any("theory_stale" in m for m in missing)

    def test_import_error_graceful(self, tmp_path):
        missing: list[str] = []
        with patch(
            "lintgate.theory_extractor.check_theory_staleness",
            side_effect=ImportError("no module"),
        ):
            _check_theory_staleness(str(tmp_path), {}, {}, missing)
        # Should not raise, missing should remain empty
        assert not any("theory_stale" in m for m in missing)


# ── _build_recommendation ────────────────────────────────────────────


class TestBuildRecommendation:
    def test_empty_missing(self):
        assert _build_recommendation([]) == ""

    def test_no_theory_profile(self):
        result = _build_recommendation(["no_theory_profile"])
        assert "extract project theory" in result
        assert result.startswith("Run bootstrap_context_files")

    def test_missing_facets(self):
        result = _build_recommendation(["missing_facet:core_theory", "missing_facet:alignment"])
        assert "core_theory" in result
        assert "alignment" in result
        assert "add claims" in result

    def test_no_enforceable_rules(self):
        result = _build_recommendation(["no_enforceable_rules"])
        assert "enforceable rules" in result
        assert "CLAUDE.md" in result

    def test_theory_stale(self):
        result = _build_recommendation(["theory_stale:3_uncommitted_files"])
        assert "build_theory_pack" in result

    def test_combined_missing(self):
        result = _build_recommendation(
            [
                "no_theory_profile",
                "no_enforceable_rules",
                "theory_stale:2_uncommitted_files",
            ]
        )
        assert "extract project theory" in result
        assert "enforceable rules" in result
        assert "build_theory_pack" in result


# ── check_session_readiness ──────────────────────────────────────────


class TestCheckSessionReadiness:
    def test_ready_session(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# Context\nLINTGATE_FORBID_REGEX: bad_pattern\n")
        profile = {
            "core_theory": [{"claims": ["claim1"]}],
            "problem_solving": [{"claims": ["claim2"]}],
            "alignment": [{"claims": ["claim3"]}],
        }
        result = check_session_readiness(str(tmp_path), theory_profile=profile)
        assert result.ready is True
        assert result.missing == []
        assert result.recommendation == ""

    def test_not_ready_no_profile(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("LINTGATE_FORBID_REGEX: x")
        result = check_session_readiness(str(tmp_path), theory_profile=None)
        assert result.ready is False
        assert "no_theory_profile" in result.missing

    def test_not_ready_no_rules(self, tmp_path):
        profile = {
            "core_theory": [{"claims": ["c1"]}],
            "problem_solving": [{"claims": ["c2"]}],
            "alignment": [{"claims": ["c3"]}],
        }
        result = check_session_readiness(str(tmp_path), theory_profile=profile)
        assert result.ready is False
        assert "no_enforceable_rules" in result.missing

    def test_git_context_triggers_staleness_check(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("LINTGATE_FORBID_REGEX: x")
        profile = {
            "core_theory": [{"claims": ["c1"]}],
            "problem_solving": [{"claims": ["c2"]}],
            "alignment": [{"claims": ["c3"]}],
        }
        git_ctx = {"modified_files": ["a.py"]}
        staleness_result = {"stale": True, "uncovered_files": ["a.py"]}
        with patch(
            "lintgate.theory_extractor.check_theory_staleness",
            return_value=staleness_result,
        ):
            result = check_session_readiness(
                str(tmp_path),
                theory_profile=profile,
                git_context=git_ctx,
            )
        assert any("theory_stale" in m for m in result.missing)

    def test_no_git_context_skips_staleness(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("LINTGATE_FORBID_REGEX: x")
        profile = {
            "core_theory": [{"claims": ["c1"]}],
            "problem_solving": [{"claims": ["c2"]}],
            "alignment": [{"claims": ["c3"]}],
        }
        result = check_session_readiness(str(tmp_path), theory_profile=profile)
        assert result.ready is True
        assert not any("theory_stale" in m for m in result.missing)


# ── audit_context_health ─────────────────────────────────────────────


class TestAuditContextHealth:
    def test_no_context_files(self, tmp_path):
        result = audit_context_health(str(tmp_path))
        assert result["context_file_count"] == 0
        assert result["audit"] == []
        assert "thresholds_used" in result

    def test_single_healthy_file(self, tmp_path):
        claude = tmp_path / "CLAUDE.md"
        claude.write_text("## Overview\nProject instructions.\n## Rules\nDo the right thing.\n")
        result = audit_context_health(str(tmp_path))
        assert result["context_file_count"] >= 1
        audit_items = result["audit"]
        assert len(audit_items) >= 1
        item = audit_items[0]
        assert item["name"] == "CLAUDE.md"
        assert "line_count" in item
        assert item["status"] in ("pass", "warn", "error")

    def test_unreadable_file(self, tmp_path):
        with patch(
            "lintgate.context.auditor.discover_context_files",
            return_value=[str(tmp_path / "ghost.md")],
        ):
            result = audit_context_health(str(tmp_path))
        assert result["audit"][0]["status"] == "unreadable"

    def test_exempt_file(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        config_dir = tmp_path / ".claude"
        (config_dir / "lintgate.yaml").write_text(
            "linters:\n  context_auditor:\n    exempt_files:\n      - CLAUDE.md\n"
        )
        claude = tmp_path / "CLAUDE.md"
        claude.write_text("# Context\nStuff here.\n")
        result = audit_context_health(str(tmp_path))
        exempt_items = [a for a in result["audit"] if a.get("status") == "exempt"]
        assert len(exempt_items) == 1
        assert exempt_items[0]["name"] == "CLAUDE.md"

    def test_threshold_overrides(self, tmp_path):
        claude = tmp_path / "CLAUDE.md"
        claude.write_text("## A\n## B\n" + "line\n" * 50)
        result = audit_context_health(
            str(tmp_path),
            thresholds={"max_lines_warn": 10, "max_lines_error": 20},
        )
        thresholds = result["thresholds_used"]
        assert thresholds["max_lines_warn"] == 10
        assert thresholds["max_lines_error"] == 20
        # File has >20 lines, should be error
        item = result["audit"][0]
        length_check = next(c for c in item["health_checks"] if c["check"] == "length")
        assert length_check["status"] == "error"

    def test_default_thresholds_applied(self, tmp_path):
        result = audit_context_health(str(tmp_path))
        thresholds = result["thresholds_used"]
        for key, value in DEFAULT_THRESHOLDS.items():
            assert thresholds[key] == value

    def test_status_aggregation_error(self, tmp_path):
        # A very long file should produce error status
        claude = tmp_path / "CLAUDE.md"
        claude.write_text("## A\n## B\n" + "x\n" * 600)
        result = audit_context_health(str(tmp_path))
        item = result["audit"][0]
        assert item["status"] == "error"

    def test_status_aggregation_warn(self, tmp_path):
        # A moderately long file should produce warn status
        claude = tmp_path / "CLAUDE.md"
        claude.write_text("## A\n## B\n" + "x\n" * 350)
        result = audit_context_health(str(tmp_path))
        item = result["audit"][0]
        assert item["status"] in ("warn", "error")

    def test_multiple_context_files(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("## A\n## B\nContent.\n")
        (tmp_path / "AGENTS.md").write_text("## X\n## Y\nMore content.\n")
        result = audit_context_health(str(tmp_path))
        assert result["context_file_count"] >= 2
        names = {a["name"] for a in result["audit"]}
        assert "CLAUDE.md" in names
        assert "AGENTS.md" in names


# ── _REQUIRED_FACETS ─────────────────────────────────────────────────


class TestRequiredFacets:
    def test_required_facets_content(self):
        assert _REQUIRED_FACETS == ("core_theory", "problem_solving", "alignment")

    def test_required_facets_is_tuple(self):
        assert isinstance(_REQUIRED_FACETS, tuple)


# ── DEFAULT_THRESHOLDS ───────────────────────────────────────────────


class TestDefaultThresholds:
    def test_keys_present(self):
        expected_keys = {
            "max_lines_warn",
            "max_lines_error",
            "staleness_days",
            "min_rule_coverage_pct",
            "max_path_references",
        }
        assert set(DEFAULT_THRESHOLDS.keys()) == expected_keys

    def test_values(self):
        assert DEFAULT_THRESHOLDS["max_lines_warn"] == 300
        assert DEFAULT_THRESHOLDS["max_lines_error"] == 500
        assert DEFAULT_THRESHOLDS["staleness_days"] == 30
        assert DEFAULT_THRESHOLDS["min_rule_coverage_pct"] == 50
        assert DEFAULT_THRESHOLDS["max_path_references"] == 50


# ── _extract_keywords ────────────────────────────────────────────────


class TestExtractKeywords:
    def test_extracts_4plus_letter_words(self):
        result = _extract_keywords({"always validate credentials before deployment"})
        assert "always" in result
        assert "validate" in result
        assert "credentials" in result

    def test_excludes_short_words(self):
        result = _extract_keywords({"do not use it"})
        assert "not" not in result
        assert "use" not in result

    def test_empty_input(self):
        result = _extract_keywords(set())
        assert result == set()


# ── _coverage_tokens ─────────────────────────────────────────────────


class TestCoverageTokens:
    def test_basic_tokenization(self):
        tokens = _coverage_tokens("hello_world testing")
        assert "hello" in tokens
        assert "world" in tokens

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


# ── _has_syntactic_id and _count_syntactic_ids ───────────────────────


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
        assert count >= 3


# ── _detect_generated_patterns ───────────────────────────────────────


class TestDetectGeneratedPatterns:
    def test_pyproject_toml(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[build-system]")
        patterns = _detect_generated_patterns(str(tmp_path))
        assert "dist" in patterns
        assert "*.egg-info" in patterns

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
        assert patterns.count("build") == 1


# ── _matches_generated_pattern ───────────────────────────────────────


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


# ── _find_bare_name_in_project ───────────────────────────────────────


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
        deep = tmp_path / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        (deep / "deep.py").write_text("pass")
        assert _find_bare_name_in_project("deep.py", str(tmp_path)) is False


# ── _directive_has_matching_rule ─────────────────────────────────────


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

    def test_min_overlap_for_long_directives_needs_two(self):
        rules = [{"message": "only secret mentioned here", "source": "", "pattern": ""}]
        directive_words = {"secret", "credential", "password", "token"}
        result = _directive_has_matching_rule(
            "secret credential password token", directive_words, rules
        )
        assert result is False

    def test_min_overlap_for_long_directives_two_matches(self):
        rules = [{"message": "secret credential leak", "source": "", "pattern": ""}]
        directive_words = {"secret", "credential", "password", "token"}
        result = _directive_has_matching_rule(
            "secret credential password token", directive_words, rules
        )
        assert result is True
