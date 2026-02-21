"""Multi-model context file renderers for the compass system.

Each renderer transforms CompassState into a context file tailored
to a specific AI coding tool (Claude, Cursor, Copilot, etc.).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from lintgate.compass import CompassState


class Renderer(Protocol):
    """Protocol for compass state renderers."""

    name: str
    output_paths: list[str]  # relative paths from project root

    def render(
        self, compass: "CompassState", metadata: dict[str, str]
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

    def render_for_targets(
        self,
        targets: list[str],
        compass: "CompassState",
        metadata: dict[str, str],
    ) -> dict[str, str]:
        """Render context files for the given target list."""
        results: dict[str, str] = {}
        for target in targets:
            renderer = self._renderers.get(target)
            if renderer is not None:
                results.update(renderer.render(compass, metadata))
        return results


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
