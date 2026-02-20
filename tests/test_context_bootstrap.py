from __future__ import annotations

import json
import re
from unittest import mock

import mcp_server
from lintgate.context_bootstrap import (
    _model_biased_guardrails,
    _select_actionable_anti_patterns,
    bootstrap_context_files,
)


def test_bootstrap_context_files_generates_theory_grounded_drafts(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "demo"',
                'description = "Demo project for context bootstrap tests"',
            ]
        )
        + "\n"
    )
    (tmp_path / "README.md").write_text("# Demo\n\nA compact demo project.\n")
    (tmp_path / "AGENTS.md").write_text(
        "\n".join(
            [
                "DO NOT import pandas",
                "MUST use typed function signatures",
            ]
        )
        + "\n"
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "THEORY.md").write_text(
        "\n".join(
            [
                "# Core Theory",
                "This system uses compositional transformations because they remain inspectable.",
                "",
                "# Problem-Solving Approach",
                "Rather than one-shot generation, iterate with narrow edits and validation.",
                "",
                "# Anti-Patterns",
                "Using black-box helper scripts will undermine correctness over time.",
            ]
        )
        + "\n"
    )

    payload = bootstrap_context_files(str(tmp_path), write=False, overwrite=False)
    file_map = {entry["relative_path"]: entry for entry in payload["files"]}

    assert ".claude/CLAUDE.md" in file_map
    assert "AGENTS.md" in file_map
    assert ".claude/rules/theory.md" in file_map
    assert file_map[".claude/CLAUDE.md"]["status"] == "planned"
    assert "LINTGATE_FORBID_REGEX:" in file_map[".claude/CLAUDE.md"]["content"]
    assert "DO NOT:" in file_map[".claude/CLAUDE.md"]["content"]


def test_bootstrap_write_respects_overwrite_flag(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Repo\n\nExample.\n")
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "CLAUDE.md").write_text("custom-sentinel\n")

    first = bootstrap_context_files(
        str(tmp_path),
        write=True,
        overwrite=False,
        include_theory_rules_doc=False,
    )
    first_map = {entry["relative_path"]: entry for entry in first["files"]}
    assert first_map[".claude/CLAUDE.md"]["status"] == "skipped_exists"
    assert (claude_dir / "CLAUDE.md").read_text().strip() == "custom-sentinel"

    second = bootstrap_context_files(
        str(tmp_path),
        write=True,
        overwrite=True,
        include_theory_rules_doc=False,
    )
    second_map = {entry["relative_path"]: entry for entry in second["files"]}
    assert second_map[".claude/CLAUDE.md"]["status"] == "written"
    assert (claude_dir / "CLAUDE.md").read_text().startswith("# ")


def test_select_actionable_anti_patterns_filters_non_negative_claims() -> None:
    claims = [
        "This project introduces a retrieval stack for clinical context.",
        "Using black-box generated scripts will break auditability.",
        "Avoid one-off task-specific helpers that bypass abstractions.",
    ]
    selected = _select_actionable_anti_patterns(claims, max_items=5)

    assert "retrieval stack" not in " ".join(selected).lower()
    assert any("break auditability" in item.lower() for item in selected)
    assert any("avoid one-off task-specific helpers" in item.lower() for item in selected)


