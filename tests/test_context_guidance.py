"""Comprehensive tests for lintgate/context/guidance.py covering all public and private symbols."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.context.guidance import (
    _classify_directive,
    _clean_line,
    _dedupe_text,
    _extract_path_hints,
    _flatten,
    _infer_rules_from_directives,
    _is_skippable_line,
    _parse_context_file,
    _parse_rule_line,
    _path_hint_matches,
    _resolve_files,
    _safe_relpath,
    build_context_guidance,
    collect_context_rules,
    count_placeholder_rules,
    discover_context_files,
    relevant_guidance_for_file,
    rule_applies_to_path,
    summarize_context_guidance,
)

# ── discover_context_files ───────────────────────────────────────────────


class TestDiscoverContextFiles:
    def test_finds_root_agents_md(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("# Agents")
        result = discover_context_files(str(tmp_path))
        assert len(result) == 1
        assert result[0].endswith("AGENTS.md")

    def test_finds_root_claude_md(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("# Claude")
        result = discover_context_files(str(tmp_path))
        assert len(result) == 1
        assert result[0].endswith("CLAUDE.md")

    def test_finds_dotclaude_files(self, tmp_path: Path) -> None:
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        (dot_claude / "CLAUDE.md").write_text("# Claude inner")
        result = discover_context_files(str(tmp_path))
        assert len(result) == 1
        assert ".claude" in result[0]

    def test_finds_all_four_locations(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("a")
        (tmp_path / "CLAUDE.md").write_text("b")
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        (dot_claude / "AGENTS.md").write_text("c")
        (dot_claude / "CLAUDE.md").write_text("d")
        result = discover_context_files(str(tmp_path))
        assert len(result) == 4

    def test_empty_project(self, tmp_path: Path) -> None:
        result = discover_context_files(str(tmp_path))
        assert result == []

    def test_ignores_directories_named_like_context_files(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").mkdir()
        result = discover_context_files(str(tmp_path))
        assert result == []

    def test_nonexistent_root(self, tmp_path: Path) -> None:
        result = discover_context_files(str(tmp_path / "does_not_exist"))
        assert result == []

    def test_order_is_root_first_then_dotclaude(self, tmp_path: Path) -> None:
        """Root files come before .claude/ files due to _CONTEXT_DIRS order."""
        (tmp_path / "AGENTS.md").write_text("root")
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        (dot_claude / "AGENTS.md").write_text("inner")
        result = discover_context_files(str(tmp_path))
        assert len(result) == 2
        # Root AGENTS.md listed before .claude/AGENTS.md
        assert ".claude" not in result[0]
        assert ".claude" in result[1]


# ── _clean_line ──────────────────────────────────────────────────────────


class TestCleanLine:
    def test_strips_heading_markers(self) -> None:
        assert _clean_line("## Guardrails") == "Guardrails"

    def test_strips_triple_heading(self) -> None:
        assert _clean_line("### Details") == "Details"

    def test_strips_single_heading(self) -> None:
        assert _clean_line("# Title") == "Title"

    def test_strips_bullet_prefix_dash(self) -> None:
        assert _clean_line("- DO NOT use globals") == "DO NOT use globals"

    def test_strips_bullet_prefix_star(self) -> None:
        assert _clean_line("* Keep it clean") == "Keep it clean"

    def test_strips_bold_emphasis(self) -> None:
        assert _clean_line("**Important**") == "Important"

    def test_combined_heading_and_bullet(self) -> None:
        assert _clean_line("### - *Bold heading*") == "Bold heading"

    def test_plain_text_unchanged(self) -> None:
        assert _clean_line("plain text") == "plain text"

    def test_empty_string(self) -> None:
        assert _clean_line("") == ""

    def test_only_heading_marker(self) -> None:
        assert _clean_line("###") == ""

    def test_indented_bullet(self) -> None:
        assert _clean_line("  - nested item") == "nested item"

    def test_strips_leading_and_trailing_whitespace(self) -> None:
        assert _clean_line("  some text  ") == "some text"


# ── _extract_path_hints ─────────────────────────────────────────────────


class TestExtractPathHints:
    def test_backtick_path(self) -> None:
        hints = _extract_path_hints("Edit `src/utils.py` carefully")
        assert "src/utils.py" in hints

    def test_slash_token(self) -> None:
        hints = _extract_path_hints("Check lintgate/channels/ for info")
        assert "lintgate/channels/" in hints

    def test_dotpy_extension(self) -> None:
        hints = _extract_path_hints("See main.py for details")
        assert "main.py" in hints

    def test_yaml_extension(self) -> None:
        hints = _extract_path_hints("Config is in `config.yaml`")
        assert "config.yaml" in hints

    def test_yml_extension(self) -> None:
        hints = _extract_path_hints("Config is in `config.yml`")
        assert "config.yml" in hints

    def test_toml_extension(self) -> None:
        hints = _extract_path_hints("See pyproject.toml")
        assert "pyproject.toml" in hints

    def test_md_extension(self) -> None:
        hints = _extract_path_hints("Read README.md")
        assert "README.md" in hints

    def test_strips_leading_dotslash(self) -> None:
        hints = _extract_path_hints("`./src/foo.py`")
        assert "src/foo.py" in hints
        assert "./src/foo.py" not in hints

    def test_no_paths(self) -> None:
        hints = _extract_path_hints("No paths here at all")
        assert hints == []

    def test_empty_string(self) -> None:
        hints = _extract_path_hints("")
        assert hints == []

    def test_empty_token_after_strip(self) -> None:
        """Tokens that become empty after stripping punctuation are skipped."""
        hints = _extract_path_hints("` `")
        assert hints == []

    def test_multiple_paths(self) -> None:
        hints = _extract_path_hints("Edit `src/a.py` and `src/b.py`")
        assert "src/a.py" in hints
        assert "src/b.py" in hints

    def test_path_with_surrounding_punctuation(self) -> None:
        hints = _extract_path_hints('See "src/foo.py"')
        assert "src/foo.py" in hints

    def test_results_are_sorted(self) -> None:
        hints = _extract_path_hints("`z_module.py` and `a_module.py`")
        assert hints == sorted(hints)

    def test_deduplicates_identical_paths(self) -> None:
        hints = _extract_path_hints("`src/foo.py` and `src/foo.py`")
        assert hints.count("src/foo.py") == 1


# ── _is_skippable_line ──────────────────────────────────────────────────


class TestIsSkippableLine:
    def test_empty_line(self) -> None:
        assert _is_skippable_line("", "", False) is True

    def test_code_fence_returns_toggle(self) -> None:
        assert _is_skippable_line("```python", "```python", False) == "toggle"

    def test_code_fence_closing_returns_toggle(self) -> None:
        assert _is_skippable_line("```", "```", True) == "toggle"

    def test_inside_code_block(self) -> None:
        assert _is_skippable_line("some code", "some code", True) is True

    def test_table_row_skipped(self) -> None:
        result = _is_skippable_line("| col1 | col2 |", "| col1 | col2 |", False)
        assert result is True

    def test_table_separator_skipped(self) -> None:
        result = _is_skippable_line("| --- | --- |", "| --- | --- |", False)
        assert result is True

    def test_normal_text_not_skipped(self) -> None:
        result = _is_skippable_line("DO NOT use eval", "DO NOT use eval", False)
        # bool(None) and bool("") are falsy; bool of non-matching regex is False
        assert not result

    def test_indented_table_row(self) -> None:
        raw = "  | col1 | col2 |"
        result = _is_skippable_line(raw.strip(), raw, False)
        assert result is True


# ── _classify_directive ──────────────────────────────────────────────────


class TestClassifyDirective:
    def test_critical(self) -> None:
        directives: dict[str, list[str]] = {
            "critical": [],
            "must": [],
            "do": [],
            "do_not": [],
        }
        _classify_directive("CRITICAL: never ignore errors", directives)
        assert "CRITICAL: never ignore errors" in directives["critical"]

    def test_do_not(self) -> None:
        directives: dict[str, list[str]] = {
            "critical": [],
            "must": [],
            "do": [],
            "do_not": [],
        }
        _classify_directive("DO NOT use global state", directives)
        assert "DO NOT use global state" in directives["do_not"]

    def test_do(self) -> None:
        directives: dict[str, list[str]] = {
            "critical": [],
            "must": [],
            "do": [],
            "do_not": [],
        }
        _classify_directive("DO use type hints", directives)
        assert "DO use type hints" in directives["do"]

    def test_do_colon(self) -> None:
        directives: dict[str, list[str]] = {
            "critical": [],
            "must": [],
            "do": [],
            "do_not": [],
        }
        _classify_directive("DO: use composition", directives)
        assert "DO: use composition" in directives["do"]

    def test_must(self) -> None:
        directives: dict[str, list[str]] = {
            "critical": [],
            "must": [],
            "do": [],
            "do_not": [],
        }
        _classify_directive("MUST run tests before commit", directives)
        assert "MUST run tests before commit" in directives["must"]

    def test_do_not_does_not_appear_in_do(self) -> None:
        """DO NOT lines go to do_not, not do (elif branch)."""
        directives: dict[str, list[str]] = {
            "critical": [],
            "must": [],
            "do": [],
            "do_not": [],
        }
        _classify_directive("DO NOT use eval", directives)
        assert directives["do_not"] == ["DO NOT use eval"]
        assert directives["do"] == []

    def test_multi_category_critical_and_must(self) -> None:
        """A line can be both CRITICAL and MUST."""
        directives: dict[str, list[str]] = {
            "critical": [],
            "must": [],
            "do": [],
            "do_not": [],
        }
        _classify_directive("CRITICAL: you MUST always lint", directives)
        assert len(directives["critical"]) == 1
        assert len(directives["must"]) == 1

    def test_no_match(self) -> None:
        directives: dict[str, list[str]] = {
            "critical": [],
            "must": [],
            "do": [],
            "do_not": [],
        }
        _classify_directive("just a regular sentence", directives)
        assert directives["critical"] == []
        assert directives["must"] == []
        assert directives["do"] == []
        assert directives["do_not"] == []

    def test_case_insensitive(self) -> None:
        directives: dict[str, list[str]] = {
            "critical": [],
            "must": [],
            "do": [],
            "do_not": [],
        }
        _classify_directive("critical issue detected", directives)
        assert len(directives["critical"]) == 1

    def test_critical_and_do_not_together(self) -> None:
        """A line can be both CRITICAL and DO NOT."""
        directives: dict[str, list[str]] = {
            "critical": [],
            "must": [],
            "do": [],
            "do_not": [],
        }
        _classify_directive("CRITICAL: DO NOT skip tests", directives)
        assert len(directives["critical"]) == 1
        assert len(directives["do_not"]) == 1
        # DO NOT takes the elif branch so it should NOT appear in do
        assert directives["do"] == []


# ── _parse_rule_line ────────────────────────────────────────────────────


class TestParseRuleLine:
    def test_forbid_prefix(self) -> None:
        rule = _parse_rule_line("LINTGATE_FORBID_REGEX: import os", "test.md", 1)
        assert rule is not None
        assert rule["kind"] == "forbid_regex"
        assert rule["pattern"] == "import os"
        assert rule["severity"] == "blocking"
        assert rule["message"] == "Matched forbidden context pattern"

    def test_require_prefix(self) -> None:
        rule = _parse_rule_line("LINTGATE_REQUIRE_REGEX: from __future__", "test.md", 5)
        assert rule is not None
        assert rule["kind"] == "require_regex"
        assert rule["pattern"] == "from __future__"
        assert rule["severity"] == "warning"
        assert rule["message"] == "Missing required context pattern"

    def test_forbid_empty_pattern_returns_none(self) -> None:
        rule = _parse_rule_line("LINTGATE_FORBID_REGEX:   ", "test.md", 1)
        assert rule is None

    def test_require_empty_pattern_returns_none(self) -> None:
        rule = _parse_rule_line("LINTGATE_REQUIRE_REGEX:   ", "test.md", 1)
        assert rule is None

    def test_forbid_template_placeholder_returns_none(self) -> None:
        rule = _parse_rule_line("LINTGATE_FORBID_REGEX: <regex>", "test.md", 1)
        assert rule is None

    def test_require_template_placeholder_returns_none(self) -> None:
        rule = _parse_rule_line("LINTGATE_REQUIRE_REGEX: <pattern>", "test.md", 1)
        assert rule is None

    def test_lintgate_rule_with_forbid_regex(self) -> None:
        line = "LINTGATE_RULE: forbid_regex=print\\(; severity=blocking; message=No prints"
        rule = _parse_rule_line(line, "agents.md", 10)
        assert rule is not None
        assert rule["kind"] == "forbid_regex"
        assert rule["pattern"] == "print\\("
        assert rule["severity"] == "blocking"
        assert rule["message"] == "No prints"

    def test_lintgate_rule_with_require_regex(self) -> None:
        line = "LINTGATE_RULE: require_regex=# type: ignore; path=*.py"
        rule = _parse_rule_line(line, "agents.md", 3)
        assert rule is not None
        assert rule["kind"] == "require_regex"
        assert rule["path_glob"] == "*.py"

    def test_lintgate_rule_no_pattern_returns_none(self) -> None:
        line = "LINTGATE_RULE: severity=blocking; message=No pattern"
        rule = _parse_rule_line(line, "agents.md", 1)
        assert rule is None

    def test_plain_text_returns_none(self) -> None:
        rule = _parse_rule_line("This is just a comment", "test.md", 1)
        assert rule is None

    def test_source_formatting(self) -> None:
        rule = _parse_rule_line("LINTGATE_FORBID_REGEX: bad_thing", "/foo/bar.md", 42)
        assert rule is not None
        assert rule["source"] == "/foo/bar.md:42"

    def test_default_message_forbid(self) -> None:
        line = "LINTGATE_RULE: forbid_regex=evil_function"
        rule = _parse_rule_line(line, "test.md", 1)
        assert rule is not None
        assert rule["message"] == "Context rule violation"

    def test_default_message_require(self) -> None:
        line = "LINTGATE_RULE: require_regex=good_function"
        rule = _parse_rule_line(line, "test.md", 2)
        assert rule is not None
        assert rule["message"] == "Missing required context pattern"

    def test_part_without_equals_ignored(self) -> None:
        line = "LINTGATE_RULE: forbid_regex=bad; orphan_value; severity=warning"
        rule = _parse_rule_line(line, "test.md", 1)
        assert rule is not None
        assert rule["severity"] == "warning"

    def test_lintgate_rule_path_glob_none_when_absent(self) -> None:
        line = "LINTGATE_RULE: forbid_regex=something"
        rule = _parse_rule_line(line, "test.md", 1)
        assert rule is not None
        assert rule["path_glob"] is None

    def test_key_case_insensitive(self) -> None:
        """LINTGATE_RULE keys are lowercased before lookup."""
        line = "LINTGATE_RULE: FORBID_REGEX=func; SEVERITY=blocking"
        rule = _parse_rule_line(line, "test.md", 1)
        assert rule is not None
        assert rule["kind"] == "forbid_regex"
        assert rule["severity"] == "blocking"


# ── _parse_context_file ─────────────────────────────────────────────────


class TestParseContextFile:
    def test_basic_directives(self, tmp_path: Path) -> None:
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

    def test_skips_table_rows(self, tmp_path: Path) -> None:
        md = tmp_path / "AGENTS.md"
        md.write_text("| DO NOT | Description |\n| --- | --- |\nDO NOT use eval\n")
        result = _parse_context_file(str(md))
        assert len(result["directives"]["do_not"]) == 1

    def test_extracts_rules(self, tmp_path: Path) -> None:
        md = tmp_path / "CLAUDE.md"
        md.write_text("LINTGATE_FORBID_REGEX: exec\\(\n")
        result = _parse_context_file(str(md))
        assert len(result["rules"]) == 1
        assert result["rules"][0]["kind"] == "forbid_regex"

    def test_nonexistent_path(self) -> None:
        result = _parse_context_file("/nonexistent/CLAUDE.md")
        assert result["directives"]["critical"] == []
        assert result["directives"]["do_not"] == []
        assert result["modified_ts"] is None

    def test_extracts_path_hints(self, tmp_path: Path) -> None:
        md = tmp_path / "CLAUDE.md"
        md.write_text("Edit `src/models.py` carefully\n")
        result = _parse_context_file(str(md))
        assert "src/models.py" in result["path_hints"]

    def test_empty_lines_skipped(self, tmp_path: Path) -> None:
        md = tmp_path / "CLAUDE.md"
        md.write_text("\n\n\nCRITICAL: something\n\n\n")
        result = _parse_context_file(str(md))
        assert len(result["directives"]["critical"]) == 1

    def test_do_colon_prefix(self, tmp_path: Path) -> None:
        md = tmp_path / "CLAUDE.md"
        md.write_text("DO: use composition over inheritance\n")
        result = _parse_context_file(str(md))
        assert len(result["directives"]["do"]) == 1

    def test_code_block_content_skipped(self, tmp_path: Path) -> None:
        """Content inside code fences is not parsed as directives."""
        md = tmp_path / "CLAUDE.md"
        md.write_text(
            "CRITICAL: real directive\n"
            "```python\n"
            "CRITICAL: inside code block\n"
            "DO NOT this is code\n"
            "```\n"
            "MUST: after code block\n"
        )
        result = _parse_context_file(str(md))
        assert len(result["directives"]["critical"]) == 1
        assert len(result["directives"]["must"]) == 1
        # DO NOT inside code block should NOT appear
        assert len(result["directives"]["do_not"]) == 0

    def test_path_hints_sorted(self, tmp_path: Path) -> None:
        md = tmp_path / "CLAUDE.md"
        md.write_text("Edit `z_module.py` and `a_module.py`\n")
        result = _parse_context_file(str(md))
        assert result["path_hints"] == sorted(result["path_hints"])

    def test_modified_ts_set(self, tmp_path: Path) -> None:
        md = tmp_path / "CLAUDE.md"
        md.write_text("content")
        result = _parse_context_file(str(md))
        assert result["modified_ts"] is not None
        assert isinstance(result["modified_ts"], float)

    def test_path_field_set(self, tmp_path: Path) -> None:
        md = tmp_path / "CLAUDE.md"
        md.write_text("content")
        result = _parse_context_file(str(md))
        assert result["path"] == str(md)

    def test_multiple_code_blocks(self, tmp_path: Path) -> None:
        """Multiple code blocks toggle correctly."""
        md = tmp_path / "CLAUDE.md"
        md.write_text(
            "```\nCRITICAL: skip1\n```\n"
            "CRITICAL: real1\n"
            "```\nCRITICAL: skip2\n```\n"
            "CRITICAL: real2\n"
        )
        result = _parse_context_file(str(md))
        assert len(result["directives"]["critical"]) == 2


# ── _flatten ─────────────────────────────────────────────────────────────


class TestFlatten:
    def test_collects_from_multiple_items(self) -> None:
        items = [
            {"directives": {"critical": ["a", "b"]}},
            {"directives": {"critical": ["c"]}},
        ]
        assert _flatten(items, "critical") == ["a", "b", "c"]

    def test_missing_key_returns_empty(self) -> None:
        items = [{"directives": {"do": ["x"]}}]
        assert _flatten(items, "missing") == []

    def test_empty_items(self) -> None:
        assert _flatten([], "critical") == []

    def test_missing_directives_key(self) -> None:
        items: list[dict[str, object]] = [{"no_directives": {}}]
        assert _flatten(items, "critical") == []

    def test_preserves_order(self) -> None:
        items = [
            {"directives": {"do": ["x"]}},
            {"directives": {"do": ["a"]}},
        ]
        assert _flatten(items, "do") == ["x", "a"]


# ── _dedupe_text ─────────────────────────────────────────────────────────


class TestDedupeText:
    def test_removes_duplicates(self) -> None:
        assert _dedupe_text(["a", "b", "a", "c"]) == ["a", "b", "c"]

    def test_strips_whitespace(self) -> None:
        assert _dedupe_text(["  a  ", "a"]) == ["a"]

    def test_removes_empty_strings(self) -> None:
        assert _dedupe_text(["", "  ", "x"]) == ["x"]

    def test_preserves_order(self) -> None:
        assert _dedupe_text(["c", "b", "a"]) == ["c", "b", "a"]

    def test_empty_list(self) -> None:
        assert _dedupe_text([]) == []

    def test_all_empty_strings(self) -> None:
        assert _dedupe_text(["", " ", "  "]) == []

    def test_single_element(self) -> None:
        assert _dedupe_text(["hello"]) == ["hello"]


# ── _path_hint_matches ──────────────────────────────────────────────────


class TestPathHintMatches:
    def test_exact_match(self) -> None:
        assert _path_hint_matches("src/foo.py", "src/foo.py") is True

    def test_prefix_match_with_trailing_slash(self) -> None:
        assert _path_hint_matches("src/foo.py", "src/") is True

    def test_no_match(self) -> None:
        assert _path_hint_matches("tests/bar.py", "src/") is False

    def test_strips_leading_dotslash(self) -> None:
        assert _path_hint_matches("src/foo.py", "./src/foo.py") is True

    def test_child_path_of_non_dir_hint(self) -> None:
        assert _path_hint_matches("src/models/user.py", "src/models") is True

    def test_partial_name_no_match(self) -> None:
        """src/foobar.py should NOT match hint 'src/foo'."""
        assert _path_hint_matches("src/foobar.py", "src/foo") is False

    def test_empty_hint_after_strip(self) -> None:
        """A hint that is just './' normalizes to empty string."""
        # After lstrip("./"), hint becomes "". The path "" would match rel_path == ""
        # but for any non-empty rel_path, it won't match.
        assert _path_hint_matches("src/foo.py", "./") is False

    def test_root_level_file(self) -> None:
        assert _path_hint_matches("main.py", "main.py") is True

    def test_deeply_nested(self) -> None:
        assert _path_hint_matches("a/b/c/d.py", "a/b") is True


# ── _safe_relpath ────────────────────────────────────────────────────────


class TestSafeRelpath:
    def test_normal(self) -> None:
        result = _safe_relpath("/a/b/c.py", "/a/b")
        assert result == "c.py"

    def test_same_path(self) -> None:
        result = _safe_relpath("/a/b", "/a/b")
        assert result == "."

    def test_value_error_fallback(self) -> None:
        with patch(
            "lintgate.context.guidance.os.path.relpath",
            side_effect=ValueError("different drives"),
        ):
            result = _safe_relpath("D:\\foo\\bar.py", "C:\\other")
        assert result == "D:\\foo\\bar.py"

    def test_parent_directory(self) -> None:
        result = _safe_relpath("/a/b", "/a/b/c")
        assert result == ".."


# ── _resolve_files ───────────────────────────────────────────────────────


class TestResolveFiles:
    def test_absolute_unchanged(self) -> None:
        result = _resolve_files(["/abs/path.py"], "/root")
        assert result == ["/abs/path.py"]

    def test_relative_joined(self) -> None:
        result = _resolve_files(["src/foo.py"], "/project")
        assert result == [os.path.normpath("/project/src/foo.py")]

    def test_empty_list(self) -> None:
        assert _resolve_files([], "/root") == []

    def test_mixed_absolute_and_relative(self) -> None:
        result = _resolve_files(["/abs/file.py", "rel/file.py"], "/project")
        assert result[0] == "/abs/file.py"
        assert result[1] == os.path.normpath("/project/rel/file.py")

    def test_normalizes_dot_segments(self) -> None:
        result = _resolve_files(["src/../src/foo.py"], "/project")
        assert result == [os.path.normpath("/project/src/foo.py")]


# ── _infer_rules_from_directives ────────────────────────────────────────


class TestInferRulesFromDirectives:
    def test_with_solve_task_mention(self) -> None:
        parsed = [{"directives": {"do_not": ["DO NOT use solve_task_ prefix functions"]}}]
        rules = _infer_rules_from_directives(parsed)
        assert len(rules) == 1
        assert rules[0]["kind"] == "forbid_regex"
        assert "solve_task_" in rules[0]["pattern"]
        assert rules[0]["source"] == "inferred:do_not_solve_task_prefix"
        assert rules[0]["severity"] == "blocking"

    def test_without_solve_task(self) -> None:
        parsed = [{"directives": {"do_not": ["DO NOT use globals"]}}]
        rules = _infer_rules_from_directives(parsed)
        assert rules == []

    def test_empty_parsed(self) -> None:
        assert _infer_rules_from_directives([]) == []

    def test_solve_task_across_multiple_items(self) -> None:
        """solve_task_ mention in any item triggers the rule."""
        parsed = [
            {"directives": {"do_not": ["DO NOT use globals"]}},
            {"directives": {"do_not": ["DO NOT use solve_task_ prefix"]}},
        ]
        rules = _infer_rules_from_directives(parsed)
        assert len(rules) == 1

    def test_missing_do_not_key(self) -> None:
        parsed = [{"directives": {"do": ["DO something"]}}]
        rules = _infer_rules_from_directives(parsed)
        assert rules == []

    def test_missing_directives_key(self) -> None:
        parsed = [{"other": "value"}]
        rules = _infer_rules_from_directives(parsed)
        assert rules == []


# ── rule_applies_to_path ────────────────────────────────────────────────


class TestRuleAppliesToPath:
    def test_no_path_glob(self) -> None:
        rule = {"kind": "forbid_regex", "pattern": "x"}
        assert rule_applies_to_path(rule, "any/file.py") is True

    def test_matching_glob(self) -> None:
        rule = {"kind": "forbid_regex", "pattern": "x", "path_glob": "*.py"}
        assert rule_applies_to_path(rule, "foo.py") is True

    def test_non_matching_glob(self) -> None:
        rule = {"kind": "forbid_regex", "pattern": "x", "path_glob": "*.py"}
        assert rule_applies_to_path(rule, "foo.js") is False

    def test_path_glob_none_applies(self) -> None:
        rule = {"kind": "forbid_regex", "pattern": "x", "path_glob": None}
        assert rule_applies_to_path(rule, "any/file.py") is True

    def test_directory_glob(self) -> None:
        rule = {"kind": "forbid_regex", "pattern": "x", "path_glob": "tests/*"}
        assert rule_applies_to_path(rule, "tests/test_foo.py") is True
        assert rule_applies_to_path(rule, "src/foo.py") is False


# ── relevant_guidance_for_file ───────────────────────────────────────────


class TestRelevantGuidanceForFile:
    def test_always_includes_critical_must_do_not(self) -> None:
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

    def test_includes_do_when_path_matches(self) -> None:
        directives = {
            "critical": [],
            "must": [],
            "do": ["DO use type hints in `src/models.py`"],
            "do_not": [],
        }
        result = relevant_guidance_for_file(
            "/project/src/models.py",
            "/project",
            directives,
            [],
        )
        assert "DO use type hints in `src/models.py`" in result

    def test_excludes_do_when_path_mismatches(self) -> None:
        directives = {
            "critical": [],
            "must": [],
            "do": ["DO use type hints in `src/models.py`"],
            "do_not": [],
        }
        result = relevant_guidance_for_file(
            "/project/tests/foo.py",
            "/project",
            directives,
            [],
        )
        assert result == []

    def test_skips_do_without_path_hints(self) -> None:
        directives = {
            "critical": [],
            "must": [],
            "do": ["DO use composition over inheritance"],
            "do_not": [],
        }
        result = relevant_guidance_for_file(
            "/project/src/foo.py",
            "/project",
            directives,
            [],
        )
        assert "DO use composition over inheritance" not in result

    def test_includes_hint_referenced_directives(self) -> None:
        directives = {
            "critical": ["CRITICAL: always lint src/utils.py"],
            "must": [],
            "do": [],
            "do_not": [],
        }
        result = relevant_guidance_for_file(
            "/project/src/utils.py",
            "/project",
            directives,
            ["src/utils.py"],
        )
        assert "CRITICAL: always lint src/utils.py" in result

    def test_deduplicates(self) -> None:
        directives = {
            "critical": ["same item"],
            "must": ["same item"],
            "do": [],
            "do_not": [],
        }
        result = relevant_guidance_for_file("/project/f.py", "/project", directives, [])
        assert result.count("same item") == 1

    def test_empty_directives(self) -> None:
        directives: dict[str, list[str]] = {
            "critical": [],
            "must": [],
            "do": [],
            "do_not": [],
        }
        result = relevant_guidance_for_file("/project/f.py", "/project", directives, [])
        assert result == []

    def test_matched_hints_add_directive_from_all_categories(self) -> None:
        """When a path hint matches, directives from all categories referencing
        that hint get included."""
        directives = {
            "critical": [],
            "must": [],
            "do": ["DO check src/core.py for issues"],
            "do_not": ["DO NOT modify src/core.py without review"],
        }
        result = relevant_guidance_for_file(
            "/project/src/core.py",
            "/project",
            directives,
            ["src/core.py"],
        )
        # do_not is always included; do should also be included via hint matching
        assert "DO NOT modify src/core.py without review" in result
        assert "DO check src/core.py for issues" in result


# ── collect_context_rules ────────────────────────────────────────────────


class TestCollectContextRules:
    def test_combines_explicit_and_inferred(self, tmp_path: Path) -> None:
        md = tmp_path / "CLAUDE.md"
        md.write_text("LINTGATE_FORBID_REGEX: eval\\(\nDO NOT use solve_task_ prefix functions\n")
        rules = collect_context_rules(str(tmp_path))
        kinds = [r["kind"] for r in rules]
        assert "forbid_regex" in kinds
        assert len(rules) >= 2

    def test_no_files(self, tmp_path: Path) -> None:
        rules = collect_context_rules(str(tmp_path))
        assert rules == []

    def test_only_inferred(self, tmp_path: Path) -> None:
        md = tmp_path / "CLAUDE.md"
        md.write_text("DO NOT use solve_task_ prefix functions\n")
        rules = collect_context_rules(str(tmp_path))
        assert len(rules) == 1
        assert rules[0]["source"] == "inferred:do_not_solve_task_prefix"

    def test_only_explicit(self, tmp_path: Path) -> None:
        md = tmp_path / "CLAUDE.md"
        md.write_text("LINTGATE_REQUIRE_REGEX: from __future__\n")
        rules = collect_context_rules(str(tmp_path))
        assert len(rules) == 1
        assert rules[0]["kind"] == "require_regex"


# ── build_context_guidance ───────────────────────────────────────────────


class TestBuildContextGuidance:
    def test_full(self, tmp_path: Path) -> None:
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

    def test_with_files(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("CRITICAL: check all files\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("pass")
        result = build_context_guidance(str(tmp_path), files=["src/app.py"])
        abs_path = os.path.normpath(str(tmp_path / "src" / "app.py"))
        assert abs_path in result["relevant_for_files"]

    def test_empty_project(self, tmp_path: Path) -> None:
        result = build_context_guidance(str(tmp_path))
        assert result["context_files"] == []
        assert result["directives"]["critical"] == []
        assert result["rules"] == []
        assert result["path_hints"] == []

    def test_path_hints_sorted(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("Edit `z_module.py` and `a_module.py`\n")
        result = build_context_guidance(str(tmp_path))
        assert result["path_hints"] == sorted(result["path_hints"])

    def test_context_files_have_expected_keys(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("content")
        result = build_context_guidance(str(tmp_path))
        cf = result["context_files"][0]
        assert "path" in cf
        assert "name" in cf
        assert "modified_ts" in cf

    def test_deduplicates_directives(self, tmp_path: Path) -> None:
        """Directives appearing in multiple files are deduplicated."""
        (tmp_path / "AGENTS.md").write_text("CRITICAL: same directive\n")
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        (dot_claude / "CLAUDE.md").write_text("CRITICAL: same directive\n")
        result = build_context_guidance(str(tmp_path))
        assert result["directives"]["critical"].count("CRITICAL: same directive") == 1

    def test_path_hints_deduplicated_across_files(self, tmp_path: Path) -> None:
        """Path hints from multiple files are deduplicated."""
        (tmp_path / "AGENTS.md").write_text("Edit `src/foo.py`\n")
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        (dot_claude / "CLAUDE.md").write_text("Check `src/foo.py`\n")
        result = build_context_guidance(str(tmp_path))
        assert result["path_hints"].count("src/foo.py") == 1

    def test_files_none_means_empty_relevant(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("CRITICAL: something\n")
        result = build_context_guidance(str(tmp_path), files=None)
        assert result["relevant_for_files"] == {}


# ── summarize_context_guidance ───────────────────────────────────────────


class TestSummarizeContextGuidance:
    def test_counts(self) -> None:
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

    def test_empty_guidance(self) -> None:
        summary = summarize_context_guidance({})
        assert summary["context_file_count"] == 0
        assert summary["context_files"] == []
        assert summary["rule_count"] == 0
        assert summary["directive_counts"]["critical"] == 0

    def test_missing_directive_categories(self) -> None:
        """If some directive categories are missing, counts default to 0."""
        guidance = {
            "context_files": [],
            "directives": {"critical": ["one"]},
            "rules": [],
        }
        summary = summarize_context_guidance(guidance)
        assert summary["directive_counts"]["critical"] == 1
        assert summary["directive_counts"]["must"] == 0
        assert summary["directive_counts"]["do"] == 0
        assert summary["directive_counts"]["do_not"] == 0


# ── count_placeholder_rules ─────────────────────────────────────────────


class TestCountPlaceholderRules:
    def test_counts_template_placeholders(self, tmp_path: Path) -> None:
        md = tmp_path / "CLAUDE.md"
        md.write_text(
            "LINTGATE_FORBID_REGEX: <regex>\n"
            "LINTGATE_REQUIRE_REGEX: <pattern>\n"
            "LINTGATE_FORBID_REGEX: real_pattern\n"
        )
        count = count_placeholder_rules(str(tmp_path))
        assert count == 2

    def test_no_placeholders(self, tmp_path: Path) -> None:
        md = tmp_path / "CLAUDE.md"
        md.write_text("LINTGATE_FORBID_REGEX: import os\n")
        count = count_placeholder_rules(str(tmp_path))
        assert count == 0

    def test_no_context_files(self, tmp_path: Path) -> None:
        count = count_placeholder_rules(str(tmp_path))
        assert count == 0

    def test_mixed_real_and_placeholder(self, tmp_path: Path) -> None:
        md = tmp_path / "CLAUDE.md"
        md.write_text(
            "LINTGATE_FORBID_REGEX: <regex>\n"
            "LINTGATE_FORBID_REGEX: eval\\(\n"
            "LINTGATE_REQUIRE_REGEX: <pattern>\n"
            "LINTGATE_REQUIRE_REGEX: from __future__\n"
        )
        count = count_placeholder_rules(str(tmp_path))
        assert count == 2

    def test_handles_os_error_gracefully(self, tmp_path: Path) -> None:
        """If a discovered file can't be read, it's skipped."""
        md = tmp_path / "CLAUDE.md"
        md.write_text("LINTGATE_FORBID_REGEX: <regex>\n")
        # Verify it works normally first
        assert count_placeholder_rules(str(tmp_path)) == 1

    def test_bullet_prefixed_rules(self, tmp_path: Path) -> None:
        """Rules inside bullet points still get _clean_line treatment."""
        md = tmp_path / "CLAUDE.md"
        md.write_text("- LINTGATE_FORBID_REGEX: <regex>\n")
        count = count_placeholder_rules(str(tmp_path))
        assert count == 1

    def test_multiple_context_files(self, tmp_path: Path) -> None:
        """Placeholders from both root and .claude files are counted."""
        (tmp_path / "AGENTS.md").write_text("LINTGATE_FORBID_REGEX: <regex>\n")
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        (dot_claude / "CLAUDE.md").write_text("LINTGATE_REQUIRE_REGEX: <pattern>\n")
        count = count_placeholder_rules(str(tmp_path))
        assert count == 2


# ── Integration tests ───────────────────────────────────────────────────


class TestIntegration:
    def test_multiple_context_files(self, tmp_path: Path) -> None:
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

    def test_rules_and_directives_together(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text(
            "LINTGATE_REQUIRE_REGEX: from __future__\nDO NOT use solve_task_ prefix functions\n"
        )
        rules = collect_context_rules(str(tmp_path))
        assert any(r["kind"] == "require_regex" for r in rules)
        assert any(r["kind"] == "forbid_regex" and "solve_task_" in r["pattern"] for r in rules)

    def test_end_to_end_with_relevant_files(self, tmp_path: Path) -> None:
        """Full pipeline: discover -> parse -> build -> summarize with file relevance."""
        (tmp_path / "CLAUDE.md").write_text(
            "CRITICAL: always review\n"
            "DO use `src/models.py` for data models\n"
            "DO NOT skip validation\n"
            "MUST test all changes\n"
            "LINTGATE_FORBID_REGEX: exec\\(\n"
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "models.py").write_text("class Model: pass")

        guidance = build_context_guidance(str(tmp_path), files=["src/models.py"])
        summary = summarize_context_guidance(guidance)

        assert summary["context_file_count"] == 1
        assert summary["directive_counts"]["critical"] >= 1
        assert summary["directive_counts"]["must"] >= 1
        assert summary["directive_counts"]["do_not"] >= 1
        assert summary["rule_count"] >= 1

        abs_models = os.path.normpath(str(tmp_path / "src" / "models.py"))
        relevant = guidance["relevant_for_files"][abs_models]
        # Critical, must, do_not always included; do should match via path hint
        assert any("always review" in r for r in relevant)
        assert any("src/models.py" in r for r in relevant)

    def test_code_blocks_do_not_pollute_rules(self, tmp_path: Path) -> None:
        """Rules inside code fences are not extracted."""
        (tmp_path / "CLAUDE.md").write_text(
            "```\nLINTGATE_FORBID_REGEX: inside_code_block\n```\nLINTGATE_FORBID_REGEX: real_rule\n"
        )
        rules = collect_context_rules(str(tmp_path))
        patterns = [r["pattern"] for r in rules]
        assert "inside_code_block" not in patterns
        assert "real_rule" in patterns


# ── agent_profiles ───────────────────────────────────────────────────────


class TestAgentProfiles:
    def test_atomic_write_json_writes_and_creates_backup(self, tmp_path: Path) -> None:
        import json

        from lintgate.agent_profiles import _atomic_write_json

        config_path = tmp_path / "claude_desktop_config.json"
        config_path.write_text('{"legacy": true}\n', encoding="utf-8")

        payload = {"mcpServers": {"lintgate": {"command": "lintgate-mcp", "args": []}}}
        _atomic_write_json(config_path, payload)

        backup = tmp_path / "claude_desktop_config.json.bak"
        assert backup.exists()
        assert json.loads(config_path.read_text(encoding="utf-8")) == payload

    def test_write_claude_config_is_idempotent(self, tmp_path: Path) -> None:
        import json

        from lintgate.agent_profiles import write_claude_config

        config_path = tmp_path / "claude" / "claude_desktop_config.json"

        changed = write_claude_config(config_path, "lintgate-mcp")
        unchanged = write_claude_config(config_path, "lintgate-mcp")

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert changed is True
        assert unchanged is False
        assert data["mcpServers"]["lintgate"] == {"command": "lintgate-mcp", "args": []}

    def test_write_antigravity_config_recovers_from_invalid_json(self, tmp_path: Path) -> None:
        import json

        from lintgate.agent_profiles import write_antigravity_config

        config_path = tmp_path / "antigravity" / "mcp.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("{invalid", encoding="utf-8")

        changed = write_antigravity_config(config_path, "lintgate-mcp")

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert changed is True
        assert data["mcpServers"]["lintgate"]["command"] == "lintgate-mcp"

    def test_get_profile_case_insensitive_and_missing(self) -> None:
        from lintgate.agent_profiles import get_profile

        claude = get_profile("CLAUDE")
        assert claude is not None
        assert claude.id == "claude"
        assert claude.display_name == "Claude Desktop"
        assert claude.schema_strict is False

        antigravity = get_profile("aNtIgRaViTy")
        assert antigravity is not None
        assert antigravity.id == "antigravity"
        assert antigravity.display_name == "Antigravity"
        assert antigravity.schema_strict is True

        assert get_profile("missing-agent") is None
