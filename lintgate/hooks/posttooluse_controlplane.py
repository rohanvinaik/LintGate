"""ControlPlane event preparation, execution, prescriptive detection, and utilities.

Split from posttooluse.py — contains the ControlPlane event preparation
pipeline, mesh execution, prescriptive spec detection, and utility functions.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from typing import Any, NoReturn

from lintgate.change_classifier import classify_change


def _prepare_controlplane_event(
    input_data: dict, config: Any, cp_config: Any, cwd: str
) -> tuple[Any, Any, str, dict, str]:
    """Extract inputs, classify change, record behavior, and build supervision event.

    Returns (event, classification, tool_name, tool_input, tool_output).
    Calls _exit_clean() if risk_level is "none".
    """
    from lintgate.controlplane.types import SupervisionEvent
    from lintgate.hooks.habit import (
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
    return event, classification, tool_name, tool_input, tool_output


def _compute_fingerprint_state(
    mesh_result: Any, session: Any
) -> tuple[str | None, str | None, dict[str, str], dict[str, str]]:
    """Compute hook fingerprint and retrieve previous fingerprint from session.

    Returns (current_fingerprint, previous_fingerprint, current_fields, previous_fields).
    The fields dicts enable delta-first output by tracking per-dimension changes.
    """
    hook_fp: str | None = None
    prev_fp: str | None = None
    current_fields: dict[str, str] = {}
    prev_fields: dict[str, str] = {}
    with contextlib.suppress(Exception):
        from lintgate.controlplane.reporter.hook import compute_hook_fingerprint_detailed

        detailed = compute_hook_fingerprint_detailed(mesh_result)
        hook_fp = detailed["fingerprint"]
        current_fields = detailed["fields"]
        if session and isinstance(getattr(session, "behavior_compass", None), dict):
            prev_fp = session.behavior_compass.get("_hook_fingerprint")
            prev_fields = session.behavior_compass.get("_hook_fields", {})
            session.behavior_compass["_hook_fingerprint"] = hook_fp
            session.behavior_compass["_hook_fields"] = current_fields
    return hook_fp, prev_fp, current_fields, prev_fields


def _evaluate_compliance(
    session: Any,
    event: Any,
    last_disposition: str | None,
    last_nudge: dict | None,
) -> str | None:
    """Evaluate compliance tracking and return outcome."""
    if session is None:
        return None
    try:
        import dataclasses

        from lintgate.orchestration.compliance import ComplianceManager

        cm = ComplianceManager(session.behavior_compass)
        return cm.evaluate_and_record(
            dataclasses.asdict(event),
            last_disposition=last_disposition,
            last_nudge=last_nudge,
        )
    except Exception:
        return None


def _collect_disposition_nudge(
    cp_config: Any,
    session: Any,
    event: Any,
    bus: Any,
) -> str | None:
    """Evaluate disposition and collect nudge into bus. Returns disposition string."""
    try:
        from lintgate.orchestration.delivery import disposition_nudge_to_item
        from lintgate.orchestration.disposition_enforcer import DispositionEnforcer

        enforcer = DispositionEnforcer(cp_config, session=session)
        disposition, rule_id = enforcer.evaluate(event)
        if disposition and rule_id:
            bus.collect(disposition_nudge_to_item(disposition, rule_id))
        return disposition
    except Exception:
        return None


def _collect_cycle_interventions(session: Any, bus: Any) -> None:
    """Collect cycle detection results from session into bus."""
    if not (
        session
        and hasattr(session, "behavior_compass")
        and isinstance(session.behavior_compass, dict)
    ):
        return

    cycle_results = session.behavior_compass.get("cycle_detections")
    if not isinstance(cycle_results, list):
        return

    from lintgate.orchestration.cycle_detector import CycleDetectionResult
    from lintgate.orchestration.delivery import cycle_result_to_item

    for cr_data in cycle_results:
        try:
            res = (
                cr_data
                if isinstance(cr_data, CycleDetectionResult)
                else CycleDetectionResult(**cr_data)
            )
            bus.collect(cycle_result_to_item(res))
        except Exception:
            pass


def _collect_delivery_items(
    cp_config: Any,
    session: Any,
    event: Any,
    mesh_result: Any,
    last_disposition: str | None,
    last_nudge: dict | None,
) -> tuple[Any, str | None, str | None]:
    """Populate delivery bus with compliance, disposition, cycle, and behavior items.

    Returns (bus, disposition, compliance_outcome).
    """
    from lintgate.orchestration.delivery import DeliveryBus, lint_finding_to_item

    bus = DeliveryBus(config=cp_config, session=session)

    compliance_outcome = _evaluate_compliance(session, event, last_disposition, last_nudge)
    disposition = _collect_disposition_nudge(cp_config, session, event, bus)
    _collect_cycle_interventions(session, bus)

    behavior_findings: list = next(
        (cr.findings for cr in mesh_result.channel_results if cr.channel == "behavior"),
        [],
    )
    for f in behavior_findings:
        bus.collect(lint_finding_to_item(f))

    return bus, disposition, compliance_outcome


def _should_suppress_report(hook_fp: str | None, prev_fp: str | None, mesh_result: Any) -> bool:
    """Check if report should be suppressed due to unchanged state and no blocking findings."""
    if hook_fp is None or prev_fp is None or hook_fp != prev_fp:
        return False
    return not any(
        f.severity == "blocking" for cr in mesh_result.channel_results for f in cr.findings
    )


# ── ControlPlane execution ────────────────────────────────────────


def _run_controlplane(
    input_data: dict, config: Any, cp_config: Any, cwd: str, start: float
) -> None:
    """Run the ControlPlane supervision mesh."""
    from lintgate.controlplane.reporter import build_finding_index, format_mesh_report
    from lintgate.controlplane.runtime import run_mesh
    from lintgate.hooks.controlplane import (
        PostProcessContext,
        accumulate_session_telemetry,
        extract_finding_indexes,
        load_global_priors,
        post_process_session,
        refresh_runtime_after_run,
        save_run_details,
        setup_session_and_gate,
    )
    from lintgate.hooks.posttooluse import (
        _build_channels,
        _finalize_report,
        _log_controlplane_metric,
    )

    event, classification, tool_name, tool_input, tool_output = _prepare_controlplane_event(
        input_data, config, cp_config, cwd
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

    hook_fp, prev_fp, current_fields, prev_fields = _compute_fingerprint_state(mesh_result, session)

    finding_index: dict = {}
    with contextlib.suppress(Exception):
        finding_index = build_finding_index(mesh_result)

    idx = extract_finding_indexes(session)
    previous_finding_index, baseline_finding_index, snapshot_count, last_disposition, last_nudge = (
        idx
    )

    bus, disposition, compliance_outcome = _collect_delivery_items(
        cp_config,
        session,
        event,
        mesh_result,
        last_disposition,
        last_nudge,
    )

    proposed_constraints = post_process_session(
        PostProcessContext(
            session=session,
            mesh_result=mesh_result,
            finding_index=finding_index,
            cp_config=cp_config,
            input_data=input_data,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            disposition=disposition,
            last_nudge=last_nudge,
            compliance_outcome=compliance_outcome,
        )
    )

    save_run_details(mesh_result, finding_index, compliance_outcome=compliance_outcome)

    # State-fingerprint suppression (#P0.1)
    if _should_suppress_report(hook_fp, prev_fp, mesh_result):
        with contextlib.suppress(Exception):
            refresh_runtime_after_run(cwd, session, cp_config, mesh_result, tool_name, tool_input)
        _exit_clean()

    report = format_mesh_report(
        mesh_result,
        cp_config,
        proposed_constraints=proposed_constraints,
        previous_finding_index=previous_finding_index,
        baseline_finding_index=baseline_finding_index,
        snapshot_count=snapshot_count,
        disposition=disposition,
    )

    # Delta-first output: inject field-level deltas so agents see only what changed
    if prev_fields and current_fields and report:
        with contextlib.suppress(Exception):
            from lintgate.controlplane.reporter.hook import compute_field_deltas

            field_deltas = compute_field_deltas(current_fields, prev_fields)
            if field_deltas:
                report["state_delta"] = field_deltas

    # Multi-channel Delivery Bus Emission (#174)
    try:
        bus_report = bus.emit(preferred_channels=["hook_text", "rule_file", "mcp_status"])
        if bus_report.get("systemMessage"):
            advisory = bus_report["systemMessage"]
    except Exception:
        pass

    # Incremental test signal — detect new functions from edits (Gap 7)
    with contextlib.suppress(Exception):
        new_funcs = _detect_new_functions(tool_name, tool_input, cwd)
        if new_funcs and report:
            if cp_config.prescriptive_spec_enabled:
                report["test_generation_hint"] = {
                    "new_functions": new_funcs,
                    "suggestion": (
                        "New functions detected. Run prescriptive_spec_compose to create "
                        "behavioral contracts, then prescriptive_spec_compile for test "
                        "skeletons + generation constraints"
                    ),
                }
            else:
                report["test_generation_hint"] = {
                    "new_functions": new_funcs,
                    "suggestion": "Consider bootstrap_tests or controlplane_test_skeleton for new functions",
                }

    # PrescriptiveSpec advisory — notify when edited functions have specs
    with contextlib.suppress(Exception):
        if tool_name in ("Write", "Edit", "MultiEdit") and cp_config.prescriptive_spec_enabled:
            pspec_msg = _check_prescriptive_specs(tool_input, cwd)
            if pspec_msg and report:
                report.setdefault("prescriptive_advisory", pspec_msg)

    # Surface proposed constraints and prescriptive advisories in systemMessage
    # so the model sees them at the highest-attention position
    with contextlib.suppress(Exception):
        high_priority_addons: list[str] = []
        if proposed_constraints:
            constraint_texts = [
                c.get("rule", c.get("text", ""))[:60] for c in proposed_constraints[:2]
            ]
            if any(constraint_texts):
                high_priority_addons.append(
                    f"[Constraint] New: {'; '.join(t for t in constraint_texts if t)}"
                )
        if report and report.get("prescriptive_advisory"):
            high_priority_addons.append(report["prescriptive_advisory"])
        if report and report.get("test_generation_hint", {}).get("new_functions"):
            hint = report["test_generation_hint"]
            func_names = [f.get("name", "") for f in hint["new_functions"][:3]]
            high_priority_addons.append(
                f"[New] {', '.join(f for f in func_names if f)}: {hint['suggestion'][:80]}"
            )
        if high_priority_addons and advisory:
            advisory += " | " + " | ".join(high_priority_addons)
        elif high_priority_addons:
            advisory = " | ".join(high_priority_addons)

    accumulate_session_telemetry(report, session)
    refresh_runtime_after_run(cwd, session, cp_config, mesh_result, tool_name, tool_input)

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


# ── Prescriptive spec detection ───────────────────────────────────


def _check_prescriptive_specs(tool_input: dict, project_root: str) -> str | None:
    """Check if written/edited functions have prescriptive specs."""
    filepath = tool_input.get("file_path", "")
    if not filepath or not filepath.endswith(".py"):
        return None

    from lintgate.specification.prescriptive.spec import load_spec_index

    index = load_spec_index(project_root)
    if not index:
        return None

    # Check if any indexed target matches this file

    rel = os.path.relpath(filepath, project_root) if os.path.isabs(filepath) else filepath
    module = rel.replace(os.sep, ".").removesuffix(".py")
    matching = [k for k in index if module in k]
    if not matching:
        return None

    funcs = [k.split("::")[-1] if "::" in k else k for k in matching[:3]]
    return f"[PSpec] {', '.join(funcs)} have prescriptive specs. Run prescriptive_spec_verify to check refinement."


def _detect_write_functions(tool_input: dict) -> list[dict] | None:
    """Detect top-level functions from a Write tool's content."""
    import ast as _ast

    content = tool_input.get("content", "")
    filepath = tool_input.get("file_path", "")
    if not content or not filepath or not filepath.endswith(".py"):
        return None
    try:
        tree = _ast.parse(content)
    except SyntaxError:
        return None
    results = [
        {"name": node.name, "file": filepath, "line": node.lineno}
        for node in tree.body
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef))
    ]
    return results or None