def test_mcp_bootstrap_context_files_returns_payload(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Repo\n\nMinimal description.\n")

    output = mcp_server.bootstrap_context_files(
        path=str(tmp_path),
        write=False,
        include_theory_rules_doc=False,
    )
    payload = json.loads(output)

    rel_paths = {item["relative_path"] for item in payload["files"]}
    assert rel_paths == {".claude/CLAUDE.md", "AGENTS.md", ".claude/rules/inquiry.md"}
    assert payload["source_signals"]["audit_summary"]["files"] >= 0


def test_zero_state_uses_battle_tested_defaults() -> None:
    """When no claims match, fallback should be the curated defaults."""
    result = _select_actionable_anti_patterns([], max_items=5)
    assert len(result) == 5
    # Verify first item is still the approach-cycling one
    assert "4th approach" in result[0]
    # Ensure performance anti-pattern is visible in the first 4 entries.
    assert any("O(n²)" in item for item in result[:4])


def test_zero_state_performance_anti_pattern_appears_in_claude_do_dont(tmp_path) -> None:
    """Zero-state CLAUDE do_dont should include performance anti-pattern guidance."""
    (tmp_path / "README.md").write_text("# Project\n")

    payload = bootstrap_context_files(str(tmp_path), write=False)
    claude_content = ""
    for entry in payload["files"]:
        if entry["relative_path"] == ".claude/CLAUDE.md":
            claude_content = entry["content"]
            break

    do_dont_match = re.search(
        r"<!-- LINTGATE:BEGIN do_dont.*?-->(.*?)<!-- LINTGATE:END do_dont -->",
        claude_content,
        re.DOTALL,
    )
    assert do_dont_match, "do_dont section should exist"
    do_dont_section = do_dont_match.group(1)
    assert "O(n²)" in do_dont_section


def test_extracted_theory_overrides_defaults(tmp_path) -> None:
    """When real claims exist, curated defaults should NOT be used in do_dont section."""
    (tmp_path / "README.md").write_text("# Project\n")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "THEORY.md").write_text(
        "# Anti-Patterns\n\n"
        "Using black-box helper scripts will ruin the design.\n"
        "Never bypass the validation pipeline for speed.\n"
        "Do not create one-off task scripts.\n"
    )

    payload = bootstrap_context_files(str(tmp_path), write=False)
    claude_content = ""
    for entry in payload["files"]:
        if entry["relative_path"] == ".claude/CLAUDE.md":
            claude_content = entry["content"]
            break

    # Extract just the do_dont managed section
    import re

    do_dont_match = re.search(
        r"<!-- LINTGATE:BEGIN do_dont.*?-->(.*?)<!-- LINTGATE:END do_dont -->",
        claude_content,
        re.DOTALL,
    )
    assert do_dont_match, "do_dont section should exist"
    do_dont_section = do_dont_match.group(1)

    # Should contain extracted claims in do_dont section
    assert (
        "black-box" in do_dont_section.lower() or "validation pipeline" in do_dont_section.lower()
    )
    # The curated default about "enumerating all known constraints" should NOT
    # appear in do_dont section when real claims are extracted
    assert "enumerating all known constraints" not in do_dont_section


# ── Phase 6: Model Profile Integration ──────────────────────────────


class TestModelBiasedGuardrails:
    def test_no_profile_returns_empty(self):
        assert _model_biased_guardrails(None) == []

    def test_empty_signal_risk_returns_empty(self):
        assert _model_biased_guardrails({"signal_risk": {}}) == []

    def test_below_threshold_returns_empty(self):
        profile = {"signal_risk": {"approach_cycling": 0.2}}
        assert _model_biased_guardrails(profile, threshold=0.3) == []

    def test_above_threshold_returns_guardrails(self):
        profile = {"signal_risk": {"approach_cycling": 0.5, "verification_debt": 0.4}}
        result = _model_biased_guardrails(profile, threshold=0.3)
        assert len(result) == 2
        assert "approach-cycling" in result[0]
        assert "verification-debt" in result[1]

    def test_max_guardrails_cap(self):
        profile = {
            "signal_risk": {
                "approach_cycling": 0.9,
                "verification_debt": 0.8,
                "premature_action": 0.7,
                "serial_discovery": 0.6,
                "failure_amnesia": 0.5,
            }
        }
        result = _model_biased_guardrails(profile, max_guardrails=3)
        assert len(result) == 3

    def test_ranked_by_risk(self):
        profile = {
            "signal_risk": {
                "verification_debt": 0.9,
                "approach_cycling": 0.4,
            }
        }
        result = _model_biased_guardrails(profile)
        assert "verification-debt" in result[0]
        assert "approach-cycling" in result[1]


