"""Tests for cross-repo diagnostic fixes.

These tests verify fixes identified by running LintGate across multiple
repos (iphone-recovery, PoT_Experiments) that exposed systematic gaps
in dead path detection, DO NOT directive coverage, and ControlPlane
activation UX.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# ── Dead Path Reference False-Positive Fixes ───────────────────────────


class TestExtractPathRefsFiltering:
    """Verify _extract_path_refs filters out non-path backtick content."""

    def test_shell_command_filtered(self) -> None:
        """Shell commands in backticks should not be treated as paths."""
        from lintgate.context_auditor import _extract_path_refs

        text = "Run `uv run ruff check .` to lint the project."
        refs = _extract_path_refs(text)
        assert not refs, f"Shell command leaked through: {refs}"

    def test_shell_commands_various_prefixes(self) -> None:
        """Common CLI tool prefixes should all be filtered."""
        from lintgate.context_auditor import _extract_path_refs

        commands = [
            "`git status`",
            "`python -m pytest`",
            "`npm run build`",
            "`cargo test`",
            "`pip install -e .`",
            "`docker build .`",
            "`grep -r pattern src/`",
        ]
        text = "\n".join(commands)
        refs = _extract_path_refs(text)
        assert not refs, f"Shell commands leaked through: {refs}"

    def test_huggingface_model_id_filtered(self) -> None:
        """HuggingFace model IDs like meta-llama/Llama-3.1-8B should not be paths."""
        from lintgate.context_auditor import _extract_path_refs

        text = "Use `meta-llama/Llama-3.1-8B` for inference."
        refs = _extract_path_refs(text)
        assert not refs, f"HF model ID leaked through: {refs}"

    def test_huggingface_various_models(self) -> None:
        """Various HuggingFace model ID formats should all be filtered."""
        from lintgate.context_auditor import _extract_path_refs

        text = (
            "Models: `meta-llama/Llama-3.1-8B`, `microsoft/phi-2`, "
            "`google/gemma-7b`, `mistralai/Mixtral-8x7B-v0.1`."
        )
        refs = _extract_path_refs(text)
        assert not refs, f"HF model IDs leaked through: {refs}"

    def test_real_paths_still_extracted(self) -> None:
        """Legitimate file paths should still be detected."""
        from lintgate.context_auditor import _extract_path_refs

        text = "See `src/main.py` for the entry point and `docs/design.md` for architecture."
        refs = _extract_path_refs(text)
        assert "src/main.py" in refs
        assert "docs/design.md" in refs

    def test_dotted_path_with_extension_extracted(self) -> None:
        """Paths with known extensions but no slash should be extracted."""
        from lintgate.context_auditor import _extract_path_refs

        text = "Edit `config.yaml` and `README.md`."
        refs = _extract_path_refs(text)
        assert "config.yaml" in refs
        assert "README.md" in refs

    def test_hf_model_with_extension_not_filtered(self) -> None:
        """A path with / AND a known extension is a real path, not a model ID."""
        from lintgate.context_auditor import _extract_path_refs

        text = "See `models/config.json` for settings."
        refs = _extract_path_refs(text)
        assert "models/config.json" in refs

    def test_nested_path_not_filtered_as_model_id(self) -> None:
        """Paths with multiple slashes should not match HF model ID pattern."""
        from lintgate.context_auditor import _extract_path_refs

        text = "Check `src/lintgate/config.py` for details."
        refs = _extract_path_refs(text)
        assert "src/lintgate/config.py" in refs


class TestFindDeadPathsImprovements:
    """Verify _find_dead_paths handles edge cases from real repos."""

    def test_home_dir_path_expanded(self, tmp_path: Path) -> None:
        """~/... paths should be expanded, not joined with project root."""
        from lintgate.context_auditor import _find_dead_paths

        # Home dir exists, so ~ should expand to something real
        home = os.path.expanduser("~")
        assert os.path.isdir(home)

        # A real home subdir shouldn't be reported as dead
        refs = ["~/"]
        dead = _find_dead_paths(refs, str(tmp_path))
        assert "~/" not in dead

    def test_home_dir_nonexistent_reported(self, tmp_path: Path) -> None:
        """Nonexistent ~/... paths should still be reported as dead."""
        from lintgate.context_auditor import _find_dead_paths

        refs = ["~/definitely_nonexistent_dir_12345"]
        dead = _find_dead_paths(refs, str(tmp_path))
        assert "~/definitely_nonexistent_dir_12345" in dead

    def test_bare_filename_found_in_subdir(self, tmp_path: Path) -> None:
        """Bare filenames like `cpid.py` should be found in src/ subdirectories."""
        from lintgate.context_auditor import _find_dead_paths

        # Create src/package/cpid.py
        pkg = tmp_path / "src" / "mypackage"
        pkg.mkdir(parents=True)
        (pkg / "cpid.py").write_text("# module\n")

        refs = ["cpid.py"]
        dead = _find_dead_paths(refs, str(tmp_path))
        assert "cpid.py" not in dead, "Bare filename in src/ should not be dead"

    def test_bare_filename_truly_missing(self, tmp_path: Path) -> None:
        """Bare filenames that don't exist anywhere should still be dead."""
        from lintgate.context_auditor import _find_dead_paths

        refs = ["nonexistent_module.py"]
        dead = _find_dead_paths(refs, str(tmp_path))
        assert "nonexistent_module.py" in dead

    def test_bare_filename_at_root_not_dead(self, tmp_path: Path) -> None:
        """Bare filenames at project root should work as before."""
        from lintgate.context_auditor import _find_dead_paths

        (tmp_path / "main.py").write_text("# entry\n")
        refs = ["main.py"]
        dead = _find_dead_paths(refs, str(tmp_path))
        assert "main.py" not in dead

    def test_path_with_slash_not_searched_broadly(self, tmp_path: Path) -> None:
        """Paths with / are relative — should NOT trigger broad search."""
        from lintgate.context_auditor import _find_dead_paths

        # Create the file in an unexpected location
        pkg = tmp_path / "src" / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "foo.py").write_text("")

        # Reference with explicit dir path that doesn't exist
        refs = ["lib/foo.py"]
        dead = _find_dead_paths(refs, str(tmp_path))
        assert "lib/foo.py" in dead, "Explicit paths should not fall back to broad search"

    def test_search_depth_limit(self, tmp_path: Path) -> None:
        """Broad search should not descend more than 3 levels."""
        from lintgate.context_auditor import _find_dead_paths

        # Create file 5 levels deep
        deep = tmp_path / "src" / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        (deep / "deep.py").write_text("")

        refs = ["deep.py"]
        dead = _find_dead_paths(refs, str(tmp_path))
        assert "deep.py" in dead, "Files beyond depth 3 should not be found"


