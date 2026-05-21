"""Platonic convergence — the execution loop that drives toward ideal specification.

Connects spec analysis, mutation profiling, test generation, and validation
into an iterative loop that runs until convergence. Each iteration:

1. Analyze spec state (σ, regime, phase, trajectory)
2. Profile mutations (sampling with budget)
3. Compute health vector per function
4. Classify functions → auto_generate_unit targets
5. Generate tests via batch regenerator (6-lens composition)
6. Validate via fresh kill rates
7. Update convergence state, check stop criteria
8. If not converged, iterate

Non-destructive: tests go to tests/generated/, never overwrites existing.

Implementation split across focused helpers:
- `lintgate.testing.platonic_entrypoints`: project/continue/apply workflow entrypoints
- `lintgate.testing.platonic_selection`: deterministic project/file routing
- `lintgate.testing.platonic_workflow`: workflow persistence and envelope building
- `lintgate.testing.platonic_mutation`: mutation sampling and cache persistence
- `lintgate.testing.platonic_health`: reconciliation-aware file health computation
- `lintgate.testing.platonic_generation`: generated-test writing
- `lintgate.testing.platonic_outcomes`: workflow-state and artifact resolution
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from lintgate.next_action import NextAction
from lintgate.testing.platonic_entrypoints import (
    run_platonic_apply as _run_platonic_apply,
)
from lintgate.testing.platonic_entrypoints import (
    run_platonic_continue as _run_platonic_continue,
)
from lintgate.testing.platonic_entrypoints import (
    run_platonic_project as _run_platonic_project,
)
from lintgate.testing.platonic_generation import generate_tests as _platonic_generate_tests
from lintgate.testing.platonic_health import compute_file_health as _platonic_compute_file_health
from lintgate.testing.platonic_mutation import (
    find_mutation_result as _platonic_find_mutation_result,
)
from lintgate.testing.platonic_mutation import (
    load_mutation_cache as _platonic_load_mutation_cache,
)
from lintgate.testing.platonic_mutation import (
    persist_mutation_cache_entries as _platonic_persist_mutation_cache_entries,
)
from lintgate.testing.platonic_mutation import (
    run_mutation_sampling as _platonic_run_mutation_sampling,
)
from lintgate.testing.platonic_outcomes import (
    platonic_config_dict as _platonic_config_dict_impl,
)
from lintgate.testing.platonic_outcomes import (
    proposed_artifacts as _proposed_artifacts_impl,
)
from lintgate.testing.platonic_outcomes import (
    reroute_manual_contract_candidates as _reroute_manual_contract_candidates_impl,
)
from lintgate.testing.platonic_outcomes import (
    workflow_state_from_outputs as _workflow_state_from_outputs_impl,
)
from mcp_tools._disk_helpers import tool_response


def _is_default_float(value: float, default: float) -> bool:
    """Treat near-identical float inputs as the unchanged public default."""
    return math.isclose(value, default, rel_tol=0.0, abs_tol=1e-9)


def impl_platonic_project(
    helpers: Any,
    path: str,
    max_files: int = 5,
    budget_ms: int = 30_000,
) -> str:
    """Select the first deterministic platonic target and start a workflow."""
    return _run_platonic_project(
        helpers,
        budget_ms=budget_ms,
        max_files=max_files,
        path=path,
        converge_fn=impl_platonic_converge,
    )


def impl_platonic_continue(
    helpers: Any,
    path: str,
    workflow_id: str,
) -> str:
    """Resume a persisted platonic workflow."""
    return _run_platonic_continue(
        helpers,
        path,
        workflow_id,
        converge_fn=impl_platonic_converge,
        validate_only_fn=impl_platonic_validate_only,
    )


def impl_platonic_apply(
    helpers: Any,
    path: str,
    workflow_id: str,
    dry_run: bool = True,
) -> str:
    """Apply a validated platonic workflow when it is explicitly ready."""
    return _run_platonic_apply(helpers, path, workflow_id, dry_run=dry_run)


def impl_platonic_validate_only(
    helpers: Any,
    path: str,
    workflow_id: str,
    workflow: Any,
) -> str:
    """Run only validation against staged artifacts for a specific workflow.

    Called by step-aware continue when state == VALIDATING.  Skips
    profiling and generation — just validates the already-staged files.
    """
    import json as _json

    from lintgate.config import load_controlplane_config
    from lintgate.controlplane.types import PlatonicWorkflowConfig
    from lintgate.testing.platonic_workflow import (
        append_history,
        save_workflow,
        workflow_envelope,
    )
    from lintgate.testing.platonic_workflow import (
        staging_dir as _staging_dir_fn,
    )
    from mcp_tools._test_regeneration_gates import impl_rebuild_validate

    project_root = helpers["_validate_project_root"](path) or path
    platonic_cfg = PlatonicWorkflowConfig()
    try:
        cp_config = load_controlplane_config(project_root)
    except Exception:
        cp_config = None
    if cp_config is not None:
        platonic_cfg = cp_config.platonic_workflow

    wf_staging_dir = str(_staging_dir_fn(project_root, platonic_cfg.workflow_dir, workflow_id))
    validation_path = workflow.validation_artifact_path or str(
        Path(project_root) / platonic_cfg.workflow_dir / workflow_id / "validation.json"
    )

    raw = impl_rebuild_validate(
        helpers,
        path,
        generated_dir=wf_staging_dir,
        validation_path=validation_path,
    )
    validation = _json.loads(raw)

    from lintgate.testing.platonic_outcomes import workflow_state_from_outputs

    conv_summary = workflow.evidence_summary.get("convergence", {})
    health_data = workflow.evidence_summary.get("health", {})
    workflow_state, reason_code, blocking_reason = workflow_state_from_outputs(
        conv_summary,
        health_data,
        validation,
        [],
        iteration_log=[{"tests_generated": 1}] if workflow.staged_artifacts else [],
    )

    history_reentries = sum(1 for h in (workflow.history or []) if h.get("step") == "validate")
    workflow.validation_reentry_count = max(workflow.validation_reentry_count, history_reentries)
    validation_ready = validation.get("ready_to_apply") or validation.get("review_ready_to_apply")
    if validation_ready:
        workflow.validation_reentry_count = 0
    elif workflow_state in ("VALIDATING", "ASSESSING"):
        if workflow.validation_reentry_count >= 1:
            workflow_state = "PROFILING"
            reason_code = "VALIDATION_FAILED_REROUTE"
            blocking_reason = (
                "Validation failed after re-entry; routing back to profiling for regeneration."
            )
            workflow.validation_reentry_count = 0
        else:
            workflow.validation_reentry_count += 1
    else:
        workflow.validation_reentry_count = 0

    workflow.state = workflow_state
    workflow.step = "profile" if workflow_state == "PROFILING" else "validate"
    workflow.reason_code = reason_code
    workflow.blocking_reason = blocking_reason
    workflow.validation_artifact_path = validation_path
    workflow.evidence_summary["validation"] = validation

    next_actions: list[NextAction] = []
    if workflow_state in ("READY_TO_APPLY", "READY_TO_APPLY_WITH_REVIEW"):
        workflow.primary_next_action = "platonic_apply"
        workflow.primary_next_args = {"path": path, "workflow_id": workflow_id, "dry_run": True}
        workflow.autopilot_safe = workflow_state == "READY_TO_APPLY"
        workflow.human_review_required = workflow_state == "READY_TO_APPLY_WITH_REVIEW"
        next_actions.append(
            NextAction(
                tool="platonic_apply",
                args={"path": path, "workflow_id": workflow_id, "dry_run": True},
                reason="Inspect the apply plan for generated tests.",
            )
        )
    elif workflow.is_terminal():
        # Terminal states (FAILED, BLOCKED_*, etc.) should not suggest continue
        workflow.primary_next_action = ""
        workflow.primary_next_args = {}
        workflow.autopilot_safe = False
    else:
        workflow.primary_next_action = "platonic_continue"
        workflow.primary_next_args = {"path": path, "workflow_id": workflow_id}
        workflow.autopilot_safe = True
        next_actions.append(
            NextAction(
                tool="platonic_continue",
                args={"path": path, "workflow_id": workflow_id},
                reason="Resume the persisted platonic workflow.",
            )
        )

    append_history(
        workflow,
        state=workflow.state,
        step=workflow.step,
        reason_code=reason_code,
        summary={"validation": validation.get("ready_to_apply", False)},
    )
    save_workflow(project_root, platonic_cfg.workflow_dir, workflow)

    envelope = workflow_envelope(workflow, next_actions=next_actions)
    summary = (
        f"Validation {workflow.state} for {workflow.rel_file} "
        f"(wf={workflow_id}, reason={reason_code}). "
        f"Next: {workflow.primary_next_action or 'none'}."
    )
    return tool_response(
        envelope,
        "platonic_validate",
        project_root,
        summary,
        run_id=workflow_id,
        next_actions=[a.to_dict() for a in next_actions] if next_actions else None,
    )


def _load_prescriptive_kill_overrides(
    project_root: str, rel_file: str, target_kill_rate: float
) -> tuple[dict[str, dict[str, bool]], float]:
    """Load prescriptive spec kill expectations for functions in this file."""
    overrides: dict[str, dict[str, bool]] = {}
    try:
        from lintgate.specification.prescriptive_spec import (
            _SPEC_DIR,
            _target_hash,
            load_all_specs,
        )

        all_pspecs = load_all_specs(project_root)
        for pspec in all_pspecs.values():
            spec_file = (
                pspec.target_key.split("::")[0].replace(".", "/") + ".py"
                if "::" in pspec.target_key
                else ""
            )
            if not (spec_file and (spec_file == rel_file or rel_file.endswith(spec_file))):
                continue
            exp_path = os.path.join(
                project_root,
                _SPEC_DIR,
                f"{_target_hash(pspec.target_key)}_expectations.json",
            )
            if not os.path.isfile(exp_path):
                continue
            with open(exp_path, encoding="utf-8") as _ef:
                exp_data = json.load(_ef)
            eks = exp_data.get("expected_kill_set", {})
            if eks:
                overrides[pspec.target_key] = eks
                expected_categories = len(eks)
                if expected_categories > 0:
                    target_kill_rate = max(
                        target_kill_rate,
                        sum(1 for v in eks.values() if v) / expected_categories,
                    )
    except Exception:
        pass
    return overrides, target_kill_rate


def _compute_iteration_metrics(
    orch_state: Any,
    spec_result: Any,
    mutation_results: list[dict],
    runtime_mutation_cache: dict[str, dict],
    reconciliation_threshold: float,
    prescriptive_kill_overrides: dict[str, dict[str, bool]],
    iter_data: dict[str, Any],
    update_target_fn: Any,
) -> None:
    """Compute per-function spec/mutation metrics and update orchestrator state."""
    from lintgate.specification.static_empirical_reconciliation import (
        build_overlay,
        reconcile_spec_level,
    )

    for func_key, target in orch_state.targets.items():
        if target.status != "eligible":
            continue

        spec_data = spec_result.functions.get(func_key, {})
        mut_result = _find_mutation_result(mutation_results, func_key)

        spec_level = spec_data.get("specification_level", 0.0)
        survival = mut_result.get("survival_rate", 1.0) if mut_result else 1.0
        kill_rate = 1.0 - survival
        phase = spec_data.get("phase", "bulk")
        sigma = spec_data.get("sigma", spec_data.get("estimated_sigma", 0))
        regime = spec_data.get("regime", "A")

        overlay = build_overlay(func_key, sigma, regime, phase, runtime_mutation_cache)
        control_spec_level, recon_source = reconcile_spec_level(
            spec_level,
            overlay,
            confidence_threshold=reconciliation_threshold,
        )

        update_target_fn(orch_state, func_key, control_spec_level, kill_rate, phase)

        func_entry: dict[str, Any] = {
            "function_key": func_key,
            "static_spec_level": round(spec_level, 4),
            "reconciled_spec_level": round(control_spec_level, 4),
            "reconciled_data_source": recon_source,
            "kill_rate": round(kill_rate, 4),
            "phase": phase,
        }

        if func_key in prescriptive_kill_overrides:
            func_entry["prescriptive_kill_status"] = _check_prescriptive_kills(
                prescriptive_kill_overrides[func_key],
                (mut_result or {}).get("per_category", []),
            )

        iter_data["functions"].append(func_entry)


def _check_prescriptive_kills(
    expected_kills: dict[str, bool],
    per_category: list[dict[str, Any]],
) -> dict[str, str]:
    """Check per-category kill results against prescriptive expectations."""
    cat_status: dict[str, str] = {}
    for cat, should_kill in expected_kills.items():
        actual_killed = None
        for cd in per_category:
            if cd.get("category") == cat:
                actual_killed = cd.get("survived", 0) == 0
                break
        if actual_killed is None:
            cat_status[cat] = "unknown"
        elif actual_killed == should_kill:
            cat_status[cat] = "pass"
        else:
            cat_status[cat] = "fail"
    return cat_status


def _update_staged_artifacts(
    staged_artifacts: list[dict[str, Any]],
    gen_result: dict[str, Any],
    iteration: int,
) -> None:
    """Track staged artifacts from test generation, including reference-only."""
    if gen_result.get("skipped_reason") == "existing_tests_adequate":
        ref_artifact = {
            "generated_path": gen_result.get("generated_path", ""),
            "staging_path": "",
            "apply_destination": gen_result.get("generated_path", ""),
            "content_hash": "",
            "source_iteration": iteration,
            "reference_only": True,
            "canonical_path": gen_result.get("canonical_path", ""),
        }
        if not any(a["generated_path"] == ref_artifact["generated_path"] for a in staged_artifacts):
            staged_artifacts.append(ref_artifact)
    elif gen_result.get("files_written", 0) > 0 and gen_result.get("staging_path"):
        staged_artifact = {
            "generated_path": gen_result.get("generated_path", ""),
            "staging_path": gen_result.get("staging_path", ""),
            "apply_destination": _apply_destination_for_generated_path(
                gen_result.get("generated_path", "")
            ),
            "content_hash": gen_result.get("content_hash", ""),
            "source_iteration": iteration,
        }
        existing_idx = next(
            (
                i
                for i, a in enumerate(staged_artifacts)
                if a["generated_path"] == staged_artifact["generated_path"]
            ),
            None,
        )
        if existing_idx is not None:
            staged_artifacts[existing_idx] = staged_artifact
        else:
            staged_artifacts.append(staged_artifact)


def _attempt_auto_decomposition(
    path: str,
    decompose_targets: list,
) -> dict[str, Any] | None:
    """Attempt to auto-plan extraction for decompose targets.

    Returns dict with tool, args, verified=True if a clean extraction is
    possible.  Returns None if manual planning is needed.
    Prefers refactor_move for module/symbol splits, refactor_extract_method
    only for extract_method plan steps.
    """
    try:
        from mcp_tools.convergence_tools import _impl_extraction_plan

        target = decompose_targets[0]
        func_key = getattr(target, "function_key", str(target))
        # Use a minimal helpers dict — extraction_plan only needs project root validation
        plan_data = _impl_extraction_plan(path, func_key, {"_validate_project_root": lambda p: p})

        steps = plan_data.get("steps", [])
        if not steps:
            return None

        first_step = steps[0]
        step_type = first_step.get("step_type", "")

        if step_type == "create_module":
            return {
                "verified": True,
                "tool": "refactor_move",
                "args": {
                    "path": path,
                    "source": first_step.get("source_file", ""),
                    "target": first_step.get("target_module", ""),
                    "symbols": first_step.get("symbols", []),
                    "dry_run": True,
                },
            }
        elif step_type in ("create_function", "extract_body"):
            return {
                "verified": True,
                "tool": "refactor_extract_method",
                "args": {
                    "path": path,
                    "file": first_step.get("source_file", ""),
                    "function": func_key,
                    "dry_run": True,
                },
            }
        return None
    except Exception:
        return None


def _build_convergence_next_actions(
    workflow_state: str,
    path: str,
    workflow_id: str | None,
    decompose_targets: list,
    orch_state: Any,
) -> tuple[list, str, dict[str, Any], bool, bool, str]:
    """Route workflow state to next actions, primary action, and flags."""
    from lintgate.next_action import NextAction

    next_actions: list[NextAction] = []
    primary = ""
    args: dict[str, Any] = {}
    autopilot = False
    human_review = False

    terminal_states = frozenset(
        {
            "CONVERGED",
            "BLOCKED_DISCOVERY",
            "BLOCKED_TOPOLOGY",
            "BLOCKED_NO_ELIGIBLE_TARGETS",
            "NEEDS_ORACLE",
            "FAILED",
            "EXISTING_TESTS_SUFFICIENT",
            "PLATEAU_NO_GENERATION",
        }
    )

    if workflow_state == "READY_TO_APPLY":
        primary, args, autopilot = (
            "platonic_apply",
            {"path": path, "workflow_id": workflow_id, "dry_run": True},
            True,
        )
    elif workflow_state == "READY_TO_APPLY_WITH_REVIEW":
        primary, args, human_review = (
            "platonic_apply",
            {"path": path, "workflow_id": workflow_id, "dry_run": True},
            True,
        )
    elif workflow_state == "NEEDS_DECOMPOSITION" and decompose_targets:
        auto = _attempt_auto_decomposition(path, decompose_targets)
        if auto and auto.get("verified"):
            primary = auto["tool"]
            args = auto["args"]
        else:
            primary = "extraction_plan"
            args = {"path": path, "function": decompose_targets[0].function_key}
    elif workflow_state == "NEEDS_ORACLE":
        primary = ""
        args = {}
        human_review = True
    elif workflow_state not in terminal_states:
        primary, args, autopilot = (
            "platonic_continue",
            {"path": path, "workflow_id": workflow_id},
            True,
        )

    if orch_state.ready_to_apply or workflow_state in (
        "READY_TO_APPLY",
        "READY_TO_APPLY_WITH_REVIEW",
    ):
        next_actions.append(
            NextAction(
                tool="platonic_apply",
                args={"path": path, "workflow_id": workflow_id, "dry_run": True},
                reason="Inspect the apply plan for generated tests.",
            )
        )
    elif primary == "platonic_continue":
        next_actions.append(
            NextAction(
                tool="platonic_continue",
                args={"path": path, "workflow_id": workflow_id},
                reason="Resume the persisted platonic workflow.",
            )
        )

    decompose_keys = [
        t.function_key for t in orch_state.targets.values() if t.status == "decompose"
    ]
    if decompose_keys:
        next_actions.append(
            NextAction(
                tool="extraction_plan",
                args={"path": path, "function": decompose_keys[0]},
                reason=f"Decomposition is now the primary next move for {decompose_keys[0]}",
            )
        )

    step = (
        "validate"
        if workflow_state in ("VALIDATING", "READY_TO_APPLY", "READY_TO_APPLY_WITH_REVIEW")
        else "profile"
    )

    return next_actions, primary, args, autopilot, human_review, step


def impl_platonic_converge(
    helpers: Any,
    path: str,
    file: str,
    max_iterations: int = 5,
    target_spec_level: float = 0.80,
    target_kill_rate: float = 0.70,
    budget_ms: float = 30_000,
    reconciliation_threshold: float = 0.7,
    workflow_id: str | None = None,
    scope: str = "file",
    orch_state_snapshot: dict[str, Any] | None = None,
    staged_artifacts_resume: list[dict[str, Any]] | None = None,
    iterations_completed: int = 0,
    decompose_mode: str = "propose",
) -> str:
    """Run the platonic convergence loop on a single file."""
    import time

    from lintgate.config import load_controlplane_config
    from lintgate.controlplane.types import PlatonicWorkflowConfig
    from lintgate.testing.convergence_orchestrator import (
        ConvergenceConfig,
        decide,
        init_from_targets,
        summarize,
        update_target,
    )
    from lintgate.testing.platonic_selection import assess_file
    from lintgate.testing.platonic_workflow import (
        PlatonicWorkflowRecord,
        append_history,
        create_workflow_id,
        save_workflow,
        workflow_envelope,
    )
    from mcp_tools._test_regeneration_gates import impl_rebuild_validate

    project_root = helpers["_validate_project_root"](path) or path
    file_path = file if os.path.isabs(file) else os.path.join(project_root, file)
    rel_file = os.path.relpath(file_path, project_root)

    if not os.path.isfile(file_path):
        return str(helpers["_json_dumps"]({"error": f"File not found: {file}"}))

    platonic_cfg = PlatonicWorkflowConfig()
    preserve_globs: list[str] | None = None
    effective_reconciliation_threshold = reconciliation_threshold
    try:
        cp_config = load_controlplane_config(project_root)
    except Exception:
        cp_config = None
    if cp_config is not None:
        platonic_cfg = cp_config.platonic_workflow
        preserve_globs = cp_config.test_regeneration.preserve_globs
        if max_iterations == 5:
            max_iterations = platonic_cfg.max_iterations
        if _is_default_float(target_spec_level, 0.80):
            target_spec_level = platonic_cfg.target_spec_level
        if _is_default_float(target_kill_rate, 0.70):
            target_kill_rate = platonic_cfg.target_kill_rate
        if budget_ms == 30_000:
            budget_ms = platonic_cfg.default_budget_ms
        if _is_default_float(reconciliation_threshold, 0.7):
            effective_reconciliation_threshold = platonic_cfg.reconciliation_confidence_threshold
    if workflow_id is None:
        workflow_id = create_workflow_id()

    # Create workflow-scoped staging and validation paths
    from lintgate.testing.platonic_workflow import staging_dir as _staging_dir_fn

    wf_staging_dir = str(_staging_dir_fn(project_root, platonic_cfg.workflow_dir, workflow_id))
    validation_artifact_path = str(
        Path(project_root) / platonic_cfg.workflow_dir / workflow_id / "validation.json"
    )
    staged_artifacts: list[dict[str, Any]] = list(staged_artifacts_resume or [])

    config = ConvergenceConfig(
        target_spec_level=target_spec_level,
        target_kill_rate=target_kill_rate,
        max_iterations=max_iterations,
        budget_ms=budget_ms,
    )

    # ── Step 1: Initial spec analysis + classification ─────────────
    assessment = assess_file(
        project_root,
        rel_file,
        preserve_globs=preserve_globs,
        write_file_manifest=True,
    )
    if assessment.get("error"):
        return str(helpers["_json_dumps"]({"error": assessment["error"]}))

    spec_result = assessment["spec_result"]
    mutation_cache = _load_mutation_cache(project_root, rel_file)
    auto_targets = assessment["auto_targets"]
    decompose_targets = assessment["decompose_targets"]
    primary_target = assessment.get("primary_target", rel_file)

    prescriptive_kill_overrides, target_kill_rate = _load_prescriptive_kill_overrides(
        project_root, rel_file, target_kill_rate
    )

    # ── decompose_mode handling ──────────────────────────────────
    if decompose_mode == "skip":
        # Bypass decomposition — treat decompose targets as manual fallback
        decompose_targets = []

    if not auto_targets:
        state = "NEEDS_DECOMPOSITION" if decompose_targets else "BLOCKED_NO_ELIGIBLE_TARGETS"
        primary_next_action = "mutation_decompose" if decompose_targets else ""
        primary_next_args = (
            {"path": path, "file": file, "function": decompose_targets[0].function_key}
            if decompose_targets
            else {}
        )

        # Build per-function classification details for diagnostic visibility
        classification_details = []
        staleness_detected = False
        for cr in assessment.get("classifications", []):
            detail: dict[str, Any] = {
                "function": cr.function_key,
                "strategy": cr.strategy.value,
                "reasons": cr.reason_codes,
                "confidence": round(cr.confidence, 3),
            }
            if "stale_environmental_state" in cr.reason_codes:
                staleness_detected = True
                detail["stale"] = True
            classification_details.append(detail)

        # Build unblocking instructions
        unblocking: list[str] = []
        if staleness_detected:
            unblocking.append(
                "Stale environmental state detected. "
                "Run reset_state(path) to clear, then re-run converge."
            )
        if decompose_targets:
            unblocking.append(
                f"Run decompose(path, file) to see decomposition plan for "
                f"{decompose_targets[0].function_key}."
            )
            unblocking.append("After decomposition, re-run converge(path, file).")
            if decompose_mode == "propose":
                unblocking.append(
                    "Or re-run with decompose_mode='skip' to bypass entanglement check."
                )
        if not decompose_targets and not staleness_detected:
            unblocking.append(
                "Run check_project(path) to resolve upstream issues."
            )

        # Build auto-extraction plan for decompose_mode="auto"
        auto_extraction_plan = None
        if decompose_mode == "auto" and decompose_targets:
            auto = _attempt_auto_decomposition(path, decompose_targets)
            if auto and auto.get("verified"):
                auto_extraction_plan = auto

        workflow = PlatonicWorkflowRecord(
            workflow_id=workflow_id,
            scope=scope,
            target=rel_file,
            rel_file=rel_file,
            state=state,
            step="assess",
            config=_platonic_config_dict(
                max_iterations,
                target_spec_level,
                target_kill_rate,
                budget_ms,
                effective_reconciliation_threshold,
                platonic_cfg.workflow_dir,
            ),
            primary_target=primary_target,
            primary_next_action=primary_next_action,
            primary_next_args=primary_next_args,
            autopilot_safe=False,
            blocking_reason=(
                "Cross-lens decomposition evidence is stronger than test generation."
                if decompose_targets
                else "No eligible auto-generate targets remained after routing."
            ),
            reason_code=(
                "NEEDS_DECOMPOSITION" if decompose_targets else "BLOCKED_NO_ELIGIBLE_TARGETS"
            ),
            human_review_required=bool(decompose_targets),
            evidence_summary=assessment["summary"],
            proposed_artifacts=[],
            manifest_path=assessment.get("manifest_path", ""),
        )
        append_history(
            workflow,
            state=workflow.state,
            step=workflow.step,
            reason_code=workflow.reason_code,
            summary=workflow.evidence_summary,
        )
        save_workflow(project_root, platonic_cfg.workflow_dir, workflow)
        next_actions = (
            [
                NextAction(
                    tool="mutation_decompose",
                    args={"path": path, "file": file},
                    reason="Cross-lens routing recommends decomposition over more tests.",
                )
            ]
            if decompose_targets
            else []
        )
        extra: dict[str, Any] = {
            "status": "no_eligible_targets",
            "total_functions": assessment["summary"]["total_functions"],
            "classifications": assessment["summary"]["strategy_distribution"],
            "classification_details": classification_details,
            "staleness_detected": staleness_detected,
            "unblocking_instructions": unblocking,
        }
        if auto_extraction_plan:
            extra["auto_extraction_plan"] = auto_extraction_plan
        output = workflow_envelope(
            workflow,
            next_actions=next_actions,
            extra=extra,
        )
        summary = (
            f"Converge {state} for {rel_file} ({assessment['summary']['total_functions']} funcs). "
            f"{workflow.blocking_reason}"
        )
        if staleness_detected:
            summary += " Stale state detected — run reset_state to clear."
        if unblocking:
            summary += f" To unblock: {unblocking[0]}"
        return tool_response(
            output,
            "platonic_converge",
            project_root,
            summary,
            run_id=workflow_id,
            next_actions=[a.to_dict() for a in next_actions] if next_actions else None,
        )

    # Initialize convergence state — hydrate from snapshot on resume
    iteration_log: list[dict[str, Any]] = []
    sampling_cap = 3  # initial max_per_category, escalates on stall

    if orch_state_snapshot:
        from lintgate.testing.convergence_orchestrator import OrchestratorState

        restored_state = OrchestratorState.from_dict(orch_state_snapshot)
    else:
        restored_state = None
    target_dicts = [
        {
            "function_key": c.evidence.function_key,
            "source_file": c.evidence.source_file,
            "target_test_file": c.target_test_file,
        }
        for c in auto_targets
    ]
    orch_state = init_from_targets(target_dicts)
    if restored_state is not None:
        for func_key, target in orch_state.targets.items():
            restored_target = restored_state.targets.get(func_key)
            if restored_target is None:
                continue
            restored_target.source_file = target.source_file or restored_target.source_file
            restored_target.target_test_file = (
                target.target_test_file or restored_target.target_test_file
            )
            orch_state.targets[func_key] = restored_target
    prev_total_kill = sum(t.kill_rate for t in orch_state.targets.values())
    start_iteration = max(1, iterations_completed + 1)

    # ── Step 2: Iteration loop ─────────────────────────────────────
    for iteration in range(start_iteration, max_iterations + 1):
        iter_start = time.monotonic()
        iter_data: dict[str, Any] = {"iteration": iteration, "functions": []}

        # Deterministic seed varies per iteration + file for stable shuffle
        iter_seed = hash((rel_file, iteration))

        # Profile mutations — include both manifest targets and staged artifacts
        # so mutation sampling tests against files we actually generated
        mutation_results = _run_mutation_sampling(
            project_root,
            rel_file,
            budget_ms=min(budget_ms / max_iterations, 5000),
            generated_test_files=_profiled_test_files(orch_state, staged_artifacts),
            max_per_category=sampling_cap,
            seed=iter_seed,
        )
        runtime_mutation_cache = dict(mutation_cache or {})
        for result in mutation_results:
            fk = result.get("function_key", "")
            if not fk:
                continue
            runtime_mutation_cache[fk] = {
                **runtime_mutation_cache.get(fk, {}),
                **result,
            }

        # Build per-function metrics from mutation + spec
        _compute_iteration_metrics(
            orch_state,
            spec_result,
            mutation_results,
            runtime_mutation_cache,
            effective_reconciliation_threshold,
            prescriptive_kill_overrides,
            iter_data,
            update_target,
        )

        mutation_cache = runtime_mutation_cache
        _persist_mutation_cache_entries(project_root, mutation_cache)

        # Stall detection: escalate sampling cap when kill rate doesn't improve
        current_total_kill = sum(f.get("kill_rate", 0.0) for f in iter_data["functions"])
        delta_kill = current_total_kill - prev_total_kill
        if iteration > 1 and delta_kill <= 0.0:
            sampling_cap = min(sampling_cap * 2, 10)
            iter_data["stall_escalation"] = sampling_cap
        prev_total_kill = current_total_kill

        # Generate tests for eligible targets (staged)
        gen_result = _generate_tests(
            project_root, file_path, auto_targets, staging_dir=wf_staging_dir
        )
        _reroute_manual_contract_candidates(
            project_root,
            gen_result.get("manual_contract_candidates", []),
        )
        iter_data["tests_generated"] = gen_result.get("files_written", 0)
        if gen_result.get("skipped_reason"):
            iter_data["skipped_reason"] = gen_result["skipped_reason"]

        _update_staged_artifacts(staged_artifacts, gen_result, iteration)
        if gen_result.get("manual_contract_candidates"):
            iter_data["manual_contract_candidates"] = gen_result["manual_contract_candidates"]
        iter_data["oracle_requests"] = gen_result.get("oracle_requests", [])

        # Decide: iterate, halt, or converged?
        decisions = decide(orch_state, config)
        iter_data["decisions"] = [
            {"function": d.function_key, "action": d.action, "reason": d.reason} for d in decisions
        ]

        iter_data["elapsed_ms"] = round((time.monotonic() - iter_start) * 1000, 1)
        iteration_log.append(iter_data)

        # Check if all targets are done
        if orch_state.eligible_count == 0:
            break

    # ── Step 3: Compute final health vector ────────────────────────
    # Re-analyze spec after test generation
    from lintgate.specification.file_analyzer import analyze_file

    final_spec = analyze_file(file_path, project_root, enrich=True)
    final_mutation = dict(_load_mutation_cache(project_root, rel_file) or {})
    if mutation_cache:
        final_mutation.update(mutation_cache)

    health_data = _compute_file_health(
        final_spec.functions,
        final_mutation or None,
        orch_state,
        confidence_threshold=effective_reconciliation_threshold,
    )

    # ── Step 4: Build result ───────────────────────────────────────
    conv_summary = summarize(orch_state)
    validation = json.loads(
        impl_rebuild_validate(
            helpers,
            path,
            generated_dir=wf_staging_dir,
            validation_path=validation_artifact_path,
        )
    )

    # Aggregate oracle requests across all iterations
    all_oracle_requests: list[dict] = []
    for ilog in iteration_log:
        all_oracle_requests.extend(ilog.get("oracle_requests", []))

    workflow_state, reason_code, blocking_reason = _workflow_state_from_outputs(
        conv_summary,
        health_data,
        validation,
        decompose_targets,
        iteration_log=iteration_log,
        oracle_requests=all_oracle_requests,
    )

    (
        converge_next_actions,
        primary_next_action,
        converge_next_args,
        autopilot_safe,
        human_review_required_flag,
        workflow_step,
    ) = _build_convergence_next_actions(
        workflow_state, path, workflow_id, decompose_targets, orch_state
    )
    workflow = PlatonicWorkflowRecord(
        workflow_id=workflow_id,
        scope=scope,
        target=rel_file,
        rel_file=rel_file,
        state=workflow_state,
        step=workflow_step,
        config=_platonic_config_dict(
            max_iterations,
            target_spec_level,
            target_kill_rate,
            budget_ms,
            effective_reconciliation_threshold,
            platonic_cfg.workflow_dir,
        ),
        primary_target=primary_target,
        primary_next_action=primary_next_action,
        primary_next_args=converge_next_args,
        autopilot_safe=autopilot_safe,
        blocking_reason=blocking_reason,
        reason_code=reason_code,
        human_review_required=human_review_required_flag
        or workflow_state in ("NEEDS_DECOMPOSITION", "FAILED"),
        evidence_summary={
            "assessment": assessment["summary"],
            "health": health_data,
            "convergence": conv_summary,
            "validation": validation,
            "oracle_requests": all_oracle_requests,
        },
        proposed_artifacts=_proposed_artifacts(validation, assessment),
        manifest_path=assessment.get("manifest_path", ""),
        iterations_completed=iterations_completed + len(iteration_log),
        staged_artifacts=staged_artifacts,
        orch_state_snapshot=orch_state.to_dict(),
        validation_artifact_path=validation_artifact_path,
        validation_reentry_count=0,
    )
    append_history(
        workflow,
        state=workflow.state,
        step=workflow.step,
        reason_code=workflow.reason_code,
        summary={
            "iterations_completed": len(iteration_log),
            "ready_to_apply": conv_summary["ready_to_apply"],
        },
    )
    save_workflow(project_root, platonic_cfg.workflow_dir, workflow)

    envelope = workflow_envelope(
        workflow,
        next_actions=converge_next_actions,
        extra={
            "file": rel_file,
            "health": health_data,
            "convergence": conv_summary,
            "iterations": iteration_log,
            "reconciliation_threshold": effective_reconciliation_threshold,
        },
    )
    iters_done = len(iteration_log)
    ready = conv_summary.get("ready_to_apply", False)
    avg_kill = conv_summary.get("mean_kill_rate", 0.0)
    summary = (
        f"Converge {workflow_state} for {rel_file} after {iters_done} iteration(s). "
        f"Kill rate: {avg_kill:.0%}, ready_to_apply={ready}. "
        f"Next: {primary_next_action or 'none'}."
    )
    return tool_response(
        envelope,
        "platonic_converge",
        project_root,
        summary,
        run_id=workflow_id,
        next_actions=[a.to_dict() for a in converge_next_actions]
        if converge_next_actions
        else None,
    )


def _platonic_config_dict(
    max_iterations: int,
    target_spec_level: float,
    target_kill_rate: float,
    budget_ms: float,
    reconciliation_threshold: float,
    workflow_dir: str,
) -> dict[str, Any]:
    """Compatibility wrapper for platonic workflow config serialization."""
    return _platonic_config_dict_impl(
        max_iterations,
        target_spec_level,
        target_kill_rate,
        budget_ms,
        reconciliation_threshold,
        workflow_dir,
    )


def _workflow_state_from_outputs(
    conv_summary: dict[str, Any],
    health_data: dict[str, Any],
    validation: dict[str, Any],
    decompose_targets: list[Any],
    *,
    iteration_log: list[dict[str, Any]] | None = None,
    oracle_requests: list[dict[str, Any]] | None = None,
) -> tuple[str, str, str]:
    """Compatibility wrapper for golden-path workflow state resolution."""
    return _workflow_state_from_outputs_impl(
        conv_summary,
        health_data,
        validation,
        decompose_targets,
        iteration_log=iteration_log,
        oracle_requests=oracle_requests,
    )


def _proposed_artifacts(
    validation: dict[str, Any],
    assessment: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compatibility wrapper for platonic artifact summaries."""
    return _proposed_artifacts_impl(validation, assessment)


