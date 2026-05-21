"""Workflow entrypoints for the platonic golden path."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from lintgate.next_action import NextAction


def run_platonic_project(
    helpers: Any,
    path: str,
    max_files: int,
    budget_ms: int,
    *,
    converge_fn: Callable[..., str],
    exclusion_set: set[str] | None = None,
) -> str:
    """Select the first deterministic platonic target and start a workflow."""
    from lintgate.config import load_controlplane_config
    from lintgate.controlplane.types import PlatonicWorkflowConfig
    from lintgate.testing.platonic_selection import select_project_target
    from lintgate.testing.platonic_workflow import (
        PlatonicWorkflowRecord,
        append_history,
        create_workflow_id,
        save_workflow,
        workflow_envelope,
    )

    project_root = helpers["_validate_project_root"](path) or path
    platonic_cfg = PlatonicWorkflowConfig()
    preserve_globs: list[str] | None = None
    try:
        cp_config = load_controlplane_config(project_root)
    except Exception:
        cp_config = None
    if cp_config is not None:
        platonic_cfg = cp_config.platonic_workflow
        preserve_globs = cp_config.test_regeneration.preserve_globs
        if max_files == 5:
            max_files = platonic_cfg.project_max_files
        if budget_ms == 30_000:
            budget_ms = platonic_cfg.default_budget_ms

    selection = select_project_target(
        project_root,
        max_files=max_files,
        preserve_globs=preserve_globs,
        exclusion_set=exclusion_set,
    )
    workflow_id = create_workflow_id()
    if not selection.get("selected_file"):
        workflow = PlatonicWorkflowRecord(
            workflow_id=workflow_id,
            scope="project",
            target="",
            rel_file="",
            state="BLOCKED_NO_ELIGIBLE_TARGETS",
            step="assess",
            config={
                "max_iterations": platonic_cfg.max_iterations,
                "target_spec_level": platonic_cfg.target_spec_level,
                "target_kill_rate": platonic_cfg.target_kill_rate,
                "budget_ms": budget_ms,
                "reconciliation_threshold": platonic_cfg.reconciliation_confidence_threshold,
                "workflow_dir": platonic_cfg.workflow_dir,
            },
            primary_target="",
            primary_next_action="",
            primary_next_args={},
            autopilot_safe=False,
            blocking_reason="No hotspot file exposed an eligible auto-generation or decomposition target.",
            reason_code="BLOCKED_NO_ELIGIBLE_TARGETS",
            human_review_required=False,
            evidence_summary={
                "files_inspected": selection.get("files_inspected", 0),
                "hotspots": getattr(selection.get("rollup"), "hotspot_files", []),
            },
            proposed_artifacts=[],
        )
        append_history(
            workflow,
            state=workflow.state,
            step=workflow.step,
            reason_code=workflow.reason_code,
            summary=workflow.evidence_summary,
        )
        save_workflow(project_root, platonic_cfg.workflow_dir, workflow)
        from mcp_tools._disk_helpers import tool_response as _tr

        payload = workflow_envelope(
            workflow, next_actions=[], extra={"status": "no_eligible_targets"}
        )
        return _tr(payload, "platonic_project", project_root, "No eligible targets for improvement.")

    return converge_fn(
        helpers,
        project_root,
        selection["selected_file"],
        max_iterations=platonic_cfg.max_iterations,
        target_spec_level=platonic_cfg.target_spec_level,
        target_kill_rate=platonic_cfg.target_kill_rate,
        budget_ms=budget_ms,
        reconciliation_threshold=platonic_cfg.reconciliation_confidence_threshold,
        workflow_id=workflow_id,
        scope="project",
    )


def run_platonic_continue(
    helpers: Any,
    path: str,
    workflow_id: str,
    *,
    converge_fn: Callable[..., str],
    validate_only_fn: Callable[..., str] | None = None,
) -> str:
    """Resume a persisted platonic workflow."""
    from lintgate.config import load_controlplane_config
    from lintgate.controlplane.types import PlatonicWorkflowConfig
    from lintgate.testing.platonic_workflow import load_workflow, workflow_envelope

    project_root = helpers["_validate_project_root"](path) or path
    platonic_cfg = PlatonicWorkflowConfig()
    try:
        cp_config = load_controlplane_config(project_root)
    except Exception:
        cp_config = None
    if cp_config is not None:
        platonic_cfg = cp_config.platonic_workflow

    workflow = load_workflow(project_root, platonic_cfg.workflow_dir, workflow_id)
    if workflow is None:
        return str(
            helpers["_json_dumps"](
                {"error": f"Workflow not found: {workflow_id}"},
                output_mode="compact",
            )
        )

    if workflow.is_terminal():
        next_actions: list[NextAction] = []
        if workflow.state in ("READY_TO_APPLY", "READY_TO_APPLY_WITH_REVIEW"):
            next_actions.append(
                NextAction(
                    tool="platonic_apply",
                    args={"path": path, "workflow_id": workflow_id, "dry_run": True},
                    reason="Workflow is already ready to apply.",
                )
            )
        elif workflow.state == "NEEDS_DECOMPOSITION" and workflow.primary_target:
            next_actions.append(
                NextAction(
                    tool="extraction_plan",
                    args={"path": path, "function": workflow.primary_target},
                    reason="Workflow is already routing to decomposition.",
                )
            )
        from mcp_tools._disk_helpers import tool_response as _tr

        payload = workflow_envelope(
            workflow, next_actions=next_actions, extra={"status": "terminal"}
        )
        return _tr(payload, "platonic_continue", project_root, f"Workflow {workflow.state}. Target: {workflow.primary_target or 'none'}.")

    # Step-aware dispatch: VALIDATING skips profiling + generation
    if workflow.state == "VALIDATING" and validate_only_fn is not None:
        return validate_only_fn(helpers, path, workflow_id, workflow)

    rel_file = workflow.rel_file or workflow.target
    cfg = workflow.config

    # PROFILING resume: pass snapshot so converge_fn skips completed iterations
    resume_kwargs: dict[str, Any] = {}
    if workflow.state == "PROFILING" and workflow.orch_state_snapshot:
        resume_kwargs["orch_state_snapshot"] = workflow.orch_state_snapshot
        resume_kwargs["staged_artifacts_resume"] = workflow.staged_artifacts
        resume_kwargs["iterations_completed"] = workflow.iterations_completed

    return converge_fn(
        helpers,
        project_root,
        rel_file,
        max_iterations=int(cfg.get("max_iterations", platonic_cfg.max_iterations)),
        target_spec_level=float(cfg.get("target_spec_level", platonic_cfg.target_spec_level)),
        target_kill_rate=float(cfg.get("target_kill_rate", platonic_cfg.target_kill_rate)),
        budget_ms=float(cfg.get("budget_ms", platonic_cfg.default_budget_ms)),
        reconciliation_threshold=float(
            cfg.get(
                "reconciliation_threshold",
                platonic_cfg.reconciliation_confidence_threshold,
            )
        ),
        workflow_id=workflow_id,
        scope=workflow.scope or "file",
        **resume_kwargs,
    )


def run_platonic_apply(
    helpers: Any,
    path: str,
    workflow_id: str,
    dry_run: bool = True,
) -> str:
    """Apply a validated platonic workflow when it is explicitly ready."""
    from lintgate.config import load_controlplane_config
    from lintgate.controlplane.types import PlatonicWorkflowConfig
    from lintgate.testing.platonic_workflow import (
        append_history,
        load_workflow,
        save_workflow,
        workflow_envelope,
    )
    from mcp_tools._disk_helpers import tool_response
    from mcp_tools._test_regeneration_apply import _load_validation, impl_rebuild_apply

    project_root = helpers["_validate_project_root"](path) or path
    platonic_cfg = PlatonicWorkflowConfig()
    try:
        cp_config = load_controlplane_config(project_root)
    except Exception:
        cp_config = None
    if cp_config is not None:
        platonic_cfg = cp_config.platonic_workflow

    workflow = load_workflow(project_root, platonic_cfg.workflow_dir, workflow_id)
    if workflow is None:
        return str(
            helpers["_json_dumps"](
                {"error": f"Workflow not found: {workflow_id}"},
                output_mode="compact",
            )
        )

    if workflow.state not in ("READY_TO_APPLY", "READY_TO_APPLY_WITH_REVIEW"):
        # Allow dry-run preview when staged artifacts exist — the agent
        # needs visibility into what was generated even in blocked states.
        if dry_run and workflow.staged_artifacts:
            from mcp_tools._disk_helpers import tool_response

            apply_log = _build_staged_apply_actions(
                project_root, workflow.staged_artifacts, dry_run=True
            )
            preview = {
                **workflow_envelope(workflow, next_actions=[]),
                "dry_run": True,
                "state": workflow.state,
                "step": "apply_preview",
                "actions": apply_log,
                "preview_note": (
                    f"Preview only — workflow is in {workflow.state} state. "
                    "Address blocking reason before applying."
                ),
                "blocking_reason": workflow.blocking_reason or "",
            }
            summary = (
                f"Preview: {len(apply_log)} staged actions. "
                f"State: {workflow.state} (apply blocked)."
            )
            return tool_response(preview, "platonic_apply", project_root, summary)

        blocking_reason = workflow.blocking_reason or (
            "Workflow is not in READY_TO_APPLY or READY_TO_APPLY_WITH_REVIEW."
        )
        failed = {
            **workflow_envelope(workflow, next_actions=[]),
            "state": "FAILED",
            "step": "apply",
            "blocking_reason": blocking_reason,
            "reason_code": "APPLY_NOT_READY",
            "autopilot_safe": False,
        }
        from mcp_tools._disk_helpers import tool_response

        summary = f"Apply blocked: {blocking_reason} State: {workflow.state}."
        return tool_response(failed, "platonic_apply", project_root, summary)

    if workflow.validation_artifact_path:
        persisted_validation = _load_validation_from_path(workflow.validation_artifact_path)
    else:
        persisted_validation = _load_validation(project_root)
    saved_validation = workflow.evidence_summary.get("validation", {})

    # READY_TO_APPLY_WITH_REVIEW is allowed when validation says review-only gap remains.
    validation_ok = False
    if persisted_validation and (
        persisted_validation.get("ready_to_apply")
        or workflow.state == "READY_TO_APPLY_WITH_REVIEW"
        and persisted_validation.get("review_ready_to_apply")
    ):
        validation_ok = True

    if not validation_ok or (
        saved_validation
        and persisted_validation
        and persisted_validation.get("ready_to_apply") != saved_validation.get("ready_to_apply")
        and not persisted_validation.get("review_ready_to_apply")
    ):
        failed = {
            **workflow_envelope(workflow, next_actions=[]),
            "state": "FAILED",
            "step": "apply",
            "blocking_reason": "Validation is missing, stale, or no longer passing.",
            "reason_code": "STALE_VALIDATION",
            "autopilot_safe": False,
        }
        return tool_response(failed, "platonic_apply", project_root, "Apply blocked: validation stale or missing.")

    # Workflow-scoped apply: promote staged artifacts if available
    if workflow.staged_artifacts:
        apply_log = _build_staged_apply_actions(
            project_root, workflow.staged_artifacts, dry_run=dry_run
        )
        result: dict[str, Any] = {
            "dry_run": dry_run,
            "actions": apply_log,
            "quarantined": sum(1 for a in apply_log if a.get("action") == "quarantine"),
            "promoted": sum(1 for a in apply_log if a.get("action") == "promote"),
        }
    else:
        raw = impl_rebuild_apply(helpers, path, dry_run=dry_run)
        result = json.loads(raw) if isinstance(raw, str) else raw
    if result.get("error"):
        failed = {
            **workflow_envelope(workflow, next_actions=[]),
            "state": "FAILED",
            "step": "apply",
            "blocking_reason": result["error"],
            "reason_code": "APPLY_ERROR",
            "autopilot_safe": False,
            "apply_result": result,
        }
        return tool_response(failed, "platonic_apply", project_root, f"Apply error: {result['error'][:200]}")

    # Detect vacuous apply: all staged artifacts were skipped, nothing promoted.
    # Only applies to workflow-scoped path where "promoted" is explicitly tracked.
    promoted_count = result.get("promoted")
    if not dry_run and promoted_count is not None and promoted_count == 0:
        failed = {
            **workflow_envelope(workflow, next_actions=[]),
            "state": "FAILED",
            "step": "apply",
            "blocking_reason": "Apply produced no promoted artifacts (all skipped or missing).",
            "reason_code": "APPLY_VACUOUS",
            "autopilot_safe": False,
            "apply_result": result,
        }
        workflow.state = "FAILED"
        workflow.step = "apply"
        workflow.reason_code = "APPLY_VACUOUS"
        workflow.blocking_reason = failed["blocking_reason"]
        append_history(
            workflow,
            state=workflow.state,
            step=workflow.step,
            reason_code=workflow.reason_code,
            summary={"applied": False, "actions": result.get("actions", [])},
        )
        save_workflow(project_root, platonic_cfg.workflow_dir, workflow)
        return tool_response(failed, "platonic_apply", project_root, "Apply vacuous: no artifacts promoted.")

    next_actions: list[NextAction] = []
    if dry_run:
        next_actions.append(
            NextAction(
                tool="platonic_apply",
                args={"path": path, "workflow_id": workflow_id, "dry_run": False},
                reason="Execute the validated platonic apply plan.",
            )
        )
        payload = workflow_envelope(
            workflow,
            next_actions=next_actions,
            extra={
                "apply_result": result,
                "state": workflow.state,
                "step": "apply",
            },
        )
        promoted = result.get("promoted", 0)
        quarantined = result.get("quarantined", 0)
        return tool_response(payload, "platonic_apply", project_root, f"Apply dry-run: {promoted} to promote, {quarantined} to quarantine.")

    workflow.state = "CONVERGED"
    workflow.step = "apply"
    workflow.reason_code = "APPLIED"
    workflow.primary_next_action = ""
    workflow.primary_next_args = {}
    workflow.autopilot_safe = False
    workflow.blocking_reason = ""
    workflow.proposed_artifacts = []
    append_history(
        workflow,
        state=workflow.state,
        step=workflow.step,
        reason_code=workflow.reason_code,
        summary={"applied": True, "actions": result.get("actions", [])},
    )
    save_workflow(project_root, platonic_cfg.workflow_dir, workflow)
    payload = workflow_envelope(workflow, next_actions=[], extra={"apply_result": result})
    promoted = result.get("promoted", 0)
    return tool_response(payload, "platonic_apply", project_root, f"Applied: {promoted} artifact(s) promoted.")


# ── Helpers ────────────────────────────────────────────────────────


def _load_validation_from_path(path: str) -> dict | None:
    """Load persisted validation result from an explicit path."""
    import json as _json
    from pathlib import Path as _Path

    vpath = _Path(path)
    if not vpath.exists():
        return None
    try:
        with open(vpath, encoding="utf-8") as f:
            data: dict = _json.load(f)
            return data
    except (OSError, _json.JSONDecodeError):
        return None


def _apply_staged_artifacts(
    project_root: str,
    staged_artifacts: list[dict[str, Any]],
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Promote staged artifacts to their apply destinations.

    For each artifact, copies from ``staging_path`` to
    ``apply_destination`` (resolved relative to *project_root*).
    Verifies content hash before promoting to detect stale artifacts.
    """
    import hashlib
    import os
    import shutil

    actions: list[dict[str, Any]] = []
    for artifact in staged_artifacts:
        # Skip reference-only artifacts (existing tests that were not overwritten)
        if artifact.get("reference_only"):
            actions.append(
                {
                    "action": "skip",
                    "source": artifact.get("canonical_path", artifact.get("generated_path", "")),
                    "reason": "reference_only_existing_tests_adequate",
                }
            )
            continue
        staging_path = artifact.get("staging_path", "")
        apply_dest = artifact.get("apply_destination", "")
        expected_hash = artifact.get("content_hash", "")

        if not staging_path or not os.path.isfile(staging_path):
            actions.append(
                {
                    "action": "skip",
                    "source": staging_path,
                    "reason": "staging_file_missing",
                }
            )
            continue

        # Verify content hash
        if expected_hash:
            with open(staging_path, encoding="utf-8") as f:
                actual_hash = hashlib.sha256(f.read().encode()).hexdigest()
            if actual_hash != expected_hash:
                actions.append(
                    {
                        "action": "skip",
                        "source": staging_path,
                        "destination": apply_dest,
                        "reason": "content_hash_mismatch",
                        "expected": expected_hash[:12],
                        "actual": actual_hash[:12],
                    }
                )
                continue

        # Resolve destination relative to project root
        dest_path = (
            apply_dest if os.path.isabs(apply_dest) else os.path.join(project_root, apply_dest)
        )
        if not dry_run:
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(staging_path, dest_path)
        actions.append(
            {
                "action": "promote",
                "source": staging_path,
                "destination": apply_dest,
                "dry_run": dry_run,
            }
        )

    return actions


def _build_staged_apply_actions(
    project_root: str,
    staged_artifacts: list[dict[str, Any]],
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Preview or execute the staged platonic apply plan.

    Quarantine is scoped to files that belong to this workflow's staged
    artifacts, not the entire project manifest.
    """
    from lintgate.specification.test_regeneration_strategy import load_manifest
    from mcp_tools._test_regeneration_apply import _quarantine_files

    actions: list[dict[str, Any]] = []
    plan = load_manifest(project_root)
    if plan is not None:
        # Scope quarantine to only files owned by this workflow
        workflow_files = {
            a.get("apply_destination", a.get("generated_path", ""))
            for a in staged_artifacts
            if not a.get("reference_only")
        }
        if workflow_files and hasattr(plan, "quarantine_test_files"):
            original_quarantine = list(plan.quarantine_test_files or [])
            plan.quarantine_test_files = [q for q in original_quarantine if q in workflow_files]
        actions = _quarantine_files(plan, project_root, dry_run, actions)
    actions.extend(_apply_staged_artifacts(project_root, staged_artifacts, dry_run=dry_run))
    return actions
