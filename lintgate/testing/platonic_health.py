"""Health/reconciliation helpers for the platonic workflow."""

from __future__ import annotations

from typing import Any


def compute_file_health(
    spec_functions: dict[str, Any],
    mutation_cache: dict[str, dict] | None,
    orch_state: Any,
    confidence_threshold: float = 0.7,
) -> dict[str, Any]:
    """Compute health vector for a file from spec, mutation, and convergence state."""
    from lintgate.specification.health_vector import compute_health
    from lintgate.specification.static_empirical_reconciliation import (
        build_overlay,
        reconcile_spec_level,
    )

    if not spec_functions:
        health = compute_health()
        return {
            "axes": health.axes,
            "scalar": health.scalar,
            "vetoed": health.vetoed,
            "vetoes": health.vetoes,
            "reconciliation_active": health.reconciliation_active,
            "axes_measured": health.axes_measured,
        }

    total_spec = 0.0
    total_reconciled = 0.0
    total_kill = 0.0
    total_convergence = 0.0
    total_composition_gamma = 0.0
    total_test_eff = 0.0
    count = len(spec_functions)
    has_artifact = False
    budget_exhausted = 0
    mock_boundary_count = 0
    reconciliation_count = 0

    for func_key, func_data in spec_functions.items():
        raw_spec = func_data.get("specification_level", 0.0)
        total_spec += raw_spec
        total_composition_gamma += func_data.get("composition_gamma", 0.0)
        total_test_eff += func_data.get("testability_score", 0.0)

        target = orch_state.targets.get(func_key) if orch_state else None
        mut_entry = mutation_cache.get(func_key) if mutation_cache else None

        if target:
            total_kill += target.kill_rate
            total_convergence += target.convergence_rate
        elif mut_entry:
            survival = mut_entry.get("survival_rate", 1.0)
            total_kill += 1.0 - survival

        if mut_entry:
            if mut_entry.get("budget_exhausted", False):
                budget_exhausted += 1
            discovery_state = mut_entry.get("discovery_state", "")
            survival_interpretation = mut_entry.get("survival_interpretation", "")
            truth_label = mut_entry.get("mutation_truth_label", "")
            if (
                discovery_state
                in (
                    "NO_TEST_FILES",
                    "TEST_FILES_FOUND_NONE_LINKED",
                    "DISCOVERY_WEAK_LINKAGE",
                    "DISCOVERY_IMPORT_FAILED",
                )
                or survival_interpretation == "DISCOVERY_ARTIFACT"
                or truth_label == "DISCOVERY_ARTIFACT"
            ):
                has_artifact = True
            if mut_entry.get("topology_state") == "MOCK_BOUNDARY_DOMINANT":
                mock_boundary_count += 1

        sigma = func_data.get("sigma", func_data.get("estimated_sigma", 0))
        regime = func_data.get("regime", "A")
        phase = func_data.get("phase", "bulk")
        overlay_entry: dict[str, Any] | None = None
        if mut_entry is not None:
            overlay_entry = dict(mut_entry)
        if target is not None:
            overlay_entry = dict(overlay_entry or {})
            overlay_entry["survival_rate"] = 1.0 - target.kill_rate
        overlay_cache = {func_key: overlay_entry} if overlay_entry is not None else mutation_cache
        overlay = build_overlay(func_key, sigma, regime, phase, overlay_cache)
        reconciled, source = reconcile_spec_level(raw_spec, overlay, confidence_threshold)
        total_reconciled += reconciled
        if source != "static":
            reconciliation_count += 1

    has_convergence_data = bool(
        orch_state and any(orch_state.targets.get(fk) is not None for fk in spec_functions)
    )

    health = compute_health(
        spec_level=total_spec / count,
        kill_rate=total_kill / count,
        convergence=total_convergence / count,
        composition_gamma=total_composition_gamma / count,
        test_efficiency=total_test_eff / count,
        has_discovery_artifact=has_artifact,
        budget_exhausted_share=budget_exhausted / count if count else 0.0,
        mock_boundary_share=mock_boundary_count / count if count else 0.0,
        reconciled_spec_level=total_reconciled / count if reconciliation_count > 0 else None,
        convergence_measured=has_convergence_data,
    )

    return {
        "axes": health.axes,
        "scalar": health.scalar,
        "vetoed": health.vetoed,
        "vetoes": health.vetoes,
        "reconciliation_active": health.reconciliation_active,
        "axes_measured": health.axes_measured,
    }
