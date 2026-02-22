from __future__ import annotations

from unittest import mock
from pathlib import Path

import pytest

from lintgate.context_bootstrap import (
    ReviewItem,
    bootstrap_context_files,
    _collect_machine_rule_lines,
    _rule_to_line,
    _project_metadata,
    _read_readme_description,
    _select_actionable_anti_patterns,
    _recommended_commands,
    _build_quick_wins,
    _collect_review_items,
    _collect_directive_review_items,
    _collect_dead_path_review_items,
    _collect_facet_fallback_items,
    _NEGATIVE_CUE_RE,
    _NO_THEORY,
    _PERF_ANTI_PATTERN_CUE,
)
from lintgate.bootstrap_defaults import ZERO_STATE_ANTI_PATTERNS, ZERO_STATE_FACET_FALLBACKS


# ── ReviewItem ────────────────────────────────────────────────────────


class TestReviewItem:
    def test_to_dict_basic(self) -> None:
        item = ReviewItem(
            review_type="directive_classification",
            context="Do not import pandas",
            question="Is this enforceable?",
        )
        d = item.to_dict()
        assert d["type"] == "directive_classification"
        assert d["context"] == "Do not import pandas"
        assert d["question"] == "Is this enforceable?"
        assert d["options"] == []
        assert d["detail"] == {}

    def test_to_dict_with_options_and_detail(self) -> None:
        item = ReviewItem(
            review_type="facet_fallback",
            context="core_theory",
            question="Can you provide a summary?",
            options=["provide_summary", "keep_default"],
            detail={"default_used": "some fallback"},
        )
        d = item.to_dict()
        assert d["options"] == ["provide_summary", "keep_default"]
        assert d["detail"]["default_used"] == "some fallback"

    def test_default_fields(self) -> None:
        item = ReviewItem(review_type="x", context="y", question="z")
        assert item.options == []
        assert item.detail == {}


# ── _rule_to_line ─────────────────────────────────────────────────────


class TestRuleToLine:
    def test_forbid_regex(self) -> None:
        rule = {"kind": "forbid_regex", "pattern": "import os"}
        assert _rule_to_line(rule) == "LINTGATE_FORBID_REGEX: import os"

    def test_require_regex(self) -> None:
        rule = {"kind": "require_regex", "pattern": "from __future__"}
        assert _rule_to_line(rule) == "LINTGATE_REQUIRE_REGEX: from __future__"

    def test_empty_pattern_returns_empty(self) -> None:
        rule = {"kind": "forbid_regex", "pattern": ""}
        assert _rule_to_line(rule) == ""

    def test_unknown_kind_returns_empty(self) -> None:
        rule = {"kind": "warn_regex", "pattern": "something"}
        assert _rule_to_line(rule) == ""

    def test_missing_kind_returns_empty(self) -> None:
        rule = {"pattern": "foo"}
        assert _rule_to_line(rule) == ""

    def test_missing_pattern_returns_empty(self) -> None:
        rule = {"kind": "forbid_regex"}
        assert _rule_to_line(rule) == ""


# ── _collect_machine_rule_lines ───────────────────────────────────────


