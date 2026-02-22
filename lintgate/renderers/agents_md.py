"""AGENTS.md renderer -- outputs AGENTS.md."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._helpers import axis_summary, format_directives, project_name

if TYPE_CHECKING:
    from ..compass import CompassState


class AgentsMdRenderer:
    name = "agents"
    output_paths = ["AGENTS.md"]

    def render(self, compass: CompassState, metadata: dict[str, str]) -> dict[str, str]:
        name = project_name(metadata)
        mission = axis_summary(compass, "problem") or "Write correct code."
        architecture = axis_summary(compass, "solution")
        toward = format_directives(compass.directives, "toward")
        away = format_directives(compass.directives, "away")

        lines = [
            "# AGENTS.md",
            "",
            "## Scope",
            f"- Applies to the entire `{name}` repository.",
            "- Follow user instructions first, then this file, then local nested guidance.",
            "",
            "## Mission",
            "",
            mission,
            "",
            "## Execution Contract",
            "- Read relevant files before editing.",
            "- Prefer minimal diffs over broad rewrites.",
            "- Avoid behavior changes unless requested or required to fix defects.",
            "- Surface assumptions and risks when information is incomplete.",
            "",
        ]
        if architecture:
            lines += ["## Architecture", "", architecture, ""]

        if toward:
            lines += ["## Preferred Practices"] + [f"- {t}" for t in toward] + [""]
        if away:
            lines += ["## Anti-Patterns"] + [f"- {a}" for a in away] + [""]

        lines += [
            "## Handoff Expectations",
            "- Summarize what changed and why.",
            "- Report what was tested and what remains unverified.",
        ]

        return {"AGENTS.md": "\n".join(lines)}
