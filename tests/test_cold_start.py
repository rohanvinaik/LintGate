"""Tests for cold-start usability improvements.

Verifies that tool docstrings, bootstrap payloads, and status outputs
include the guidance a cold agent needs to use LintGate effectively.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


# ── _MCP_INSTRUCTIONS ─────────────────────────────────────────────────


class TestMCPInstructions:
    """The global _MCP_INSTRUCTIONS string guides cold agents."""

    def test_mentions_essential_tools(self) -> None:
        """All essential tools named in instructions."""
        from mcp_server import _MCP_INSTRUCTIONS

        for tool in [
            "lint_files",
            "lint_project",
            "lint_fix",
            "controlplane_run",
            "bootstrap_context_files",
            "controlplane_get_details",
            "getting_started",
        ]:
            assert tool in _MCP_INSTRUCTIONS, f"Missing tool: {tool}"

    def test_no_unexplained_jargon(self) -> None:
        """Instructions avoid LintGate-specific jargon."""
        from mcp_server import _MCP_INSTRUCTIONS

        for jargon in [
            "theory pack",
            "supervision mesh",
            "coherence diagnosis",
            "facet summaries",
            "Architecture of Inquiry",
        ]:
            assert jargon.lower() not in _MCP_INSTRUCTIONS.lower(), f"Jargon found: {jargon}"

    def test_includes_workflow_guidance(self) -> None:
        from mcp_server import _MCP_INSTRUCTIONS

        assert "first session" in _MCP_INSTRUCTIONS.lower()

    def test_mentions_next_actions(self) -> None:
        from mcp_server import _MCP_INSTRUCTIONS

        assert "next_actions" in _MCP_INSTRUCTIONS

    def test_mentions_tool_count(self) -> None:
        from mcp_server import _MCP_INSTRUCTIONS

        assert "35" in _MCP_INSTRUCTIONS


# ── Essential Tool Docstrings ──────────────────────────────────────────


class TestEssentialToolDocstrings:
    """Essential tool docstrings include WHEN TO USE guidance and call examples."""

    def test_lint_files_has_when_and_example(self) -> None:
        from mcp_server import lint_files

        assert "WHEN TO USE" in lint_files.__doc__
        assert "Example:" in lint_files.__doc__

    def test_lint_project_has_when_and_example(self) -> None:
        from mcp_server import lint_project

        assert "WHEN TO USE" in lint_project.__doc__
        assert "Example:" in lint_project.__doc__

    def test_lint_fix_has_when_and_example(self) -> None:
        from mcp_server import lint_fix

        assert "WHEN TO USE" in lint_fix.__doc__
        assert "Example:" in lint_fix.__doc__

    def test_controlplane_run_has_when_and_example(self) -> None:
        from mcp_server import controlplane_run

        assert "WHEN TO USE" in controlplane_run.__doc__
        assert "Example:" in controlplane_run.__doc__

    def test_controlplane_run_mentions_no_config_needed(self) -> None:
        """Agent should know it works without config."""
        from mcp_server import controlplane_run

        doc = controlplane_run.__doc__.lower()
        assert "without" in doc and "config" in doc

    def test_controlplane_run_mentions_get_details(self) -> None:
        from mcp_server import controlplane_run

        assert "controlplane_get_details" in controlplane_run.__doc__

    def test_controlplane_get_details_has_when_and_example(self) -> None:
        from mcp_server import controlplane_get_details

        assert "WHEN TO USE" in controlplane_get_details.__doc__
        assert "Example:" in controlplane_get_details.__doc__

    def test_bootstrap_has_when_and_example(self) -> None:
        from mcp_server import bootstrap_context_files

        assert "WHEN TO USE" in bootstrap_context_files.__doc__
        assert "Example:" in bootstrap_context_files.__doc__

    def test_bootstrap_mentions_needs_review(self) -> None:
        from mcp_server import bootstrap_context_files

        assert "needs_review" in bootstrap_context_files.__doc__

    def test_bootstrap_mentions_quick_wins(self) -> None:
        from mcp_server import bootstrap_context_files

        assert "quick_wins" in bootstrap_context_files.__doc__

    def test_bootstrap_mentions_agent_instructions(self) -> None:
        from mcp_server import bootstrap_context_files

        assert "agent_instructions" in bootstrap_context_files.__doc__


# ── getting_started tool ───────────────────────────────────────────────


class TestGettingStarted:
    """The getting_started tool is the robust cold-start entry point."""

    def test_exists_as_mcp_tool(self) -> None:
        from mcp_server import getting_started

        assert callable(getting_started)

    def test_returns_essential_tools(self, tmp_path: Path) -> None:
        import json

        from mcp_server import getting_started

        result = json.loads(getting_started(str(tmp_path)))
        assert "essential_tools" in result
        for tool in [
            "lint_files",
            "lint_project",
            "lint_fix",
            "controlplane_run",
            "controlplane_get_details",
            "bootstrap_context_files",
        ]:
            assert tool in result["essential_tools"], f"Missing: {tool}"

    def test_returns_first_session_workflow(self, tmp_path: Path) -> None:
        import json

        from mcp_server import getting_started

        result = json.loads(getting_started(str(tmp_path)))
        assert "first_session_workflow" in result
        assert isinstance(result["first_session_workflow"], list)
        assert len(result["first_session_workflow"]) >= 3

    def test_returns_onboarding_status(self, tmp_path: Path) -> None:
        import json

        from mcp_server import getting_started

        result = json.loads(getting_started(str(tmp_path)))
        assert "config_status" in result
        cs = result["config_status"]
        # Machine-readable flags
        assert "config_found" in cs
        assert "config_state" in cs
        assert "controlplane_enabled" in cs
        assert "automatic_hook_active" in cs
        assert "config_path_checked" in cs

    def test_returns_tool_count(self, tmp_path: Path) -> None:
        import json

        from mcp_server import getting_started

        result = json.loads(getting_started(str(tmp_path)))
        assert result["all_tools_count"] == 35

    def test_returns_next_actions(self, tmp_path: Path) -> None:
        import json

        from mcp_server import getting_started

        result = json.loads(getting_started(str(tmp_path)))
        assert "next_actions" in result
        assert isinstance(result["next_actions"], list)
        assert len(result["next_actions"]) >= 1


# ── _build_onboarding_status helper ────────────────────────────────────


class TestOnboardingStatusHelper:
    """The reusable onboarding helper distinguishes config states cleanly."""

    def test_no_config_state(self, tmp_path: Path) -> None:
        from mcp_server import _build_onboarding_status

        status = _build_onboarding_status(str(tmp_path))
        assert status["config_state"] == "no_config"
        assert status["config_found"] is False
        assert status["controlplane_enabled"] is False
        assert status["automatic_hook_active"] is False
        assert status["using_default_config"] is True
        assert "setup_hint" in status

    def test_config_disabled_state(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: false\n")
        from mcp_server import _build_onboarding_status

        status = _build_onboarding_status(str(tmp_path))
        assert status["config_state"] == "config_disabled"
        assert status["config_found"] is True
        assert status["controlplane_enabled"] is False
        assert status["automatic_hook_active"] is False
        assert status["using_default_config"] is False
        assert "setup_hint" in status

    def test_config_without_controlplane_section_state(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text("linters:\n  ruff_check:\n    enabled: true\n")
        from mcp_server import _build_onboarding_status

        status = _build_onboarding_status(str(tmp_path))
        assert status["config_state"] == "config_no_controlplane_section"
        assert status["config_found"] is True
        assert status["controlplane_enabled"] is False
        assert status["automatic_hook_active"] is False
        assert status["using_default_config"] is True
        assert "setup_hint" in status

    def test_config_enabled_state(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")
        from mcp_server import _build_onboarding_status

        status = _build_onboarding_status(str(tmp_path))
        assert status["config_state"] == "config_enabled"
        assert status["config_found"] is True
        assert status["controlplane_enabled"] is True
        assert status["automatic_hook_active"] is True
        assert status["using_default_config"] is False
        assert "setup_hint" not in status

    def test_machine_flags_always_present(self, tmp_path: Path) -> None:
        """All machine-readable flags exist regardless of state."""
        from mcp_server import _build_onboarding_status

        status = _build_onboarding_status(str(tmp_path))
        required_keys = {
            "config_found",
            "config_path_checked",
            "controlplane_enabled",
            "automatic_hook_active",
            "using_default_config",
            "config_state",
        }
        assert required_keys.issubset(status.keys())

    def test_config_path_is_consistent(self, tmp_path: Path) -> None:
        """Config path always points to .claude/lintgate.yaml."""
        from mcp_server import _build_onboarding_status

        status = _build_onboarding_status(str(tmp_path))
        assert status["config_path_checked"].endswith(".claude/lintgate.yaml")


# ── Bootstrap agent_instructions ────────────────────────────────────────


class TestBootstrapAgentInstructions:
    """Bootstrap payload includes agent_instructions for workflow guidance."""

    def test_agent_instructions_in_payload(self, tmp_path: Path) -> None:
        from lintgate.context_bootstrap import bootstrap_context_files

        result = bootstrap_context_files(str(tmp_path))
        assert "agent_instructions" in result
        assert isinstance(result["agent_instructions"], list)
        assert len(result["agent_instructions"]) >= 2

    def test_agent_instructions_mention_claude_md_by_path(self, tmp_path: Path) -> None:
        from lintgate.context_bootstrap import bootstrap_context_files

        result = bootstrap_context_files(str(tmp_path))
        text = " ".join(result["agent_instructions"])
        assert "CLAUDE.md" in text

    def test_agent_instructions_mention_write_true(self, tmp_path: Path) -> None:
        from lintgate.context_bootstrap import bootstrap_context_files

        result = bootstrap_context_files(str(tmp_path))
        text = " ".join(result["agent_instructions"])
        assert "write=true" in text.lower() or "write=True" in text

    def test_agent_instructions_mention_controlplane(self, tmp_path: Path) -> None:
        from lintgate.context_bootstrap import bootstrap_context_files

        result = bootstrap_context_files(str(tmp_path))
        text = " ".join(result["agent_instructions"])
        assert "controlplane_run" in text

    def test_agent_instructions_robust_to_file_order(self, tmp_path: Path) -> None:
        """Instructions find CLAUDE.md by relative_path search, not position."""
        from lintgate.context_bootstrap import bootstrap_context_files

        result = bootstrap_context_files(str(tmp_path))
        # Even if file_reports order changes, CLAUDE.md is found by search
        text = " ".join(result["agent_instructions"])
        assert "CLAUDE.md" in text


# ── controlplane_run onboarding ─────────────────────────────────────────


class TestControlPlaneRunOnboarding:
    """controlplane_run includes onboarding when using default config."""

    def test_onboarding_present_no_config(self, tmp_path: Path) -> None:
        import json

        from mcp_server import controlplane_run

        result = json.loads(controlplane_run(str(tmp_path)))
        assert "onboarding" in result
        assert result["onboarding"]["config_state"] == "no_config"
        assert "setup_hint" in result["onboarding"]

    def test_no_onboarding_when_config_enabled(self, tmp_path: Path) -> None:
        import json

        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")
        from mcp_server import controlplane_run

        result = json.loads(controlplane_run(str(tmp_path)))
        assert "onboarding" not in result

    def test_onboarding_present_when_config_disabled(self, tmp_path: Path) -> None:
        import json

        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: false\n")
        from mcp_server import controlplane_run

        result = json.loads(controlplane_run(str(tmp_path)))
        assert "onboarding" in result
        assert result["onboarding"]["config_state"] == "config_disabled"


# ── controlplane_status backward compat ─────────────────────────────────


class TestControlPlaneStatusBackwardCompat:
    """controlplane_status preserves old keys and adds onboarding."""

    def test_preserves_old_keys_when_no_config(self, tmp_path: Path) -> None:
        import json

        from mcp_server import controlplane_status

        result = json.loads(controlplane_status(str(tmp_path)))
        # Old keys must remain
        assert "controlplane_enabled" in result
        assert "note" in result
        assert "available_channels" in result
        # New key added
        assert "onboarding" in result

    def test_preserves_old_keys_when_config_disabled(self, tmp_path: Path) -> None:
        import json

        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: false\n")
        from mcp_server import controlplane_status

        result = json.loads(controlplane_status(str(tmp_path)))
        assert "controlplane_enabled" in result
        assert result["controlplane_enabled"] is False
        assert "onboarding" in result
        assert result["onboarding"]["config_state"] == "config_disabled"

    def test_no_onboarding_when_fully_configured(self, tmp_path: Path) -> None:
        import json

        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")
        from mcp_server import controlplane_status

        result = json.loads(controlplane_status(str(tmp_path)))
        assert result["controlplane_enabled"] is True
        assert "onboarding" not in result


# ── lint_status onboarding ──────────────────────────────────────────────


class TestLintStatusOnboarding:
    """lint_status includes onboarding when ControlPlane is not fully configured."""

    def test_onboarding_when_no_controlplane(self, tmp_path: Path) -> None:
        import json

        from mcp_server import lint_status

        result = json.loads(lint_status(str(tmp_path)))
        assert "onboarding" in result
        assert result["onboarding"]["config_state"] == "no_config"

    def test_no_onboarding_when_fully_configured(self, tmp_path: Path) -> None:
        import json

        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")
        from mcp_server import lint_status

        result = json.loads(lint_status(str(tmp_path)))
        assert "onboarding" not in result


# ── Deglosified docstrings ──────────────────────────────────────────────


class TestDeglosifiedDocstrings:
    """Secondary tool docstrings use plain language, not jargon."""

    def test_build_theory_pack_plain_language(self) -> None:
        from mcp_server import build_theory_pack

        doc = build_theory_pack.__doc__
        # Jargon removed
        assert "theory digest" not in doc.lower()
        assert "context injection" not in doc.lower()
        # Plain language present
        assert "summary" in doc.lower() or "principles" in doc.lower()

    def test_extract_project_theory_plain_language(self) -> None:
        from mcp_server import extract_project_theory

        doc = extract_project_theory.__doc__
        assert "theoretical framework" not in doc.lower()
        assert "conceptual space" not in doc.lower()
        assert "principles" in doc.lower() or "documented" in doc.lower()

    def test_get_theory_context_plain_language(self) -> None:
        from mcp_server import get_theory_context

        doc = get_theory_context.__doc__
        assert "tier 2" not in doc.lower()
        assert "theory claims" not in doc.lower()
        assert "principles" in doc.lower() or "documented" in doc.lower()

    def test_behavior_precheck_has_example(self) -> None:
        from mcp_server import behavior_precheck

        assert "Example:" in behavior_precheck.__doc__

    def test_context_patch_review_plain_language(self) -> None:
        from mcp_server import context_patch_review

        doc = context_patch_review.__doc__
        assert "auto-managed" in doc.lower() or "updates" in doc.lower()

    def test_global_memory_status_plain_language(self) -> None:
        from mcp_server import global_memory_status

        doc = global_memory_status.__doc__
        assert "signal priors" not in doc.lower()
        assert "session" in doc.lower() or "behavioral" in doc.lower()
