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


def _inject_field_deltas(
    report: dict | None, current_fields: dict[str, str], prev_fields: dict[str, str]
) -> None:
    """Inject field-level deltas into report for delta-first output."""
    if not (prev_fields and current_fields and report):
        return
    with contextlib.suppress(Exception):
        from lintgate.controlplane.reporter.hook import compute_field_deltas

        field_deltas = compute_field_deltas(current_fields, prev_fields)
        if field_deltas:
            report["state_delta"] = field_deltas


def _emit_delivery_bus(bus: Any, advisory: str | None) -> str | None:
    """Emit delivery bus and return updated advisory string."""
    try:
        bus_report = bus.emit(preferred_channels=["hook_text", "rule_file", "mcp_status"])
        if bus_report.get("systemMessage"):
            return bus_report["systemMessage"]
    except Exception:
        pass
    return advisory


def _inject_report_extras(
    report: dict | None, tool_name: str, tool_input: dict, cwd: str, cp_config: Any
) -> None:
    """Inject test generation hints and prescriptive advisories into report."""
    if not report:
        return
    with contextlib.suppress(Exception):
        new_funcs = _detect_new_functions(tool_name, tool_input, cwd)
        if new_funcs:
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
    with contextlib.suppress(Exception):
        if tool_name in ("Write", "Edit", "MultiEdit") and cp_config.prescriptive_spec_enabled:
            pspec_msg = _check_prescriptive_specs(tool_input, cwd)
            if pspec_msg:
                report.setdefault("prescriptive_advisory", pspec_msg)


def _build_high_priority_advisory(
    report: dict | None, proposed_constraints: list[dict], advisory: str | None
) -> str | None:
    """Build high-priority advisory string from constraints and report extras."""
    with contextlib.suppress(Exception):
        addons: list[str] = []
        if proposed_constraints:
            texts = [c.get("rule", c.get("text", ""))[:60] for c in proposed_constraints[:2]]
            if any(texts):
                addons.append(f"[Constraint] New: {'; '.join(t for t in texts if t)}")
        if report and report.get("prescriptive_advisory"):
            addons.append(report["prescriptive_advisory"])
        if report and report.get("test_generation_hint", {}).get("new_functions"):
            hint = report["test_generation_hint"]
            names = [f.get("name", "") for f in hint["new_functions"][:3]]
            addons.append(f"[New] {', '.join(n for n in names if n)}: {hint['suggestion'][:80]}")
        if addons and advisory:
            return advisory + " | " + " | ".join(addons)
        if addons:
            return " | ".join(addons)
    return advisory


def _load_pre_edit_stash(cwd: str) -> dict | None:
    """Load and consume the pre-edit snapshot stashed by PreToolUse."""
    stash_path = os.path.join(cwd, ".lintgate", "pre_edit_snapshot.json")
    try:
        with open(stash_path) as f:
            data = json.load(f)
        os.remove(stash_path)  # consume: one stash per edit
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _compute_surgical_delta(
    pre_stash: dict, mesh_result: Any, cwd: str
) -> dict:
    """Compute scoped delta between pre-edit stash and post-edit findings.

    Returns: {
        "delta": int (positive = new findings, negative = improvements),
        "edit_file": str,
        "pre_count": int,
        "post_count": int,
        "new_findings": list[str],  # file:line:code for new issues
    }
    """
    edit_file = pre_stash.get("file", "")
    pre_count = pre_stash.get("finding_count", 0)

    # Count post-edit findings scoped to the edited file
    post_count = 0
    new_findings: list[str] = []
    for cr in mesh_result.channel_results:
        for finding in cr.findings:
            f_file = str(getattr(finding, "file", "") or "")
            if not f_file:
                continue
            rel = os.path.relpath(f_file, cwd) if os.path.isabs(f_file) else f_file
            if rel == edit_file:
                post_count += 1
                line = getattr(finding, "line", "")
                code = getattr(finding, "code", "") or getattr(finding, "kind", "")
                msg = getattr(finding, "message", "")[:60]
                severity = str(getattr(finding, "severity", "")).lower()
                new_findings.append(f"  {rel}:{line}:{code} ({severity}) {msg}")

    return {
        "delta": post_count - pre_count,
        "edit_file": edit_file,
        "pre_count": pre_count,
        "post_count": post_count,
        "new_findings": new_findings[:8],
    }


