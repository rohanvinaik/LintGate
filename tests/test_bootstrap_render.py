"""Tests for bootstrap_render.py rendering functions."""

from __future__ import annotations

import os

from lintgate.bootstrap_defaults import ZERO_STATE_FACET_FALLBACKS
from lintgate.context.bootstrap_render import (
    _GUARDRAIL_MAP,
    _NO_THEORY,
    facet_or_fallback,
    model_biased_guardrails,
    normalize_sentence,
    render_agents_md,
    render_claude_md,
    render_inquiry_md,
    render_theory_rules_md,
)

# ── normalize_sentence ─────────────────────────────────────────────────


class TestNormalizeSentence:
    def test_removes_backticks(self) -> None:
        assert normalize_sentence("use `foo` here") == "use foo here"

    def test_removes_multiple_backtick_spans(self) -> None:
        assert normalize_sentence("`a` and `b`") == "a and b"

    def test_removes_bold_asterisks(self) -> None:
        assert normalize_sentence("**bold** text") == "bold text"

    def test_removes_italic_asterisks(self) -> None:
        assert normalize_sentence("*italic* word") == "italic word"

    def test_collapses_whitespace(self) -> None:
        assert normalize_sentence("hello   world") == "hello world"

    def test_strips_leading_trailing_whitespace(self) -> None:
        assert normalize_sentence("  hello  ") == "hello"

    def test_combined_formatting(self) -> None:
        assert normalize_sentence("  **use**  `foo`   bar  ") == "use foo bar"

    def test_empty_string(self) -> None:
        assert normalize_sentence("") == ""

    def test_no_formatting(self) -> None:
        assert normalize_sentence("plain text") == "plain text"

    def test_backtick_with_special_chars(self) -> None:
        assert normalize_sentence("`a.b()`") == "a.b()"


# ── facet_or_fallback ──────────────────────────────────────────────────


class TestFacetOrFallback:
    def test_returns_value_when_present(self) -> None:
        result = facet_or_fallback({"key": "hello world"}, "key", "fallback")
        assert result == "hello world"

    def test_returns_fallback_when_key_missing(self) -> None:
        result = facet_or_fallback({}, "key", "fallback")
        assert result == "fallback"

    def test_returns_fallback_when_value_empty(self) -> None:
        result = facet_or_fallback({"key": ""}, "key", "fallback")
        assert result == "fallback"

    def test_returns_fallback_when_value_is_whitespace(self) -> None:
        result = facet_or_fallback({"key": "   "}, "key", "fallback")
        assert result == "fallback"

    def test_returns_fallback_when_value_is_no_theory(self) -> None:
        result = facet_or_fallback({"key": _NO_THEORY}, "key", "fallback")
        assert result == "fallback"

    def test_normalizes_returned_value(self) -> None:
        result = facet_or_fallback({"key": "**bold** `code`"}, "key", "fallback")
        assert result == "bold code"

    def test_strips_value_before_checking(self) -> None:
        result = facet_or_fallback({"key": "  valid  "}, "key", "fallback")
        assert result == "valid"

    def test_non_string_value_converted(self) -> None:
        # The function does str() on the value from dict.get()
        result = facet_or_fallback({"key": 42}, "key", "fallback")  # type: ignore[dict-item]
        assert result == "42"

    def test_none_value_uses_fallback(self) -> None:
        # dict.get returns None for missing keys, but str(None) = "None"
        # However the code does str(facet_summaries.get(key, "")), so missing key -> ""
        result = facet_or_fallback({"key": None}, "key", "fallback")  # type: ignore[dict-item]
        # str(None) = "None", which is truthy and not _NO_THEORY
        assert result == "None"


# ── model_biased_guardrails ────────────────────────────────────────────


