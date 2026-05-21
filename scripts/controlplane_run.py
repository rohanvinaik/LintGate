#!/usr/bin/env python3
"""ControlPlane orchestration — standalone CLI.

Commands:
    run PATH [--channels ...] [--strictness ...] [--scope ...] [--file ...]
    get-details RUN_ID [--channel X] [--severity X] [--max-issues N]
                [--section X ...] [--top-n N] [--time-budget-minutes N]
                [--finding-domain X]
    status [--path PATH]
    test-skeleton PATH --target-file X
    report-repair PATH --action-id X [--outcome X]
    agent-feedback PATH [--run-id X] [--disagreement X]
                   [--accept X ...] [--reject X ...]
                   [--tuned-json JSON] [--classifications-json JSON]
    apply-repairs PATH [--action-id X ...] [--unsafe] [--run-id X]
    get-work-queue RUN_ID [--max-items N]
    execute PATH [--budget-s N] [--max-files N] [--unsafe] [--exclude X ...]
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

from mcp_tools._controlplane_impl_details import (
    _impl_controlplane_get_details,
    _impl_controlplane_status,
)
from mcp_tools._controlplane_impl_feedback import (
    _impl_controlplane_agent_feedback,
    _impl_controlplane_apply_repairs,
)
from mcp_tools._controlplane_impl_run import _impl_controlplane_run
from mcp_tools._disk_helpers import _safe_json, tool_response


# ── helpers dict for impl functions ───────────────────────────────────────


def _validate_project_root(path: str, arg_name: str = "path") -> str:
    """Mirror mcp_server._validate_project_root — minimal resolve+check."""
    if not path:
        raise ValueError(f"{arg_name} is required")
    p = os.path.abspath(path)
    if not os.path.isdir(p):
        raise ValueError(f"{arg_name} is not a directory: {path}")
    return p


def _json_dumps(data: Any, output_mode: str = "compact") -> str:
    """Mirror mcp_server._json_dumps — compact serialization."""
    if output_mode == "full":
        return json.dumps(data, indent=2, default=str)
    return json.dumps(data, separators=(",", ":"), default=str)


def _collect_python_files(project_root: str) -> list[str]:
    from lintgate.discovery import discover_project_files

    return discover_project_files(project_root)


def _build_onboarding_status(project_root: str) -> dict[str, Any]:
    """Minimal onboarding status — mirrors mcp_server._build_onboarding_status."""
    from lintgate.config import load_controlplane_config

    config_path = os.path.join(project_root, ".claude", "lintgate.yaml")
    config_file_exists = os.path.exists(config_path)
    cp_config = load_controlplane_config(project_root)
    has_controlplane_section = False
    if config_file_exists:
        with contextlib.suppress(Exception):
            import yaml as _yaml

            with open(config_path) as _f:
                _raw = _yaml.safe_load(_f) or {}
            has_controlplane_section = bool(
                isinstance(_raw, dict) and isinstance(_raw.get("controlplane"), dict)
            )

    status: dict[str, Any] = {
        "config_found": config_file_exists,
        "config_path_checked": config_path,
        "controlplane_enabled": cp_config.enabled if cp_config else False,
        "automatic_hook_active": cp_config.enabled if cp_config else False,
        "using_default_config": cp_config is None,
    }
    if not config_file_exists:
        status["config_state"] = "no_config"
    elif cp_config is None and not has_controlplane_section:
        status["config_state"] = "config_no_controlplane_section"
    elif cp_config is not None and not cp_config.enabled:
        status["config_state"] = "config_disabled"
    else:
        status["config_state"] = "config_enabled"
    return status


def _build_cp_full_details(mesh_result: Any, finding_index: dict[str, Any]) -> dict[str, Any]:
    """Mirror mcp_server._build_cp_full_details — full drill-down payload."""
    details: dict[str, Any] = {
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
        channel_data: dict[str, Any] = {
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
        details["channels"][cr.channel] = channel_data
    return details


def _helpers() -> dict[str, Any]:
    return {
        "_validate_project_root": _validate_project_root,
        "_json_dumps": _json_dumps,
        "_collect_python_files": _collect_python_files,
        "_build_onboarding_status": _build_onboarding_status,
        "_build_cp_full_details": _build_cp_full_details,
    }


# ── command handlers (impls already emit tool_response strings) ───────────


def _emit_passthrough(result: Any) -> None:
    """Impl functions already return slim JSON envelopes — print verbatim."""
    if isinstance(result, str):
        print(result)
    else:
        print(_json_dumps(result))


def cmd_run(args: argparse.Namespace) -> None:
    files = args.file or None
    result = _impl_controlplane_run(
        args.path, args.channels, args.strictness, args.scope, files, _helpers()
    )
    _emit_passthrough(result)


def cmd_get_details(args: argparse.Namespace) -> None:
    try:
        result = _impl_controlplane_get_details(
            args.run_id,
            args.channel,
            args.severity,
            args.max_issues,
            args.section or None,
            _helpers(),
            finding_domain=args.finding_domain,
            top_n=args.top_n,
            time_budget_minutes=args.time_budget_minutes,
        )
    except (ValueError, FileNotFoundError) as exc:
        disk_file = os.path.join(
            os.getcwd(), ".lintgate", "analysis", "controlplane_run", f"{args.run_id}.json"
        )
        if os.path.isfile(disk_file):
            print(tool_response(
                {"error": str(exc), "note": "Run not in session memory but available on disk."},
                "controlplane_get_details",
                os.getcwd(),
                f"Run {args.run_id} not in session memory. Full data at: {disk_file}",
                run_id=args.run_id,
            ))
            return
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
    _emit_passthrough(result)


def cmd_status(args: argparse.Namespace) -> None:
    result = _impl_controlplane_status(args.path, _helpers())
    _emit_passthrough(result)


def cmd_test_skeleton(args: argparse.Namespace) -> None:
    from lintgate.controlplane.skeleton_generator import (
        generate_test_path,
        generate_test_skeleton,
    )
    from lintgate.next_action import NextAction, serialize_next_actions

    project_root = _validate_project_root(args.path)
    target_file = args.target_file
    if not os.path.isabs(target_file):
        target_file = os.path.normpath(os.path.join(project_root, target_file))
    if not os.path.exists(target_file):
        print(json.dumps({"error": f"Source file not found: {target_file}"}))
        sys.exit(1)

    skeleton = generate_test_skeleton(target_file, project_root=project_root)
    test_path = generate_test_path(target_file, project_root)
    rel_file = os.path.relpath(target_file, project_root)

    next_actions = serialize_next_actions(
        [
            NextAction(
                tool="mutation_run_sampling",
                args={"path": args.path, "file": rel_file},
                reason="Run mutation sampling to validate generated skeleton",
            ),
            NextAction(
                tool="spec_file_analyze",
                args={"path": args.path, "file": rel_file},
                reason="View specification analysis for test prioritization",
            ),
        ]
    )

    result = {
        "source_file": target_file,
        "test_path": test_path,
        "skeleton": skeleton,
        "status": "skeleton_needs_edit",
        "action_required": (
            "This is a SKELETON — it will NOT pass if run directly. "
            "Replace all placeholder values (pass stubs, ..., EXPECTED) "
            "with real values, then write with the Write tool."
        ),
        "next_actions": next_actions,
    }
    summary = f"Test skeleton for {rel_file}."
    print(tool_response(
        result, "controlplane_test_skeleton", project_root, summary, next_actions=next_actions
    ))


def cmd_report_repair(args: argparse.Namespace) -> None:
    from lintgate.controlplane.session_memory import (
        get_or_create_session,
        report_repair_outcome,
        save_session,
    )

    project_root = _validate_project_root(args.path)
    valid_outcomes = {"applied", "ignored", "rejected"}
    if args.outcome not in valid_outcomes:
        print(json.dumps({
            "error": f"Invalid outcome '{args.outcome}'; expected one of: {sorted(valid_outcomes)}"
        }))
        sys.exit(1)

    session = get_or_create_session(project_root)
    report_repair_outcome(session, args.action_id, args.outcome)
    save_session(session)

    result = {
        "action_id": args.action_id,
        "outcome": args.outcome,
        "session_id": session.session_id,
        "pending_repairs": sum(1 for v in session.repair_outcomes.values() if v == "pending"),
        "total_repairs_tracked": len(session.repair_outcomes),
    }
    summary = f"Repair reported for {args.action_id}."
    print(tool_response(result, "controlplane_report_repair", project_root, summary))


def cmd_agent_feedback(args: argparse.Namespace) -> None:
    tuned = json.loads(args.tuned_json) if args.tuned_json else None
    classifications = json.loads(args.classifications_json) if args.classifications_json else None
    result = _impl_controlplane_agent_feedback(
        args.path,
        args.run_id,
        args.disagreement,
        args.accept or None,
        args.reject or None,
        _helpers(),
        tuned_findings=tuned,
        test_failure_classifications=classifications,
    )
    _emit_passthrough(result)


def cmd_apply_repairs(args: argparse.Namespace) -> None:
    result = _impl_controlplane_apply_repairs(
        args.path,
        args.action_id or None,
        not args.unsafe,
        _helpers(),
        run_id=args.run_id,
    )
    _emit_passthrough(result)


def cmd_get_work_queue(args: argparse.Namespace) -> None:
    from lintgate.state import load_controlplane_run

    run_data = load_controlplane_run(args.run_id)
    if run_data is None:
        print(json.dumps({"error": f"Run {args.run_id} not found"}))
        return

    finding_index = run_data.get("finding_index", {})
    if not finding_index:
        print(_safe_json({"run_id": args.run_id, "note": "No findings in this run."}))
        return

    import_graph: dict = {}
    file_map: dict = {}
    for ch_data in run_data.get("channels", {}).values():
        metrics = ch_data.get("metrics", {})
        if "_import_graph" in metrics:
            import_graph = metrics["_import_graph"]
            file_map = metrics.get("_file_map", {})
            break

    try:
        from lintgate.controlplane.work_queue import build_work_queue

        finding_list = list(finding_index.values())
        wq = build_work_queue(finding_list, import_graph, file_map)
        wq_dict = wq.to_dict()
        total_items = len(wq_dict.get("items", []))
        if total_items > args.max_items:
            wq_dict["items"] = wq_dict["items"][:args.max_items]
            wq_dict["truncated"] = True
            wq_dict["total_items"] = total_items
        wq_result = {"run_id": args.run_id, "work_queue": wq_dict}
        n_files = wq_dict.get("total_files", total_items)
        wq_summary = f"Work queue: {n_files} files."
        print(tool_response(wq_result, "controlplane_get_work_queue", os.getcwd(), wq_summary))
    except Exception as e:
        print(_safe_json({"run_id": args.run_id, "error": f"Failed to build work queue: {e}"}))


def cmd_execute(args: argparse.Namespace) -> None:
    from mcp_tools._controlplane_execute_impl import impl_controlplane_execute

    result = impl_controlplane_execute(
        _helpers(),
        args.path,
        budget_s=args.budget_s,
        max_files=args.max_files,
        safe_only=not args.unsafe,
        exclusion_set=args.exclude or None,
    )
    _emit_passthrough(result)


def main() -> None:
    parser = argparse.ArgumentParser(prog="controlplane_run", description="ControlPlane orchestration")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run")
    p_run.add_argument("path")
    p_run.add_argument("--channels", default=None)
    p_run.add_argument("--strictness", default="normal")
    p_run.add_argument("--scope", default=None)
    p_run.add_argument("--file", action="append", default=[])

    p_gd = sub.add_parser("get-details")
    p_gd.add_argument("run_id")
    p_gd.add_argument("--channel", default=None)
    p_gd.add_argument("--severity", default=None)
    p_gd.add_argument("--max-issues", type=int, default=10)
    p_gd.add_argument("--section", action="append", default=[])
    p_gd.add_argument("--top-n", type=int, default=None)
    p_gd.add_argument("--time-budget-minutes", type=float, default=None)
    p_gd.add_argument("--finding-domain", default=None)

    p_status = sub.add_parser("status")
    p_status.add_argument("--path", default=None)

    p_ts = sub.add_parser("test-skeleton")
    p_ts.add_argument("path")
    p_ts.add_argument("--target-file", required=True)

    p_rr = sub.add_parser("report-repair")
    p_rr.add_argument("path")
    p_rr.add_argument("--action-id", required=True)
    p_rr.add_argument("--outcome", default="applied")

    p_af = sub.add_parser("agent-feedback")
    p_af.add_argument("path")
    p_af.add_argument("--run-id", default=None)
    p_af.add_argument("--disagreement", default=None)
    p_af.add_argument("--accept", action="append", default=[])
    p_af.add_argument("--reject", action="append", default=[])
    p_af.add_argument("--tuned-json", default=None)
    p_af.add_argument("--classifications-json", default=None)

    p_ar = sub.add_parser("apply-repairs")
    p_ar.add_argument("path")
    p_ar.add_argument("--action-id", action="append", default=[])
    p_ar.add_argument("--unsafe", action="store_true")
    p_ar.add_argument("--run-id", default=None)

    p_wq = sub.add_parser("get-work-queue")
    p_wq.add_argument("run_id")
    p_wq.add_argument("--max-items", type=int, default=25)

    p_ex = sub.add_parser("execute")
    p_ex.add_argument("path")
    p_ex.add_argument("--budget-s", type=float, default=300.0)
    p_ex.add_argument("--max-files", type=int, default=10)
    p_ex.add_argument("--unsafe", action="store_true")
    p_ex.add_argument("--exclude", action="append", default=[])

    args = parser.parse_args()
    {
        "run": cmd_run,
        "get-details": cmd_get_details,
        "status": cmd_status,
        "test-skeleton": cmd_test_skeleton,
        "report-repair": cmd_report_repair,
        "agent-feedback": cmd_agent_feedback,
        "apply-repairs": cmd_apply_repairs,
        "get-work-queue": cmd_get_work_queue,
        "execute": cmd_execute,
    }[args.command](args)


if __name__ == "__main__":
    main()
