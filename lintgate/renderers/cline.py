"""Cline renderer -- outputs .clinerules/compass.md.

Dynamic files: .clinerules/lg_session.md, .clinerules/lg_focus.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._helpers import axis_summary, format_directives, project_name
from .dynamic import delete_dynamic_file, render_focus_content, render_session_content
from .host_adapter import CLINE_CAPABILITIES, HostCapabilities

if TYPE_CHECKING:
    from ..compass import CompassState
    from ..runtime_state import RuntimeState

_SESSION_PATH = ".clinerules/lg_session.md"
_FOCUS_PATH = ".clinerules/lg_focus.md"


class ClineRenderer:
    name = "cline"
    output_paths = [".clinerules/compass.md"]
    capabilities: HostCapabilities = CLINE_CAPABILITIES

    def render(self, compass: CompassState, metadata: dict[str, str]) -> dict[str, str]:
        name = project_name(metadata)
        mission = axis_summary(compass, "problem") or "Write correct code."
        architecture = axis_summary(compass, "solution")
        impl = axis_summary(compass, "implementation")
        toward = format_directives(compass.directives, "toward")
        away = format_directives(compass.directives, "away")
        forbidden = format_directives(compass.directives, "forbidden")

        lines = [
            f"# {name} -- Cline Rules",
            "",
            "## Mission",
            "",
            mission,
            "",
        ]
        if architecture:
            lines += ["## Architecture", "", architecture, ""]
        if impl:
            lines += ["## Implementation Notes", "", impl, ""]

        if toward:
            lines += ["## Preferred Practices"] + [f"- {t}" for t in toward] + [""]
        if away:
            lines += ["## Avoid"] + [f"- {a}" for a in away] + [""]
        if forbidden:
            lines += ["## Forbidden"] + [f"- {f}" for f in forbidden] + [""]

        # Include claim details for richer context
        for axis_name in ("problem", "solution", "implementation", "world"):
            axis = compass.axes.get(axis_name)
            if axis and axis.claims:
                lines.append(f"## {axis_name.capitalize()} Claims")
                for claim in axis.claims:
                    lines.append(f"- {claim.text}")
                lines.append("")

        return {".clinerules/compass.md": "\n".join(lines)}

    # ── Dynamic rule files ───────────────────────────────────────────

    def render_session(self, runtime: RuntimeState) -> dict[str, str]:
        """Render dynamic session state to .clinerules/lg_session.md."""
        return {_SESSION_PATH: render_session_content(runtime)}

    def render_focus(self, runtime: RuntimeState) -> dict[str, str]:
        """Render dynamic focus state to .clinerules/lg_focus.md."""
        return {_FOCUS_PATH: render_focus_content(runtime)}

    def cleanup_dynamic(self, project_root: str) -> list[str]:
        """Remove session-scoped dynamic files."""
        deleted = []
        if delete_dynamic_file(project_root, _SESSION_PATH):
            deleted.append(_SESSION_PATH)
        if delete_dynamic_file(project_root, _FOCUS_PATH):
            deleted.append(_FOCUS_PATH)
        return deleted
