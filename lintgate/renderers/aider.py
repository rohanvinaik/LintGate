"""Aider renderer -- outputs CONVENTIONS.md."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._helpers import axis_summary, format_directives, project_name

if TYPE_CHECKING:
    from ..compass import CompassState


class AiderRenderer:
    name = "aider"
    output_paths = ["CONVENTIONS.md"]

    def render(self, compass: CompassState, metadata: dict[str, str]) -> dict[str, str]:
        name = project_name(metadata)
        mission = axis_summary(compass, "problem") or "Write correct code."
        architecture = axis_summary(compass, "solution")
        impl = axis_summary(compass, "implementation")
        toward = format_directives(compass.directives, "toward")
        away = format_directives(compass.directives, "away")
        forbidden = format_directives(compass.directives, "forbidden")

        lines = [
            f"# {name} Conventions",
            "",
            "## Goal",
            "",
            mission,
            "",
        ]
        if architecture:
            lines += ["## Architecture", "", architecture, ""]
        if impl:
            lines += ["## Implementation", "", impl, ""]

        if toward:
            lines += ["## Do"] + [f"- {t}" for t in toward] + [""]
        if away:
            lines += ["## Do Not"] + [f"- {a}" for a in away] + [""]
        if forbidden:
            lines += ["## Hard Rules"] + [f"- {f}" for f in forbidden] + [""]

        return {"CONVENTIONS.md": "\n".join(lines)}
