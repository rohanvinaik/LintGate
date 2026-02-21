"""SessionEnd hook — persist compass and log telemetry.

Fires asynchronously when the session ends. Ensures compass state
is saved to disk and logs basic session telemetry.

Protocol: stdin JSON → stdout JSON, exit 0 always.
"""

from __future__ import annotations

import contextlib
import json
import sys
from typing import Any


def handle(data: dict[str, Any]) -> dict[str, Any]:
    """Process SessionEnd event."""
    project_root = data.get("cwd", ".")

    try:
        from lintgate.compass_io import load_compass, save_compass
    except ImportError:
        return {"continue": True}

    compass = load_compass(project_root)
    if compass is None:
        return {"continue": True}

    # Re-save to ensure any in-memory changes are persisted
    with contextlib.suppress(Exception):
        save_compass(project_root, compass)

    return {"continue": True}


def main() -> None:
    data = json.loads(sys.stdin.read())
    result = handle(data)
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
