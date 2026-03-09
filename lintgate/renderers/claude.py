"""Claude renderer -- outputs .claude/CLAUDE.md and .claude/rules/theory.md.

Dynamic files: .claude/rules/lg_session.md, .claude/rules/lg_focus.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._helpers import axis_summary, format_directives, project_name, truncate_lines
from .dynamic import (
    delete_dynamic_file,
    render_focus_content,
    render_session_content,
)
from .host_adapter import CLAUDE_CAPABILITIES, HostCapabilities

if TYPE_CHECKING:
    from ..compass import CompassState
    from ..runtime_state import RuntimeState

_SESSION_PATH = ".claude/rules/lg_session.md"
_FOCUS_PATH = ".claude/rules/lg_focus.md"

_FRONTMATTER = '---\npaths:\n  - "**/*.py"\n---\n\n'


class ClaudeRenderer:
    name = "claude"
    output_paths = [".claude/CLAUDE.md", ".claude/rules/theory.md"]
    capabilities: HostCapabilities = CLAUDE_CAPABILITIES

    def render(self, compass: CompassState, metadata: dict[str, str]) -> dict[str, str]:
        return {
            ".claude/CLAUDE.md": self._render_claude_md(compass, metadata),
            ".claude/rules/theory.md": self._render_theory_md(compass, metadata),
        }

    def _render_claude_md(self, compass: CompassState, metadata: dict[str, str]) -> str:
        name = project_name(metadata)
        mission = axis_summary(compass, "problem") or "Write correct, maintainable code."
        architecture = axis_summary(compass, "solution")
        toward = format_directives(compass.directives, "toward")
        away = format_directives(compass.directives, "away")
        forbidden = format_directives(compass.directives, "forbidden")

        lines = [
            f"# {name} Context",
            "",
            "<!-- LINTGATE:BEGIN compass_state v1 -->",
            "## True North",
            "",
            mission,
            "",
        ]
        if architecture:
            lines += ["## Architecture Philosophy", "", architecture, ""]

        if toward:
            lines += ["## DO"] + [f"- {t}" for t in toward] + [""]
        if away:
            lines += ["## DO NOT"] + [f"- {a}" for a in away] + [""]

        if forbidden:
            lines += ["## Machine Rules"]
            for rule in forbidden:
                lines.append(f"# LINTGATE_FORBID: {rule}")
            lines.append("")

        lines.append("<!-- LINTGATE:END compass_state -->")

        impl = axis_summary(compass, "implementation")
        if impl:
            lines += ["", "## Implementation Notes", "", impl]

        return "\n".join(truncate_lines(lines, 3000))

    def _render_theory_md(self, compass: CompassState, metadata: dict[str, str]) -> str:
        name = project_name(metadata)
        lines = [
            "---",
            "paths:",
            '  - "**/*.py"',
            "---",
            "",
            "# Theory Rules",
            "",
            f"Extracted compass state for `{name}`.",
            "",
            "## Axis Summaries",
        ]
        for axis_name in ("problem", "solution", "implementation", "world"):
            summary = axis_summary(compass, axis_name)
            label = axis_name.capitalize()
            lines.append(f"- {label}: {summary or 'No signal yet.'}")

        away = format_directives(compass.directives, "away")
        if away:
            lines += ["", "## Anti-Patterns"] + [f"- {a}" for a in away]

        return "\n".join(lines)

    # ── Dynamic rule files ───────────────────────────────────────────

    def render_session(self, runtime: RuntimeState) -> dict[str, str]:
        """Render dynamic session state to .claude/rules/lg_session.md."""
        content = _FRONTMATTER + render_session_content(runtime)
        return {_SESSION_PATH: content}

    def render_focus(self, runtime: RuntimeState) -> dict[str, str]:
        """Render dynamic focus state to .claude/rules/lg_focus.md."""
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