class TestModelBiasedGuardrails:
    def test_returns_empty_for_none_profile(self) -> None:
        assert model_biased_guardrails(None) == []

    def test_returns_empty_for_empty_profile(self) -> None:
        assert model_biased_guardrails({}) == []

    def test_returns_empty_for_missing_signal_risk(self) -> None:
        assert model_biased_guardrails({"other": "data"}) == []

    def test_returns_empty_for_empty_signal_risk(self) -> None:
        assert model_biased_guardrails({"signal_risk": {}}) == []

    def test_filters_by_threshold(self) -> None:
        profile = {"signal_risk": {"approach_cycling": 0.5, "verification_debt": 0.1}}
        result = model_biased_guardrails(profile, threshold=0.3)
        assert len(result) == 1
        assert result[0] == _GUARDRAIL_MAP["approach_cycling"]

    def test_default_threshold_is_0_3(self) -> None:
        profile = {"signal_risk": {"approach_cycling": 0.3, "verification_debt": 0.29}}
        result = model_biased_guardrails(profile)
        assert len(result) == 1
        assert result[0] == _GUARDRAIL_MAP["approach_cycling"]

    def test_sorts_by_risk_descending(self) -> None:
        profile = {
            "signal_risk": {
                "verification_debt": 0.9,
                "approach_cycling": 0.5,
                "premature_action": 0.7,
            }
        }
        result = model_biased_guardrails(profile)
        assert result[0] == _GUARDRAIL_MAP["verification_debt"]
        assert result[1] == _GUARDRAIL_MAP["premature_action"]
        assert result[2] == _GUARDRAIL_MAP["approach_cycling"]

    def test_max_guardrails_limits_output(self) -> None:
        profile = {
            "signal_risk": {
                "approach_cycling": 0.8,
                "verification_debt": 0.7,
                "premature_action": 0.6,
                "serial_discovery": 0.5,
                "failure_amnesia": 0.4,
            }
        }
        result = model_biased_guardrails(profile, max_guardrails=2)
        assert len(result) == 2

    def test_default_max_guardrails_is_4(self) -> None:
        profile = {
            "signal_risk": {
                "approach_cycling": 0.9,
                "verification_debt": 0.8,
                "premature_action": 0.7,
                "serial_discovery": 0.6,
                "failure_amnesia": 0.5,
                "stale_model": 0.4,
                "tool_repetition": 0.35,
            }
        }
        result = model_biased_guardrails(profile)
        assert len(result) == 4

    def test_unknown_signals_skipped(self) -> None:
        profile = {"signal_risk": {"unknown_signal": 0.9, "approach_cycling": 0.5}}
        result = model_biased_guardrails(profile)
        assert len(result) == 1
        assert result[0] == _GUARDRAIL_MAP["approach_cycling"]

    def test_all_below_threshold_returns_empty(self) -> None:
        profile = {"signal_risk": {"approach_cycling": 0.1, "verification_debt": 0.2}}
        result = model_biased_guardrails(profile, threshold=0.5)
        assert result == []

    def test_custom_threshold_zero_includes_all_known(self) -> None:
        profile = {
            "signal_risk": {
                "approach_cycling": 0.01,
                "verification_debt": 0.02,
            }
        }
        result = model_biased_guardrails(profile, threshold=0.0)
        assert len(result) == 2


# ── render_claude_md ───────────────────────────────────────────────────


