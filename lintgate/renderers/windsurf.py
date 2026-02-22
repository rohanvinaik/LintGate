"""Windsurf renderer -- outputs .windsurf/rules/compass.md.

Dynamic files: .windsurf/rules/lg_session.md, .windsurf/rules/lg_focus.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._helpers import axis_summary, format_directives, project_name, truncate_lines
from .dynamic import delete_dynamic_file, render_focus_content, render_session_content
from .host_adapter import WINDSURF_CAPABILITIES, HostCapabilities

if TYPE_CHECKING:
    from ..compass import CompassState

_SESSION_PATH = ".windsurf/rules/lg_session.md"
_FOCUS_PATH = ".windsurf/rules/lg_focus.md"


class WindsurfRenderer:
    name = "windsurf"
    output_paths = [".windsurf/rules/compass.md"]
    capabilities: HostCapabilities = WINDSURF_CAPABILITIES

    def render(self, compass: CompassState, metadata: dict[str, str]) -> dict[str, str]:
        name = project_name(metadata)
        mission = axis_summary(compass, "problem") or "Write correct code."
        architecture = axis_summary(compass, "solution")
        impl = axis_summary(compass, "implementation")
        toward = format_directives(compass.directives, "toward")
        away = format_directives(compass.directives, "away")
        forbidden = format_directives(compass.directives, "forbidden")

        lines = [
            f"# {name} -- Windsurf Rules",
            "",
            "## Mission",
            "",
            mission,
            "",
        ]
        if architecture:
            lines += ["## Architecture", "", architecture, ""]
        if impl:
            lines += ["## Implementation", "", impl, ""]

        if toward:
            lines += ["## Best Practices"] + [f"- {t}" for t in toward] + [""]
        if away:
            lines += ["## Anti-Patterns"] + [f"- {a}" for a in away] + [""]
        if forbidden:
            lines += ["## Hard Constraints"] + [f"- {f}" for f in forbidden] + [""]

        # Axis detail section for deeper context
        lines.append("## Compass Axes")
        for axis_name in ("problem", "solution", "implementation", "world"):
            axis = compass.axes.get(axis_name)
            if axis and axis.claims:
                lines.append(f"### {axis_name.capitalize()}")
                for claim in axis.claims[:8]:
                    lines.append(f"- {claim.text}")
                lines.append("")

        return {".windsurf/rules/compass.md": "\n".join(truncate_lines(lines, 6000))}

    # ── Dynamic rule files ───────────────────────────────────────────

    def render_session(self, runtime: object) -> dict[str, str]:
        """Render dynamic session state to .windsurf/rules/lg_session.md."""
        return {_SESSION_PATH: render_session_content(runtime)}

    def render_focus(self, runtime: object) -> dict[str, str]:
        """Render dynamic focus state to .windsurf/rules/lg_focus.md."""
        return {_FOCUS_PATH: render_focus_content(runtime)}

    def cleanup_dynamic(self, project_root: str) -> list[str]:
        """Remove session-scoped dynamic files."""
        deleted = []
        if delete_dynamic_file(project_root, _SESSION_PATH):
            deleted.append(_SESSION_PATH)
        if delete_dynamic_file(project_root, _FOCUS_PATH):
            deleted.append(_FOCUS_PATH)
        return deleted
