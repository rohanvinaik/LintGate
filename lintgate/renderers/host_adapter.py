"""Host adapter protocol — capability detection and dynamic rendering.

Extends the existing ``Renderer`` protocol with:
- ``HostCapabilities``: what rendering surfaces a host supports
- ``HostAdapter``: extended protocol adding dynamic rule file rendering

The existing ``Renderer`` protocol is a structural typing subset of
``HostAdapter``, so all existing renderers continue working without
modification until dynamic methods are added.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from lintgate.compass import CompassState
    from lintgate.runtime_state import RuntimeState


@dataclass
class HostCapabilities:
    """What rendering surfaces a host supports."""

    supports_rules: bool = False  # Host reloads rule files into system prompt
    supports_hooks: bool = False  # Host fires hook events (pre/post tool use)
    supports_mcp: bool = False  # Host connects to MCP servers
    supports_frontmatter: bool = False  # Rule files support YAML frontmatter
    rule_file_extension: str = ".md"  # ".md" or ".mdc"
    max_rule_files: int = 10  # Practical limit on rule files
    system_prompt_token_budget: int = 8000  # Approximate tokens for rules


# ── Capability presets ───────────────────────────────────────────────

CLAUDE_CAPABILITIES = HostCapabilities(
    supports_rules=True,
    supports_hooks=True,
    supports_mcp=True,
    supports_frontmatter=True,
    rule_file_extension=".md",
    system_prompt_token_budget=12000,
)

CURSOR_CAPABILITIES = HostCapabilities(
    supports_rules=True,
    supports_hooks=False,
    supports_mcp=True,
    supports_frontmatter=True,
    rule_file_extension=".mdc",
    system_prompt_token_budget=8000,
)

COPILOT_CAPABILITIES = HostCapabilities(
    supports_rules=False,
    supports_hooks=False,
    supports_mcp=False,
    supports_frontmatter=False,
    rule_file_extension=".md",
    system_prompt_token_budget=4000,
)

WINDSURF_CAPABILITIES = HostCapabilities(
    supports_rules=True,
    supports_hooks=False,
    supports_mcp=True,
    supports_frontmatter=False,
    rule_file_extension=".md",
    system_prompt_token_budget=8000,
)

CLINE_CAPABILITIES = HostCapabilities(
    supports_rules=True,
    supports_hooks=False,
    supports_mcp=True,
    supports_frontmatter=False,
    rule_file_extension=".md",
    system_prompt_token_budget=8000,
)

AIDER_CAPABILITIES = HostCapabilities(
    supports_rules=False,
    supports_hooks=False,
    supports_mcp=False,
    supports_frontmatter=False,
    rule_file_extension=".md",
    system_prompt_token_budget=4000,
)

GENERIC_CAPABILITIES = HostCapabilities(
    supports_rules=False,
    supports_hooks=False,
    supports_mcp=False,
    supports_frontmatter=False,
    rule_file_extension=".md",
    system_prompt_token_budget=4000,
)

MCP_ONLY_CAPABILITIES = HostCapabilities(
    supports_rules=False,
    supports_hooks=False,
    supports_mcp=True,
    supports_frontmatter=False,
    rule_file_extension=".md",
    system_prompt_token_budget=4000,
)


@runtime_checkable
class HostAdapter(Protocol):
    """Extended protocol for host-specific rendering + dynamics.

    Adds dynamic rule file rendering on top of the existing static
    ``Renderer`` protocol. Renderers that don't support dynamic files
    can return empty dicts from render_session/render_focus.
    """

    name: str
    output_paths: list[str]
    capabilities: HostCapabilities

    def render(self, compass: CompassState, metadata: dict[str, str]) -> dict[str, str]:
        """Static compass rendering (existing contract)."""
        ...

    def render_session(self, runtime: RuntimeState) -> dict[str, str]:
        """Dynamic session state rendering. Returns {path: content}."""
        ...

    def render_focus(self, runtime: RuntimeState) -> dict[str, str]:
        """Dynamic focus state rendering. Returns {path: content}."""
        ...

    def cleanup_dynamic(self, project_root: str) -> list[str]:
        """Remove session-scoped dynamic files. Returns deleted paths."""
        ...
