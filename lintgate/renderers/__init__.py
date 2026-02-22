"""Multi-model context file renderers for the compass system.

Each renderer transforms CompassState into a context file tailored
to a specific AI coding tool (Claude, Cursor, Copilot, etc.).

The ``Renderer`` protocol covers static compass rendering.
The ``HostAdapter`` protocol (in ``host_adapter.py``) extends it with
dynamic rule file rendering and capability detection.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from lintgate.compass import CompassState
    from lintgate.runtime_state import RuntimeState


class Renderer(Protocol):
    """Protocol for compass state renderers."""

    name: str
    output_paths: list[str]  # relative paths from project root

    def render(
        self, compass: CompassState, metadata: dict[str, str]
    ) -> dict[str, str]:
        """Return {relative_path: rendered_content}."""
        ...


class RendererRegistry:
    """Registry of available renderers with auto-detection."""

    def __init__(self) -> None:
        self._renderers: dict[str, Renderer] = {}

    def register(self, renderer: Renderer) -> None:
        """Register a renderer by its name."""
        self._renderers[renderer.name] = renderer

    def get(self, name: str) -> Renderer | None:
        """Look up a renderer by name."""
        return self._renderers.get(name)

    def list_available(self) -> list[str]:
        """Return sorted list of registered renderer names."""
        return sorted(self._renderers.keys())

    def detect_tools(self, project_root: str) -> list[str]:
        """Auto-detect AI tools present in the project directory."""
        detections: dict[str, str] = {
            ".cursor": "cursor",
            ".github": "copilot",
            ".windsurf": "windsurf",
            ".clinerules": "cline",
        }
        found: list[str] = []
        for directory, target in detections.items():
            if os.path.isdir(os.path.join(project_root, directory)):
                found.append(target)
        return sorted(found)

    def detect_host(self, project_root: str) -> str | None:
        """Detect which AI coding host is actively running.

        Checks environment variables first (most reliable), then
        falls back to directory-based detection. Returns the name
        of the detected host or None.
        """
        hosts = self.detect_runtime_hosts(project_root)
        return hosts[0] if hosts else None

    def detect_runtime_hosts(self, project_root: str) -> list[str]:
        """Detect all active/runtime hosts that can consume dynamic rules.

        If host env vars are present, returns only that host. Otherwise returns
        all host directories discovered in the project root.
        """
        # Env var detection (most reliable — set by the host itself)
        if os.environ.get("CLAUDE_CODE"):
            return ["claude"]
        if os.environ.get("CURSOR_SESSION_ID"):
            return ["cursor"]
        if os.environ.get("WINDSURF_SESSION"):
            return ["windsurf"]

        # Directory-based fallback (may include multiple hosts)
        found: list[str] = []
        if os.path.isdir(os.path.join(project_root, ".claude")):
            found.append("claude")
        if os.path.isdir(os.path.join(project_root, ".cursor")):
            found.append("cursor")
        if os.path.isdir(os.path.join(project_root, ".windsurf")):
            found.append("windsurf")
        if os.path.isdir(os.path.join(project_root, ".clinerules")):
            found.append("cline")

        return found

    def render_for_targets(
        self,
        targets: list[str],
        compass: CompassState,
        metadata: dict[str, str],
    ) -> dict[str, str]:
        """Render context files for the given target list."""
        results: dict[str, str] = {}
        for target in targets:
            renderer = self._renderers.get(target)
            if renderer is not None:
                results.update(renderer.render(compass, metadata))
        return results

    def render_dynamic_for_targets(
        self,
        targets: list[str],
        runtime: RuntimeState,
    ) -> dict[str, str]:
        """Render dynamic rule files for targets that support them.

        Calls ``render_session()`` and ``render_focus()`` on each
        renderer that has these methods (HostAdapter-compatible).
        """
        results: dict[str, str] = {}
        for target in targets:
            renderer = self._renderers.get(target)
            if renderer is None:
                continue
            if hasattr(renderer, "render_session"):
                results.update(renderer.render_session(runtime))
            if hasattr(renderer, "render_focus"):
                results.update(renderer.render_focus(runtime))
        return results

    def cleanup_dynamic_for_targets(
        self,
        targets: list[str],
        project_root: str,
    ) -> list[str]:
        """Clean up dynamic files for all targets. Returns deleted paths."""
        deleted: list[str] = []
        for target in targets:
            renderer = self._renderers.get(target)
            if renderer is not None and hasattr(renderer, "cleanup_dynamic"):
                deleted.extend(renderer.cleanup_dynamic(project_root))
        return deleted


def build_default_registry() -> RendererRegistry:
    """Build a registry with all built-in renderers."""
    from .agents_md import AgentsMdRenderer
    from .aider import AiderRenderer
    from .claude import ClaudeRenderer
    from .cline import ClineRenderer
    from .copilot import CopilotRenderer
    from .cursor import CursorRenderer
    from .generic import GenericRenderer
    from .windsurf import WindsurfRenderer

    registry = RendererRegistry()
    registry.register(ClaudeRenderer())
    registry.register(CursorRenderer())
    registry.register(CopilotRenderer())
    registry.register(WindsurfRenderer())
    registry.register(ClineRenderer())
    registry.register(AiderRenderer())
    registry.register(AgentsMdRenderer())
    registry.register(GenericRenderer())
    return registry