# ── DO NOT Directive Coverage Classification ────────────────────────────


class TestDirectiveClassification:
    """Verify _is_regex_enforceable classifies directives correctly."""

    def test_specific_technology_bare_name_not_enforceable(self) -> None:
        """'DO NOT use checkra1n' — bare name without backticks/dots is ambiguous."""
        from lintgate.context_auditor import _is_regex_enforceable

        # Bare single words without syntactic markers (backticks, dots,
        # UPPER_CASE, snake_case) are conservatively classified as
        # non-enforceable since the heuristic can't distinguish them
        # from generic English words.  Users should use backticks:
        # "DO NOT use `checkra1n`" for enforceable directives.
        assert _is_regex_enforceable("DO NOT use checkra1n") is False

    def test_specific_technology_with_backticks_is_enforceable(self) -> None:
        """'DO NOT use `checkra1n`' — backtick-quoted, regex-enforceable."""
        from lintgate.context_auditor import _is_regex_enforceable

        assert _is_regex_enforceable("DO NOT use `checkra1n`") is True

    def test_dotted_import_is_enforceable(self) -> None:
        """'DO NOT import threading.Thread' — dotted name, enforceable."""
        from lintgate.context_auditor import _is_regex_enforceable

        assert _is_regex_enforceable("DO NOT import threading.Thread") is True

    def test_backtick_identifier_is_enforceable(self) -> None:
        """'DO NOT use `shutil.which` directly' — backtick ref, enforceable."""
        from lintgate.context_auditor import _is_regex_enforceable

        assert _is_regex_enforceable("DO NOT use `shutil.which` directly") is True

    def test_upper_case_constant_is_enforceable(self) -> None:
        """'DO NOT use CHECKRA1N_CHIPS' — upper-case constant, enforceable."""
        from lintgate.context_auditor import _is_regex_enforceable

        assert _is_regex_enforceable("DO NOT use CHECKRA1N_CHIPS") is True

    def test_architectural_directive_not_enforceable(self) -> None:
        """'DO NOT bypass shared abstractions' — architectural, not regex."""
        from lintgate.context_auditor import _is_regex_enforceable

        assert (
            _is_regex_enforceable(
                "Do not add task-specific one-off code that bypasses shared abstractions"
            )
            is False
        )

    def test_process_directive_not_enforceable(self) -> None:
        """'DO NOT iterate without understanding' — process, not regex."""
        from lintgate.context_auditor import _is_regex_enforceable

        assert (
            _is_regex_enforceable("Do not iterate without understanding constraints first") is False
        )

    def test_behavioral_directive_not_enforceable(self) -> None:
        """'DO NOT repeat the same approach 4 times' — behavioral."""
        from lintgate.context_auditor import _is_regex_enforceable

        assert _is_regex_enforceable("Do not repeat the same approach 4 times") is False

    def test_vague_directive_not_enforceable(self) -> None:
        """'DO NOT write code that is hard to debug' — too vague for regex."""
        from lintgate.context_auditor import _is_regex_enforceable

        # No specific tech/API cue → not enforceable
        assert _is_regex_enforceable("Do not write code that is hard to debug") is False


