"""Claude renderer -- outputs .claude/CLAUDE.md and .claude/rules/theory.md."""

from __future__ import annotations

from ..compass import CompassState
from ._helpers import axis_summary, format_directives, project_name, truncate_lines


class ClaudeRenderer:
    name = "claude"
    output_paths = [".claude/CLAUDE.md", ".claude/rules/theory.md"]

    def render(
        self, compass: CompassState, metadata: dict[str, str]
    ) -> dict[str, str]:
        return {
            ".claude/CLAUDE.md": self._render_claude_md(compass, metadata),
            ".claude/rules/theory.md": self._render_theory_md(compass, metadata),
        }

    def _render_claude_md(
        self, compass: CompassState, metadata: dict[str, str]
    ) -> str:
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

    def _render_theory_md(
        self, compass: CompassState, metadata: dict[str, str]
    ) -> str:
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