class TestBootstrapModelProfileIntegration:
    def test_model_profile_applied_in_source_signals(self, tmp_path):
        """source_signals should report model profile status."""
        (tmp_path / "README.md").write_text("# Repo\n")
        payload = bootstrap_context_files(str(tmp_path), write=False)
        assert payload["source_signals"]["model_profile_applied"] is False
        assert payload["source_signals"]["model_key"] is None

    def test_model_profile_injects_guardrails(self, tmp_path):
        """Usable model profile should inject calibrated guardrails."""
        (tmp_path / "README.md").write_text("# Repo\n")

        from lintgate.controlplane.model_profiles import ModelProfile

        fake_profile = ModelProfile(
            model_key="anthropic:claude-opus-4",
            confidence=0.8,
            signal_risk={"approach_cycling": 0.6, "verification_debt": 0.5},
            custom_anti_patterns=["Do not try a 4th approach without enumerating constraints."],
            custom_dispositions=["MUST run constraint_check before 3rd approach."],
        )

        with (
            mock.patch(
                "lintgate.controlplane.model_profiles.get_profile",
                return_value=fake_profile,
            ),
            mock.patch(
                "lintgate.controlplane.model_profiles.resolve_model_key",
                return_value="anthropic:claude-opus-4",
            ),
        ):
            payload = bootstrap_context_files(
                str(tmp_path),
                write=False,
                model_id="claude-opus-4",
            )

        assert payload["source_signals"]["model_profile_applied"] is True
        assert payload["source_signals"]["model_key"] == "anthropic:claude-opus-4"
        assert payload["source_signals"]["model_profile_confidence"] == 0.8

        claude_content = ""
        for entry in payload["files"]:
            if entry["relative_path"] == ".claude/CLAUDE.md":
                claude_content = entry["content"]
                break

        assert "Model-profile calibrated" in claude_content
        assert "approach-cycling" in claude_content

    def test_model_mismatch_no_profile(self, tmp_path):
        """When model_id resolves but no profile exists, fall back to defaults."""
        (tmp_path / "README.md").write_text("# Repo\n")

        with (
            mock.patch(
                "lintgate.controlplane.model_profiles.get_profile",
                return_value=None,
            ),
            mock.patch(
                "lintgate.controlplane.model_profiles.resolve_model_key",
                return_value="openai:gpt-4o",
            ),
        ):
            payload = bootstrap_context_files(
                str(tmp_path),
                write=False,
                model_id="gpt-4o",
            )

        assert payload["source_signals"]["model_profile_applied"] is False
        claude_content = ""
        for entry in payload["files"]:
            if entry["relative_path"] == ".claude/CLAUDE.md":
                claude_content = entry["content"]
                break
        assert "Model-profile calibrated" not in claude_content

    def test_model_profile_custom_anti_patterns_take_precedence(self, tmp_path):
        """Model-specific anti-patterns replace defaults in do_dont section."""
        (tmp_path / "README.md").write_text("# Repo\n")

        from lintgate.controlplane.model_profiles import ModelProfile

        fake_profile = ModelProfile(
            model_key="anthropic:claude-opus-4",
            confidence=0.8,
            signal_risk={"approach_cycling": 0.6},
            custom_anti_patterns=[
                "Do not cycle through approaches — the model profile reveals high cycling risk.",
            ],
        )

        with (
            mock.patch(
                "lintgate.controlplane.model_profiles.get_profile",
                return_value=fake_profile,
            ),
            mock.patch(
                "lintgate.controlplane.model_profiles.resolve_model_key",
                return_value="anthropic:claude-opus-4",
            ),
        ):
            payload = bootstrap_context_files(
                str(tmp_path),
                write=False,
                model_id="claude-opus-4",
            )

        claude_content = ""
        for entry in payload["files"]:
            if entry["relative_path"] == ".claude/CLAUDE.md":
                claude_content = entry["content"]
                break

        do_dont_match = re.search(
            r"<!-- LINTGATE:BEGIN do_dont.*?-->(.*?)<!-- LINTGATE:END do_dont -->",
            claude_content,
            re.DOTALL,
        )
        assert do_dont_match
        do_dont = do_dont_match.group(1)
        assert "cycling risk" in do_dont

    def test_use_model_profile_false_skips_model(self, tmp_path):
        """use_model_profile=False should ignore model_id entirely."""
        (tmp_path / "README.md").write_text("# Repo\n")

        payload = bootstrap_context_files(
            str(tmp_path),
            write=False,
            model_id="claude-opus-4",
            use_model_profile=False,
        )
        assert payload["source_signals"]["model_profile_applied"] is False
