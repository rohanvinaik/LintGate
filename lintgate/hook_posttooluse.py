#!/usr/bin/env python3
"""LintGate PostToolUse hook — intelligent change-aware linting for Claude Code.

This is the main entry point. Claude Code fires this after every
Write, Edit, MultiEdit, or Bash tool use. It classifies the change,
selects appropriate linters, runs them, and reports structured JSON
back to the agent via systemMessage.

Protocol: stdin JSON → stdout JSON (systemMessage), exit 0 always.
Silent success ({}), fast-path read-only, graceful degradation, 8s timeout.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from typing import TYPE_CHECKING, Any, NoReturn

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

# ── Backward-compatible re-exports (tests import these names) ────────
from lintgate.hook_arbitration import arbitrate_output as _arbitrate_output  # noqa: F401,I001
from lintgate.hook_controlplane import (
    can_apply_session_telemetry as _can_apply_session_telemetry,  # noqa: F401
    mark_session_telemetry_applied as _mark_session_telemetry_applied,  # noqa: F401
    resolve_event_model_key as _resolve_event_model_key,  # noqa: F401
    select_telemetry_profile as _select_telemetry_profile,  # noqa: F401
    session_telemetry_updates_used as _session_telemetry_updates_used,  # noqa: F401
)
from lintgate.hook_habit import (
    record_habit_event_lightweight as _record_habit_event_lightweight,  # noqa: F401
)
from lintgate.hook_runtime_state import (
    refresh_runtime_state_lightweight as _refresh_runtime_state_lightweight,  # noqa: F401
)


def _parse_hook_input() -> dict | None:
    """Parse and validate stdin JSON. Returns None on invalid input."""
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return None
    return input_data if isinstance(input_data, dict) else None


def _normalize_fields(input_data: dict) -> tuple[str, dict, str, str]:
    """Extract and normalize tool_name, tool_input, tool_output, cwd from hook input."""
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

    return tool_name, tool_input, tool_output, cwd


def _run_legacy_pipeline(
    tool_name: str,
    tool_input: dict,
    tool_output: str,
    cwd: str,
    config: Any,
    start: float,
) -> None:
    """Run the legacy lint pipeline (non-ControlPlane path)."""
    classification = classify_change(tool_name, tool_input, tool_output, cwd, config)
    if classification.risk_level == "none":
        _exit_clean()

    dep_warnings: list[str] = []
    if classification.change_kind in ("dependency", "build"):
        with contextlib.suppress(Exception):
            from lintgate.dependency_health import quick_dependency_check

            dep_warnings = quick_dependency_check(
                cwd, classification.change_kind, tool_input
            )

    tier = select_tier(classification, config)
    if tier.skip:
        _exit_clean()

    registry = build_registry(config)
    remaining_ms = max(
        config.total_timeout_ms - int((time.perf_counter() - start) * 1000), 2000
    )
    linter_results = run_linters(tier, config, registry, timeout_ms=remaining_ms)

    aggregated = aggregate_results(
        linter_results, config, tier_name=tier.name, tier_reason=tier.reason
    )

    all_issues = [*aggregated.blocking, *aggregated.warnings, *aggregated.informational]
    recurrence = {
        "repeated_issue_count": 0,
        "unique_signatures_tracked": 0,
        "top_repeated": [],
    }
    with contextlib.suppress(Exception):
        recurrence = update_issue_memory(cwd, all_issues)

    pattern_report: dict[str, list[str]] = {
        "alerted_patterns": [],
        "top_categories": [],
    }
    with contextlib.suppress(Exception):
        from lintgate.pattern_bank import update_pattern_bank

        pattern_report = update_pattern_bank(cwd, all_issues)

    report = format_report(
        aggregated,
        load_last_run(cwd),
        recurrence_summary=recurrence,
        pattern_report=pattern_report,
    )

    with contextlib.suppress(Exception):
        save_run(cwd, aggregated)
    with contextlib.suppress(Exception):
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
                "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                "repeated_issue_count": recurrence.get("repeated_issue_count", 0),
            }
        )

    if dep_warnings:
        report = report or {}
        dep_msg = "\n".join(dep_warnings)
        existing = report.get("systemMessage", "")
        sep = (
            "\n\n--- Dependency Health ---\n"
            if existing
            else "--- Dependency Health ---\n"
        )
        report["systemMessage"] = (
            (existing + sep + dep_msg) if existing else sep + dep_msg
        )

    print(json.dumps(report if report else {}))
    sys.exit(0)


def main() -> None:
    """Main hook entry point."""
    start = time.perf_counter()

    input_data = _parse_hook_input()
    if input_data is None:
        _exit_clean()

    tool_name, tool_input, tool_output, cwd = _normalize_fields(input_data)

    try:
        # Record meta-tool events for behavioral tracking before early exit
        if tool_name in ("Agent", "EnterPlanMode", "Task"):
            try:
                from lintgate.config import load_controlplane_config
                from lintgate.hook_habit import record_behavior_event

                cp_config = load_controlplane_config(cwd)
                if cp_config and cp_config.enabled:
                    record_behavior_event(
                        cp_config, cwd, tool_name, tool_input, tool_output
                    )
            except Exception:
                pass
            _exit_clean()

        if tool_name not in ("Write", "Edit", "MultiEdit", "Bash"):
            _exit_clean()

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
                return
        except Exception:
            pass

        _run_legacy_pipeline(tool_name, tool_input, tool_output, cwd, config, start)
    except Exception:
        _exit_clean()


def _build_channels(cp_config: Any) -> list[Channel]:
    """Build the channel list based on config."""
    from lintgate.channels.dependency_channel import DependencyChannel
    from lintgate.channels.git_channel import GitChannel
    from lintgate.channels.lint_channel import LintChannel
    from lintgate.channels.performance_channel import PerformanceChannel
    from lintgate.channels.test_channel import TestChannel

    channels: list[Channel] = [
        LintChannel(),
        TestChannel(),
        DependencyChannel(),
        GitChannel(),
        PerformanceChannel(),
    ]
    if cp_config.channel_enabled("structure"):
        from lintgate.channels.structure_channel import StructureChannel

        channels.append(StructureChannel())
    if cp_config.channel_enabled("behavior"):
        from lintgate.channels.behavior_channel import BehaviorChannel

        channels.append(BehaviorChannel())
    return channels


def _log_controlplane_metric(
    context: tuple[str, str],
    classification: Any,
    mesh_result: Any,
    session: Any,
    *,
    telemetry: dict,
    start: float,
) -> None:
    """Log metrics for a controlplane run (non-blocking)."""
    cwd, tool_name = context
    elapsed_ms = (time.perf_counter() - start) * 1000
    metric_data: dict[str, Any] = {
        "event": "controlplane_run",
        "project": cwd,
        "tool_name": tool_name,
        "change_kind": classification.change_kind,
        "risk_level": classification.risk_level,
        "coherence_state": mesh_result.coherence.state,
        "channels_run": sum(
            1 for r in mesh_result.channel_results if r.status != "skip"
        ),
        "partial": mesh_result.partial,
        "duration_ms": round(elapsed_ms, 1),
        "session_active": session is not None,
    }
    if telemetry:
        metric_data["telemetry"] = telemetry
    log_metric(metric_data)


def _finalize_report(
    report: dict, advisory: str | None, session: Any, cp_config: Any
) -> tuple[dict, dict]:
    """Apply advisory, arbitration, and strip internal telemetry from report."""
    from lintgate.hook_arbitration import arbitrate_output

    telemetry = report.get("_telemetry", {}) if report else {}
    if report and "_telemetry" in report:
        del report["_telemetry"]

    if advisory and report:
        existing_msg = report.get("systemMessage", "")
        report["systemMessage"] = (
            (advisory + "\n\n" + existing_msg) if existing_msg else advisory
        )

    session_data = session.behavior_compass if session else {}
    if not isinstance(session_data, dict):
        session_data = {}
    report = arbitrate_output(report if report else {}, cp_config, session_data)
    return report, telemetry


def _run_controlplane(
    input_data: dict, config: Any, cp_config: Any, cwd: str, start: float
) -> None:
    """Run the ControlPlane supervision mesh."""
    from lintgate.controlplane.reporter import format_mesh_report
    from lintgate.controlplane.runtime import run_mesh
    from lintgate.controlplane.types import SupervisionEvent
    from lintgate.hook_controlplane import (
        accumulate_session_telemetry,
        extract_finding_indexes,
        load_global_priors,
        post_process_session,
        refresh_runtime_after_run,
        save_run_details,
        setup_session_and_gate,
    )
    from lintgate.hook_habit import (
        record_behavior_event,
        record_habit_event_lightweight,
    )

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})
    tool_output = input_data.get("tool_output", "")

    classification = classify_change(tool_name, tool_input, tool_output, cwd, config)

    record_behavior_event(cp_config, cwd, tool_name, tool_input, tool_output)
    record_habit_event_lightweight(cp_config, cwd, tool_name, tool_input, tool_output)

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

    channels = _build_channels(cp_config)
    session, advisory = setup_session_and_gate(
        cp_config,
        cwd,
        tool_name,
        event,
        channels,
        load_global_priors(cp_config),
    )

    mesh_result = run_mesh(event, cp_config, channels, session=session)

    finding_index: dict = {}
    with contextlib.suppress(Exception):
        from lintgate.controlplane.reporter import build_finding_index

        finding_index = build_finding_index(mesh_result)

    idx = extract_finding_indexes(session)
    (
        previous_finding_index,
        baseline_finding_index,
        snapshot_count,
        last_disposition,
        last_nudge,
    ) = idx

    # Compliance Tracking (#164)
    compliance_outcome = None
    if session is not None:
        try:
            from lintgate.orchestration.compliance import ComplianceManager

            import dataclasses

            cm = ComplianceManager(session.behavior_compass)
            compliance_outcome = cm.evaluate_and_record(
                dataclasses.asdict(event),
                last_disposition=last_disposition,
                last_nudge=last_nudge,
            )
        except Exception:
            pass

    # Unified Behavioral Delivery Bus (#174)
    from lintgate.orchestration.delivery import (
        DeliveryBus,
        cycle_result_to_item,
        disposition_nudge_to_item,
        lint_finding_to_item,
    )

    bus = DeliveryBus(config=cp_config, session=session)

    # 1. Collect Disposition Nudges (#155)
    disposition: str | None = None
    try:
        from lintgate.orchestration.disposition_enforcer import DispositionEnforcer

        enforcer = DispositionEnforcer(cp_config, session=session)
        disposition, rule_id = enforcer.evaluate(event)
        if disposition and rule_id:
            bus.collect(disposition_nudge_to_item(disposition, rule_id))
    except Exception:
        pass

    # 2. Collect Cycle Interventions (#147)
    if (
        session
        and hasattr(session, "behavior_compass")
        and isinstance(session.behavior_compass, dict)
    ):
        cycle_results = session.behavior_compass.get("cycle_detections")
        if isinstance(cycle_results, list):
            for cr_data in cycle_results:
                from lintgate.orchestration.cycle_detector import CycleDetectionResult

                try:
                    res = (
                        cr_data
                        if isinstance(cr_data, CycleDetectionResult)
                        else CycleDetectionResult(**cr_data)
                    )
                    bus.collect(cycle_result_to_item(res))
                except Exception:
                    pass

    # 3. Collect Behavior Findings from Mesh (#159)
    behavior_findings = next(
        (cr.findings for cr in mesh_result.channel_results if cr.channel == "behavior"),
        [],
    )
    for f in behavior_findings:
        bus.collect(lint_finding_to_item(f))

    proposed_constraints = post_process_session(
        session,
        mesh_result,
        finding_index,
        cp_config,
        input_data,
        tool_name,
        tool_input,
        tool_output,
        disposition=disposition,
        last_nudge=last_nudge,
        compliance_outcome=compliance_outcome,
    )

    save_run_details(mesh_result, finding_index, compliance_outcome=compliance_outcome)

    report = format_mesh_report(
        mesh_result,
        cp_config,
        proposed_constraints=proposed_constraints,
        previous_finding_index=previous_finding_index,
        baseline_finding_index=baseline_finding_index,
        snapshot_count=snapshot_count,
        disposition=disposition,
    )

    # Multi-channel Delivery Bus Emission (#174)
    try:
        preferred = ["hook_text", "rule_file", "mcp_status"]
        bus_report = bus.emit(preferred_channels=preferred)
        if bus_report.get("systemMessage"):
            # Unified advisory message overrides any previous specific advisory
            advisory = bus_report["systemMessage"]
    except Exception:
        pass

    # Incremental test signal — detect new functions from edits (Gap 7)
    with contextlib.suppress(Exception):
        new_funcs = _detect_new_functions(tool_name, tool_input, cwd)
        if new_funcs and report:
            report["test_generation_hint"] = {
                "new_functions": new_funcs,
                "suggestion": "Consider bootstrap_tests or controlplane_test_skeleton for new functions",
            }

    accumulate_session_telemetry(report, session)
    refresh_runtime_after_run(
        cwd, session, cp_config, mesh_result, tool_name, tool_input
    )

    report, telemetry = _finalize_report(report, advisory, session, cp_config)

    with contextlib.suppress(Exception):
        _log_controlplane_metric(
            (cwd, tool_name),
            classification,
            mesh_result,
            session,
            telemetry=telemetry,
            start=start,
        )

    print(json.dumps(report if report else {}))
    sys.exit(0)


def _detect_new_functions(
    tool_name: str,
    tool_input: dict,
    cwd: str,
) -> list[dict] | None:
    """Detect newly added functions from Write/Edit tool use.

    For Write: parse full file content, all top-level defs are "new".
    For Edit: check if new_string contains 'def ' not in old_string.

    Returns list of {"name": str, "file": str, "line": int} or None.
    Lightweight: string search + optional partial AST parse.
    """
    import ast as _ast

    if tool_name == "Write":
        content = tool_input.get("content", "")
        filepath = tool_input.get("file_path", "")
        if not content or not filepath:
            return None
        if not filepath.endswith(".py"):
            return None
        try:
            tree = _ast.parse(content)
        except SyntaxError:
            return None
        results = []
        for node in tree.body:
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                results.append(
                    {
                        "name": node.name,
                        "file": filepath,
                        "line": node.lineno,
                    }
                )
        return results if results else None

    if tool_name == "Edit":
        old_string = tool_input.get("old_string", "")
        new_string = tool_input.get("new_string", "")
        filepath = tool_input.get("file_path", "")
        if not new_string or not filepath:
            return None
        if not filepath.endswith(".py"):
            return None

        # Quick check: does new_string introduce a 'def ' not in old_string?
        new_lines = new_string.splitlines()
        old_lines = set(old_string.splitlines())
        results = []
        for i, line in enumerate(new_lines, 1):
            stripped = line.lstrip()
            if (
                stripped.startswith("def ") or stripped.startswith("async def ")
            ) and line not in old_lines:
                # Extract function name
                name_part = stripped
                if name_part.startswith("async def "):
                    name_part = name_part[len("async def ") :]
                elif name_part.startswith("def "):
                    name_part = name_part[len("def ") :]
                func_name = name_part.split("(")[0].strip()
                if func_name:
                    results.append(
                        {
                            "name": func_name,
                            "file": filepath,
                            "line": i,  # Line within the edit
                        }
                    )
        return results if results else None

    return None


def _fallback_config(cwd: str) -> Any:
    """Minimal config when loading fails."""
    from lintgate.types import ProjectConfig

    return ProjectConfig(project_root=cwd)


def _exit_clean() -> NoReturn:
    """Exit cleanly with empty output."""
    print(json.dumps({}))
    sys.exit(0)


if __name__ == "__main__":
    main()