def _extract_func_name(stripped: str) -> str:
    """Extract function name from a stripped 'def ...' or 'async def ...' line."""
    if stripped.startswith("async def "):
        name_part = stripped[len("async def ") :]
    elif stripped.startswith("def "):
        name_part = stripped[len("def ") :]
    else:
        return ""
    return name_part.split("(")[0].strip()


def _detect_edit_functions(tool_input: dict) -> list[dict] | None:
    """Detect newly introduced function defs from an Edit tool's diff."""
    old_string = tool_input.get("old_string", "")
    new_string = tool_input.get("new_string", "")
    filepath = tool_input.get("file_path", "")
    if not new_string or not filepath or not filepath.endswith(".py"):
        return None

    old_lines = set(old_string.splitlines())
    results = []
    for i, line in enumerate(new_string.splitlines(), 1):
        stripped = line.lstrip()
        if not (stripped.startswith("def ") or stripped.startswith("async def ")):
            continue
        if line in old_lines:
            continue
        func_name = _extract_func_name(stripped)
        if func_name:
            results.append({"name": func_name, "file": filepath, "line": i})
    return results or None


def _detect_new_functions(
    tool_name: str,
    tool_input: dict,
    cwd: str,
) -> list[dict] | None:
    """Detect newly added functions from Write/Edit tool use.

    Returns list of {"name": str, "file": str, "line": int} or None.
    """
    if tool_name == "Write":
        return _detect_write_functions(tool_input)
    if tool_name == "Edit":
        return _detect_edit_functions(tool_input)
    return None


# ── Utilities ─────────────────────────────────────────────────────


def _fallback_config(cwd: str) -> Any:
    """Minimal config when loading fails."""
    from lintgate.types import ProjectConfig

    return ProjectConfig(project_root=cwd)


def _exit_clean() -> NoReturn:
    """Exit cleanly with empty output."""
    print(json.dumps({}))
    sys.exit(0)
