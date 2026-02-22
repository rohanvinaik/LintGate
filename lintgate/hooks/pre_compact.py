"""PreCompact hook — compaction shaping with dual-write strategy.

Fires before context compaction. Builds a structured capsule from
RuntimeState and writes it to BOTH:
1. Hook output (hookSpecificOutput.additionalContext) — injected post-compact
2. Dynamic rule files — reloaded from disk into system prompt by the host

This dual-write ensures state survives compaction through two independent paths.
Falls back to compass-only capsule when RuntimeState is unavailable.

Protocol: stdin JSON → stdout JSON, exit 0 always.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def _build_capsule_from_runtime(project_root: str) -> dict[str, Any] | None:
    """Build a compaction capsule from RuntimeState.

    Returns the capsule dict, or None if RuntimeState is unavailable.
    MAX ~800 tokens.
    """
    try:
        from lintgate.runtime_state import load_runtime_state, save_runtime_state

        runtime = load_runtime_state(project_root)
        if runtime is None:
            return None

        # Increment compaction count
        runtime.compaction_count += 1
        save_runtime_state(project_root, runtime)

        return {
            "compass_capsule": {
                "true_north": runtime.true_north[:120],
                "toward": runtime.toward[:6],
                "away": runtime.away[:6],
                "forbidden": runtime.forbidden[:6],
            },
            "session_state": {
                "mode": runtime.mode,
                "focus_files": [f.rsplit("/", 1)[-1] for f in runtime.active_files[:5]],
                "test_status": runtime.last_test_status,
                "blocking": runtime.blocking_issues,
                "coherence": runtime.coherence_state,
            },
            "behavioral": {
                "approach_failures": runtime.approach_failures,
                "top_constraint": runtime.top_constraint[:80],
                "prediction_accuracy": runtime.prediction_accuracy,
            },
            "token_state": {
                "pct_used": round(runtime.estimated_tokens_pct, 1),
                "compaction_number": runtime.compaction_count,
                "tool_calls": runtime.tool_calls_total,
            },
        }
    except Exception:
        return None


def _write_dynamic_files(project_root: str) -> None:
    """Write dynamic rule files as an immediate compaction trigger."""
    try:
        from lintgate.renderers import build_default_registry
        from lintgate.runtime_state import load_runtime_state

        runtime = load_runtime_state(project_root)
        if runtime is None:
            return

        registry = build_default_registry()
        targets = registry.detect_runtime_hosts(project_root)
        if not targets:
            # Default to claude if .claude/rules exists
            from pathlib import Path

            if (Path(project_root) / ".claude" / "rules").is_dir():
                targets = ["claude"]
            else:
                return

        files = registry.render_dynamic_for_targets(targets, runtime)
        from lintgate.renderers.dynamic import write_dynamic_file

        for rel_path, content in files.items():
            write_dynamic_file(project_root, rel_path, content)
    except Exception:
        pass  # Fail-open


def _build_legacy_capsule(project_root: str) -> dict[str, Any] | None:
    """Fallback: build capsule from compass only (original behavior)."""
    try:
        from lintgate.compass_io import load_compass
        from lintgate.modes.execution_compass import ExecutionCompass
    except ImportError:
        return None

    compass = load_compass(project_root)
    if compass is None:
        return None

    exec_compass = ExecutionCompass.from_compass_state(compass)
    capsule = exec_compass.to_compact_json()

    axes_brief = {
        name: {"depth": axis.depth, "summary": axis.summary[:80]}
        for name, axis in compass.axes.items()
        if axis.depth > 0
    }

    return {
        "compass_capsule": capsule,
        "axes_brief": axes_brief,
        "true_north": exec_compass.true_north[:120],
    }


def handle(data: dict[str, Any]) -> dict[str, Any]:
    """Process PreCompact event."""
    project_root = data.get("cwd", ".")

    # Try enhanced capsule from RuntimeState first
    capsule = _build_capsule_from_runtime(project_root)

    if capsule is not None:
        # Dual-write: also update dynamic rule files
        _write_dynamic_files(project_root)

        # Build summary for systemMessage
        compass = capsule.get("compass_capsule", {})
        n_toward = len(compass.get("toward", []))
        n_away = len(compass.get("away", []))
        n_forbidden = len(compass.get("forbidden", []))
        session = capsule.get("session_state", {})
        mode = session.get("mode", "normal")
        compaction_num = capsule.get("token_state", {}).get("compaction_number", 0)

        return {
            "continue": True,
            "systemMessage": (
                f"[LG] Pre-compact #{compaction_num} — mode={mode},"
                f" {n_toward} toward, {n_away} away, {n_forbidden} forbidden"
            ),
            "hookSpecificOutput": {
                "hookEventName": "PreCompact",
                "additionalContext": json.dumps(capsule, ensure_ascii=False),
            },
        }

    # Fallback: compass-only capsule (legacy behavior)
    legacy = _build_legacy_capsule(project_root)
    if legacy is None:
        return {"continue": True}

    compass_capsule = legacy.get("compass_capsule", {})
    n_toward = len(compass_capsule.get("toward", [])) if isinstance(compass_capsule, dict) else 0
    n_away = len(compass_capsule.get("away", [])) if isinstance(compass_capsule, dict) else 0
    n_forbidden = (
        len(compass_capsule.get("forbidden", [])) if isinstance(compass_capsule, dict) else 0
    )

    return {
        "continue": True,
        "systemMessage": (
            f"[Compass] Pre-compact checkpoint — {n_toward} toward,"
            f" {n_away} away, {n_forbidden} forbidden directives preserved."
        ),
        "hookSpecificOutput": {
            "hookEventName": "PreCompact",
            "additionalContext": json.dumps(legacy, ensure_ascii=False),
        },
    }


def main() -> None:
    data = json.loads(sys.stdin.read())
    result = handle(data)
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
