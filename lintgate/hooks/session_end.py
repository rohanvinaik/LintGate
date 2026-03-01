"""SessionEnd hook — persist compass, clean up dynamic files, log telemetry.

Fires asynchronously when the session ends. Ensures compass state
is saved to disk, cleans up session-scoped dynamic rule files, deletes
RuntimeState, and logs basic session telemetry.

Protocol: stdin JSON → stdout JSON, exit 0 always.
"""

from __future__ import annotations

import contextlib
import json
import sys
from typing import Any


def _cleanup_session_state(project_root: str) -> None:
    """Clean up RuntimeState and dynamic rule files.

    Removes dynamic rule files for all detected hosts and deletes
    the RuntimeState file. Fail-open: errors are silently ignored.
    """
    try:
        from lintgate.renderers import build_default_registry
        from lintgate.runtime_state import delete_runtime_state

        registry = build_default_registry()

        # Clean up dynamic files for all hosts that have rule directories
        detected = registry.detect_host(project_root)
        if detected is not None:
            registry.cleanup_dynamic_for_targets([detected], project_root)

        # Also clean up any other hosts that might have leftover files
        for host_name in registry.list_available():
            renderer = registry.get(host_name)
            if renderer is not None and hasattr(renderer, "cleanup_dynamic"):
                cleanup_fn = getattr(renderer, "cleanup_dynamic")
                with contextlib.suppress(Exception):
                    cleanup_fn(project_root)

        # Delete RuntimeState file
        delete_runtime_state(project_root)
    except Exception:
        pass  # Fail-open


def handle(data: dict[str, Any]) -> dict[str, Any]:
    """Process SessionEnd event."""
    project_root = data.get("cwd", ".")

    try:
        from lintgate.compass_io import load_compass, save_compass
    except ImportError:
        return {"continue": True}

    compass = load_compass(project_root)
    if compass is not None:
        # Re-save to ensure any in-memory changes are persisted
        with contextlib.suppress(Exception):
            save_compass(project_root, compass)

    # Clean up session-scoped state and dynamic files
    _cleanup_session_state(project_root)

    return {"continue": True}


def main() -> None:
    data = json.loads(sys.stdin.read())
    result = handle(data)
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