def _reroute_manual_contract_candidates(
    project_root: str,
    function_keys: list[str],
) -> None:
    """Compatibility wrapper for witness-less auto-target rerouting."""
    _reroute_manual_contract_candidates_impl(project_root, function_keys)


def _profiled_test_files(
    orch_state: Any,
    staged_artifacts: list[dict[str, Any]],
) -> list[str]:
    """Return actual test files that should be visible to mutation sampling."""
    staged_by_generated = {
        artifact.get("generated_path", ""): artifact.get("staging_path", "")
        for artifact in staged_artifacts
        if artifact.get("generated_path") and artifact.get("staging_path")
    }
    profiled: list[str] = []
    for target in orch_state.targets.values():
        generated_path = getattr(target, "target_test_file", "")
        if not generated_path:
            continue
        actual_path = staged_by_generated.get(generated_path, generated_path)
        if actual_path and actual_path not in profiled:
            profiled.append(actual_path)
    return profiled


def _apply_destination_for_generated_path(generated_path: str) -> str:
    """Map a generated test path to the destination used by platonic_apply."""
    normalized = generated_path.replace("\\", "/")
    prefix = "tests/generated/"
    if normalized.startswith(prefix):
        return f"tests/{normalized[len(prefix) :]}"
    return generated_path


def _load_mutation_cache(
    project_root: str,
    rel_file: str,
) -> dict[str, dict] | None:
    """Compatibility wrapper for file-scoped mutation cache loading."""
    return _platonic_load_mutation_cache(project_root, rel_file)


