"""Unified orchestration controller — controlplane_execute.

Single entry point that chains: controlplane_run → apply safe repairs →
platonic_project/converge/apply loop → typed terminal state.

Derives file exclusion set from existing workflow records on disk,
not from a shadow history file.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from lintgate.next_action import NextAction, serialize_next_actions
from mcp_tools._disk_helpers import load_tool_response, tool_response

# Terminal states for the unified orchestration
EXECUTE_TERMINAL_STATES = frozenset({
    "COMPLETE",
    "READY_FOR_REVIEW",
    "NEEDS_ORACLE",
    "NEEDS_DECOMPOSITION",
    "BLOCKED_BY_VERIFIER",
    "BLOCKED_BY_ENVIRONMENT",
    "ADVISORY_ONLY",
    "TOOL_FAILURE",
})


def _converged_files_from_workflows(project_root: str, workflow_dir: str) -> set[str]:
    """Scan persisted workflow records for converged files."""
    converged: set[str] = set()
    wf_path = Path(project_root) / workflow_dir
    if not wf_path.exists():
        return converged
    for wf_file in wf_path.glob("*.json"):
        try:
            data = json.loads(wf_file.read_text(encoding="utf-8"))
            if data.get("state") in ("CONVERGED", "EXISTING_TESTS_SUFFICIENT"):
                rel = data.get("rel_file", "")
                if rel:
                    converged.add(rel)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
    return converged


def _classify_terminal_state(
    cp_result: dict[str, Any],
    repair_result: dict[str, Any] | None,
    file_outcomes: list[dict[str, Any]],
) -> str:
    """Map combined results to a terminal state."""
    has_oracle = any(f.get("state") == "NEEDS_ORACLE" for f in file_outcomes)
    has_decompose = any(f.get("state") == "NEEDS_DECOMPOSITION" for f in file_outcomes)
    has_review = any(
        f.get("state") in ("READY_TO_APPLY_WITH_REVIEW", "READY_FOR_REVIEW")
        for f in file_outcomes
    )
    has_failure = any(
        f.get("state") in ("FAILED", "TOOL_FAILURE") for f in file_outcomes
    )
    has_blocked = any(
        f.get("state") in ("BLOCKED_DISCOVERY", "BLOCKED_TOPOLOGY")
        for f in file_outcomes
    )

    # Check controlplane blocking findings
    counts = cp_result.get("counts", {})
    blocking = counts.get("blocking", 0)

    if has_failure:
        return "TOOL_FAILURE"
    if has_oracle:
        return "NEEDS_ORACLE"
    if has_decompose:
        return "NEEDS_DECOMPOSITION"
    if has_blocked and blocking > 0:
        return "BLOCKED_BY_VERIFIER"
    if has_review:
        return "READY_FOR_REVIEW"

    # All files converged or sufficient
    all_terminal = all(
        f.get("state") in (
            "CONVERGED", "EXISTING_TESTS_SUFFICIENT",
            "READY_TO_APPLY", "PLATEAU_NO_GENERATION",
        )
        for f in file_outcomes
    ) if file_outcomes else True

    if all_terminal and blocking == 0:
        return "COMPLETE"
    if all_terminal:
        return "ADVISORY_ONLY"
    return "ADVISORY_ONLY"


def impl_controlplane_execute(
    helpers: Any,
    path: str,
    budget_s: float = 300.0,
    max_files: int = 10,
    safe_only: bool = True,
    exclusion_set: list[str] | None = None,
) -> str:
    """Unified orchestration: run → repair → converge/apply loop → terminal state."""
    from lintgate.config import load_controlplane_config
    from lintgate.controlplane.types import PlatonicWorkflowConfig
    from mcp_tools._controlplane_impl_feedback import _impl_controlplane_apply_repairs
    from mcp_tools._controlplane_impl_run import _impl_controlplane_run
    from mcp_tools._platonic_impl import (
        impl_platonic_apply,
        impl_platonic_converge,
    )

    project_root = helpers["_validate_project_root"](path) or path
    start = time.monotonic()

    platonic_cfg = PlatonicWorkflowConfig()
    try:
        cp_config = load_controlplane_config(project_root)
    except Exception:
        cp_config = None
    if cp_config is not None:
        platonic_cfg = cp_config.platonic_workflow

    result: dict[str, Any] = {
        "phases": [],
        "file_outcomes": [],
        "terminal_state": "TOOL_FAILURE",
    }

    # ── Phase 1: ControlPlane analysis ────────────────────────────────
    try:
        cp_raw = _impl_controlplane_run(path, None, "normal", "project", None, helpers)
        cp_result = load_tool_response(cp_raw) if isinstance(cp_raw, str) else {}
    except Exception as exc:
        result["phases"].append({"phase": "analysis", "error": str(exc)})
        result["terminal_state"] = "TOOL_FAILURE"
        return tool_response(
            result, "controlplane_execute", project_root,
            f"Analysis failed: {exc}",
        )

    run_id = cp_result.get("run_id", "")
    counts = cp_result.get("counts", {})
    repair_counts = counts.get("repair_counts", {})
    result["phases"].append({
        "phase": "analysis",
        "run_id": run_id,
        "blocking": counts.get("blocking", 0),
        "warnings": counts.get("warning", 0),
        "repair_counts": repair_counts,
    })

    # ── Phase 2: Apply safe repairs ───────────────────────────────────
    repair_result: dict[str, Any] | None = None
    safe_executable = repair_counts.get("safe_executable", 0)
    if safe_executable > 0:
        try:
            repair_raw = _impl_controlplane_apply_repairs(
                path, None, safe_only, helpers, run_id=run_id,
            )
            repair_result = load_tool_response(repair_raw) if isinstance(repair_raw, str) else {}
        except Exception as exc:
            repair_result = {"error": str(exc)}

        result["phases"].append({
            "phase": "repair",
            "safe_executable": safe_executable,
            "result": {
                "applied": repair_result.get("applied", 0) if repair_result else 0,
                "skipped": repair_result.get("skipped", 0) if repair_result else 0,
            },
        })

    # ── Phase 3: Platonic convergence loop ────────────────────────────
    # Build exclusion set from workflow records + caller
    converged = _converged_files_from_workflows(project_root, platonic_cfg.workflow_dir)
    if exclusion_set:
        converged.update(exclusion_set)

    file_outcomes: list[dict[str, Any]] = []
    files_processed = 0

    while files_processed < max_files:
        elapsed = time.monotonic() - start
        if elapsed >= budget_s:
            break

        iter_budget_ms = max((budget_s - elapsed) * 1000, 10_000)

        # Select next target, excluding already-converged files
        try:
            from lintgate.testing.platonic_selection import select_project_target

            selection = select_project_target(
                project_root,
                max_files=5,
                exclusion_set=converged,
            )
        except Exception as exc:
            file_outcomes.append({"file": "", "state": "TOOL_FAILURE", "error": str(exc)})
            break

        selected_file = selection.get("selected_file", "")
        if not selected_file:
            break  # No more eligible targets

        # Run convergence on this file
        try:
            converge_raw = impl_platonic_converge(
                helpers,
                project_root,
                selected_file,
                budget_ms=iter_budget_ms,
            )
            converge_result = load_tool_response(converge_raw) if isinstance(converge_raw, str) else {}
        except Exception as exc:
            file_outcomes.append({
                "file": selected_file,
                "state": "TOOL_FAILURE",
                "error": str(exc),
            })
            converged.add(selected_file)
            files_processed += 1
            continue

        workflow_state = converge_result.get("state", "FAILED")
        workflow_id = converge_result.get("workflow_id", "")

        # If ready to apply, apply immediately (non-dry-run)
        if workflow_state in ("READY_TO_APPLY",) and workflow_id:
            try:
                apply_raw = impl_platonic_apply(
                    helpers, project_root, workflow_id, dry_run=False,
                )
                apply_result = load_tool_response(apply_raw) if isinstance(apply_raw, str) else {}
                workflow_state = apply_result.get("state", workflow_state)
            except Exception:
                pass  # Apply failure is non-fatal, state stays as-is

        file_outcomes.append({
            "file": selected_file,
            "state": workflow_state,
            "workflow_id": workflow_id,
        })

        converged.add(selected_file)
        files_processed += 1

        # Only stop the batch on environment-level failures.
        # NEEDS_ORACLE and NEEDS_DECOMPOSITION are per-file states —
        # other files in the batch may still be processable.
        if workflow_state == "TOOL_FAILURE":
            break

    result["file_outcomes"] = file_outcomes
    result["phases"].append({
        "phase": "convergence",
        "files_processed": files_processed,
        "exclusions_from_history": len(converged) - files_processed - len(exclusion_set or []),
    })

    # ── Phase 4: Classify terminal state ──────────────────────────────
    terminal = _classify_terminal_state(cp_result, repair_result, file_outcomes)
    result["terminal_state"] = terminal
    result["elapsed_s"] = round(time.monotonic() - start, 1)

    action_counts = {
        "safe_repairs_applied": (repair_result or {}).get("applied", 0),
        "files_converged": sum(
            1 for f in file_outcomes
            if f.get("state") in ("CONVERGED", "EXISTING_TESTS_SUFFICIENT")
        ),
        "oracle_requests": sum(1 for f in file_outcomes if f.get("state") == "NEEDS_ORACLE"),
        "decomposition_needed": sum(
            1 for f in file_outcomes if f.get("state") == "NEEDS_DECOMPOSITION"
        ),
        "failures": sum(
            1 for f in file_outcomes
            if f.get("state") in ("FAILED", "TOOL_FAILURE")
        ),
    }
    result["action_counts"] = action_counts

    # Build next_actions based on terminal state
    next_actions: list[NextAction] = []
    if terminal == "NEEDS_ORACLE":
        oracle_files = [f["file"] for f in file_outcomes if f.get("state") == "NEEDS_ORACLE"]
        next_actions.append(NextAction(
            tool="platonic_continue",
            args={"path": path, "workflow_id": file_outcomes[-1].get("workflow_id", "")},
            reason=f"Oracle input needed for {', '.join(oracle_files[:3])}",
            safe=False,
        ))
    elif terminal == "NEEDS_DECOMPOSITION":
        decompose_files = [
            f["file"] for f in file_outcomes if f.get("state") == "NEEDS_DECOMPOSITION"
        ]
        next_actions.append(NextAction(
            tool="extraction_plan",
            args={"path": path, "function": decompose_files[0] if decompose_files else ""},
            reason=f"Decomposition needed for {', '.join(decompose_files[:3])}",
        ))

    result["next_actions"] = serialize_next_actions(next_actions)

    # ── Phase 5: Classify survivors ───────────────────────────────────
    try:
        from lintgate.controlplane.issue_classification import (
            classify_controller_outcomes,
        )

        classified = classify_controller_outcomes(
            terminal, action_counts, file_outcomes,
        )
        result["classified_survivors"] = [c.to_dict() for c in classified]
        result["survivor_summary"] = {}
        for c in classified:
            key = c.classification.value
            result["survivor_summary"][key] = result["survivor_summary"].get(key, 0) + 1
    except Exception:
        pass  # Classification is advisory, not fatal

    # Summary
    parts = [f"Terminal: {terminal}"]
    if action_counts["safe_repairs_applied"]:
        parts.append(f"{action_counts['safe_repairs_applied']} repairs applied")
    if action_counts["files_converged"]:
        parts.append(f"{action_counts['files_converged']} files converged")
    if action_counts["oracle_requests"]:
        parts.append(f"{action_counts['oracle_requests']} need oracle")
    if action_counts["decomposition_needed"]:
        parts.append(f"{action_counts['decomposition_needed']} need decomposition")
    summary = ". ".join(parts)

    return tool_response(
        result, "controlplane_execute", project_root, summary,
        next_actions=result.get("next_actions"),
    )
