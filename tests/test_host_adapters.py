"""Tests for host adapter protocol and registry extensions."""

from __future__ import annotations

from unittest.mock import MagicMock

from lintgate.renderers import RendererRegistry, build_default_registry
from lintgate.renderers.host_adapter import (
    AIDER_CAPABILITIES,
    CLAUDE_CAPABILITIES,
    CLINE_CAPABILITIES,
    COPILOT_CAPABILITIES,
    CURSOR_CAPABILITIES,
    MCP_ONLY_CAPABILITIES,
    WINDSURF_CAPABILITIES,
    HostCapabilities,
)

# ── HostCapabilities ─────────────────────────────────────────────────


class TestHostCapabilities:
    def test_defaults(self):
        cap = HostCapabilities()
        assert cap.supports_rules is False
        assert cap.supports_hooks is False
        assert cap.supports_mcp is False
        assert cap.rule_file_extension == ".md"

    def test_claude_capabilities(self):
        assert CLAUDE_CAPABILITIES.supports_rules is True
        assert CLAUDE_CAPABILITIES.supports_hooks is True
        assert CLAUDE_CAPABILITIES.supports_mcp is True
        assert CLAUDE_CAPABILITIES.supports_frontmatter is True

    def test_cursor_capabilities(self):
        assert CURSOR_CAPABILITIES.supports_rules is True
        assert CURSOR_CAPABILITIES.supports_hooks is False
        assert CURSOR_CAPABILITIES.supports_mcp is True
        assert CURSOR_CAPABILITIES.rule_file_extension == ".mdc"

    def test_copilot_no_rules(self):
        assert COPILOT_CAPABILITIES.supports_rules is False
        assert COPILOT_CAPABILITIES.supports_mcp is False

    def test_windsurf_rules_and_mcp(self):
        assert WINDSURF_CAPABILITIES.supports_rules is True
        assert WINDSURF_CAPABILITIES.supports_mcp is True
        assert WINDSURF_CAPABILITIES.supports_hooks is False

    def test_cline_rules_and_mcp(self):
        assert CLINE_CAPABILITIES.supports_rules is True
        assert CLINE_CAPABILITIES.supports_mcp is True

    def test_aider_static_only(self):
        assert AIDER_CAPABILITIES.supports_rules is False
        assert AIDER_CAPABILITIES.supports_mcp is False

    def test_mcp_only_preset(self):
        assert MCP_ONLY_CAPABILITIES.supports_rules is False
        assert MCP_ONLY_CAPABILITIES.supports_mcp is True


# ── detect_host ──────────────────────────────────────────────────────


