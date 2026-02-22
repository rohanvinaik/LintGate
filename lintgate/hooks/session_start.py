"""SessionStart hook — load compass, initialize runtime state, inject advisory.

Fired when a Claude Code session begins. Loads existing compass state,
initializes RuntimeState for cross-surface rendering, writes initial
dynamic rule files for the detected host, and reports staleness so the
agent knows whether to re-extract.

Protocol: stdin JSON → stdout JSON, exit 0 always.
"""

from __future__ import annotations

import contextlib
import json
import sys
from typing import Any


def _initialize_runtime_state(project_root: str) -> None:
    """Initialize RuntimeState and write initial dynamic rule files.

    Rehydrates RuntimeState from existing session/habit/token stores (when
    available), persists it, and writes initial dynamic rule files for any
    detected host. Fail-open: errors are silently ignored.
    """
    try:
        from lintgate.renderers import build_default_registry
        from lintgate.renderers.dynamic import write_dynamic_file
        from lintgate.runtime_state import build_runtime_state, save_runtime_state

        session = None
        habit_state = None
        tracker = None
        compass = None

        with contextlib.suppress(Exception):
            from lintgate.controlplane.session_memory import get_or_create_session

            session = get_or_create_session(project_root)

        if session is not None:
            with contextlib.suppress(Exception):
                from lintgate.habit_mode import load_habit_state

                habit_state = load_habit_state(session.behavior_compass)
            with contextlib.suppress(Exception):
                from lintgate.token_tracker import load_tracker_state

                tracker = load_tracker_state(session.behavior_compass)
        else:
            with contextlib.suppress(Exception):
                from lintgate.habit_mode import (
                    load_habit_state_standalone,
                    load_standalone_extras,
                )
                from lintgate.token_tracker import TokenTrackerState

                habit_state, _action_ring = load_habit_state_standalone(project_root)
                extras = load_standalone_extras(project_root)
                raw_tracker = extras.get("token_tracker", {})
                if isinstance(raw_tracker, dict):
                    tracker = TokenTrackerState.from_dict(raw_tracker)

        with contextlib.suppress(Exception):
            from lintgate.compass_io import load_compass

            compass = load_compass(project_root)

        state = build_runtime_state(
            project_root,
            session=session,
            habit_state=habit_state,
            tracker=tracker,
            compass=compass,
        )
        save_runtime_state(project_root, state)

        # Write initial dynamic files for detected hosts
        registry = build_default_registry()
        targets = registry.detect_runtime_hosts(project_root)
        if not targets:
            return

        files = registry.render_dynamic_for_targets(targets, state)
        for rel_path, content in files.items():
            write_dynamic_file(project_root, rel_path, content)
    except Exception:
        pass  # Fail-open


def handle(data: dict[str, Any]) -> dict[str, Any]:
    """Process SessionStart event."""
    project_root = data.get("cwd", ".")

    # Initialize runtime state and dynamic files
    _initialize_runtime_state(project_root)

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
