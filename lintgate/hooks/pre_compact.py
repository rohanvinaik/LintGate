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

        capsule: dict[str, Any] = {
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

        # Auto-checkpoint: capture refactor progress before compaction
        refactor_progress = _capture_refactor_checkpoint(project_root)
        if refactor_progress:
            capsule["refactor_progress"] = refactor_progress

        # Auto-checkpoint: capture prescriptive spec state before compaction
        prescriptive_state = _capture_prescriptive_state(project_root)
        if prescriptive_state:
            capsule["prescriptive_specs"] = prescriptive_state

        return capsule
    except Exception:
        return None


def _capture_refactor_checkpoint(project_root: str) -> dict[str, Any] | None:
    """Auto-save refactor checkpoint before compaction.

    When a refactor session is active, captures a compact progress summary
    so the post-compaction context can resume where the agent left off.
    Returns None if no active refactor session exists.
    """
    try:
        from lintgate.refactor_state import load_state

        state = load_state(project_root)
        if state is None or not state.session_id:
            return None

        files = state.files
        completed = sum(1 for f in files.values() if f.status == "completed")
        pending = sum(1 for f in files.values() if f.status == "pending")
        in_progress = sum(1 for f in files.values() if f.status == "in_progress")

        # Find the current in-progress file
        current_files = [name for name, f in files.items() if f.status == "in_progress"]

        progress: dict[str, Any] = {
            "session_id": state.session_id,
            "thesis": state.thesis[:120] if state.thesis else "",
            "completed": completed,
            "pending": pending,
            "in_progress": in_progress,
            "total": len(files),
        }
        if current_files:
            progress["current_files"] = current_files[:3]
        # Identify next suggested file (first pending)
        next_pending = [name for name, f in files.items() if f.status == "pending"]
        if next_pending:
            progress["next_suggested"] = next_pending[0]

        return progress
    except Exception:
        return None


def _capture_prescriptive_state(project_root: str) -> dict[str, Any] | None:
    """Capture prescriptive spec state before compaction."""
    try:
        from lintgate.specification.prescriptive.spec import load_all_specs

        all_specs = load_all_specs(project_root)
        if not all_specs:
            return None

        class_dist: dict[str, int] = {"pure": 0, "stateful": 0, "distributed": 0}
        total_sigma = 0
        for spec in all_specs.values():
            class_dist[spec.problem_class] = class_dist.get(spec.problem_class, 0) + 1
            total_sigma += spec.prescriptive_sigma

        n = len(all_specs)
        return {
            "total_specs": n,
            "problem_classes": class_dist,
            "mean_prescriptive_sigma": round(total_sigma / n, 2) if n else 0.0,
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

        capsule_str = json.dumps(capsule, ensure_ascii=False)
        return {
            "continue": True,
            "systemMessage": (
                f"[LG] Pre-compact #{compaction_num} — mode={mode},"
                f" {n_toward} toward, {n_away} away, {n_forbidden} forbidden\n"
                f"<lintgate-compact-state>{capsule_str}</lintgate-compact-state>"
            ),
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

    legacy_str = json.dumps(legacy, ensure_ascii=False)
    return {
        "continue": True,
        "systemMessage": (
            f"[Compass] Pre-compact checkpoint — {n_toward} toward,"
            f" {n_away} away, {n_forbidden} forbidden directives preserved.\n"
            f"<lintgate-compact-state>{legacy_str}</lintgate-compact-state>"
        ),
    }


def main() -> None:
    data = json.loads(sys.stdin.read())
    result = handle(data)
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