class TestRenderClaudeMd:
    def _minimal_kwargs(self, **overrides: object) -> dict:
        defaults: dict = {
            "metadata": {"name": "testproj", "description": "A test project"},
            "facet_summaries": {},
            "anti_patterns": [],
            "rule_lines": [],
            "project_root": "",
            "model_profile": None,
        }
        defaults.update(overrides)
        return defaults

    def test_starts_with_project_name_heading(self) -> None:
        result = render_claude_md(**self._minimal_kwargs())
        assert result.startswith("# testproj Context")

    def test_default_name_is_project(self) -> None:
        result = render_claude_md(**self._minimal_kwargs(metadata={}))
        assert result.startswith("# project Context")

    def test_description_used_as_mission(self) -> None:
        result = render_claude_md(**self._minimal_kwargs())
        assert "A test project. Your job is to write correct code" in result

    def test_mission_with_trailing_period(self) -> None:
        result = render_claude_md(
            **self._minimal_kwargs(metadata={"name": "x", "description": "Ends with period."})
        )
        assert "Ends with period. Your job is to write correct code" in result

    def test_facet_summaries_used_when_provided(self) -> None:
        facets = {
            "core_theory": "Custom core theory",
            "problem_solving": "Custom approach",
            "alignment": "Custom alignment",
            "architecture": "Custom architecture",
        }
        result = render_claude_md(**self._minimal_kwargs(facet_summaries=facets))
        assert "- Core theory: Custom core theory" in result
        assert "- Preferred approach: Custom approach" in result
        assert "- Alignment criteria: Custom alignment" in result
        assert "- Architecture intent: Custom architecture" in result

    def test_fallbacks_used_when_facets_empty(self) -> None:
        result = render_claude_md(**self._minimal_kwargs(facet_summaries={}))
        assert f"- Core theory: {ZERO_STATE_FACET_FALLBACKS['core_theory']}" in result

    def test_anti_patterns_rendered(self) -> None:
        result = render_claude_md(
            **self._minimal_kwargs(anti_patterns=["Don't do X", "Don't do Y"])
        )
        assert "- DO NOT: Don't do X" in result
        assert "- DO NOT: Don't do Y" in result

    def test_anti_patterns_capped_at_4(self) -> None:
        patterns = [f"Pattern {i}" for i in range(10)]
        result = render_claude_md(**self._minimal_kwargs(anti_patterns=patterns))
        assert "- DO NOT: Pattern 0" in result
        assert "- DO NOT: Pattern 3" in result
        assert "- DO NOT: Pattern 4" not in result

    def test_rule_lines_rendered(self) -> None:
        result = render_claude_md(**self._minimal_kwargs(rule_lines=["LINTGATE_FORBID_REGEX: foo"]))
        assert "LINTGATE_FORBID_REGEX: foo" in result

    def test_empty_rule_lines_shows_placeholder(self) -> None:
        result = render_claude_md(**self._minimal_kwargs(rule_lines=[]))
        assert "# Add project-specific constraints" in result

    def test_contains_managed_sections(self) -> None:
        result = render_claude_md(**self._minimal_kwargs())
        assert "<!-- LINTGATE:BEGIN theory_alignment v1 -->" in result
        assert "<!-- LINTGATE:END theory_alignment -->" in result
        assert "<!-- LINTGATE:BEGIN do_dont v1 -->" in result
        assert "<!-- LINTGATE:END do_dont -->" in result
        assert "<!-- LINTGATE:BEGIN machine_rules v1 -->" in result
        assert "<!-- LINTGATE:END machine_rules -->" in result
        assert "<!-- LINTGATE:BEGIN context_map v1 -->" in result
        assert "<!-- LINTGATE:END context_map -->" in result

    def test_context_map_no_lintgate_yaml(self) -> None:
        result = render_claude_md(**self._minimal_kwargs(project_root=""))
        assert "**not yet created**" in result

    def test_context_map_with_root_lintgate_yaml(self, tmp_path: object) -> None:
        root = str(tmp_path)
        # Create lintgate.yaml at root
        with open(os.path.join(root, "lintgate.yaml"), "w") as f:
            f.write("controlplane:\n  enabled: true\n")
        result = render_claude_md(**self._minimal_kwargs(project_root=root))
        assert "- `lintgate.yaml` - lint and ControlPlane configuration." in result
        assert "**not yet created**" not in result

    def test_context_map_with_claude_dir_lintgate_yaml(self, tmp_path: object) -> None:
        root = str(tmp_path)
        claude_dir = os.path.join(root, ".claude")
        os.makedirs(claude_dir)
        with open(os.path.join(claude_dir, "lintgate.yaml"), "w") as f:
            f.write("controlplane:\n  enabled: true\n")
        result = render_claude_md(**self._minimal_kwargs(project_root=root))
        assert "- `.claude/lintgate.yaml` - lint and ControlPlane configuration." in result
        assert "**not yet created**" not in result

    def test_model_guardrails_injected(self) -> None:
        profile = {"signal_risk": {"approach_cycling": 0.8}}
        result = render_claude_md(**self._minimal_kwargs(model_profile=profile))
        assert "<!-- Model-profile calibrated guardrails -->" in result
        assert "approach-cycling risk" in result

    def test_no_model_guardrails_when_profile_none(self) -> None:
        result = render_claude_md(**self._minimal_kwargs(model_profile=None))
        assert "<!-- Model-profile calibrated guardrails -->" not in result

    def test_no_model_guardrails_when_all_below_threshold(self) -> None:
        profile = {"signal_risk": {"approach_cycling": 0.1}}
        result = render_claude_md(**self._minimal_kwargs(model_profile=profile))
        assert "<!-- Model-profile calibrated guardrails -->" not in result

    def test_contains_debt_tracking_policy(self) -> None:
        result = render_claude_md(**self._minimal_kwargs())
        assert "## Debt Tracking Policy" in result

    def test_contains_deep_reference(self) -> None:
        result = render_claude_md(**self._minimal_kwargs())
        assert "## Deep Reference" in result
        assert "`.claude/rules/inquiry.md`" in result

    def test_contains_epistemic_state_section(self) -> None:
        result = render_claude_md(**self._minimal_kwargs())
        assert "## Know Your Epistemic State" in result

    def test_contains_dispositions_section(self) -> None:
        result = render_claude_md(**self._minimal_kwargs())
        assert "## Dispositions" in result

    def test_contains_guardrails_section(self) -> None:
        result = render_claude_md(**self._minimal_kwargs())
        assert "## Guardrails" in result
        assert "DO NOT disable lint channels" in result

    def test_do_lines_use_approach_and_alignment(self) -> None:
        facets = {"problem_solving": "Use TDD", "alignment": "Keep it simple"}
        result = render_claude_md(**self._minimal_kwargs(facet_summaries=facets))
        assert "- DO: Use TDD" in result
        assert "- DO: Keep it simple" in result

    def test_result_is_stripped(self) -> None:
        result = render_claude_md(**self._minimal_kwargs())
        assert result == result.strip()

    def test_empty_description_uses_core_theory(self) -> None:
        result = render_claude_md(
            **self._minimal_kwargs(
                metadata={"name": "proj", "description": ""},
                facet_summaries={"core_theory": "Core insight"},
            )
        )
        assert "Core insight" in result.split("## Know Your Epistemic State")[0]