def _format_surgical_report(delta: dict, heartbeat_count: int) -> dict:
    """Format a surgical-mode report from a delta computation.

    Returns {} for silent-on-clean, or a minimal systemMessage for deltas.
    """
    d = delta["delta"]
    edit_file = delta["edit_file"]

    if d == 0 and delta["post_count"] == 0:
        # Clean edit — silence with periodic heartbeat
        if heartbeat_count > 0 and heartbeat_count % 5 == 0:
            return {"systemMessage": f"[surgical] {edit_file}: clean ({heartbeat_count} edits)"}
        return {}

    if d == 0 and delta["post_count"] > 0:
        # No change from this edit, pre-existing issues
        if heartbeat_count > 0 and heartbeat_count % 5 == 0:
            return {"systemMessage": f"[surgical] {edit_file}: {delta['post_count']} pre-existing (no change)"}
        return {}

    if d < 0:
        return {"systemMessage": f"[surgical] improved: {d} in {edit_file}"}

    # d > 0: this edit introduced findings
    lines = [f"[surgical] +{d} in {edit_file}:"]
    lines.extend(delta["new_findings"])
    return {"systemMessage": "\n".join(lines)}


def _get_surgical_heartbeat_count(session: Any) -> int:
    """Get and increment the surgical heartbeat counter from session."""
    if session is None or not hasattr(session, "behavior_compass"):
        return 0
    bc = session.behavior_compass
    if not isinstance(bc, dict):
        return 0
    count = bc.get("_surgical_heartbeat", 0)
    bc["_surgical_heartbeat"] = count + 1
    return count + 1


def _is_surgical_mode(cwd: str) -> bool:
    """Check if the current session is in surgical workflow mode."""
    try:
        from lintgate.runtime_state import load_runtime_state

        runtime = load_runtime_state(cwd)
        return runtime is not None and runtime.workflow_mode == "surgical"
    except Exception:
        return False


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

    # ── Surgical mode short-circuit ──────────────────────────────────
    # In surgical mode: compute scoped delta, emit minimal or nothing.
    # Full controlplane still runs (state tracking, session updates) but
    # the REPORT is replaced with the delta-only format.
    if _is_surgical_mode(cwd):
        pre_stash = _load_pre_edit_stash(cwd)
        if pre_stash is not None:
            delta = _compute_surgical_delta(pre_stash, mesh_result, cwd)
            heartbeat = _get_surgical_heartbeat_count(session)
            report = _format_surgical_report(delta, heartbeat)

            # Still refresh runtime state for downstream consumers
            with contextlib.suppress(Exception):
                from lintgate.hooks.controlplane import refresh_runtime_after_run

                refresh_runtime_after_run(
                    cwd, session, cp_config, mesh_result, tool_name, tool_input
                )

            _emit_hook_output(report, cwd)
            sys.exit(0)

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

    _inject_field_deltas(report, current_fields, prev_fields)
    advisory = _emit_delivery_bus(bus, advisory)
    _inject_report_extras(report, tool_name, tool_input, cwd, cp_config)
    advisory = _build_high_priority_advisory(report, proposed_constraints, advisory)

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

    _emit_hook_output(report if report else {}, cwd)
    sys.exit(0)


def _emit_hook_output(report: dict, project_root: str) -> None:
    """Save full report to disk, print slim summary to stdout."""
    if not report:
        print("{}")
        return

    # Save full report to disk
    try:
        import time as _time

        hook_dir = os.path.join(project_root, ".lintgate", "hooks", "posttooluse")
        os.makedirs(hook_dir, exist_ok=True)
        ts = int(_time.time())
        hook_file = os.path.join(hook_dir, f"{ts}.json")
        with open(hook_file, "w", encoding="utf-8") as f:
            json.dump(report, f, separators=(",", ":"), default=str)

        # Build slim summary from full report
        full_msg = report.get("systemMessage", "")
        # Extract key signals: coherence, blocking count, channel summary
        slim_parts: list[str] = []
        if full_msg:
            # Take first line only (usually the coherence/status line)
            first_line = full_msg.split("\n")[0][:200]
            slim_parts.append(first_line)
        slim_parts.append(f"Full: {os.path.relpath(hook_file, project_root)}")
        slim_msg = " | ".join(slim_parts)

        slim_report = dict(report)
        slim_report["systemMessage"] = slim_msg
        # Remove heavy payload keys from the slim version
        for key in ("hookSpecificOutput", "state_delta", "findings_detail"):
            slim_report.pop(key, None)
        print(json.dumps(slim_report, separators=(",", ":"), default=str))
    except Exception:
        # Fail-open: if disk write fails, print original report
        print(json.dumps(report, separators=(",", ":"), default=str))


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
