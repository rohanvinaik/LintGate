"""Stop hook — advisory-only session exit checkpoint.

Never blocks session exit. Reports compass state summary so the user
knows what was preserved. Suggests saving compass if unsaved changes exist.

Protocol: stdin JSON → stdout JSON, exit 0 always.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def handle(data: dict[str, Any]) -> dict[str, Any]:
    """Process Stop event. Advisory only — never blocks."""
    project_root = data.get("cwd", ".")

    try:
        from lintgate.compass import compute_staleness
        from lintgate.compass_io import load_compass
    except ImportError:
        return {"continue": True}

    compass = load_compass(project_root)
    if compass is None:
        return {"continue": True}

    staleness = compute_staleness(compass)
    populated = sum(1 for a in compass.axes.values() if a.depth > 0)

    return {
        "continue": True,
        "systemMessage": (
            f"[Compass] Session ending — {populated}/4 axes populated, staleness={staleness:.0%}."
        ),
    }


def main() -> None:
    data = json.loads(sys.stdin.read())
    result = handle(data)
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