class TestDetectHost:
    def test_detect_claude_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE", "1")
        registry = RendererRegistry()
        assert registry.detect_host(str(tmp_path)) == "claude"

    def test_detect_cursor_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CURSOR_SESSION_ID", "abc")
        monkeypatch.delenv("CLAUDE_CODE", raising=False)
        registry = RendererRegistry()
        assert registry.detect_host(str(tmp_path)) == "cursor"

    def test_detect_claude_dir(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE", raising=False)
        monkeypatch.delenv("CURSOR_SESSION_ID", raising=False)
        monkeypatch.delenv("WINDSURF_SESSION", raising=False)
        (tmp_path / ".claude").mkdir()
        registry = RendererRegistry()
        assert registry.detect_host(str(tmp_path)) == "claude"

    def test_detect_cursor_dir(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE", raising=False)
        monkeypatch.delenv("CURSOR_SESSION_ID", raising=False)
        monkeypatch.delenv("WINDSURF_SESSION", raising=False)
        (tmp_path / ".cursor").mkdir()
        registry = RendererRegistry()
        assert registry.detect_host(str(tmp_path)) == "cursor"

    def test_detect_windsurf_dir(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE", raising=False)
        monkeypatch.delenv("CURSOR_SESSION_ID", raising=False)
        monkeypatch.delenv("WINDSURF_SESSION", raising=False)
        (tmp_path / ".windsurf").mkdir()
        registry = RendererRegistry()
        assert registry.detect_host(str(tmp_path)) == "windsurf"

    def test_detect_cline_dir(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE", raising=False)
        monkeypatch.delenv("CURSOR_SESSION_ID", raising=False)
        monkeypatch.delenv("WINDSURF_SESSION", raising=False)
        (tmp_path / ".clinerules").mkdir()
        registry = RendererRegistry()
        assert registry.detect_host(str(tmp_path)) == "cline"

    def test_detect_none(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE", raising=False)
        monkeypatch.delenv("CURSOR_SESSION_ID", raising=False)
        monkeypatch.delenv("WINDSURF_SESSION", raising=False)
        registry = RendererRegistry()
        assert registry.detect_host(str(tmp_path)) is None

    def test_env_takes_precedence_over_dir(self, tmp_path, monkeypatch):
        """Env var detection wins over directory detection."""
        monkeypatch.setenv("CLAUDE_CODE", "1")
        (tmp_path / ".cursor").mkdir()  # cursor dir present but Claude env set
        registry = RendererRegistry()
        assert registry.detect_host(str(tmp_path)) == "claude"


class TestDetectRuntimeHosts:
    def test_detect_multiple_host_dirs(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE", raising=False)
        monkeypatch.delenv("CURSOR_SESSION_ID", raising=False)
        monkeypatch.delenv("WINDSURF_SESSION", raising=False)
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".cursor").mkdir()
        registry = RendererRegistry()
        assert registry.detect_runtime_hosts(str(tmp_path)) == ["claude", "cursor"]

    def test_detect_runtime_hosts_env_takes_precedence(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CURSOR_SESSION_ID", "abc")
        (tmp_path / ".claude").mkdir()
        registry = RendererRegistry()
        assert registry.detect_runtime_hosts(str(tmp_path)) == ["cursor"]


# ── render_dynamic_for_targets ───────────────────────────────────────


class TestRenderDynamicForTargets:
    def test_renderer_without_dynamic_methods_skipped(self):
        """Renderers without render_session/render_focus are gracefully skipped."""
        renderer = MagicMock(spec=["name", "render"])
        renderer.name = "basic"
        registry = RendererRegistry()
        registry.register(renderer)

        runtime = MagicMock()
        result = registry.render_dynamic_for_targets(["basic"], runtime)
        assert result == {}

    def test_renderer_with_dynamic_methods_called(self):
        renderer = MagicMock()
        renderer.name = "dynamic"
        renderer.render_session.return_value = {".claude/rules/lg_session.md": "session content"}
        renderer.render_focus.return_value = {".claude/rules/lg_focus.md": "focus content"}
        registry = RendererRegistry()
        registry.register(renderer)

        runtime = MagicMock()
        result = registry.render_dynamic_for_targets(["dynamic"], runtime)
        assert ".claude/rules/lg_session.md" in result
        assert ".claude/rules/lg_focus.md" in result
        renderer.render_session.assert_called_once_with(runtime)
        renderer.render_focus.assert_called_once_with(runtime)

    def test_unknown_target_skipped(self):
        registry = RendererRegistry()
        runtime = MagicMock()
        result = registry.render_dynamic_for_targets(["nonexistent"], runtime)
        assert result == {}


# ── cleanup_dynamic_for_targets ──────────────────────────────────────


class TestCleanupDynamicForTargets:
    def test_renderer_without_cleanup_skipped(self):
        renderer = MagicMock(spec=["name", "render"])
        renderer.name = "basic"
        registry = RendererRegistry()
        registry.register(renderer)

        result = registry.cleanup_dynamic_for_targets(["basic"], "/tmp")
        assert result == []

    def test_renderer_with_cleanup_called(self):
        renderer = MagicMock()
        renderer.name = "dynamic"
        renderer.cleanup_dynamic.return_value = ["/tmp/.claude/rules/lg_session.md"]
        registry = RendererRegistry()
        registry.register(renderer)

        result = registry.cleanup_dynamic_for_targets(["dynamic"], "/tmp")
        assert result == ["/tmp/.claude/rules/lg_session.md"]


# ── Backward compatibility ───────────────────────────────────────────


class TestBackwardCompatibility:
    def test_existing_renderers_still_register(self):
        """All existing renderers register without error."""
        registry = build_default_registry()
        names = registry.list_available()
        assert "claude" in names
        assert "cursor" in names
        assert "copilot" in names
        assert "windsurf" in names
        assert "cline" in names
        assert "aider" in names
        assert "generic" in names

    def test_static_render_still_works(self):
        """Existing static render() contract is unchanged."""
        registry = build_default_registry()
        claude = registry.get("claude")
        assert claude is not None
        assert hasattr(claude, "render")
        assert hasattr(claude, "name")
        assert hasattr(claude, "output_paths")