class TestRuleCoverageWithClassification:
    """Verify _check_rule_coverage uses directive classification."""

    def test_all_architectural_directives_pass(self) -> None:
        """When all directives are architectural, coverage should pass."""
        from lintgate.context_auditor import _check_rule_coverage

        checks: list[dict] = []
        suggestions: list[str] = []
        guidance = {
            "directives": {
                "do_not": [
                    "Do not bypass shared abstractions",
                    "Do not iterate without understanding constraints",
                    "Do not repeat the same approach",
                ],
            },
        }
        _check_rule_coverage(checks, suggestions, guidance, [], {"min_rule_coverage_pct": 50})
        assert len(checks) == 1
        assert checks[0]["status"] == "pass"
        assert "architectural" in checks[0]["detail"]

    def test_enforceable_without_rules_warns(self) -> None:
        """Enforceable directives without rules should warn."""
        from lintgate.context_auditor import _check_rule_coverage

        checks: list[dict] = []
        suggestions: list[str] = []
        guidance = {
            "directives": {
                "do_not": [
                    "DO NOT use `checkra1n`",  # backtick → enforceable
                    "DO NOT import threading.Thread",  # dotted → enforceable
                ],
            },
        }
        _check_rule_coverage(checks, suggestions, guidance, [], {"min_rule_coverage_pct": 50})
        assert len(checks) == 1
        assert checks[0]["status"] == "warn"
        assert "0/2" in checks[0]["detail"]

    def test_mixed_directives_only_counts_enforceable(self) -> None:
        """Mixed architectural + enforceable: coverage only counts enforceable."""
        from lintgate.context_auditor import _check_rule_coverage

        checks: list[dict] = []
        suggestions: list[str] = []
        guidance = {
            "directives": {
                "do_not": [
                    "DO NOT use `checkra1n`",  # backtick → enforceable
                    "Do not bypass shared abstractions",  # architectural
                    "Do not iterate without understanding",  # architectural
                ],
            },
        }
        existing_rules = [
            {
                "kind": "forbid_regex",
                "pattern": "checkra1n",
                "message": "replaced by gaster",
                "source": "CLAUDE.md",
            }
        ]
        _check_rule_coverage(
            checks, suggestions, guidance, existing_rules, {"min_rule_coverage_pct": 50}
        )
        assert len(checks) == 1
        assert checks[0]["status"] == "pass"
        assert "1/1" in checks[0]["detail"]
        # Should mention architectural count
        assert "2 architectural" in checks[0]["detail"]

    def test_require_regex_counts_as_coverage(self) -> None:
        """require_regex rules should also contribute to coverage."""
        from lintgate.context_auditor import _check_rule_coverage

        checks: list[dict] = []
        suggestions: list[str] = []
        guidance = {
            "directives": {
                "do_not": [
                    "DO NOT write Python files without `from __future__ import annotations`",
                ],
            },
        }
        existing_rules = [
            {
                "kind": "require_regex",
                "pattern": "from __future__ import annotations",
                "message": "all Python files",
                "source": "CLAUDE.md",
            }
        ]
        _check_rule_coverage(
            checks, suggestions, guidance, existing_rules, {"min_rule_coverage_pct": 50}
        )
        assert len(checks) == 1
        # The backtick reference should make this enforceable,
        # and the require_regex should cover it
        assert checks[0]["status"] == "pass"


