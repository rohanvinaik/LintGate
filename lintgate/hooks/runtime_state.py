"""Runtime state management for the PostToolUse hook.

Handles RuntimeState persistence, dynamic rule file generation, write scheduling,
and telemetry logging for cadence tuning.
"""

from __future__ import annotations

import contextlib
import os
import re
from dataclasses import dataclass
from typing import Any


@dataclass
class RuntimeStateWriteMetric:
    """Bundle of telemetry fields for a runtime state write event.

    Replaces the 12-argument _log_runtime_state_write_metric function signature.
    """

    project_root: str
    path_kind: str
    trigger: str
    mode: str
    generation: int
    attempted: int
    success: int
    skipped_by_cadence: int
    save_ok: bool
    lock_acquired: bool
    lock_contention_count: int
    dynamic_status: str


def log_runtime_state_write_metric(metric: RuntimeStateWriteMetric) -> None:
    """Log runtime state write telemetry for cadence tuning."""
    with contextlib.suppress(Exception):
        from lintgate.state import log_metric

        log_metric(
            {
                "event": "runtime_state_write",
                "project": metric.project_root,
                "path": metric.path_kind,
                "trigger": metric.trigger,
                "mode": metric.mode,
                "generation": metric.generation,
                "attempted": int(bool(metric.attempted)),
                "success": int(bool(metric.success)),
                "skipped_by_cadence": int(bool(metric.skipped_by_cadence)),
                "save_ok": bool(metric.save_ok),
                "lock_acquired": bool(metric.lock_acquired),
                "lock_contention_count": max(0, int(metric.lock_contention_count)),
                "dynamic_status": metric.dynamic_status,
            }
        )


def derive_focus_intent(tool_name: str, tool_input: Any) -> str:
    """Extract a short focus sentence from the current tool action."""
    if tool_name in {"Write", "Edit", "MultiEdit", "NotebookEdit"} and isinstance(tool_input, dict):
        path = str(tool_input.get("file_path") or tool_input.get("path") or "").strip()
        if path:
            return f"Edit {os.path.basename(path)}"

    if tool_name == "Bash":
        command = ""
        if isinstance(tool_input, dict):
            command = str(tool_input.get("command", "")).strip()
        elif isinstance(tool_input, str):
            command = tool_input.strip()
        if command:
            normalized = re.sub(r"\s+", " ", command)
            return f"Run `{normalized[:96]}`"

    return f"Use {tool_name}" if tool_name else ""


def mesh_finding_counts(mesh_result: Any) -> tuple[int, int]:
    """Return (blocking, warning) finding counts from a mesh result."""
    blocking = 0
    warnings = 0
    for channel_result in mesh_result.channel_results:
        for finding in channel_result.findings:
            severity = str(getattr(finding, "severity", "")).lower()
            if severity == "blocking":
                blocking += 1
            elif severity == "warning":
                warnings += 1
    return blocking, warnings


def mesh_symbol_blocker_count(mesh_result: Any) -> int:
    """Count blocking symbol-coverage findings from the tests channel."""
    count = 0
    for channel_result in mesh_result.channel_results:
        if channel_result.channel != "tests":
            continue
        for finding in channel_result.findings:
            severity = str(getattr(finding, "severity", "")).lower()
            if severity != "blocking":
                continue
            kind = str(getattr(finding, "kind", "") or "")
            if kind in {"symbol_uncovered", "unresolved_required_symbol"}:
                count += 1
    return count


def runtime_targets(registry: Any, project_root: str) -> list[str]:
    """Resolve runtime host targets for dynamic rule writes."""
    targets: list[str] = []
    with contextlib.suppress(Exception):
        targets = list(registry.detect_runtime_hosts(project_root))
    if targets:
        return targets

    with contextlib.suppress(Exception):
        detected = registry.detect_host(project_root)
        if detected:
            return [detected]
    return []


def write_dynamic_runtime_files(project_root: str, runtime_state: Any) -> tuple[bool, str]:
    """Write dynamic rule files for all detected runtime hosts.

    Returns:
        (success, status)
        status is one of: "success", "no_targets", "write_failed", "error".
    """
    try:
        from lintgate.renderers import build_default_registry
        from lintgate.renderers.dynamic import write_dynamic_file

        registry = build_default_registry()
        targets = runtime_targets(registry, project_root)
        if not targets:
            return False, "no_targets"
        files = registry.render_dynamic_for_targets(targets, runtime_state)
        if not files:
            return False, "no_targets"
        wrote_any = False
        for rel_path, content in files.items():
            if write_dynamic_file(project_root, rel_path, content):
                wrote_any = True
        return (wrote_any, "success" if wrote_any else "write_failed")
    except Exception:
        return False, "error"


