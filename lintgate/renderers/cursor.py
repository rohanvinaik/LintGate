"""Cursor renderer -- outputs .cursor/rules/compass.mdc.

Dynamic files: .cursor/rules/lg_session.mdc, .cursor/rules/lg_focus.mdc
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._helpers import axis_summary, format_directives, project_name, truncate_lines
from .dynamic import delete_dynamic_file, render_focus_content, render_session_content
from .host_adapter import CURSOR_CAPABILITIES, HostCapabilities

if TYPE_CHECKING:
    from ..compass import CompassState
    from ..runtime_state import RuntimeState

_SESSION_PATH = ".cursor/rules/lg_session.mdc"
_FOCUS_PATH = ".cursor/rules/lg_focus.mdc"

_FRONTMATTER = "---\ndescription: LintGate session state\nglobs: ['**/*']\n---\n\n"


class CursorRenderer:
    name = "cursor"
    output_paths = [".cursor/rules/compass.mdc"]
    capabilities: HostCapabilities = CURSOR_CAPABILITIES

    def render(self, compass: CompassState, metadata: dict[str, str]) -> dict[str, str]:
        name = project_name(metadata)
        mission = axis_summary(compass, "problem") or "Write correct code."
        architecture = axis_summary(compass, "solution")
        toward = format_directives(compass.directives, "toward")
        away = format_directives(compass.directives, "away")
        forbidden = format_directives(compass.directives, "forbidden")

        lines = [
            "---",
            "description: Compass-derived project rules",
            "globs: ['**/*']",
            "---",
            "",
            f"# {name} Rules",
            "",
            f"**Mission:** {mission}",
            "",
        ]
        if architecture:
            lines.append(f"**Architecture:** {architecture}")
            lines.append("")

        if toward:
            lines.append("**Do:**")
            lines += [f"- {t}" for t in toward]
            lines.append("")
        if away:
            lines.append("**Avoid:**")
            lines += [f"- {a}" for a in away]
            lines.append("")
        if forbidden:
            lines.append("**Forbidden:**")
            lines += [f"- {f}" for f in forbidden]
            lines.append("")

        return {".cursor/rules/compass.mdc": "\n".join(truncate_lines(lines, 2000))}

    # ── Dynamic rule files ───────────────────────────────────────────

    def render_session(self, runtime: RuntimeState) -> dict[str, str]:
        """Render dynamic session state to .cursor/rules/lg_session.mdc."""
        content = _FRONTMATTER + render_session_content(runtime)
        return {_SESSION_PATH: content}

    def render_focus(self, runtime: RuntimeState) -> dict[str, str]:
        """Render dynamic focus state to .cursor/rules/lg_focus.mdc."""
        content = _FRONTMATTER + render_focus_content(runtime)
        return {_FOCUS_PATH: content}

    def cleanup_dynamic(self, project_root: str) -> list[str]:
        """Remove session-scoped dynamic files."""
        deleted = []
        if delete_dynamic_file(project_root, _SESSION_PATH):
            deleted.append(_SESSION_PATH)
        if delete_dynamic_file(project_root, _FOCUS_PATH):
            deleted.append(_FOCUS_PATH)
        return deleted
