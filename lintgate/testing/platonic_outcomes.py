"""Workflow outcome and manifest-routing helpers for the platonic workflow."""

from __future__ import annotations

from typing import Any


def platonic_config_dict(
    max_iterations: int,
    target_spec_level: float,
    target_kill_rate: float,
    budget_ms: float,
    reconciliation_threshold: float,
    workflow_dir: str,
) -> dict[str, Any]:
    """Serialize the effective workflow config for persistence."""
    return {
        "max_iterations": max_iterations,
        "target_spec_level": target_spec_level,
        "target_kill_rate": target_kill_rate,
        "budget_ms": budget_ms,
        "reconciliation_threshold": reconciliation_threshold,
        "workflow_dir": workflow_dir,
    }


def workflow_state_from_outputs(
    conv_summary: dict[str, Any],
    health_data: dict[str, Any],
    validation: dict[str, Any],
    decompose_targets: list[Any],
    *,
    iteration_log: list[dict[str, Any]] | None = None,
    oracle_requests: list[dict[str, Any]] | None = None,
) -> tuple[str, str, str]:
    """Resolve the canonical golden-path workflow state."""
    iteration_log = iteration_log or []
    runtime_decompose = any(
        target.get("status") == "decompose" for target in conv_summary.get("targets", [])
    )
    if decompose_targets or runtime_decompose:
        return (
            "NEEDS_DECOMPOSITION",
            "NEEDS_DECOMPOSITION",
            "Cross-lens evidence indicates structural decomposition is the next move.",
        )

    if oracle_requests:
        return (
            "NEEDS_ORACLE",
            "NEEDS_ORACLE",
            f"{len(oracle_requests)} function(s) need human-provided expected values.",
        )

    vetoes = health_data.get("vetoes", {})
    if vetoes.get("discovery_artifact"):
        return (
            "BLOCKED_DISCOVERY",
            "DISCOVERY_ARTIFACT",
            "Discovery artifacts make the current mutation/test signal untrustworthy.",
        )
    if vetoes.get("mock_boundary"):
        return (
            "BLOCKED_TOPOLOGY",
            "MOCK_BOUNDARY_ARTIFACT",
            "Mock-boundary topology dominates the current tests, so survival is not meaningful.",
        )
    if vetoes.get("budget_instability"):
        return (
            "FAILED",
            "BUDGET_INSTABILITY",
            "Mutation execution exhausted its budget too often to continue automatically.",
        )
    if validation.get("error"):
        return ("FAILED", "VALIDATION_ERROR", str(validation["error"]))
    if validation.get("ready_to_apply"):
        return ("READY_TO_APPLY", "READY_TO_APPLY", "")
    if validation.get("review_ready_to_apply"):
        return (
            "READY_TO_APPLY_WITH_REVIEW",
            "REVIEW_CEILING_EXCEEDED",
            "Generated tests pass quality gates but manual review share exceeds ceiling.",
        )
    if conv_summary.get("total_targets", 0) > 0 and conv_summary.get(
        "converged", 0
    ) == conv_summary.get("total_targets", 0):
        return ("CONVERGED", "CONVERGED", "")

    # Terminal: all iterations skipped generation because existing tests are adequate
    if iteration_log and all(
        iteration.get("skipped_reason") == "existing_tests_adequate" for iteration in iteration_log
    ):
        return (
            "EXISTING_TESTS_SUFFICIENT",
            "EXISTING_TESTS_SUFFICIENT",
            "All generation targets already have adequate nontrivial tests.",
        )

    # Terminal: iterations ran but no tests were generated (plateau)
    if iteration_log and not any(
        iteration.get("tests_generated", 0) > 0 for iteration in iteration_log
    ):
        return (
            "PLATEAU_NO_GENERATION",
            "PLATEAU_NO_GENERATION",
            "Profiling completed but generation produced no new tests across all iterations.",
        )

    if iteration_log and any(
        iteration.get("tests_generated", 0) > 0 for iteration in iteration_log
    ):
        return (
            "VALIDATING",
            "CONTINUE",
            "Generated tests exist but validation gates are not yet satisfied.",
        )
    if iteration_log:
        return (
            "PROFILING",
            "CONTINUE",
            "Additional profiling and generation iterations are available.",
        )

    # No iterations ran: check if all targets are already halted/converged
    # (e.g. exhausted PROFILING snapshot resumed with zero remaining iterations)
    targets = conv_summary.get("targets", [])
    if targets and all(t.get("status") in ("halted", "converged", "decompose") for t in targets):
        halted = sum(1 for t in targets if t.get("status") == "halted")
        if halted > 0:
            return (
                "PLATEAU_NO_GENERATION",
                "ALL_TARGETS_HALTED",
                "All targets reached halt conditions before generation could produce tests.",
            )

    return ("ASSESSING", "CONTINUE", "")


def proposed_artifacts(
    validation: dict[str, Any],
    assessment: dict[str, Any],
) -> list[dict[str, Any]]:
    """Summarize filesystem artifacts produced by the workflow."""
    artifacts: list[dict[str, Any]] = []
    manifest_path = assessment.get("manifest_path", "")
    if manifest_path:
        artifacts.append({"kind": "manifest", "path": manifest_path})
    for target in assessment.get("auto_targets", []):
        if target.target_test_file:
            artifacts.append({"kind": "generated_test", "path": target.target_test_file})
    if validation:
        artifacts.append(
            {
                "kind": "validation",
                "path": ".lintgate/test_rebuild_validation.json",
                "ready_to_apply": bool(validation.get("ready_to_apply")),
            }
        )
    return artifacts


def reroute_manual_contract_candidates(
    project_root: str,
    function_keys: list[str],
) -> None:
    """Downgrade auto targets with no executable witness to manual_contract."""
    if not function_keys:
        return
    try:
        from lintgate.specification._regeneration_types import ExistingTestAction, Strategy
        from lintgate.specification.test_regeneration_strategy import load_manifest, write_manifest

        manifest = load_manifest(project_root)
        if manifest is None:
            return
        keys = set(function_keys)
        changed = False
        for func in manifest.functions:
            if func.function_key not in keys:
                continue
            func.strategy = Strategy.MANUAL_CONTRACT
            func.existing_test_action = ExistingTestAction.QUARANTINE_ONLY
            func.target_test_file = ""
            func.generation_mode = "manual_contract"
            func.manual_review_required = True
            if "no_executable_witness" not in func.reason_codes:
                func.reason_codes.append("no_executable_witness")
            changed = True
        if changed:
            write_manifest(manifest, project_root)
    except Exception:
        return