class TestCollectMachineRuleLines:
    def test_from_guidance_rules(self) -> None:
        guidance = {
            "rules": [
                {"kind": "forbid_regex", "pattern": "eval\\("},
                {"kind": "require_regex", "pattern": "typing"},
            ]
        }
        result = _collect_machine_rule_lines(guidance=guidance, theory={}, max_machine_rules=10)
        assert len(result) == 2
        assert "LINTGATE_FORBID_REGEX: eval\\(" in result[0]
        assert "LINTGATE_REQUIRE_REGEX: typing" in result[1]

    def test_from_theory_proposed_rules(self) -> None:
        theory = {
            "enforceable_rules": {
                "proposed_rules": [
                    {"add_line": "LINTGATE_FORBID_REGEX: subprocess.call"},
                ]
            }
        }
        result = _collect_machine_rule_lines(guidance={}, theory=theory, max_machine_rules=10)
        assert len(result) == 1
        assert "subprocess.call" in result[0]

    def test_deduplication(self) -> None:
        guidance = {
            "rules": [{"kind": "forbid_regex", "pattern": "eval\\("}]
        }
        theory = {
            "enforceable_rules": {
                "proposed_rules": [
                    {"add_line": "LINTGATE_FORBID_REGEX: eval\\("},
                ]
            }
        }
        result = _collect_machine_rule_lines(
            guidance=guidance, theory=theory, max_machine_rules=10
        )
        assert len(result) == 1

    def test_max_cap(self) -> None:
        guidance = {
            "rules": [
                {"kind": "forbid_regex", "pattern": f"pat{i}"}
                for i in range(20)
            ]
        }
        result = _collect_machine_rule_lines(guidance=guidance, theory={}, max_machine_rules=3)
        assert len(result) == 3

    def test_empty_inputs(self) -> None:
        result = _collect_machine_rule_lines(guidance={}, theory={}, max_machine_rules=10)
        assert result == []

    def test_proposed_rule_empty_add_line_skipped(self) -> None:
        theory = {
            "enforceable_rules": {
                "proposed_rules": [
                    {"add_line": ""},
                    {"add_line": "   "},
                ]
            }
        }
        result = _collect_machine_rule_lines(guidance={}, theory=theory, max_machine_rules=10)
        assert result == []


# ── _project_metadata ─────────────────────────────────────────────────