def refresh_runtime_state_with_session(
    project_root: str,
    session: Any,
    *,
    compass: Any | None = None,
    habit_state: Any | None = None,
    tracker: Any | None = None,
    mesh_result: Any | None = None,
    tool_name: str = "",
    tool_input: Any = None,
    trigger: str = "tool_call",
    transition: str | None = None,
) -> None:
    """Refresh persisted RuntimeState and cadenced dynamic rule files."""
    with contextlib.suppress(Exception):
        from lintgate.runtime_state import (
            build_runtime_state,
            save_runtime_state_with_meta,
        )
        from lintgate.write_scheduler import (
            WriteScheduler,
            mark_dirty,
            record_tool_call,
            record_write,
            should_write,
        )

        coherence_state = ""
        blocking: int | None = None
        warnings: int | None = None
        if mesh_result is not None:
            coherence_state = str(mesh_result.coherence.state or "")
            blocking, warnings = mesh_finding_counts(mesh_result)

        runtime = build_runtime_state(
            project_root,
            session=session,
            habit_state=habit_state,
            tracker=tracker,
            compass=compass,
            last_coherence_state=coherence_state,
            last_blocking=blocking,
            last_warnings=warnings,
        )
        if mesh_result is not None:
            runtime.symbol_coverage_blockers = mesh_symbol_blocker_count(mesh_result)
        focus_intent = derive_focus_intent(tool_name, tool_input)
        if focus_intent:
            runtime.focus_intent = focus_intent[:160]
        save_result = save_runtime_state_with_meta(project_root, runtime)
        save_ok = bool(save_result.written)

        raw_scheduler = session.behavior_compass.get("write_scheduler", {})
        if isinstance(raw_scheduler, dict):
            scheduler = WriteScheduler.from_dict(raw_scheduler)
        else:
            scheduler = WriteScheduler()

        mark_dirty(scheduler)
        if trigger == "tool_call":
            record_tool_call(scheduler)
        effective_trigger = "mode_transition" if transition else trigger
        should_emit = save_ok and should_write(scheduler, runtime.generation, effective_trigger)
        wrote_dynamic = False
        dynamic_status = "save_failed" if not save_ok else "skipped_by_cadence"
        if should_emit:
            wrote_dynamic, dynamic_status = write_dynamic_runtime_files(project_root, runtime)
            if wrote_dynamic:
                record_write(scheduler, runtime.generation)

        session.behavior_compass["write_scheduler"] = scheduler.to_dict()
        log_runtime_state_write_metric(
            RuntimeStateWriteMetric(
                project_root=project_root,
                path_kind="session",
                trigger=effective_trigger,
                mode=str(runtime.mode or "normal"),
                generation=int(runtime.generation),
                attempted=1,
                success=int(wrote_dynamic),
                skipped_by_cadence=int(not should_emit),
                save_ok=save_ok,
                lock_acquired=save_result.lock_acquired,
                lock_contention_count=save_result.contention_count,
                dynamic_status=dynamic_status,
            )
        )


def refresh_runtime_state_lightweight(
    project_root: str,
    *,
    habit_state: Any | None = None,
    tracker: Any | None = None,
    mesh_result: Any | None = None,
    tool_name: str = "",
    tool_input: Any = None,
    trigger: str = "tool_call",
    transition: str | None = None,
    scheduler_dict: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Refresh RuntimeState for non-session paths with scheduler persistence."""
    with contextlib.suppress(Exception):
        from lintgate.runtime_state import (
            build_runtime_state,
            save_runtime_state_with_meta,
        )
        from lintgate.write_scheduler import (
            WriteScheduler,
            mark_dirty,
            record_tool_call,
            record_write,
            should_write,
        )

        runtime = build_runtime_state(
            project_root,
            habit_state=habit_state,
            tracker=tracker,
        )
        if habit_state is not None:
            runtime.mode = "habit" if habit_state.active else "normal"
        if mesh_result is not None:
            runtime.coherence_state = str(mesh_result.coherence.state or runtime.coherence_state)
            runtime.blocking_issues, runtime.warning_issues = mesh_finding_counts(mesh_result)
            runtime.symbol_coverage_blockers = mesh_symbol_blocker_count(mesh_result)

        focus_intent = derive_focus_intent(tool_name, tool_input)
        if focus_intent:
            runtime.focus_intent = focus_intent[:160]
        save_result = save_runtime_state_with_meta(project_root, runtime)
        save_ok = bool(save_result.written)

        if isinstance(scheduler_dict, dict):
            scheduler = WriteScheduler.from_dict(scheduler_dict)
        else:
            scheduler = WriteScheduler()

        mark_dirty(scheduler)
        if trigger == "tool_call":
            record_tool_call(scheduler)
        effective_trigger = "mode_transition" if transition else trigger
        should_emit = save_ok and should_write(scheduler, runtime.generation, effective_trigger)
        wrote_dynamic = False
        dynamic_status = "save_failed" if not save_ok else "skipped_by_cadence"
        if should_emit:
            wrote_dynamic, dynamic_status = write_dynamic_runtime_files(project_root, runtime)
            if wrote_dynamic:
                record_write(scheduler, runtime.generation)

        log_runtime_state_write_metric(
            RuntimeStateWriteMetric(
                project_root=project_root,
                path_kind="standalone",
                trigger=effective_trigger,
                mode=str(runtime.mode or "normal"),
                generation=int(runtime.generation),
                attempted=1,
                success=int(wrote_dynamic),
                skipped_by_cadence=int(not should_emit),
                save_ok=save_ok,
                lock_acquired=save_result.lock_acquired,
                lock_contention_count=save_result.contention_count,
                dynamic_status=dynamic_status,
            )
        )
        return scheduler.to_dict()

    return None
