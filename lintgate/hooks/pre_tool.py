"""PreToolUse hook — compass-aware advisories before tool execution.

Theory mode: advisory on write operations (suggests reading more first).
Normal/Habit mode: alignment check against toward/away/forbidden directives.

Protocol: stdin JSON → stdout JSON, exit 0 always.
"""

from __future__ import annotations

import json
import sys
from typing import Any

_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})


def _load_mode(project_root: str) -> str:
    """Load current cognitive mode from session memory."""
    try:
        from lintgate.controlplane.session_memory import get_or_create_session

        session = get_or_create_session(project_root)
        mode = session.behavior_compass.get("mode_state", {}).get("current", "normal")
        return str(mode or "normal")
    except Exception:
        return "normal"


def _check_theory_mode(mode: str, tool_name: str) -> str:
    """Return advisory if theory mode is active and tool is a write."""
    if mode == "theory" and tool_name in _WRITE_TOOLS:
        return (
            "[Compass] Theory mode active — consider reading more"
            " before writing. Use `theory_mode_freeze` when ready."
        )
    return ""


def _check_bash_alignment(data: dict[str, Any], exec_compass: Any) -> str:
    """Check Bash command alignment against compass directives."""
    tool_input = data.get("tool_input", data.get("input", {}))
    command = ""
    if isinstance(tool_input, dict):
        command = str(tool_input.get("command", ""))
    elif isinstance(tool_input, str):
        command = tool_input

    if not command:
        return ""

    result = exec_compass.check_alignment(command)
    if result.get("violations"):
        return f"[Compass] Alignment violation: {result['violations'][0]}"
    if result.get("warnings"):
        return f"[Compass] Alignment warning: {result['warnings'][0]}"
    return ""


def handle(data: dict[str, Any]) -> dict[str, Any]:
    """Process PreToolUse event."""
    tool_name = data.get("tool_name", data.get("toolName", ""))
    project_root = data.get("cwd", ".")
    mode = _load_mode(project_root)

    try:
        from lintgate.compass_io import load_compass
        from lintgate.modes.execution_compass import ExecutionCompass
    except ImportError:
        return {"continue": True}

    compass = load_compass(project_root)
    if compass is None and not (mode == "theory" and tool_name in _WRITE_TOOLS):
        return {"continue": True}

    exec_compass = ExecutionCompass.from_compass_state(compass) if compass is not None else None
    messages = [
        msg
        for msg in [
            _check_theory_mode(mode, tool_name),
            _check_bash_alignment(data, exec_compass) if tool_name == "Bash" and exec_compass else "",
        ]
        if msg
    ]

    return {
        "continue": True,
        "systemMessage": " | ".join(messages) if messages else "",
    }


def main() -> None:
    data = json.loads(sys.stdin.read())
    result = handle(data)
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
