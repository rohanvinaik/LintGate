"""Comprehensive tests for lintgate/context_guidance.py covering all public and private symbols."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.context_guidance import (
    _clean_line,
    _dedupe_text,
    _extract_path_hints,
    _flatten,
    _infer_rules_from_directives,
    _parse_context_file,
    _parse_rule_line,
    _path_hint_matches,
    _resolve_files,
    _safe_relpath,
    build_context_guidance,
    collect_context_rules,
    discover_context_files,
    relevant_guidance_for_file,
    rule_applies_to_path,
    summarize_context_guidance,
)

# ── discover_context_files ───────────────────────────────────────────────


def test_discover_finds_root_agents_md(tmp_path: Path) -> None:
    """AGENTS.md at project root is discovered."""
    (tmp_path / "AGENTS.md").write_text("# Agents")
    result = discover_context_files(str(tmp_path))
    assert len(result) == 1
    assert result[0].endswith("AGENTS.md")


def test_discover_finds_root_claude_md(tmp_path: Path) -> None:
    """CLAUDE.md at project root is discovered."""
    (tmp_path / "CLAUDE.md").write_text("# Claude")
    result = discover_context_files(str(tmp_path))
    assert len(result) == 1
    assert result[0].endswith("CLAUDE.md")


def test_discover_finds_dotclaude_files(tmp_path: Path) -> None:
    """Files inside .claude/ directory are discovered."""
    dot_claude = tmp_path / ".claude"
    dot_claude.mkdir()
    (dot_claude / "CLAUDE.md").write_text("# Claude inner")
    result = discover_context_files(str(tmp_path))
    assert len(result) == 1
    assert ".claude" in result[0]


def test_discover_all_four_locations(tmp_path: Path) -> None:
    """All four possible context files are discovered."""
    (tmp_path / "AGENTS.md").write_text("a")
    (tmp_path / "CLAUDE.md").write_text("b")
    dot_claude = tmp_path / ".claude"
    dot_claude.mkdir()
    (dot_claude / "AGENTS.md").write_text("c")
    (dot_claude / "CLAUDE.md").write_text("d")
    result = discover_context_files(str(tmp_path))
    assert len(result) == 4


def test_discover_empty_project(tmp_path: Path) -> None:
    """Empty project returns no context files."""
    result = discover_context_files(str(tmp_path))
    assert result == []


def test_discover_ignores_directories_named_like_context_files(tmp_path: Path) -> None:
    """Directories named CLAUDE.md are not returned."""
    (tmp_path / "CLAUDE.md").mkdir()
    result = discover_context_files(str(tmp_path))
    assert result == []


# ── _clean_line ──────────────────────────────────────────────────────────


def test_clean_line_strips_heading_markers() -> None:
    assert _clean_line("## Guardrails") == "Guardrails"


def test_clean_line_strips_bullet_prefix() -> None:
    assert _clean_line("- DO NOT use globals") == "DO NOT use globals"


def test_clean_line_strips_star_bullet() -> None:
    assert _clean_line("* Keep it clean") == "Keep it clean"


def test_clean_line_strips_bold_emphasis() -> None:
    assert _clean_line("**Important**") == "Important"


def test_clean_line_combined_heading_and_bullet() -> None:
    assert _clean_line("### - *Bold heading*") == "Bold heading"


def test_clean_line_plain_text_unchanged() -> None:
    assert _clean_line("plain text") == "plain text"


# ── _extract_path_hints ─────────────────────────────────────────────────


def test_extract_path_hints_backtick_path() -> None:
    hints = _extract_path_hints("Edit `src/utils.py` carefully")
    assert "src/utils.py" in hints


def test_extract_path_hints_slash_token() -> None:
    hints = _extract_path_hints("Check lintgate/channels/ for info")
    assert "lintgate/channels/" in hints


def test_extract_path_hints_dotpy_extension() -> None:
    hints = _extract_path_hints("See main.py for details")
    assert "main.py" in hints


def test_extract_path_hints_yaml_extension() -> None:
    hints = _extract_path_hints("Config is in `config.yaml`")
    assert "config.yaml" in hints


def test_extract_path_hints_strips_leading_dotslash() -> None:
    hints = _extract_path_hints("`./src/foo.py`")
    assert "src/foo.py" in hints
    assert "./src/foo.py" not in hints


def test_extract_path_hints_no_paths() -> None:
    hints = _extract_path_hints("No paths here at all")
    assert hints == []


def test_extract_path_hints_empty_token_after_strip() -> None:
    """Tokens that become empty after stripping punctuation are skipped (line 212)."""
    # A backtick containing only punctuation chars produces empty tokens after strip
    hints = _extract_path_hints("` `")
    assert hints == []


def test_extract_path_hints_toml_extension() -> None:
    hints = _extract_path_hints("See pyproject.toml")
    assert "pyproject.toml" in hints


def test_extract_path_hints_md_extension() -> None:
    hints = _extract_path_hints("Read README.md")
    assert "README.md" in hints


# ── _parse_rule_line ────────────────────────────────────────────────────


def test_parse_rule_forbid_prefix() -> None:
    rule = _parse_rule_line("LINTGATE_FORBID_REGEX: import os", "test.md", 1)
    assert rule is not None
    assert rule["kind"] == "forbid_regex"
    assert rule["pattern"] == "import os"
    assert rule["severity"] == "blocking"


def test_parse_rule_require_prefix() -> None:
    rule = _parse_rule_line("LINTGATE_REQUIRE_REGEX: from __future__", "test.md", 5)
    assert rule is not None
    assert rule["kind"] == "require_regex"
    assert rule["pattern"] == "from __future__"
    assert rule["severity"] == "warning"


def test_parse_rule_forbid_empty_pattern_returns_none() -> None:
    rule = _parse_rule_line("LINTGATE_FORBID_REGEX:   ", "test.md", 1)
    assert rule is None


def test_parse_rule_require_empty_pattern_returns_none() -> None:
    rule = _parse_rule_line("LINTGATE_REQUIRE_REGEX:   ", "test.md", 1)
    assert rule is None


def test_parse_rule_lintgate_rule_with_forbid_regex() -> None:
    line = "LINTGATE_RULE: forbid_regex=print\\(; severity=blocking; message=No prints"
    rule = _parse_rule_line(line, "agents.md", 10)
    assert rule is not None
    assert rule["kind"] == "forbid_regex"
    assert rule["pattern"] == "print\\("
    assert rule["severity"] == "blocking"
    assert rule["message"] == "No prints"


def test_parse_rule_lintgate_rule_with_require_regex() -> None:
    line = "LINTGATE_RULE: require_regex=# type: ignore; path=*.py"
    rule = _parse_rule_line(line, "agents.md", 3)
    assert rule is not None
    assert rule["kind"] == "require_regex"
    assert rule["path_glob"] == "*.py"


def test_parse_rule_lintgate_rule_no_pattern_returns_none() -> None:
    line = "LINTGATE_RULE: severity=blocking; message=No pattern"
    rule = _parse_rule_line(line, "agents.md", 1)
    assert rule is None


def test_parse_rule_line_plain_text_returns_none() -> None:
    rule = _parse_rule_line("This is just a comment", "test.md", 1)
    assert rule is None


def test_parse_rule_source_formatting() -> None:
    rule = _parse_rule_line("LINTGATE_FORBID_REGEX: bad_thing", "/foo/bar.md", 42)
    assert rule is not None
    assert rule["source"] == "/foo/bar.md:42"


def test_parse_rule_default_messages() -> None:
    """LINTGATE_RULE with forbid_regex uses the right default message."""
    line = "LINTGATE_RULE: forbid_regex=evil_function"
    rule = _parse_rule_line(line, "test.md", 1)
    assert rule is not None
    assert rule["message"] == "Context rule violation"

    line2 = "LINTGATE_RULE: require_regex=good_function"
    rule2 = _parse_rule_line(line2, "test.md", 2)
    assert rule2 is not None
    assert rule2["message"] == "Missing required context pattern"


def test_parse_rule_lintgate_rule_part_without_equals_ignored() -> None:
    """Parts in LINTGATE_RULE without '=' are silently ignored."""
    line = "LINTGATE_RULE: forbid_regex=bad; orphan_value; severity=warning"
    rule = _parse_rule_line(line, "test.md", 1)
    assert rule is not None
    assert rule["severity"] == "warning"


# ── _parse_context_file ─────────────────────────────────────────────────


def test_parse_context_file_basic_directives(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text(
        "CRITICAL: never ignore errors\n"
        "DO NOT use global state\n"
        "DO use type hints\n"
        "MUST run tests before commit\n"
    )
    result = _parse_context_file(str(md))
    assert "never ignore errors" in result["directives"]["critical"][0]
    assert len(result["directives"]["do_not"]) == 1
    assert len(result["directives"]["do"]) == 1
    assert len(result["directives"]["must"]) == 1


def test_parse_context_file_skips_table_rows(tmp_path: Path) -> None:
    md = tmp_path / "AGENTS.md"
    md.write_text("| DO NOT | Description |\n| --- | --- |\nDO NOT use eval\n")
    result = _parse_context_file(str(md))
    # Only the non-table line should produce a directive
    assert len(result["directives"]["do_not"]) == 1


def test_parse_context_file_extracts_rules(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("LINTGATE_FORBID_REGEX: exec\\(\n")
    result = _parse_context_file(str(md))
    assert len(result["rules"]) == 1
    assert result["rules"][0]["kind"] == "forbid_regex"


def test_parse_context_file_nonexistent_path() -> None:
    result = _parse_context_file("/nonexistent/CLAUDE.md")
    assert result["directives"]["critical"] == []
    assert result["directives"]["do_not"] == []
    assert result["modified_ts"] is None


def test_parse_context_file_extracts_path_hints(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("Edit `src/models.py` carefully\n")
    result = _parse_context_file(str(md))
    assert "src/models.py" in result["path_hints"]


def test_parse_context_file_empty_lines_skipped(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("\n\n\nCRITICAL: something\n\n\n")
    result = _parse_context_file(str(md))
    assert len(result["directives"]["critical"]) == 1


def test_parse_context_file_do_colon_prefix(tmp_path: Path) -> None:
    """Lines starting with DO: are classified as do directives."""
    md = tmp_path / "CLAUDE.md"
    md.write_text("DO: use composition over inheritance\n")
    result = _parse_context_file(str(md))
    assert len(result["directives"]["do"]) == 1


# ── _flatten ─────────────────────────────────────────────────────────────


def test_flatten_collects_from_multiple_items() -> None:
    items = [
        {"directives": {"critical": ["a", "b"]}},
        {"directives": {"critical": ["c"]}},
    ]
    assert _flatten(items, "critical") == ["a", "b", "c"]


def test_flatten_missing_key_returns_empty() -> None:
    items = [{"directives": {"do": ["x"]}}]
    assert _flatten(items, "missing") == []


def test_flatten_empty_items() -> None:
    assert _flatten([], "critical") == []


def test_flatten_missing_directives_key() -> None:
    items = [{"no_directives": {}}]
    assert _flatten(items, "critical") == []


# ── _dedupe_text ─────────────────────────────────────────────────────────


def test_dedupe_removes_duplicates() -> None:
    assert _dedupe_text(["a", "b", "a", "c"]) == ["a", "b", "c"]


def test_dedupe_strips_whitespace() -> None:
    assert _dedupe_text(["  a  ", "a"]) == ["a"]


def test_dedupe_removes_empty_strings() -> None:
    assert _dedupe_text(["", "  ", "x"]) == ["x"]


def test_dedupe_preserves_order() -> None:
    assert _dedupe_text(["c", "b", "a"]) == ["c", "b", "a"]


# ── _path_hint_matches ──────────────────────────────────────────────────


def test_path_hint_exact_match() -> None:
    assert _path_hint_matches("src/foo.py", "src/foo.py") is True


def test_path_hint_prefix_match() -> None:
    assert _path_hint_matches("src/foo.py", "src/") is True


def test_path_hint_no_match() -> None:
    assert _path_hint_matches("tests/bar.py", "src/") is False


def test_path_hint_strips_leading_dotslash() -> None:
    assert _path_hint_matches("src/foo.py", "./src/foo.py") is True


def test_path_hint_child_path_of_non_dir_hint() -> None:
    """A non-directory hint matches if rel_path starts with hint/."""
    assert _path_hint_matches("src/models/user.py", "src/models") is True


def test_path_hint_partial_name_no_match() -> None:
    """src/foobar.py should NOT match hint 'src/foo'."""
    assert _path_hint_matches("src/foobar.py", "src/foo") is False


# ── _safe_relpath ────────────────────────────────────────────────────────


def test_safe_relpath_normal() -> None:
    result = _safe_relpath("/a/b/c.py", "/a/b")
    assert result == "c.py"


def test_safe_relpath_same_path() -> None:
    result = _safe_relpath("/a/b", "/a/b")
    assert result == "."


def test_safe_relpath_value_error_fallback() -> None:
    """When os.path.relpath raises ValueError, the original path is returned (lines 324-325)."""
    from unittest.mock import patch

    with patch(
        "lintgate.context_guidance.os.path.relpath", side_effect=ValueError("different drives")
    ):
        result = _safe_relpath("D:\\foo\\bar.py", "C:\\other")
    assert result == "D:\\foo\\bar.py"


# ── _resolve_files ───────────────────────────────────────────────────────


def test_resolve_files_absolute_unchanged() -> None:
    result = _resolve_files(["/abs/path.py"], "/root")
    assert result == ["/abs/path.py"]


def test_resolve_files_relative_joined() -> None:
    result = _resolve_files(["src/foo.py"], "/project")
    assert result == [os.path.normpath("/project/src/foo.py")]


def test_resolve_files_empty_list() -> None:
    assert _resolve_files([], "/root") == []


# ── _infer_rules_from_directives ────────────────────────────────────────


def test_infer_rules_with_solve_task_mention() -> None:
    parsed = [{"directives": {"do_not": ["DO NOT use solve_task_ prefix functions"]}}]
    rules = _infer_rules_from_directives(parsed)
    assert len(rules) == 1
    assert rules[0]["kind"] == "forbid_regex"
    assert "solve_task_" in rules[0]["pattern"]
    assert rules[0]["source"] == "inferred:do_not_solve_task_prefix"


def test_infer_rules_without_solve_task() -> None:
    parsed = [{"directives": {"do_not": ["DO NOT use globals"]}}]
    rules = _infer_rules_from_directives(parsed)
    assert rules == []


def test_infer_rules_empty_parsed() -> None:
    assert _infer_rules_from_directives([]) == []


# ── rule_applies_to_path ────────────────────────────────────────────────


def test_rule_applies_no_path_glob() -> None:
    rule = {"kind": "forbid_regex", "pattern": "x"}
    assert rule_applies_to_path(rule, "any/file.py") is True


def test_rule_applies_matching_glob() -> None:
    rule = {"kind": "forbid_regex", "pattern": "x", "path_glob": "*.py"}
    assert rule_applies_to_path(rule, "foo.py") is True


def test_rule_applies_non_matching_glob() -> None:
    rule = {"kind": "forbid_regex", "pattern": "x", "path_glob": "*.py"}
    assert rule_applies_to_path(rule, "foo.js") is False


# ── relevant_guidance_for_file ───────────────────────────────────────────


def test_relevant_guidance_always_includes_critical_must_do_not() -> None:
    directives = {
        "critical": ["Critical item"],
        "must": ["Must item"],
        "do": ["DO use type hints for `src/models.py`"],
        "do_not": ["Do not item"],
    }
    result = relevant_guidance_for_file("/project/unrelated.py", "/project", directives, [])
    assert "Critical item" in result
    assert "Must item" in result
    assert "Do not item" in result


def test_relevant_guidance_includes_do_when_path_matches() -> None:
    directives = {
        "critical": [],
        "must": [],
        "do": ["DO use type hints in `src/models.py`"],
        "do_not": [],
    }
    result = relevant_guidance_for_file("/project/src/models.py", "/project", directives, [])
    assert "DO use type hints in `src/models.py`" in result


def test_relevant_guidance_excludes_do_when_path_mismatches() -> None:
    directives = {
        "critical": [],
        "must": [],
        "do": ["DO use type hints in `src/models.py`"],
        "do_not": [],
    }
    result = relevant_guidance_for_file("/project/tests/foo.py", "/project", directives, [])
    assert result == []


def test_relevant_guidance_skips_do_without_path_hints() -> None:
    """DO directives with no extractable path hints are skipped (line 120 continue)."""
    directives = {
        "critical": [],
        "must": [],
        "do": ["DO use composition over inheritance"],
        "do_not": [],
    }
    result = relevant_guidance_for_file("/project/src/foo.py", "/project", directives, [])
    # The do directive has no path hints, so it should not appear
    assert "DO use composition over inheritance" not in result


def test_relevant_guidance_includes_hint_referenced_directives() -> None:
    directives = {
        "critical": ["CRITICAL: always lint src/utils.py"],
        "must": [],
        "do": [],
        "do_not": [],
    }
    result = relevant_guidance_for_file(
        "/project/src/utils.py", "/project", directives, ["src/utils.py"]
    )
    # The critical directive is included both as critical and because hint matches
    assert "CRITICAL: always lint src/utils.py" in result


def test_relevant_guidance_deduplicates() -> None:
    directives = {
        "critical": ["same item"],
        "must": ["same item"],
        "do": [],
        "do_not": [],
    }
    result = relevant_guidance_for_file("/project/f.py", "/project", directives, [])
    assert result.count("same item") == 1


# ── collect_context_rules ────────────────────────────────────────────────


def test_collect_context_rules_combines_explicit_and_inferred(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("LINTGATE_FORBID_REGEX: eval\\(\nDO NOT use solve_task_ prefix functions\n")
    rules = collect_context_rules(str(tmp_path))
    kinds = [r["kind"] for r in rules]
    assert "forbid_regex" in kinds
    assert len(rules) >= 2  # At least one explicit + one inferred


def test_collect_context_rules_no_files(tmp_path: Path) -> None:
    rules = collect_context_rules(str(tmp_path))
    assert rules == []


# ── build_context_guidance ───────────────────────────────────────────────


def test_build_context_guidance_full(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text(
        "CRITICAL: never skip tests\n"
        "MUST run linter\n"
        "DO NOT commit secrets\n"
        "DO use `src/models.py` for data classes\n"
        "LINTGATE_FORBID_REGEX: eval\\(\n"
    )
    result = build_context_guidance(str(tmp_path))
    assert result["project"] == str(tmp_path)
    assert len(result["context_files"]) == 1
    assert result["context_files"][0]["name"] == "CLAUDE.md"
    assert len(result["directives"]["critical"]) >= 1
    assert len(result["rules"]) >= 1
    assert result["relevant_for_files"] == {}


def test_build_context_guidance_with_files(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("CRITICAL: check all files\n")
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("pass")
    result = build_context_guidance(str(tmp_path), files=["src/app.py"])
    assert "relevant_for_files" in result
    # The relative file should have been resolved
    abs_path = os.path.normpath(str(tmp_path / "src" / "app.py"))
    assert abs_path in result["relevant_for_files"]


def test_build_context_guidance_empty_project(tmp_path: Path) -> None:
    result = build_context_guidance(str(tmp_path))
    assert result["context_files"] == []
    assert result["directives"]["critical"] == []
    assert result["rules"] == []
    assert result["path_hints"] == []


def test_build_context_guidance_path_hints_sorted(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("Edit `z_module.py` and `a_module.py`\n")
    result = build_context_guidance(str(tmp_path))
    assert result["path_hints"] == sorted(result["path_hints"])


# ── summarize_context_guidance ───────────────────────────────────────────


def test_summarize_context_guidance_counts() -> None:
    guidance = {
        "context_files": [{"name": "CLAUDE.md"}, {"name": "AGENTS.md"}],
        "directives": {
            "critical": ["a"],
            "must": ["b", "c"],
            "do": [],
            "do_not": ["d"],
        },
        "rules": [{"kind": "forbid_regex"}],
    }
    summary = summarize_context_guidance(guidance)
    assert summary["context_file_count"] == 2
    assert summary["context_files"] == ["CLAUDE.md", "AGENTS.md"]
    assert summary["directive_counts"]["critical"] == 1
    assert summary["directive_counts"]["must"] == 2
    assert summary["directive_counts"]["do"] == 0
    assert summary["directive_counts"]["do_not"] == 1
    assert summary["rule_count"] == 1


def test_summarize_empty_guidance() -> None:
    summary = summarize_context_guidance({})
    assert summary["context_file_count"] == 0
    assert summary["context_files"] == []
    assert summary["rule_count"] == 0
    assert summary["directive_counts"]["critical"] == 0


# ── Integration: end-to-end with multiple context files ─────────────────


def test_integration_multiple_context_files(tmp_path: Path) -> None:
    """Multiple context files across root and .claude/ are merged correctly."""
    (tmp_path / "AGENTS.md").write_text("CRITICAL: from agents root\n")
    dot_claude = tmp_path / ".claude"
    dot_claude.mkdir()
    (dot_claude / "CLAUDE.md").write_text("MUST: from dotclaude\n")

    result = build_context_guidance(str(tmp_path))
    assert len(result["context_files"]) == 2
    crits = result["directives"]["critical"]
    musts = result["directives"]["must"]
    assert any("agents root" in c for c in crits)
    assert any("dotclaude" in m for m in musts)


def test_integration_rules_and_directives_together(tmp_path: Path) -> None:
    """Explicit rules and inferred rules from directives appear together."""
    (tmp_path / "CLAUDE.md").write_text(
        "LINTGATE_REQUIRE_REGEX: from __future__\nDO NOT use solve_task_ prefix functions\n"
    )
    rules = collect_context_rules(str(tmp_path))
    assert any(r["kind"] == "require_regex" for r in rules)
    assert any(r["kind"] == "forbid_regex" and "solve_task_" in r["pattern"] for r in rules)
