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


def _append_drift_signal(project_root: str, msg_parts: list[str]) -> None:
    """Check last controlplane run for suite health drift."""
    import glob
    import os
    import time as _time

    cp_dir = os.path.join(project_root, ".lintgate", "analysis", "controlplane_run")
    if not os.path.isdir(cp_dir):
        return

    # Find most recent analysis file
    files = glob.glob(os.path.join(cp_dir, "*.json"))
    if not files:
        return

    latest = max(files, key=os.path.getmtime)
    age_secs = _time.time() - os.path.getmtime(latest)
    age_hours = age_secs / 3600

    # Read blocking count from cached analysis
    with open(latest, encoding="utf-8") as f:
        data = json.load(f)

    blocking = 0
    for ch in data.get("channel_results", {}).values():
        if isinstance(ch, dict) and ch.get("severity") == "blocking":
            blocking += len(ch.get("findings", []))
    # Also check top-level blocking count
    blocking = max(blocking, data.get("blocking_count", 0))

    if age_hours > 24:
        msg_parts.append(f"Suite health stale ({age_hours:.0f}h). Run check_project.")
    elif blocking > 0:
        msg_parts.append(f"Suite: {blocking} blocking issue(s) at last check")


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
        # No compass — provide actionable routing
        no_compass_msg = (
            "[Session] No compass found. "
            "Start with `getting_started(path)` for auto-setup, "
            "or `controlplane_run(path)` for immediate health check, "
            "or `compass_update(path, write=True)` for theory extraction."
        )
        # Still surface blocking/prescriptive state if available
        try:
            from lintgate.runtime_state import load_runtime_state

            state = load_runtime_state(project_root)
            if state and state.blocking_issues > 0:
                no_compass_msg += f" | BLOCKING: {state.blocking_issues} issues"
        except Exception:
            pass
        return {"continue": True, "systemMessage": no_compass_msg}

    staleness = compute_staleness(compass)
    axes_summary = {name: axis.depth for name, axis in compass.axes.items()}

    import time as _time

    age_hours = (_time.time() - compass.forged_at) / 3600 if compass.forged_at else 0
    if age_hours < 1:
        age_str = f"{age_hours * 60:.0f}m"
    elif age_hours < 24:
        age_str = f"{age_hours:.0f}h"
    else:
        age_str = f"{age_hours / 24:.1f}d"

    msg_parts = [f"[Compass] Loaded ({age_str} old, staleness={staleness:.0%})"]
    if staleness > 0.8:
        msg_parts.append("STALE — run `compass_update` to re-extract")
    msg_parts.append(f"axes={axes_summary}")

    if compass.gap_report.interview_recommended:
        msg_parts.append("gaps detected — run `compass_interview`")

    # Surface highest-priority state signals from RuntimeState
    try:
        from lintgate.runtime_state import load_runtime_state

        state = load_runtime_state(project_root)
        if state is not None:
            if state.blocking_issues > 0:
                msg_parts.append(f"BLOCKING: {state.blocking_issues} issues")
            if state.coherence_state in ("coupled", "systemic"):
                msg_parts.append(f"Coherence: {state.coherence_state}")
            if state.prescriptive_spec_count > 0:
                msg_parts.append(
                    f"PSpec: {state.prescriptive_spec_count} specs"
                    f" ({state.prescriptive_coverage_ratio:.0%} covered)"
                )
    except Exception:
        pass

    # Suite health drift signal — compare to last controlplane run
    with contextlib.suppress(Exception):
        _append_drift_signal(project_root, msg_parts)

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