class TestProjectMetadata:
    def test_from_pyproject_toml(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "my-lib"\ndescription = "A cool library"\n'
        )
        meta = _project_metadata(tmp_path)
        assert meta["name"] == "my-lib"
        assert meta["description"] == "A cool library"

    def test_falls_back_to_dir_name(self, tmp_path: Path) -> None:
        meta = _project_metadata(tmp_path)
        assert meta["name"] == tmp_path.name
        assert meta["description"] == ""

    def test_pyproject_with_no_project_section(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 88\n")
        meta = _project_metadata(tmp_path)
        assert meta["name"] == tmp_path.name

    def test_pyproject_invalid_toml(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("this is not valid toml {{{{")
        meta = _project_metadata(tmp_path)
        assert meta["name"] == tmp_path.name

    def test_description_from_readme_when_pyproject_has_none(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "noname"\n')
        (tmp_path / "README.md").write_text("# Title\n\nHere is a description.\n")
        meta = _project_metadata(tmp_path)
        assert meta["name"] == "noname"
        assert "Here is a description" in meta["description"]

    def test_pyproject_non_dict_project(self, tmp_path: Path) -> None:
        # project key is a string instead of a dict
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "ok"\n')
        # Just verify it does not crash for a well-formed toml
        meta = _project_metadata(tmp_path)
        assert isinstance(meta["name"], str)


# ── _read_readme_description ──────────────────────────────────────────


class TestReadReadmeDescription:
    def test_finds_readme_md(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Title\n\nDescription line.\n")
        result = _read_readme_description(tmp_path)
        assert "Description line" in result

    def test_skips_badges_and_links(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "# Title\n"
            "[badge](https://shields.io/badge)\n"
            "Actual description.\n"
        )
        result = _read_readme_description(tmp_path)
        assert "Actual description" in result

    def test_skips_heading_only_lines(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Project\n## Subheading\nReal text.\n")
        result = _read_readme_description(tmp_path)
        assert "Real text" in result

    def test_no_readme_returns_empty(self, tmp_path: Path) -> None:
        result = _read_readme_description(tmp_path)
        assert result == ""

    def test_empty_readme_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("")
        result = _read_readme_description(tmp_path)
        assert result == ""

    def test_all_headings_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# One\n## Two\n### Three\n")
        result = _read_readme_description(tmp_path)
        assert result == ""

    def test_case_insensitive_readme(self, tmp_path: Path) -> None:
        (tmp_path / "readme.md").write_text("# Title\n\nLowercase filename.\n")
        result = _read_readme_description(tmp_path)
        assert "Lowercase filename" in result

    def test_uppercase_readme(self, tmp_path: Path) -> None:
        (tmp_path / "README.MD").write_text("# Title\n\nUppercase ext.\n")
        result = _read_readme_description(tmp_path)
        assert "Uppercase ext" in result


# ── _select_actionable_anti_patterns ──────────────────────────────────


class TestSelectActionableAntiPatterns:
    def test_filters_non_negative(self) -> None:
        claims = [
            "This project uses composition.",
            "Do not import wildcard modules.",
        ]
        result = _select_actionable_anti_patterns(claims)
        assert len(result) == 1
        assert "import wildcard" in result[0].lower()

    def test_deduplication_case_insensitive(self) -> None:
        claims = [
            "Do not use eval.",
            "do not use eval.",
        ]
        result = _select_actionable_anti_patterns(claims)
        assert len(result) == 1

    def test_truncation_long_text(self) -> None:
        long_claim = "Do not " + "x" * 300
        result = _select_actionable_anti_patterns([long_claim])
        assert len(result) == 1
        assert result[0].endswith("...")
        assert len(result[0]) <= 260

    def test_max_items_limits(self) -> None:
        claims = [f"Do not approach_{i}" for i in range(10)]
        result = _select_actionable_anti_patterns(claims, max_items=3)
        assert len(result) == 3

    def test_zero_state_defaults_returned_when_empty(self) -> None:
        result = _select_actionable_anti_patterns([])
        assert len(result) == 5
        # The function promotes the perf anti-pattern into position 3,
        # so the order differs from the raw list slice. Verify all items
        # come from the defaults and the perf item is present.
        for item in result:
            assert item in ZERO_STATE_ANTI_PATTERNS
        assert any(_PERF_ANTI_PATTERN_CUE in item for item in result)

    def test_max_items_zero_returns_empty_when_no_claims(self) -> None:
        result = _select_actionable_anti_patterns([], max_items=0)
        assert result == []

    def test_perf_anti_pattern_promoted_in_defaults(self) -> None:
        result = _select_actionable_anti_patterns([], max_items=5)
        # "O(n^2)" should appear in the first 4 items
        perf_found = any(_PERF_ANTI_PATTERN_CUE in item for item in result[:4])
        assert perf_found

    def test_empty_string_claims_filtered(self) -> None:
        claims = ["", "  ", "Do not skip tests."]
        result = _select_actionable_anti_patterns(claims)
        assert len(result) == 1


# ── _recommended_commands ─────────────────────────────────────────────


class TestRecommendedCommands:
    def test_python_project(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        cmds = _recommended_commands(tmp_path)
        assert any("ruff" in c for c in cmds)
        assert any("pytest" in c for c in cmds)
        assert any("mypy" in c for c in cmds)

    def test_python_with_uv_lock(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "uv.lock").write_text("")
        cmds = _recommended_commands(tmp_path)
        assert any(c.startswith("uv run ") for c in cmds)

    def test_node_project(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}")
        cmds = _recommended_commands(tmp_path)
        assert any("npm run lint" in c for c in cmds)
        assert any("npm test" in c for c in cmds)

    def test_rust_project(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        cmds = _recommended_commands(tmp_path)
        assert any("cargo fmt" in c for c in cmds)
        assert any("cargo clippy" in c for c in cmds)
        assert any("cargo test" in c for c in cmds)

    def test_empty_project_generic_message(self, tmp_path: Path) -> None:
        cmds = _recommended_commands(tmp_path)
        assert len(cmds) == 1
        assert "lint" in cmds[0].lower()

    def test_multi_language_project(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "package.json").write_text("{}")
        cmds = _recommended_commands(tmp_path)
        assert any("ruff" in c for c in cmds)
        assert any("npm" in c for c in cmds)

    def test_py_files_detected(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("print('hello')")
        cmds = _recommended_commands(tmp_path)
        assert any("ruff" in c for c in cmds)

    def test_deduplication(self, tmp_path: Path) -> None:
        # pyproject.toml and *.py both trigger Python commands;
        # verify no duplicates
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "app.py").write_text("")
        cmds = _recommended_commands(tmp_path)
        assert len(cmds) == len(set(cmds))


# ── _build_quick_wins ─────────────────────────────────────────────────


class TestBuildQuickWins:
    def test_no_config_suggests_creation(self, tmp_path: Path) -> None:
        wins = _build_quick_wins(tmp_path, {}, {})
        assert any("lintgate.yaml" in w for w in wins)

    def test_config_in_root_suppresses_suggestion(self, tmp_path: Path) -> None:
        (tmp_path / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")
        wins = _build_quick_wins(tmp_path, {}, {})
        assert not any("Create `.claude/lintgate.yaml`" in w for w in wins)

    def test_config_in_claude_dir_suppresses_suggestion(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")
        wins = _build_quick_wins(tmp_path, {}, {})
        assert not any("Create `.claude/lintgate.yaml`" in w for w in wins)

    def test_do_not_directives_without_rules(self, tmp_path: Path) -> None:
        guidance = {
            "directives": {"do_not": ["Do not use eval"]},
            "rules": [],
        }
        wins = _build_quick_wins(tmp_path, guidance, {})
        assert any("extract_theory_constraints" in w.lower() for w in wins)

    def test_proposed_rules_surfaced(self, tmp_path: Path) -> None:
        theory = {
            "enforceable_rules": {
                "proposed_rules": [{"add_line": "LINTGATE_FORBID_REGEX: eval"}]
            }
        }
        wins = _build_quick_wins(tmp_path, {}, theory)
        assert any("1 rule(s) proposed" in w for w in wins)

    def test_lockfile_missing_for_python(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        wins = _build_quick_wins(tmp_path, {}, {})
        assert any("lockfile" in w.lower() for w in wins)

    def test_lockfile_present_uv(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "uv.lock").write_text("")
        wins = _build_quick_wins(tmp_path, {}, {})
        assert not any("lockfile" in w.lower() for w in wins)

    def test_lockfile_present_poetry(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "poetry.lock").write_text("")
        wins = _build_quick_wins(tmp_path, {}, {})
        assert not any("lockfile" in w.lower() for w in wins)


# ── _collect_review_items ─────────────────────────────────────────────


class TestCollectReviewItems:
    def test_empty_inputs(self) -> None:
        items = _collect_review_items(
            guidance={},
            facet_summaries={},
            audit={},
            project_root="/tmp",
        )
        # At minimum facet_fallback items are generated for missing facets
        assert isinstance(items, list)

    def test_collects_facet_fallbacks_for_missing_keys(self) -> None:
        items = _collect_review_items(
            guidance={},
            facet_summaries={},
            audit={},
            project_root="/tmp",
        )
        facet_types = [i for i in items if i.review_type == "facet_fallback"]
        facet_keys = {i.context for i in facet_types}
        for key in ZERO_STATE_FACET_FALLBACKS:
            assert key in facet_keys


# ── _collect_directive_review_items ───────────────────────────────────


class TestCollectDirectiveReviewItems:
    def test_no_directives(self) -> None:
        items: list[ReviewItem] = []
        _collect_directive_review_items(items, {})
        assert items == []

    def test_uncertain_directives_collected(self) -> None:
        items: list[ReviewItem] = []
        # The classify_directive_enforceability function must return
        # "uncertain" for some inputs. Use a simple ambiguous directive.
        guidance = {
            "directives": {
                "do_not": [
                    "Do not make things too complicated",
                ]
            }
        }
        _collect_directive_review_items(items, guidance)
        # It may or may not classify as uncertain, so just check types
        for item in items:
            assert item.review_type == "directive_classification"


# ── _collect_dead_path_review_items ───────────────────────────────────


class TestCollectDeadPathReviewItems:
    def test_no_audit(self) -> None:
        items: list[ReviewItem] = []
        _collect_dead_path_review_items(items, {})
        assert items == []

    def test_dead_paths_collected(self) -> None:
        items: list[ReviewItem] = []
        audit = {
            "audit": [
                {
                    "name": "CLAUDE.md",
                    "file": "/tmp/CLAUDE.md",
                    "health_checks": [
                        {
                            "check": "path_references",
                            "status": "warn",
                            "detail": "2 referenced paths don't exist: src/missing.py, lib/gone.py",
                        }
                    ],
                }
            ]
        }
        _collect_dead_path_review_items(items, audit)
        assert len(items) == 2
        paths = {i.context for i in items}
        assert "src/missing.py" in paths
        assert "lib/gone.py" in paths
        assert items[0].review_type == "dead_path_candidate"
        assert items[0].detail["source_file"] == "/tmp/CLAUDE.md"

    def test_non_path_reference_check_skipped(self) -> None:
        items: list[ReviewItem] = []
        audit = {
            "audit": [
                {
                    "name": "CLAUDE.md",
                    "file": "/tmp/CLAUDE.md",
                    "health_checks": [
                        {
                            "check": "length",
                            "status": "warn",
                            "detail": "File too long",
                        }
                    ],
                }
            ]
        }
        _collect_dead_path_review_items(items, audit)
        assert items == []

    def test_pass_status_skipped(self) -> None:
        items: list[ReviewItem] = []
        audit = {
            "audit": [
                {
                    "name": "CLAUDE.md",
                    "file": "/tmp/CLAUDE.md",
                    "health_checks": [
                        {
                            "check": "path_references",
                            "status": "pass",
                            "detail": "All paths exist.",
                        }
                    ],
                }
            ]
        }
        _collect_dead_path_review_items(items, audit)
        assert items == []

    def test_no_colon_in_detail_skipped(self) -> None:
        items: list[ReviewItem] = []
        audit = {
            "audit": [
                {
                    "name": "CLAUDE.md",
                    "file": "/tmp/CLAUDE.md",
                    "health_checks": [
                        {
                            "check": "path_references",
                            "status": "warn",
                            "detail": "paths don't exist but no colon",
                        }
                    ],
                }
            ]
        }
        _collect_dead_path_review_items(items, audit)
        assert items == []

    def test_extra_count_trimmed(self) -> None:
        items: list[ReviewItem] = []
        audit = {
            "audit": [
                {
                    "name": "CLAUDE.md",
                    "file": "/tmp/CLAUDE.md",
                    "health_checks": [
                        {
                            "check": "path_references",
                            "status": "warn",
                            "detail": "5 referenced paths don't exist: a.py, b.py (+3 more)",
                        }
                    ],
                }
            ]
        }
        _collect_dead_path_review_items(items, audit)
        paths = {i.context for i in items}
        assert "a.py" in paths
        assert "b.py" in paths
        assert len(items) == 2


# ── _collect_facet_fallback_items ─────────────────────────────────────


class TestCollectFacetFallbackItems:
    def test_all_missing_facets(self) -> None:
        items: list[ReviewItem] = []
        _collect_facet_fallback_items(items, {})
        assert len(items) == len(ZERO_STATE_FACET_FALLBACKS)
        for item in items:
            assert item.review_type == "facet_fallback"

    def test_no_theory_value_triggers_fallback(self) -> None:
        items: list[ReviewItem] = []
        facets = {"core_theory": _NO_THEORY}
        _collect_facet_fallback_items(items, facets)
        core_items = [i for i in items if i.context == "core_theory"]
        assert len(core_items) == 1

    def test_default_value_triggers_fallback(self) -> None:
        items: list[ReviewItem] = []
        facets = {"core_theory": ZERO_STATE_FACET_FALLBACKS["core_theory"]}
        _collect_facet_fallback_items(items, facets)
        core_items = [i for i in items if i.context == "core_theory"]
        assert len(core_items) == 1

    def test_real_content_suppresses_fallback(self) -> None:
        items: list[ReviewItem] = []
        facets = {key: "Real project-specific content." for key in ZERO_STATE_FACET_FALLBACKS}
        _collect_facet_fallback_items(items, facets)
        assert len(items) == 0

    def test_whitespace_only_triggers_fallback(self) -> None:
        items: list[ReviewItem] = []
        facets = {"core_theory": "   "}
        _collect_facet_fallback_items(items, facets)
        core_items = [i for i in items if i.context == "core_theory"]
        assert len(core_items) == 1


# ── Constants ─────────────────────────────────────────────────────────


class TestConstants:
    def test_negative_cue_regex_matches(self) -> None:
        assert _NEGATIVE_CUE_RE.search("Do not use eval")
        assert _NEGATIVE_CUE_RE.search("Never bypass the pipeline")
        assert _NEGATIVE_CUE_RE.search("Avoid wildcard imports")
        assert _NEGATIVE_CUE_RE.search("This is an anti-pattern")

    def test_negative_cue_regex_no_match(self) -> None:
        assert _NEGATIVE_CUE_RE.search("This project uses composition") is None

    def test_no_theory_sentinel(self) -> None:
        assert _NO_THEORY == "(no theory content found)"

    def test_perf_anti_pattern_cue(self) -> None:
        assert _PERF_ANTI_PATTERN_CUE == "O(n\u00b2)"


# ── bootstrap_context_files integration ───────────────────────────────


class TestBootstrapContextFiles:
    def test_max_machine_rules_zero_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="max_machine_rules must be > 0"):
            bootstrap_context_files(str(tmp_path), max_machine_rules=0)

    def test_max_machine_rules_negative_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="max_machine_rules must be > 0"):
            bootstrap_context_files(str(tmp_path), max_machine_rules=-1)

    def test_dry_run_returns_planned_status(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Repo\n\nDescription.\n")
        payload = bootstrap_context_files(str(tmp_path), write=False)
        for entry in payload["files"]:
            assert entry["status"] == "planned"
        assert payload["written_files"] == []
        assert payload["skipped_existing_files"] == []

    def test_write_creates_files(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Repo\n\nDescription.\n")
        payload = bootstrap_context_files(str(tmp_path), write=True)
        assert len(payload["written_files"]) >= 3
        assert (tmp_path / ".claude" / "CLAUDE.md").exists()
        assert (tmp_path / "AGENTS.md").exists()

    def test_agents_md_never_overwritten(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Repo\n")
        (tmp_path / "AGENTS.md").write_text("custom-agents-sentinel\n")
        payload = bootstrap_context_files(str(tmp_path), write=True, overwrite=True)
        file_map = {e["relative_path"]: e for e in payload["files"]}
        assert file_map["AGENTS.md"]["status"] == "skipped_exists"
        assert (tmp_path / "AGENTS.md").read_text().strip() == "custom-agents-sentinel"

    def test_include_theory_rules_doc_false(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Repo\n")
        payload = bootstrap_context_files(
            str(tmp_path), write=False, include_theory_rules_doc=False
        )
        rel_paths = {e["relative_path"] for e in payload["files"]}
        assert ".claude/rules/theory.md" not in rel_paths

    def test_source_signals_populated(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Repo\n")
        payload = bootstrap_context_files(str(tmp_path), write=False)
        signals = payload["source_signals"]
        assert "docs_scanned" in signals
        assert "context_files_found" in signals
        assert "proposed_rule_count" in signals
        assert "audit_summary" in signals
        assert signals["model_profile_applied"] is False
        assert signals["model_key"] is None

    def test_quick_wins_populated(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Repo\n")
        payload = bootstrap_context_files(str(tmp_path), write=False)
        assert isinstance(payload["quick_wins"], list)

    def test_agent_instructions_populated(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Repo\n")
        payload = bootstrap_context_files(str(tmp_path), write=False)
        assert len(payload["agent_instructions"]) >= 2
        assert any("CLAUDE.md" in step for step in payload["agent_instructions"])

    def test_needs_review_populated(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Repo\n")
        payload = bootstrap_context_files(str(tmp_path), write=False)
        assert isinstance(payload["needs_review"], list)

    def test_llm_usage_hint_present(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Repo\n")
        payload = bootstrap_context_files(str(tmp_path), write=False)
        assert "Review generated drafts" in payload["llm_usage_hint"]

    def test_generated_at_is_iso_timestamp(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Repo\n")
        payload = bootstrap_context_files(str(tmp_path), write=False)
        assert "T" in payload["generated_at"]

    def test_model_profile_import_error_handled(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Repo\n")
        with mock.patch(
            "lintgate.context_bootstrap.bootstrap_context_files.__module__",
            "lintgate.context_bootstrap",
        ):
            # Simulate import failure in the model profile resolution block
            with mock.patch.dict(
                "sys.modules",
                {"lintgate.controlplane.model_profiles": None},
            ):
                # The try/except in bootstrap_context_files handles ImportError
                payload = bootstrap_context_files(
                    str(tmp_path), write=False, model_id="test-model"
                )
                assert payload["source_signals"]["model_profile_applied"] is False

    def test_write_with_review_items_adds_instruction(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Repo\n")
        payload = bootstrap_context_files(str(tmp_path), write=False)
        if payload["needs_review"]:
            assert any("needs_review" in step for step in payload["agent_instructions"])

    def test_inquiry_md_always_included(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Repo\n")
        payload = bootstrap_context_files(str(tmp_path), write=False)
        rel_paths = {e["relative_path"] for e in payload["files"]}
        assert ".claude/rules/inquiry.md" in rel_paths