# ── Bootstrap Quick-Wins and ControlPlane Suggestion ────────────────────


class TestBootstrapQuickWins:
    """Verify bootstrap_context_files surfaces actionable quick-win suggestions."""

    def test_quick_wins_suggests_controlplane(self, tmp_path: Path) -> None:
        """Projects without lintgate.yaml should get ControlPlane suggestion."""
        from lintgate.context_bootstrap import _build_quick_wins

        wins = _build_quick_wins(tmp_path, {"rules": [], "directives": {}}, {})
        assert any("controlplane" in w.lower() for w in wins)

    def test_quick_wins_no_controlplane_when_config_exists(self, tmp_path: Path) -> None:
        """Projects WITH lintgate.yaml should not get ControlPlane suggestion."""
        from lintgate.context_bootstrap import _build_quick_wins

        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")
        wins = _build_quick_wins(tmp_path, {"rules": [], "directives": {}}, {})
        assert not any("controlplane" in w.lower() for w in wins)

    def test_quick_wins_suggests_rule_extraction(self, tmp_path: Path) -> None:
        """Projects with DO NOT directives but no rules should get extraction suggestion."""
        from lintgate.context_bootstrap import _build_quick_wins

        guidance = {
            "rules": [],
            "directives": {"do_not": ["DO NOT use X", "DO NOT import Y"]},
        }
        wins = _build_quick_wins(tmp_path, guidance, {})
        assert any("extract_theory_constraints" in w for w in wins)

    def test_quick_wins_suggests_lockfile(self, tmp_path: Path) -> None:
        """Python projects without lockfile should get lockfile suggestion."""
        from lintgate.context_bootstrap import _build_quick_wins

        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n")
        wins = _build_quick_wins(tmp_path, {"rules": [], "directives": {}}, {})
        assert any("lockfile" in w.lower() or "lock" in w.lower() for w in wins)

    def test_quick_wins_no_lockfile_hint_when_present(self, tmp_path: Path) -> None:
        """Projects with lockfile should not get lockfile suggestion."""
        from lintgate.context_bootstrap import _build_quick_wins

        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n")
        (tmp_path / "uv.lock").write_text("# lock\n")
        wins = _build_quick_wins(tmp_path, {"rules": [], "directives": {}}, {})
        assert not any("lockfile" in w.lower() for w in wins)

    def test_context_map_shows_activation_hint_when_no_yaml(self, tmp_path: Path) -> None:
        """Context map should suggest creating lintgate.yaml when missing."""
        import re

        from lintgate.context_bootstrap import _render_claude_md

        text = _render_claude_md(
            metadata={"name": "test"},
            facet_summaries={},
            anti_patterns=[],
            rule_lines=[],
            project_root=str(tmp_path),
        )
        ctx_match = re.search(
            r"<!-- LINTGATE:BEGIN context_map.*?-->(.+?)<!-- LINTGATE:END context_map -->",
            text,
            re.DOTALL,
        )
        assert ctx_match is not None
        section = ctx_match.group(1)
        assert "not yet created" in section
        assert "controlplane" in section.lower()

    def test_context_map_normal_when_yaml_exists(self, tmp_path: Path) -> None:
        """Context map should reference existing config normally."""
        import re

        from lintgate.context_bootstrap import _render_claude_md

        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "lintgate.yaml").write_text("version: 1\n")
        text = _render_claude_md(
            metadata={"name": "test"},
            facet_summaries={},
            anti_patterns=[],
            rule_lines=[],
            project_root=str(tmp_path),
        )
        ctx_match = re.search(
            r"<!-- LINTGATE:BEGIN context_map.*?-->(.+?)<!-- LINTGATE:END context_map -->",
            text,
            re.DOTALL,
        )
        assert ctx_match is not None
        section = ctx_match.group(1)
        assert "`.claude/lintgate.yaml`" in section
        assert "not yet created" not in section