# ── render_agents_md ───────────────────────────────────────────────────


class TestRenderAgentsMd:
    def _minimal_kwargs(self, **overrides: object) -> dict:
        defaults: dict = {
            "metadata": {"name": "testproj"},
            "facet_summaries": {},
            "commands": [],
        }
        defaults.update(overrides)
        return defaults

    def test_starts_with_heading(self) -> None:
        result = render_agents_md(**self._minimal_kwargs())
        assert result.startswith("# AGENTS.md")

    def test_scope_uses_project_name(self) -> None:
        result = render_agents_md(**self._minimal_kwargs())
        assert "`testproj`" in result

    def test_default_name_is_project(self) -> None:
        result = render_agents_md(**self._minimal_kwargs(metadata={}))
        assert "`project`" in result

    def test_commands_rendered(self) -> None:
        result = render_agents_md(**self._minimal_kwargs(commands=["pytest", "ruff check ."]))
        assert "- `pytest`" in result
        assert "- `ruff check .`" in result

    def test_empty_commands(self) -> None:
        result = render_agents_md(**self._minimal_kwargs(commands=[]))
        # Should still have the validation heading but no command bullets
        assert "## Required Validation" in result

    def test_alignment_facet_used(self) -> None:
        result = render_agents_md(
            **self._minimal_kwargs(facet_summaries={"alignment": "Custom alignment"})
        )
        assert "Custom alignment" in result

    def test_alignment_fallback(self) -> None:
        result = render_agents_md(**self._minimal_kwargs(facet_summaries={}))
        assert "correct, maintainable, and aligned" in result

    def test_contains_execution_contract(self) -> None:
        result = render_agents_md(**self._minimal_kwargs())
        assert "## Execution Contract" in result
        assert "Prefer minimal diffs" in result

    def test_contains_theory_and_context(self) -> None:
        result = render_agents_md(**self._minimal_kwargs())
        assert "## Theory and Context" in result

    def test_contains_handoff_expectations(self) -> None:
        result = render_agents_md(**self._minimal_kwargs())
        assert "## Handoff Expectations" in result

    def test_result_is_stripped(self) -> None:
        result = render_agents_md(**self._minimal_kwargs())
        assert result == result.strip()


# ── render_theory_rules_md ─────────────────────────────────────────────


