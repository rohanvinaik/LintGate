"""PreCompact hook — build execution capsule and checkpoint compass.

Fires before context compaction. Produces an execution capsule from the
frozen compass directives for injection into the compacted context.
This ensures the agent retains its compass orientation after compaction.

Protocol: stdin JSON → stdout JSON, exit 0 always.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def handle(data: dict[str, Any]) -> dict[str, Any]:
    """Process PreCompact event."""
    project_root = data.get("cwd", ".")

    try:
        from lintgate.compass_io import load_compass
        from lintgate.modes.execution_compass import ExecutionCompass
    except ImportError:
        return {"continue": True}

    compass = load_compass(project_root)
    if compass is None:
        return {"continue": True}

    exec_compass = ExecutionCompass.from_compass_state(compass)
    capsule = exec_compass.to_compact_json()

    # Build the execution capsule for post-compact injection
    axes_brief = {
        name: {"depth": axis.depth, "summary": axis.summary[:80]}
        for name, axis in compass.axes.items()
        if axis.depth > 0
    }

    return {
        "continue": True,
        "systemMessage": (
            f"[Compass] Pre-compact checkpoint — {len(exec_compass.toward)} toward,"
            f" {len(exec_compass.away)} away,"
            f" {len(exec_compass.forbidden)} forbidden directives preserved."
        ),
        "hookSpecificOutput": {
            "hookEventName": "PreCompact",
            "additionalContext": json.dumps(
                {
                    "compass_capsule": capsule,
                    "axes_brief": axes_brief,
                    "true_north": exec_compass.true_north[:120],
                },
                ensure_ascii=False,
            ),
        },
    }


def main() -> None:
    data = json.loads(sys.stdin.read())
    result = handle(data)
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