def _persist_mutation_cache_entries(
    project_root: str,
    mutation_cache: dict[str, dict] | None,
) -> None:
    """Compatibility wrapper for per-function mutation cache persistence."""
    _platonic_persist_mutation_cache_entries(project_root, mutation_cache)


def _run_mutation_sampling(
    project_root: str,
    rel_file: str,
    budget_ms: float = 5000,
    generated_test_files: list[str] | None = None,
    max_per_category: int = 3,
    seed: int | None = None,
) -> list[dict]:
    """Compatibility wrapper for mutation sampling in the platonic loop."""
    return _platonic_run_mutation_sampling(
        project_root,
        rel_file,
        budget_ms=budget_ms,
        generated_test_files=generated_test_files,
        max_per_category=max_per_category,
        seed=seed,
    )


def _find_mutation_result(
    results: list[dict],
    func_key: str,
) -> dict | None:
    """Compatibility wrapper for mutation result lookup."""
    return _platonic_find_mutation_result(results, func_key)


def _generate_tests(
    project_root: str,
    file_path: str,
    auto_targets: list,
    *,
    staging_dir: str | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper for platonic test generation."""
    return _platonic_generate_tests(project_root, file_path, auto_targets, staging_dir=staging_dir)


def _compute_file_health(
    spec_functions: dict[str, Any],
    mutation_cache: dict[str, dict] | None,
    orch_state: Any,
    confidence_threshold: float = 0.7,
) -> dict[str, Any]:
    """Compatibility wrapper for platonic file health computation."""
    return _platonic_compute_file_health(
        spec_functions,
        mutation_cache,
        orch_state,
        confidence_threshold=confidence_threshold,
    )


def impl_platonic_sweep(
    helpers: Any,
    path: str,
    budget_s: float = 300.0,
    max_files: int = 10,
    batch_size: int = 10,
    resume: bool = True,
) -> str:
    """Run scheduler-driven multi-file sweep toward specification closure."""
    import time

    from lintgate.next_action import NextAction, serialize_next_actions
    from lintgate.specification.project_rollup import rollup_project
    from lintgate.specification.scheduler import MutationScheduler, SchedulerConfig
    from lintgate.specification.static_empirical_reconciliation import build_overlay
    from lintgate.testing.platonic_selection import assess_file

    project_root = helpers["_validate_project_root"](path)
    cache_dir_path = os.path.join(project_root, ".lintgate", "mutation")
    from pathlib import Path

    cache_dir = Path(cache_dir_path)

    # Load rollup for hotspot prioritization
    rollup = rollup_project(project_root, use_cache=True, analyze_uncached=True)

    # Init scheduler
    scheduler = MutationScheduler(SchedulerConfig(batch_size=batch_size))
    resumed = False
    if resume:
        resumed = scheduler.load_state(cache_dir)

    # If not resumed or queue is empty, enqueue from rollup hotspots
    if not resumed or scheduler.status().queue_depth == 0:
        for hotspot in rollup.hotspot_files[:max_files]:
            rel_file = hotspot.get("file", "")
            if not rel_file:
                continue
            assessment = assess_file(project_root, rel_file)
            if assessment.get("error") or assessment.get("majority_hard_veto"):
                continue

            spec_result = assessment.get("spec_result")
            if spec_result is None:
                continue

            for func_key, func_data in spec_result.functions.items():
                if not isinstance(func_data, dict):
                    continue
                sigma = func_data.get("sigma", func_data.get("estimated_sigma", 0)) or 0
                risk = func_data.get("risk_score", 0.0)
                is_pure = func_data.get("is_pure", False)
                hints = func_data.get("optimization_hints", [])
                overlay = build_overlay(
                    func_key,
                    int(sigma),
                    func_data.get("regime", "A"),
                    func_data.get("phase", "bulk"),
                    None,
                )
                scheduler.enqueue(
                    function_key=func_key,
                    file_path=rel_file,
                    sigma=int(sigma),
                    risk_score=float(risk),
                    is_pure=bool(is_pure),
                    has_hints=bool(hints),
                    overlay_status=overlay.status.value,
                    overlay_confidence=overlay.overlay_confidence,
                )

    # Batch loop with budget
    start = time.monotonic()
    sweep_results: list[dict[str, Any]] = []
    batches_run = 0

    while time.monotonic() - start < budget_s:
        batch = scheduler.next_batch()
        if not batch:
            break
        batches_run += 1

        for item in batch:
            # Run mutation sampling on each item
            result_entry: dict[str, Any] = {
                "function_key": item.function_key,
                "file_path": item.file_path,
                "tier": item.tier,
                "survival_rate": 1.0,
            }
            try:
                from lintgate.testing.platonic_mutation import run_mutation_sampling

                mut_results = run_mutation_sampling(
                    project_root,
                    item.file_path,
                    budget_ms=5000,
                )
                # Find result for this function
                for mr in mut_results:
                    if mr.get("function_key") == item.function_key:
                        result_entry["survival_rate"] = mr.get("survival_rate", 1.0)
                        result_entry["per_category"] = mr.get("per_category", [])
                        break
            except Exception:
                result_entry["error"] = "sampling_failed"

            scheduler.report_result(
                item,
                survival_rate=result_entry["survival_rate"],
                budget_exhausted=result_entry.get("error") is not None,
            )
            sweep_results.append(result_entry)

        # Save state after each batch
        scheduler.save_state(cache_dir)

    # Build output
    status = scheduler.status()

    # Aggregate per-file results
    file_summary: dict[str, dict[str, Any]] = {}
    for r in sweep_results:
        fp = r["file_path"]
        if fp not in file_summary:
            file_summary[fp] = {"functions_swept": 0, "avg_survival": 0.0, "total_survival": 0.0}
        file_summary[fp]["functions_swept"] += 1
        file_summary[fp]["total_survival"] += r["survival_rate"]
    for summary in file_summary.values():
        n = summary["functions_swept"]
        summary["avg_survival"] = round(summary["total_survival"] / n, 3) if n > 0 else 0.0
        del summary["total_survival"]

    next_actions = []
    if status.queue_depth > 0:
        next_actions.append(
            NextAction(
                tool="platonic_sweep",
                args={"path": path, "resume": True},
                reason=f"{status.queue_depth} functions remain in queue",
            )
        )
    next_actions.append(
        NextAction(
            tool="spec_project_rollup",
            args={"path": path},
            reason="View updated project-wide specification state",
        )
    )

    elapsed_s = round(time.monotonic() - start, 1)
    output: dict[str, Any] = {
        "project": project_root,
        "resumed": resumed,
        "budget_s": budget_s,
        "elapsed_s": elapsed_s,
        "batches_run": batches_run,
        "functions_swept": len(sweep_results),
        "scheduler_status": status.to_dict(),
        "file_summary": file_summary,
        "rollup": {
            "mean_spec_level": round(rollup.mean_spec_level, 3),
            "mean_reconciled_spec_level": round(rollup.mean_reconciled_spec_level, 3),
            "total_functions": rollup.total_functions,
        },
        "next_actions": serialize_next_actions(next_actions),
    }
    summary = (
        f"Sweep: {len(sweep_results)} functions across {len(file_summary)} files "
        f"in {elapsed_s}s ({batches_run} batches). "
        f"Queue remaining: {status.queue_depth}. "
        f"Mean spec level: {rollup.mean_spec_level:.3f}."
    )
    return tool_response(
        output,
        "platonic_sweep",
        project_root,
        summary,
        next_actions=[a.to_dict() for a in next_actions] if next_actions else None,
    )
