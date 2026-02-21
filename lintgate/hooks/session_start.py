"""SessionStart hook — load compass and inject staleness advisory.

Fired when a Claude Code session begins. Loads existing compass state
and reports staleness so the agent knows whether to re-extract.

Protocol: stdin JSON → stdout JSON, exit 0 always.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def handle(data: dict[str, Any]) -> dict[str, Any]:
    """Process SessionStart event."""
    project_root = data.get("cwd", ".")

    try:
        from lintgate.compass import compute_staleness
        from lintgate.compass_io import load_compass
    except ImportError:
        return {"continue": True}

    compass = load_compass(project_root)
    if compass is None:
        return {
            "continue": True,
            "systemMessage": (
                "[Compass] No compass found. Run `compass_update` to extract project understanding."
            ),
        }

    staleness = compute_staleness(compass)
    axes_summary = {name: axis.depth for name, axis in compass.axes.items()}

    msg_parts = [f"[Compass] Loaded — staleness={staleness:.0%}"]
    if staleness > 0.8:
        msg_parts.append("(stale — consider re-extracting with `compass_update`)")
    msg_parts.append(f"axes={axes_summary}")

    if compass.gap_report.interview_recommended:
        msg_parts.append("gaps detected — run `compass_interview`")

    return {
        "continue": True,
        "systemMessage": " ".join(msg_parts),
    }


def main() -> None:
    data = json.loads(sys.stdin.read())
    result = handle(data)
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