class TestRenderTheoryRulesMd:
    def _minimal_kwargs(self, **overrides: object) -> dict:
        defaults: dict = {
            "metadata": {"name": "testproj"},
            "theory_pack": {"facet_summaries": {}},
            "theory_full": {},
            "anti_patterns": [],
            "rule_lines": [],
        }
        defaults.update(overrides)
        return defaults

    def test_starts_with_frontmatter(self) -> None:
        result = render_theory_rules_md(**self._minimal_kwargs())
        assert result.startswith("---\npaths:\n")

    def test_frontmatter_targets_python_files(self) -> None:
        result = render_theory_rules_md(**self._minimal_kwargs())
        assert '"**/*.py"' in result

    def test_heading(self) -> None:
        result = render_theory_rules_md(**self._minimal_kwargs())
        assert "# Theory Rules" in result

    def test_project_name_in_description(self) -> None:
        result = render_theory_rules_md(**self._minimal_kwargs())
        assert "`testproj`" in result

    def test_facet_summaries_rendered(self) -> None:
        theory_pack = {
            "facet_summaries": {
                "core_theory": "The core",
                "problem_solving": "The approach",
                "alignment": "The alignment",
                "architecture": "The arch",
                "anti_patterns": "The patterns",
                "abstractions": "The abstractions",
            }
        }
        result = render_theory_rules_md(**self._minimal_kwargs(theory_pack=theory_pack))
        assert "- Core Theory: The core" in result
        assert "- Problem-Solving: The approach" in result
        assert "- Alignment: The alignment" in result
        assert "- Architecture: The arch" in result
        assert "- Anti-Patterns: The patterns" in result
        assert "- Key Abstractions: The abstractions" in result

    def test_facet_fallback_for_missing(self) -> None:
        result = render_theory_rules_md(**self._minimal_kwargs())
        assert "No strong signal extracted for this facet yet." in result

    def test_anti_patterns_rendered(self) -> None:
        result = render_theory_rules_md(
            **self._minimal_kwargs(anti_patterns=["Bad pattern 1", "Bad pattern 2"])
        )
        assert "- Bad pattern 1" in result
        assert "- Bad pattern 2" in result

    def test_rule_lines_rendered(self) -> None:
        result = render_theory_rules_md(
            **self._minimal_kwargs(rule_lines=["FORBID: foo", "REQUIRE: bar"])
        )
        assert "- `FORBID: foo`" in result
        assert "- `REQUIRE: bar`" in result

    def test_empty_rule_lines_shows_placeholder(self) -> None:
        result = render_theory_rules_md(**self._minimal_kwargs(rule_lines=[]))
        assert "- No enforceable rules extracted yet." in result

    def test_extraction_quality_section(self) -> None:
        theory_full = {
            "docs_scanned": 10,
            "validity": {
                "status": "valid",
                "total_claims": 50,
                "missing_required_facets": [],
                "warnings": [],
            },
        }
        result = render_theory_rules_md(**self._minimal_kwargs(theory_full=theory_full))
        assert "- Validity status: valid" in result
        assert "- Docs scanned: 10" in result
        assert "- Total claims: 50" in result
        assert "- Missing required facets: none" in result

    def test_missing_facets_listed(self) -> None:
        theory_full = {
            "validity": {
                "status": "partial",
                "missing_required_facets": ["core_theory", "alignment"],
            }
        }
        result = render_theory_rules_md(**self._minimal_kwargs(theory_full=theory_full))
        assert "core_theory, alignment" in result

    def test_warnings_rendered_up_to_3(self) -> None:
        theory_full = {
            "validity": {
                "warnings": ["warn1", "warn2", "warn3", "warn4"],
            }
        }
        result = render_theory_rules_md(**self._minimal_kwargs(theory_full=theory_full))
        assert "- Warning: warn1" in result
        assert "- Warning: warn2" in result
        assert "- Warning: warn3" in result
        assert "warn4" not in result

    def test_warnings_normalized(self) -> None:
        theory_full = {
            "validity": {
                "warnings": ["**bold** `code` warning"],
            }
        }
        result = render_theory_rules_md(**self._minimal_kwargs(theory_full=theory_full))
        assert "- Warning: bold code warning" in result

    def test_empty_theory_full(self) -> None:
        result = render_theory_rules_md(**self._minimal_kwargs(theory_full={}))
        assert "- Validity status: unknown" in result
        assert "- Docs scanned: 0" in result
        assert "- Total claims: 0" in result

    def test_result_is_stripped(self) -> None:
        result = render_theory_rules_md(**self._minimal_kwargs())
        assert result == result.strip()


# ── render_inquiry_md ──────────────────────────────────────────────────


class TestRenderInquiryMd:
    def test_returns_nonempty_string(self) -> None:
        result = render_inquiry_md()
        assert len(result) > 0

    def test_starts_with_heading(self) -> None:
        result = render_inquiry_md()
        assert result.startswith("# Architecture of Inquiry")

    def test_contains_all_five_features(self) -> None:
        result = render_inquiry_md()
        assert "### theory_grounded_signals" in result
        assert "### prediction_tracking" in result
        assert "### theory_coherence_check" in result
        assert "### living_context" in result
        assert "### session_gate" in result

    def test_contains_enabling_section(self) -> None:
        result = render_inquiry_md()
        assert "## Enabling" in result
        assert "controlplane:" in result

    def test_contains_yaml_config(self) -> None:
        result = render_inquiry_md()
        assert "theory_grounded_signals: true" in result
        assert "prediction_tracking: true" in result
        assert "theory_coherence_check: true" in result
        assert "living_context: true" in result
        assert "session_gate: true" in result

    def test_idempotent(self) -> None:
        assert render_inquiry_md() == render_inquiry_md()

    def test_mentions_graceful_degradation(self) -> None:
        result = render_inquiry_md()
        assert "degrades gracefully" in result
