#!/usr/bin/env python3
"""LintGate PostToolUse hook — intelligent change-aware linting for Claude Code.

This is the main entry point. Claude Code fires this after every
Write, Edit, MultiEdit, or Bash tool use. It classifies the change,
selects appropriate linters, runs them, and reports structured JSON
back to the agent via systemMessage.

Protocol (from hookify reference):
- stdin: JSON with tool_name, tool_input, tool_output, cwd, etc.
- stdout: JSON with systemMessage and optional hookSpecificOutput
- exit: ALWAYS 0 (non-zero blocks the hook system)

Design principles:
- Silent success: {} when no issues found (zero noise)
- Fast path: read-only Bash commands exit immediately
- Graceful degradation: missing tools are skipped, never crash
- Total timeout: 8 seconds max for entire hook
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintgate.controlplane.channel import Channel

try:
    from lintgate.agent_reporter import format_report
    from lintgate.change_classifier import classify_change
    from lintgate.config import load_config
    from lintgate.lint_runner import run_linters
    from lintgate.registry import build_registry
    from lintgate.results_aggregator import aggregate_results
    from lintgate.state import load_last_run, log_metric, save_run, update_issue_memory
    from lintgate.tier_selector import select_tier
except ModuleNotFoundError:
    _LINTGATE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _LINTGATE_DIR not in sys.path:
        sys.path.insert(0, _LINTGATE_DIR)
    from lintgate.agent_reporter import format_report
    from lintgate.change_classifier import classify_change
    from lintgate.config import load_config
    from lintgate.lint_runner import run_linters
    from lintgate.registry import build_registry
    from lintgate.results_aggregator import aggregate_results
    from lintgate.state import load_last_run, log_metric, save_run, update_issue_memory
    from lintgate.tier_selector import select_tier


def _resolve_event_model_key(input_data: dict[str, Any]) -> str | None:
    """Resolve model identity from hook payload fields/env vars.

    Returns canonical provider:model key, or None when unavailable/unresolvable.
    """
    from lintgate.controlplane.model_profiles import resolve_model_key

    candidates: list[str | None] = [
        input_data.get("model"),
        input_data.get("model_id"),
        input_data.get("model_name"),
        input_data.get("assistant_model"),
    ]

    metadata = input_data.get("metadata")
    if isinstance(metadata, dict):
        candidates.extend(
            [
                metadata.get("model"),
                metadata.get("model_id"),
                metadata.get("model_name"),
            ]
        )

    session_meta = input_data.get("session")
    if isinstance(session_meta, dict):
        candidates.extend(
            [
                session_meta.get("model"),
                session_meta.get("model_id"),
                session_meta.get("model_name"),
            ]
        )

    tool_input = input_data.get("tool_input")
    if isinstance(tool_input, dict):
        candidates.extend(
            [
                tool_input.get("model"),
                tool_input.get("model_id"),
            ]
        )

    for env_key in ("LINTGATE_MODEL_ID", "CLAUDE_MODEL", "OPENAI_MODEL", "MODEL"):
        candidates.append(os.environ.get(env_key))

    for raw in candidates:
        if not isinstance(raw, str) or not raw.strip():
            continue
        canonical = resolve_model_key(raw)
        if canonical:
            return canonical

    return None


def _select_telemetry_profile(store: Any, input_data: dict[str, Any]):
    """Pick the exact model profile for telemetry updates.

    Ambiguous fallback (e.g., "most recently updated profile") is intentionally
    disallowed to prevent cross-model contamination.
    """
    model_key = _resolve_event_model_key(input_data)
    if not model_key:
        return None
    profile = store.profiles.get(model_key)
    if profile and profile.is_usable():
        return profile
    return None


_SESSION_TELEMETRY_UPDATE_CAP = 10
_SESSION_TELEMETRY_COUNTER_KEY = "_model_profile_telem_updates"


def _session_telemetry_updates_used(session: Any) -> int:
    """Return telemetry updates applied in the current session."""
    if session is None or not hasattr(session, "behavior_compass"):
        return 0
    bc = session.behavior_compass
    if not isinstance(bc, dict):
        return 0
    value = bc.get(_SESSION_TELEMETRY_COUNTER_KEY, 0)
    return value if isinstance(value, int) and value >= 0 else 0


def _can_apply_session_telemetry(session: Any) -> bool:
    """Check whether this session still has telemetry update budget."""
    return _session_telemetry_updates_used(session) < _SESSION_TELEMETRY_UPDATE_CAP


def _mark_session_telemetry_applied(session: Any) -> None:
    """Increment the per-session telemetry update counter."""
    if session is None or not hasattr(session, "behavior_compass"):
        return
    bc = session.behavior_compass
    if not isinstance(bc, dict):
        return
    bc[_SESSION_TELEMETRY_COUNTER_KEY] = _session_telemetry_updates_used(session) + 1


def main() -> None:
    """Main hook entry point."""
    start = time.perf_counter()

    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        # No input or malformed — pass through silently
        _exit_clean()

    if not isinstance(input_data, dict):
        _exit_clean()

    tool_name = input_data.get("tool_name", "")
    if not isinstance(tool_name, str):
        tool_name = ""

    raw_tool_input = input_data.get("tool_input", {})
    if isinstance(raw_tool_input, dict):
        tool_input = raw_tool_input
    elif tool_name == "Bash" and isinstance(raw_tool_input, str):
        tool_input = {"command": raw_tool_input}
    else:
        tool_input = {}

    tool_output = input_data.get("tool_output", "")
    if not isinstance(tool_output, str):
        tool_output = str(tool_output)

    cwd = input_data.get("cwd", os.getcwd())
    if not isinstance(cwd, str) or not cwd:
        cwd = os.getcwd()

    try:
        # Quick exit: not a tool we care about
        if tool_name not in ("Write", "Edit", "MultiEdit", "Bash"):
            _exit_clean()

        # Phase 0: Load config (fast — reads one YAML file or auto-detects)
        try:
            config = load_config(cwd)
        except Exception:
            config = _fallback_config(cwd)

        # ControlPlane dispatch: if enabled, run the supervision mesh instead
        try:
            from lintgate.config import load_controlplane_config

            cp_config = load_controlplane_config(cwd)
            if cp_config and cp_config.enabled:
                _run_controlplane(input_data, config, cp_config, cwd, start)
                return  # controlplane handles output and exit
        except Exception:
            pass  # Fall through to legacy pipeline on any error

        # Phase 1: Classify the change
        classification = classify_change(tool_name, tool_input, tool_output, cwd, config)

        # Quick exit: no-op changes
        if classification.risk_level == "none":
            _exit_clean()

        # Phase 1.5: Dependency health check (fast path, < 5ms)
        dep_warnings: list[str] = []
        if classification.change_kind in ("dependency", "build"):
            with contextlib.suppress(Exception):
                from lintgate.dependency_health import quick_dependency_check

                dep_warnings = quick_dependency_check(cwd, classification.change_kind, tool_input)

        # Phase 2: Select lint tier
        tier = select_tier(classification, config)

        # Quick exit: tier says skip
        if tier.skip:
            _exit_clean()

        # Phase 3: Build linter registry and run
        registry = build_registry(config)
        remaining_ms = config.total_timeout_ms - int((time.perf_counter() - start) * 1000)
        remaining_ms = max(remaining_ms, 2000)  # At least 2s for linters

        linter_results = run_linters(tier, config, registry, timeout_ms=remaining_ms)

        # Phase 4: Aggregate results
        aggregated = aggregate_results(
            linter_results,
            config,
            tier_name=tier.name,
            tier_reason=tier.reason,
        )

        # Track recurring issue patterns before formatting.
        all_issues = [*aggregated.blocking, *aggregated.warnings, *aggregated.informational]
        recurrence = {"repeated_issue_count": 0, "unique_signatures_tracked": 0, "top_repeated": []}
        with contextlib.suppress(Exception):
            recurrence = update_issue_memory(cwd, all_issues)

        # Phase 3.5: Update pattern bank (categorical anti-tail-chasing)
        pattern_report: dict[str, list[str]] = {"alerted_patterns": [], "top_categories": []}
        with contextlib.suppress(Exception):
            from lintgate.pattern_bank import update_pattern_bank

            pattern_report = update_pattern_bank(cwd, all_issues)

        # Phase 5: Format report
        last_run = load_last_run(cwd)
        report = format_report(
            aggregated,
            last_run,
            recurrence_summary=recurrence,
            pattern_report=pattern_report,
        )

        # Save state for delta tracking
        with contextlib.suppress(Exception):
            save_run(cwd, aggregated)

        # Log metrics (non-blocking)
        with contextlib.suppress(Exception):
            elapsed_ms = (time.perf_counter() - start) * 1000
            log_metric(
                {
                    "event": "lint_run",
                    "project": cwd,
                    "tier": tier.name,
                    "change_kind": classification.change_kind,
                    "risk_level": classification.risk_level,
                    "files": classification.files_changed,
                    "blocking_count": len(aggregated.blocking),
                    "warning_count": len(aggregated.warnings),
                    "info_count": len(aggregated.informational),
                    "linters_run": aggregated.metrics.get("linters_run", 0),
                    "duration_ms": round(elapsed_ms, 1),
                    "repeated_issue_count": recurrence.get("repeated_issue_count", 0),
                }
            )

        # Inject dependency health warnings into report
        if dep_warnings:
            if not report:
                report = {}
            dep_msg = "\n".join(dep_warnings)
            existing = report.get("systemMessage", "")
            if existing:
                report["systemMessage"] = existing + "\n\n--- Dependency Health ---\n" + dep_msg
            else:
                report["systemMessage"] = "--- Dependency Health ---\n" + dep_msg

        # Output
        if report:
            print(json.dumps(report))
        else:
            print(json.dumps({}))

        sys.exit(0)
    except Exception:
        _exit_clean()


def _derive_focus_intent(tool_name: str, tool_input: Any) -> str:
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


def _mesh_finding_counts(mesh_result: Any) -> tuple[int, int]:
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


def _runtime_targets(registry: Any, project_root: str) -> list[str]:
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


def _write_dynamic_runtime_files(project_root: str, runtime_state: Any) -> tuple[bool, str]:
    """Write dynamic rule files for all detected runtime hosts.

    Returns:
        (success, status)
        status ∈ {"success", "no_targets", "write_failed", "error"}.
    """
    try:
        from lintgate.renderers import build_default_registry
        from lintgate.renderers.dynamic import write_dynamic_file

        registry = build_default_registry()
        targets = _runtime_targets(registry, project_root)
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


def _log_runtime_state_write_metric(
    project_root: str,
    *,
    path_kind: str,
    trigger: str,
    mode: str,
    generation: int,
    attempted: int,
    success: int,
    skipped_by_cadence: int,
    save_ok: bool,
    lock_acquired: bool,
    lock_contention_count: int,
    dynamic_status: str,
) -> None:
    """Log runtime state write telemetry for cadence tuning."""
    with contextlib.suppress(Exception):
        from lintgate.state import log_metric

        log_metric(
            {
                "event": "runtime_state_write",
                "project": project_root,
                "path": path_kind,
                "trigger": trigger,
                "mode": mode,
                "generation": generation,
                "attempted": int(bool(attempted)),
                "success": int(bool(success)),
                "skipped_by_cadence": int(bool(skipped_by_cadence)),
                "save_ok": bool(save_ok),
                "lock_acquired": bool(lock_acquired),
                "lock_contention_count": max(0, int(lock_contention_count)),
                "dynamic_status": dynamic_status,
            }
        )


def _refresh_runtime_state_with_session(
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
            blocking, warnings = _mesh_finding_counts(mesh_result)

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
        focus_intent = _derive_focus_intent(tool_name, tool_input)
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
            wrote_dynamic, dynamic_status = _write_dynamic_runtime_files(project_root, runtime)
            if wrote_dynamic:
                record_write(scheduler, runtime.generation)

        session.behavior_compass["write_scheduler"] = scheduler.to_dict()
        _log_runtime_state_write_metric(
            project_root,
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


def _refresh_runtime_state_lightweight(
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
            runtime.blocking_issues, runtime.warning_issues = _mesh_finding_counts(mesh_result)

        focus_intent = _derive_focus_intent(tool_name, tool_input)
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
            wrote_dynamic, dynamic_status = _write_dynamic_runtime_files(project_root, runtime)
            if wrote_dynamic:
                record_write(scheduler, runtime.generation)

        _log_runtime_state_write_metric(
            project_root,
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
        return scheduler.to_dict()

    return None


def _record_behavior_event(cp_config, cwd: str, tool_name: str, tool_input, tool_output) -> None:
    """Record tool event in behavior compass (all events, including read-only).

    Path A: When session_memory is enabled, piggybacks habit mode tracking
    on the existing compass/session flow for richer signals.
    """
    if not (cp_config.channel_enabled("behavior") and cp_config.session_memory):
        return
    with contextlib.suppress(Exception):
        from lintgate.controlplane.behavior_compass import record_tool_event
        from lintgate.controlplane.session_memory import (
            get_or_create_session,
            load_behavior_compass,
            save_behavior_compass,
            save_session,
        )

        session = get_or_create_session(cwd, cp_config.session_max_age_hours)
        compass = load_behavior_compass(session)
        record_tool_event(compass, tool_name, tool_input, tool_output)
        save_behavior_compass(session, compass)

        # Path A: Habit mode tracking piggybacking on compass/session
        if cp_config.habit_mode_enabled:
            _update_habit_mode_path_a(
                cp_config,
                session,
                compass,
                cwd,
                tool_name,
                tool_input,
                tool_output,
            )
        else:
            _refresh_runtime_state_with_session(
                cwd,
                session,
                compass=compass,
                tool_name=tool_name,
                tool_input=tool_input,
                trigger="tool_call",
            )

        save_session(session)


def _check_habit_api_calibration(
    tracker,
    event_counter: int,
    cwd: str,
    overrides: dict,
    cp_config,
) -> None:
    """Run token API calibration if interval has elapsed."""
    from lintgate.state import log_metric
    from lintgate.token_tracker import do_api_calibration, should_api_check

    api_interval = overrides.get("token_api_interval", cp_config.habit_mode_token_api_interval)
    if should_api_check(tracker, event_counter, interval=api_interval):
        with contextlib.suppress(Exception):
            result = do_api_calibration(tracker, event_counter, cwd)
            if result:
                log_metric(
                    {
                        "event": "token_estimate",
                        "project": cwd,
                        "source": "api",
                        **result,
                    }
                )


def _try_habit_compaction(
    tracker,
    habit_state,
    overrides: dict,
    cp_config,
    cwd: str,
    event_counter: int,
    *,
    session_memory: dict | None = None,
    compass_dict: dict | None = None,
    last_lint_run: dict | None = None,
) -> tuple[bool, dict | None]:
    """Check compaction trigger and build snapshot if needed.

    Returns (did_compact, snapshot_or_None).
    """
    from lintgate.habit_mode import build_compaction_snapshot
    from lintgate.state import log_metric
    from lintgate.token_tracker import get_usage_summary, reset_post_compaction, should_compact

    compact_threshold = float(
        overrides.get("compact_threshold", cp_config.habit_mode_compact_threshold)
    )
    if not should_compact(tracker, habit_state.active, threshold=compact_threshold):
        return False, None

    snapshot = None
    with contextlib.suppress(Exception):
        token_summary = get_usage_summary(tracker)
        snapshot = build_compaction_snapshot(
            habit_state,
            cwd,
            session_memory=session_memory,
            compass=compass_dict,
            last_lint_run=last_lint_run,
            token_estimate=token_summary,
        )
        habit_state.compaction_count += 1
        habit_state.last_compaction_event = event_counter
        estimated_before = tracker.estimated_tokens_used
        calls_compacted = tracker.tool_calls_since_compact
        sections_included = sum(1 for v in snapshot.values() if v is not None)
        reset_post_compaction(tracker)
        log_metric(
            {
                "event": "habit_compact",
                "project": cwd,
                "compaction_number": habit_state.compaction_count,
                "habit_score": habit_state.habit_score,
                "estimated_tokens_before": estimated_before,
                "tool_calls_compacted": calls_compacted,
                "sections_included": sections_included,
                "trigger": "auto",
            }
        )
    return snapshot is not None, snapshot


def _update_habit_mode_path_a(
    cp_config,
    session,
    compass,
    cwd,
    tool_name,
    tool_input,
    tool_output,
) -> None:
    """Path A: Habit mode tracking with full session/compass context.

    Called from _record_behavior_event when session_memory is enabled.
    Piggybacks on existing compass action_history for richer signals.
    """
    with contextlib.suppress(Exception):
        from lintgate.habit_mode import (
            detect_test_result,
            load_habit_state,
            save_habit_state,
            track_active_files,
            update_mode,
            update_signals,
        )
        from lintgate.state import load_last_run, log_feature_usage, log_metric
        from lintgate.token_tracker import (
            estimate_tool_tokens,
            load_tracker_state,
            save_tracker_state,
        )

        habit_state = load_habit_state(session.behavior_compass)
        tracker = load_tracker_state(session.behavior_compass)

        # Feature-usage telemetry: one emission per session.
        if not session.behavior_compass.get("_feature_habit_mode_logged", False):
            with contextlib.suppress(Exception):
                log_feature_usage("habit_mode", cwd, {"source": "hook_posttooluse"})
            session.behavior_compass["_feature_habit_mode_logged"] = True
        if not session.behavior_compass.get("_feature_token_tracking_logged", False):
            with contextlib.suppress(Exception):
                log_feature_usage("token_tracking", cwd, {"source": "hook_posttooluse"})
            session.behavior_compass["_feature_token_tracking_logged"] = True

        # Update signals from compass action_history (rich data)
        update_signals(habit_state, compass.action_history)
        track_active_files(habit_state, tool_name, tool_input)
        estimate_tool_tokens(tracker, tool_name, tool_input, tool_output)

        # Test result detection
        if tool_name == "Bash":
            cmd_sig = ""
            if compass.action_history:
                cmd_sig = compass.action_history[-1].get("sig", "")
            out_str = tool_output if isinstance(tool_output, str) else ""
            detect_test_result(habit_state, out_str, cmd_sig)

        # Runtime overrides + auto-detect gating
        overrides = session.behavior_compass.get("habit_config_overrides", {})
        if not isinstance(overrides, dict):
            overrides = {}

        context_window_size = overrides.get("context_window_size")
        if context_window_size is not None:
            with contextlib.suppress(Exception):
                tracker.context_window_size = int(context_window_size)

        auto_detect_enabled = bool(overrides.get("auto_detect", cp_config.habit_mode_auto_detect))
        transition = None
        if auto_detect_enabled:
            transition = update_mode(
                habit_state,
                compass.event_counter,
                enter_score=overrides.get("enter_score", cp_config.habit_mode_enter_score),
                exit_score=overrides.get("exit_score", cp_config.habit_mode_exit_score),
                sustain_calls=overrides.get("sustain_calls", cp_config.habit_mode_sustain_calls),
            )
        elif habit_state.active:
            # Declaration-driven mode still tracks event volume while active.
            habit_state.total_events_in_habit += 1

        _check_habit_api_calibration(tracker, compass.event_counter, cwd, overrides, cp_config)

        if transition:
            with contextlib.suppress(Exception):
                log_metric(
                    {
                        "event": "habit_mode_transition",
                        "project": cwd,
                        "transition": transition,
                        "habit_score": habit_state.habit_score,
                        "trigger": "auto_detect",
                        "event_counter": compass.event_counter,
                    }
                )

        # Auto-compaction trigger in active habit mode.
        did_compact, snapshot = _try_habit_compaction(
            tracker,
            habit_state,
            overrides,
            cp_config,
            cwd,
            compass.event_counter,
            session_memory=session.to_dict(),
            compass_dict=compass.to_dict(),
            last_lint_run=load_last_run(cwd),
        )
        if did_compact and snapshot:
            session.behavior_compass["habit_last_snapshot"] = snapshot

        save_habit_state(session.behavior_compass, habit_state)
        save_tracker_state(session.behavior_compass, tracker)

        # Write-through to standalone file so MCP tools (separate process)
        # can read habit state even when session_memory is enabled.
        with contextlib.suppress(Exception):
            from lintgate.habit_mode import save_habit_state_standalone

            action_ring = [
                a for a in (compass.action_history or [])[-20:]
            ]
            save_habit_state_standalone(
                cwd,
                habit_state,
                action_ring,
                tracker_dict=tracker.to_dict(),
                config_overrides=overrides,
                last_snapshot=session.behavior_compass.get("habit_last_snapshot"),
            )

        _refresh_runtime_state_with_session(
            cwd,
            session,
            compass=compass,
            habit_state=habit_state,
            tracker=tracker,
            tool_name=tool_name,
            tool_input=tool_input,
            trigger="compaction" if did_compact else "tool_call",
            transition=transition,
        )


def _record_habit_event_lightweight(
    cp_config,
    cwd: str,
    tool_name: str,
    tool_input,
    tool_output,
) -> None:
    """Path B: Lightweight habit mode tracking when session_memory is off.

    Uses standalone file-backed state with a minimal action ring buffer.
    Called from _run_controlplane when habit_mode is enabled but session_memory is not.
    """
    if not cp_config.habit_mode_enabled:
        return
    # Skip if session_memory is on — Path A handles it
    if cp_config.session_memory and cp_config.channel_enabled("behavior"):
        return

    with contextlib.suppress(Exception):
        import re
        import time

        from lintgate.habit_mode import (
            MAX_ACTION_RING,
            detect_test_result,
            load_habit_state_standalone,
            load_standalone_extras,
            quick_intent,
            save_habit_state_standalone,
            track_active_files,
            update_mode,
            update_signals,
        )
        from lintgate.state import log_metric
        from lintgate.token_tracker import (
            TokenTrackerState,
            estimate_tool_tokens,
        )

        habit_state, action_ring = load_habit_state_standalone(cwd)
        extras = load_standalone_extras(cwd)
        raw_tracker = extras.get("token_tracker", {})
        if not isinstance(raw_tracker, dict):
            raw_tracker = {}
        tracker = TokenTrackerState.from_dict(raw_tracker)
        standalone_overrides = extras.get("config_overrides", {})
        if not isinstance(standalone_overrides, dict):
            standalone_overrides = {}
        standalone_scheduler = extras.get("write_scheduler", {})
        if not isinstance(standalone_scheduler, dict):
            standalone_scheduler = {}
        last_snapshot = extras.get("habit_last_snapshot")
        if not isinstance(last_snapshot, dict):
            last_snapshot = None
        context_window_size = standalone_overrides.get("context_window_size")
        if context_window_size is not None:
            with contextlib.suppress(Exception):
                tracker.context_window_size = int(context_window_size)

        # Maintain minimal action ring buffer
        sig = ""
        command_text = ""
        if isinstance(tool_input, dict):
            sig = str(tool_input.get("file_path") or tool_input.get("path") or "")
            command_text = str(tool_input.get("command", ""))
        elif isinstance(tool_input, str):
            command_text = tool_input
        if tool_name == "Bash":
            sig = command_text
        action_ring.append(
            {
                "tool": tool_name,
                "ts": time.time(),
                "intent": quick_intent(tool_name),
                "sig": sig,
            }
        )
        if len(action_ring) > MAX_ACTION_RING:
            action_ring = action_ring[-MAX_ACTION_RING:]

        update_signals(habit_state, action_ring)
        track_active_files(habit_state, tool_name, tool_input)
        estimate_tool_tokens(tracker, tool_name, tool_input, tool_output)

        # Test result detection for Bash
        if tool_name == "Bash":
            out_str = tool_output if isinstance(tool_output, str) else ""
            if command_text and re.search(r"\b(pytest|test)\b", command_text.lower()):
                detect_test_result(habit_state, out_str, command_text)

        event_counter = tracker.tool_call_count
        auto_detect_enabled = bool(
            standalone_overrides.get(
                "auto_detect",
                cp_config.habit_mode_auto_detect,
            )
        )
        transition = None
        if auto_detect_enabled:
            transition = update_mode(
                habit_state,
                event_counter,
                enter_score=standalone_overrides.get(
                    "enter_score", cp_config.habit_mode_enter_score
                ),
                exit_score=standalone_overrides.get("exit_score", cp_config.habit_mode_exit_score),
                sustain_calls=standalone_overrides.get(
                    "sustain_calls",
                    cp_config.habit_mode_sustain_calls,
                ),
            )
        elif habit_state.active:
            habit_state.total_events_in_habit += 1

        _check_habit_api_calibration(tracker, event_counter, cwd, standalone_overrides, cp_config)

        if transition:
            with contextlib.suppress(Exception):
                log_metric(
                    {
                        "event": "habit_mode_transition",
                        "project": cwd,
                        "transition": transition,
                        "habit_score": habit_state.habit_score,
                        "trigger": "auto_detect_lightweight",
                        "event_counter": event_counter,
                    }
                )

        did_compact, compact_snapshot = _try_habit_compaction(
            tracker,
            habit_state,
            standalone_overrides,
            cp_config,
            cwd,
            event_counter,
        )
        if did_compact and compact_snapshot:
            last_snapshot = compact_snapshot

        updated_scheduler = _refresh_runtime_state_lightweight(
            cwd,
            habit_state=habit_state,
            tracker=tracker,
            tool_name=tool_name,
            tool_input=tool_input,
            trigger="compaction" if did_compact else "tool_call",
            transition=transition,
            scheduler_dict=standalone_scheduler,
        )
        if isinstance(updated_scheduler, dict):
            standalone_scheduler = updated_scheduler

        save_habit_state_standalone(
            cwd,
            habit_state,
            action_ring,
            tracker_dict=tracker.to_dict(),
            config_overrides=standalone_overrides,
            last_snapshot=last_snapshot,
            scheduler_dict=standalone_scheduler,
        )


def _load_global_priors(cp_config) -> dict | None:
    """Load global behavior profile priors if enabled and sufficient data exists."""
    if not (cp_config.global_memory_enabled and cp_config.channel_enabled("behavior")):
        return None
    try:
        from lintgate.controlplane.global_behavior_profile import (
            MIN_SAMPLE_SIZE,
            load_global_profile,
        )

        gp = load_global_profile(ttl_days=cp_config.global_memory_ttl_days)
        if gp.session_count >= MIN_SAMPLE_SIZE:
            return {
                "enabled": True,
                "alpha": cp_config.global_memory_alpha,
                "decay_horizon": cp_config.global_memory_decay_horizon,
                "computed_bias_adjustments": gp.computed_bias_adjustments,
            }
    except Exception:
        pass
    return None


def _setup_session_and_gate(
    cp_config, cwd: str, tool_name: str, event, channels: list, global_priors: dict | None
) -> tuple[Any, str | None]:
    """Set up session memory, theory profile, and session gate. Returns (session, advisory)."""
    session = None
    advisory: str | None = None

    if cp_config.session_memory:
        with contextlib.suppress(Exception):
            from lintgate.controlplane.session_memory import get_or_create_session

            session = get_or_create_session(cwd, cp_config.session_max_age_hours)

    if session is not None and cp_config.channel_enabled("behavior"):
        event.raw_input["behavior_compass"] = session.behavior_compass

    if global_priors is not None:
        event.raw_input["behavior_global_priors"] = global_priors

    # Cache theory profile once per mesh run
    if session is not None and cp_config.inquiry.any_enabled():
        try:
            from lintgate.theory_extractor import extract_theory

            session.theory_profile_cache = extract_theory(cwd).get("theory_profile")
        except Exception:
            session.theory_profile_cache = None

        if session.theory_profile_cache is not None:
            event.raw_input["theory_profile"] = session.theory_profile_cache

    # Advisory gate: warn when editing without sufficient theory context
    if (
        session is not None
        and cp_config.inquiry.session_gate
        and tool_name in ("Write", "Edit", "MultiEdit")
        and not session.behavior_compass.get("_session_ready", False)
    ):
        with contextlib.suppress(Exception):
            from lintgate.context_auditor import check_session_readiness

            readiness = check_session_readiness(cwd, theory_profile=session.theory_profile_cache)
            if not readiness.ready:
                advisory = (
                    f"[Session Advisory] Context not ready for deep supervision. "
                    f"Missing: {', '.join(readiness.missing)}. "
                    f"{readiness.recommendation}"
                )
                channels[:] = [ch for ch in channels if ch.name != "behavior"]
            else:
                session.behavior_compass["_session_ready"] = True

    return session, advisory


def _apply_behavior_delta(session, cr, cp_config, input_data: dict) -> list[str]:
    """Apply behavior compass delta, global profile delta, and model telemetry from a channel result."""
    snapshot_alerts = [f.kind for f in cr.findings]

    # Apply compass delta (cooldown counters, nudge flags)
    if "behavior_compass_delta" in cr.metrics:
        from lintgate.controlplane.session_memory import (
            load_behavior_compass,
            save_behavior_compass,
        )

        delta = cr.metrics["behavior_compass_delta"]
        existing_telem = _session_telemetry_updates_used(session)
        bc = load_behavior_compass(session)
        for key in (
            "last_fired",
            "signal_fire_counts",
            "early_nudge_emitted",
            "pending_nudge_signals",
            "pending_nudge_constraint_check_count",
            "nudge_outcomes",
        ):
            if key in delta:
                setattr(bc, key, delta[key])
        save_behavior_compass(session, bc)
        if existing_telem > 0:
            session.behavior_compass[_SESSION_TELEMETRY_COUNTER_KEY] = existing_telem
        # Merge theory coda dedup state
        if "_theory_recent_codas" in delta:
            existing_codas = session.behavior_compass.get("_theory_recent_codas", {})
            existing_codas.update(delta["_theory_recent_codas"])
            session.behavior_compass["_theory_recent_codas"] = existing_codas

    # Apply global profile delta
    if cp_config.global_memory_enabled and "global_profile_delta" in cr.metrics:
        with contextlib.suppress(Exception):
            from lintgate.controlplane.global_behavior_profile import (
                apply_session_delta,
                load_global_profile,
                save_global_profile,
            )

            gp = load_global_profile(ttl_days=cp_config.global_memory_ttl_days)
            apply_session_delta(
                gp,
                cr.metrics["global_profile_delta"],
                session_id=session.session_id if session else "",
            )
            save_global_profile(gp)

    # Model profile telemetry refinement
    with contextlib.suppress(Exception):
        from lintgate.controlplane.model_profiles import (
            apply_telemetry_update,
            load_profiles,
            save_profiles,
        )

        store = load_profiles()
        active = _select_telemetry_profile(store, input_data)
        signal_fires = {}
        event_count = 0
        if session:
            bc_data = session.behavior_compass
            if isinstance(bc_data, dict):
                signal_fires = bc_data.get("signal_fire_counts", {})
                event_count = bc_data.get("event_counter", 0)
        if (
            active is not None
            and signal_fires
            and event_count >= 10
            and _can_apply_session_telemetry(session)
        ):
            apply_telemetry_update(active, signal_fires, event_count)
            _mark_session_telemetry_applied(session)
            save_profiles(store)

    return snapshot_alerts


def _record_snapshot_behavior(snapshot, tool_name: str, tool_input, tool_output) -> None:
    """Record tool-level behavioral fields on a snapshot."""
    snapshot.behavior.action_type = tool_name.lower()
    if tool_name != "Bash":
        return

    from lintgate.controlplane.behavior_compass import extract_error_sig, normalize_command_sig

    cmd = (
        tool_input.get("command", "")
        if isinstance(tool_input, dict)
        else (tool_input if isinstance(tool_input, str) else "")
    )
    snapshot.behavior.command_signature = normalize_command_sig(cmd)
    snapshot.behavior.error_signature = extract_error_sig(
        tool_output if isinstance(tool_output, str) else ""
    )
    output_str = tool_output if isinstance(tool_output, str) else str(tool_output)
    exit_match = re.search(
        r"(?:exit[_ ]code|exit[_ ]status|exitstatus)[: =]+(\d+)",
        output_str,
        re.IGNORECASE,
    )
    if exit_match:
        snapshot.behavior.exit_code = int(exit_match.group(1))
    elif "error" in output_str.lower() or "failed" in output_str.lower():
        snapshot.behavior.exit_code = 1
    else:
        snapshot.behavior.exit_code = 0


def _run_constraint_proposer(session, mesh_result, cp_config) -> list[dict]:
    """Run constraint proposer on pattern alerts and return proposed constraints."""
    proposed: list[dict] = []
    with contextlib.suppress(Exception):
        from lintgate.controlplane.constraint_proposer import (
            propose_constraints_from_patterns,
            store_proposals_in_session,
        )

        pattern_alerts: list[dict] = []
        for cr in mesh_result.channel_results:
            if cr.channel == "lint":
                pattern_alerts.extend(cr.metrics.get("pattern_alerts", []))
                break

        # Promote recurring behavior findings via session trend history
        for key, counts in session.pattern_trend.items():
            if "|" not in key:
                continue
            linter, kind = key.split("|", 1)
            if linter != "behavior_channel":
                continue
            recent = counts[-5:]
            recent_run_count = sum(1 for c in recent if c > 0)
            if recent_run_count > 0:
                pattern_alerts.append(
                    {
                        "linter": linter,
                        "kind": kind,
                        "alert_reason": "recurring_across_runs",
                        "recent_run_count": recent_run_count,
                    }
                )

        if pattern_alerts:
            proposals = propose_constraints_from_patterns(
                {"alerted_patterns": pattern_alerts},
                session=session,
                threshold=cp_config.constraint_proposal_threshold,
                config=cp_config,
            )
            if proposals:
                store_proposals_in_session(session, proposals)

        proposed = session.proposed_constraints
    return proposed


def _save_run_details(mesh_result, finding_index: dict) -> None:
    """Persist full run details for controlplane_get_details drill-down."""
    if not finding_index:
        return
    with contextlib.suppress(Exception):
        from lintgate.state import save_controlplane_run

        details: dict = {
            "coherence": {
                "state": mesh_result.coherence.state,
                "summary": mesh_result.coherence.summary,
                "recommended_action": mesh_result.coherence.recommended_action,
                "silent_channels": list(mesh_result.coherence.silent_channels),
                "loud_channels": list(mesh_result.coherence.loud_channels),
            },
            "duration_ms": mesh_result.duration_ms,
            "partial": mesh_result.partial,
            "incomplete_channels": mesh_result.incomplete_channels,
            "finding_index": finding_index,
            "channels": {},
        }
        for cr in mesh_result.channel_results:
            if cr.status == "skip":
                continue
            details["channels"][cr.channel] = {
                "status": cr.status,
                "severity": cr.severity,
                "duration_ms": round(cr.duration_ms, 1),
                "error": cr.error_message,
                "findings": [f.to_dict() for f in cr.findings],
                "repairs": [
                    {
                        "action_id": r.action_id,
                        "kind": r.kind,
                        "summary": r.summary,
                        "safe": r.safe,
                        "payload": r.payload,
                    }
                    for r in cr.repairs
                ],
                "metrics": cr.metrics,
            }
        run_id = mesh_result.event.event_id if mesh_result.event else ""
        if run_id:
            save_controlplane_run(run_id, details)


def _extract_finding_indexes(
    session: Any,
) -> tuple[dict | None, dict | None, int]:
    """Extract previous and baseline finding indexes from session snapshots."""
    previous_finding_index: dict | None = None
    baseline_finding_index: dict | None = None
    snapshot_count = 0
    if session is not None:
        with contextlib.suppress(Exception):
            if session.snapshots:
                snapshot_count = len(session.snapshots)
                previous_finding_index = session.snapshots[-1].finding_index
                baseline_finding_index = session.snapshots[0].finding_index
    return previous_finding_index, baseline_finding_index, snapshot_count


def _post_process_session(
    session: Any,
    mesh_result: Any,
    finding_index: dict,
    cp_config: Any,
    input_data: dict,
    tool_name: str,
    tool_input: Any,
    tool_output: str,
) -> list[dict]:
    """Post-process session after mesh run: record, apply deltas, propose constraints."""
    proposed_constraints: list[dict] = []
    if session is None:
        return proposed_constraints

    with contextlib.suppress(Exception):
        from lintgate.controlplane.session_memory import record_mesh_run

        snapshot = record_mesh_run(session, mesh_result, finding_index=finding_index)

        for cr in mesh_result.channel_results:
            if cr.channel == "behavior":
                snapshot.behavior.behavior_alerts = _apply_behavior_delta(
                    session,
                    cr,
                    cp_config,
                    input_data,
                )
                break

        _record_snapshot_behavior(snapshot, tool_name, tool_input, tool_output)

    proposed_constraints = _run_constraint_proposer(session, mesh_result, cp_config)

    with contextlib.suppress(Exception):
        from lintgate.controlplane.session_memory import save_session

        save_session(session)
    session.theory_profile_cache = None

    return proposed_constraints


def _accumulate_session_telemetry(report: dict | None, session: Any) -> None:
    """Merge telemetry counters from report into session memory."""
    telemetry = report.get("_telemetry", {}) if report else {}
    if not telemetry or session is None:
        return
    with contextlib.suppress(Exception):
        from lintgate.controlplane.session_memory import save_session

        existing = session.behavior_compass.get("telemetry_counters", {})
        if not isinstance(existing, dict):
            existing = {}
        for key, value in telemetry.items():
            existing[key] = existing.get(key, 0) + value
        session.behavior_compass["telemetry_counters"] = existing
        save_session(session)


def _refresh_runtime_after_run(
    cwd: str,
    session: Any,
    cp_config: Any,
    mesh_result: Any,
    tool_name: str,
    tool_input: Any,
) -> None:
    """Refresh runtime state after a controlplane run."""
    if session is not None:
        _refresh_runtime_state_with_session(
            cwd,
            session,
            mesh_result=mesh_result,
            tool_name=tool_name,
            tool_input=tool_input,
            trigger="lint_complete",
        )
        with contextlib.suppress(Exception):
            from lintgate.controlplane.session_memory import save_session

            save_session(session)
    else:
        scheduler_dict: dict[str, Any] | None = None
        if cp_config.habit_mode_enabled and not cp_config.session_memory:
            with contextlib.suppress(Exception):
                from lintgate.habit_mode import (
                    load_habit_state_standalone,
                    load_standalone_extras,
                    save_habit_state_standalone,
                )

                extras = load_standalone_extras(cwd)
                raw_scheduler = extras.get("write_scheduler", {})
                if isinstance(raw_scheduler, dict):
                    scheduler_dict = raw_scheduler
                updated = _refresh_runtime_state_lightweight(
                    cwd,
                    mesh_result=mesh_result,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    trigger="lint_complete",
                    scheduler_dict=scheduler_dict,
                )
                if isinstance(updated, dict):
                    scheduler_dict = updated
                    habit_state, action_ring = load_habit_state_standalone(cwd)
                    save_habit_state_standalone(
                        cwd,
                        habit_state,
                        action_ring,
                        tracker_dict=extras.get("token_tracker")
                        if isinstance(extras.get("token_tracker"), dict)
                        else None,
                        config_overrides=extras.get("config_overrides")
                        if isinstance(extras.get("config_overrides"), dict)
                        else None,
                        last_snapshot=extras.get("habit_last_snapshot")
                        if isinstance(extras.get("habit_last_snapshot"), dict)
                        else None,
                        scheduler_dict=scheduler_dict,
                    )
        else:
            _refresh_runtime_state_lightweight(
                cwd,
                mesh_result=mesh_result,
                tool_name=tool_name,
                tool_input=tool_input,
                trigger="lint_complete",
            )


def _run_controlplane(input_data: dict, config, cp_config, cwd: str, start: float) -> None:
    """Run the ControlPlane supervision mesh."""
    from lintgate.channels.dependency_channel import DependencyChannel
    from lintgate.channels.git_channel import GitChannel
    from lintgate.channels.lint_channel import LintChannel
    from lintgate.channels.structure_channel import StructureChannel
    from lintgate.channels.test_channel import TestChannel
    from lintgate.controlplane.reporter import format_mesh_report
    from lintgate.controlplane.runtime import run_mesh
    from lintgate.controlplane.types import SupervisionEvent

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})
    tool_output = input_data.get("tool_output", "")

    classification = classify_change(tool_name, tool_input, tool_output, cwd, config)

    _record_behavior_event(cp_config, cwd, tool_name, tool_input, tool_output)
    _record_habit_event_lightweight(cp_config, cwd, tool_name, tool_input, tool_output)
    global_priors = _load_global_priors(cp_config)

    if classification.risk_level == "none":
        _exit_clean()

    event = SupervisionEvent(
        surface="hook",
        project_root=cwd,
        tool_name=tool_name,
        files_changed=classification.files_changed,
        change_classification=classification,
        raw_input=input_data,
    )

    channels: list[Channel] = [LintChannel(), TestChannel(), DependencyChannel(), GitChannel()]
    if cp_config.channel_enabled("structure"):
        channels.append(StructureChannel())
    if cp_config.channel_enabled("behavior"):
        from lintgate.channels.behavior_channel import BehaviorChannel

        channels.append(BehaviorChannel())

    session, advisory = _setup_session_and_gate(
        cp_config,
        cwd,
        tool_name,
        event,
        channels,
        global_priors,
    )

    mesh_result = run_mesh(event, cp_config, channels, session=session)

    # Build finding index for session delta tracking
    finding_index: dict = {}
    with contextlib.suppress(Exception):
        from lintgate.controlplane.reporter import build_finding_index

        finding_index = build_finding_index(mesh_result)

    previous_finding_index, baseline_finding_index, snapshot_count = _extract_finding_indexes(
        session
    )

    proposed_constraints = _post_process_session(
        session,
        mesh_result,
        finding_index,
        cp_config,
        input_data,
        tool_name,
        tool_input,
        tool_output,
    )

    _save_run_details(mesh_result, finding_index)

    report = format_mesh_report(
        mesh_result,
        cp_config,
        proposed_constraints=proposed_constraints,
        previous_finding_index=previous_finding_index,
        baseline_finding_index=baseline_finding_index,
        snapshot_count=snapshot_count,
    )

    _accumulate_session_telemetry(report, session)
    _refresh_runtime_after_run(cwd, session, cp_config, mesh_result, tool_name, tool_input)

    # Strip internal telemetry from output (not for the agent)
    telemetry = report.get("_telemetry", {}) if report else {}
    if report and "_telemetry" in report:
        del report["_telemetry"]

    with contextlib.suppress(Exception):
        elapsed_ms = (time.perf_counter() - start) * 1000
        metric_data: dict[str, Any] = {
            "event": "controlplane_run",
            "project": cwd,
            "tool_name": tool_name,
            "change_kind": classification.change_kind,
            "risk_level": classification.risk_level,
            "coherence_state": mesh_result.coherence.state,
            "channels_run": len([r for r in mesh_result.channel_results if r.status != "skip"]),
            "partial": mesh_result.partial,
            "duration_ms": round(elapsed_ms, 1),
            "session_active": session is not None,
        }
        if telemetry:
            metric_data["telemetry"] = telemetry
        log_metric(metric_data)

    if advisory and report:
        existing_msg = report.get("systemMessage", "")
        report["systemMessage"] = (advisory + "\n\n" + existing_msg) if existing_msg else advisory

    print(json.dumps(report if report else {}))
    sys.exit(0)


def _fallback_config(cwd: str):
    """Minimal config when loading fails."""
    from lintgate.types import ProjectConfig

    return ProjectConfig(project_root=cwd)


def _exit_clean() -> None:
    """Exit cleanly with empty output."""
    print(json.dumps({}))
    sys.exit(0)


if __name__ == "__main__":
    main()
