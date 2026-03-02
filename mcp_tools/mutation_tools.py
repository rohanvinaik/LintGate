"""Mutation testing tools — run sampling, full profiling, and state management."""

from __future__ import annotations

import contextlib
import json
from typing import Any


def _profile_survival_rate(profile: dict[str, Any] | None) -> float:
    """Compute survival rate even if the serialized profile lacks the derived field."""
    if not profile:
        return 0.0
    if "survival_rate" in profile:
        try:
            return float(profile["survival_rate"])
        except (TypeError, ValueError):
            return 0.0
    total = profile.get("total", 0) or 0
    survived = profile.get("survived", 0) or 0
    return (float(survived) / float(total)) if total else 1.0


def _get_engine(project_root: str):
    """Helper to initialize MutationEngine with project-aware state."""
    import os

    from lintgate.mutation.engine import MutationEngine
    from lintgate.mutation.policy import RuntimeBudget
    from lintgate.mutation.state import MutationStateManager
    from lintgate.state import MUTATION_CACHE_DIR

    state_path = os.path.join(MUTATION_CACHE_DIR, "state.json")
    state_manager = MutationStateManager(state_path)
    with contextlib.suppress(OSError, ValueError):
        state_manager.load()

    budget = RuntimeBudget()
    return MutationEngine(state_manager, budget)


def _merge_decomposition_candidates(
    dynamic: list[Any],
    static: list[Any],
) -> list[Any]:
    """Merge dynamic (mutation) and static (AST) decomposition candidates.

    Rules:
    - Dynamic takes priority for the same function.
    - If both exist for the same function → source="converged", confidence boosted.
    - Static-only candidates are kept with their original lower confidence.
    """
    # Index dynamic candidates by function_id
    by_id: dict[str, Any] = {}
    for c in dynamic:
        by_id[c.function_id] = c

    for sc in static:
        if sc.function_id in by_id:
            # Convergence: both static and dynamic agree this function needs work
            dc = by_id[sc.function_id]
            dc.source = "converged"
            dc.confidence = min(dc.confidence + 0.10, 0.95)
            dc.evidence = list(set(dc.evidence + sc.evidence))
            dc.expected_benefit = f"{dc.expected_benefit}; {sc.expected_benefit}"
        else:
            by_id[sc.function_id] = sc

    return sorted(by_id.values(), key=lambda c: c.confidence, reverse=True)


# ---------------------------------------------------------------------------
# Implementation functions — extracted from register() for testability and CC
# ---------------------------------------------------------------------------


def _impl_run_sampling(
    engine: Any,
    files: list[str] | None,
    project_root: str | None = None,
) -> dict[str, Any] | None:
    """Run sampled mutation analysis. Returns None if no files provided."""
    from lintgate.mutation.engine import _is_mutant_path
    from lintgate.mutation.policy import MutationTelemetry

    if not files:
        return None

    files = [f for f in files if not _is_mutant_path(f)]
    if not files:
        return None

    telemetry = MutationTelemetry("sampling_run")
    results = engine.run_inline_sampling(
        files, telemetry, project_root=project_root
    )

    return {
        "run_id": telemetry.run_id,
        "functions_profiled": len(results),
        "results": [r.to_dict() for r in results],
        "telemetry": {
            "time_ms": telemetry.inline_time_ms_spent,
            "mutants_executed": telemetry.mutants_executed,
        },
    }


def _impl_run_full(
    engine: Any, project_root: str, files: list[str] | None
) -> dict[str, Any]:
    """Run full mutation profiling with test-impact gating."""
    from lintgate.mutation.engine import _is_mutant_path
    from lintgate.mutation.policy import MutationTelemetry

    if not files:
        from lintgate.channels.performance_channel import _discover_python_files

        files = _discover_python_files(project_root)

    files = [f for f in files if not _is_mutant_path(f)]

    test_mapping: dict[str, list[str]] = {}
    telemetry = MutationTelemetry("full_profiling_run")
    results = engine.run_background_profiling(
        files, test_mapping, telemetry, project_root=project_root
    )

    return {
        "run_id": telemetry.run_id,
        "functions_profiled": len(results),
        "results": [r.to_dict() for r in results],
        "telemetry": {
            "functions_background": telemetry.background_functions_profiled,
        },
    }


def _impl_get_state(engine: Any, file: str | None) -> dict[str, Any]:
    """Retrieve mutation state, optionally filtered by file."""
    all_states = engine.state_manager.state
    if file:
        all_states = {k: v for k, v in all_states.items() if k.startswith(file)}

    by_file: dict[str, list[dict[str, Any]]] = {}
    for _key, state in all_states.items():
        f_path = state.file_path
        by_file.setdefault(f_path, []).append(state.to_dict())

    return {"files": by_file}


def _impl_clear_state(engine: Any, files: list[str] | None) -> dict[str, str]:
    """Clear mutation state for specific files or all."""
    if not files:
        engine.state_manager.state = {}
        msg = "Cleared all mutation state"
    else:
        for f in files:
            keys_to_remove = [k for k in engine.state_manager.state if k.startswith(f)]
            for k in keys_to_remove:
                del engine.state_manager.state[k]
        msg = f"Cleared state for {len(files)} files"

    engine.state_manager.save()
    return {"message": msg}


def _impl_prescribe(
    engine: Any,
    file: str | None,
    function: str | None,
    min_depth: str,
) -> dict[str, Any]:
    """Generate prescriptions from mutation profiles."""
    import dataclasses

    from lintgate.mutation.prescriptions import PrescriptionEngine
    from lintgate.mutation.state import CoverageDepth

    p_engine = PrescriptionEngine()
    min_depth_enum = (
        CoverageDepth.SAMPLED
        if min_depth.lower() == "sampled"
        else CoverageDepth.PROFILED
    )

    filtered_states = []
    for _key, state in engine.state_manager.state.items():
        if file and not state.file_path.endswith(file):
            continue
        if function and state.function_name != function:
            continue
        if (
            min_depth_enum == CoverageDepth.PROFILED
            and state.depth != CoverageDepth.PROFILED
        ):
            continue
        filtered_states.append(state)

    from lintgate.next_action import NextAction, serialize_next_actions

    profiles: list[dict[str, Any]] = []
    diagnoses: list[dict[str, Any]] = []
    all_prescriptions: list[dict[str, Any]] = []
    all_next_actions: list[NextAction] = []
    seen_tools: set[str] = set()
    overall_gate = "PASS"

    for state in filtered_states:
        diag = p_engine.diagnose(state)

        if diag.gate_status == "FAIL":
            overall_gate = "FAIL"
        elif diag.gate_status == "WARN" and overall_gate == "PASS":
            overall_gate = "WARN"

        profiles.append(state.to_dict())
        diagnoses.append(
            {
                "function_id": diag.function_id,
                "overall_survival_rate": diag.overall_survival_rate,
                "surviving_categories": list(diag.surviving_categories),
                "gate_status": diag.gate_status,
            }
        )
        for p in diag.prescriptions:
            all_prescriptions.append(dataclasses.asdict(p))
        for action in diag.next_actions:
            if action.tool not in seen_tools:
                seen_tools.add(action.tool)
                all_next_actions.append(action)

    # Deduplicate prescriptions to avoid overwhelming UX
    seen_prescriptions: set[tuple[str, ...]] = set()
    unique_prescriptions: list[dict[str, Any]] = []
    for p in all_prescriptions:
        key = (p["category"], p["reason"], p["suggested_action"])
        if key not in seen_prescriptions:
            seen_prescriptions.add(key)
            unique_prescriptions.append(p)

    return {
        "schema_version": 2,
        "profiles": profiles,
        "diagnoses": diagnoses,
        "prescriptions": unique_prescriptions,
        "gate_status": overall_gate,
        "next_actions": serialize_next_actions(all_next_actions),
    }


def _impl_decompose(
    engine: Any,
    project_root: str,
    file: str | None,
    threshold: float,
) -> dict[str, Any]:
    """Find entangled functions needing structural decomposition."""
    import dataclasses

    from lintgate.mutation.decomposition import DecompositionCoordinator

    coordinator = DecompositionCoordinator(
        engine.state_manager,
        project_root,
        threshold=threshold,
    )
    candidates = coordinator.get_candidates(file_path=file, mode="auto")

    already_tractable = []
    for state in engine.state_manager.state.values():
        if file and not state.file_path.endswith(file):
            continue
        if state.total > 0 and state.survival_rate < threshold:
            already_tractable.append(f"{state.file_path}::{state.function_name}")

    return {
        "schema_version": 3,
        "decomposition_candidates": [dataclasses.asdict(c) for c in candidates],
        "already_tractable": already_tractable,
        "summary": (
            f"Found {len(candidates)} candidates "
            f"({sum(1 for c in candidates if c.source == 'dynamic')} dynamic, "
            f"{sum(1 for c in candidates if c.source == 'static')} static, "
            f"{sum(1 for c in candidates if c.source == 'converged')} converged)."
        ),
    }


def _find_before_state(engine: Any, file: str, function: str | None) -> Any:
    """Find the before-state for a function in the mutation state manager."""
    for _key, state in engine.state_manager.state.items():
        if state.file_path.endswith(file) and (
            not function or state.function_name == function
        ):
            return state
    return None


def _reprofile_function(
    engine: Any, project_root: str, file: str, function: str | None
) -> dict[str, Any] | None:
    """Re-profile a function via inline sampling and return the after-state dict."""
    import os

    from lintgate.mutation.policy import MutationTelemetry

    telemetry = MutationTelemetry("refactor_loop")
    abs_file = os.path.join(project_root, file)
    if not os.path.exists(abs_file):
        abs_file = file
    results = engine.run_inline_sampling(
        [abs_file], telemetry, project_root=project_root
    )

    for state in results:
        if not function or state.function_name == function:
            return state.to_dict()
    return None


def _compute_refactor_delta(
    before_dict: dict[str, Any] | None,
    after_dict: dict[str, Any] | None,
) -> dict[str, float | int]:
    """Compute survival rate delta between before and after profiles."""
    if not before_dict or not after_dict:
        return {}
    before_rate = _profile_survival_rate(before_dict)
    after_rate = _profile_survival_rate(after_dict)
    return {
        "survival_rate_change": after_rate - before_rate,
        "mutants_survived_change": after_dict["survived"] - before_dict["survived"],
    }


def _generate_after_prescriptions(
    engine: Any, file: str, function: str | None
) -> list[dict[str, Any]]:
    """Generate prescriptions for the after-state of a refactor loop."""
    import dataclasses

    from lintgate.mutation.prescriptions import PrescriptionEngine

    p_engine = PrescriptionEngine()
    for state in engine.state_manager.state.values():
        if state.file_path.endswith(file) and (
            not function or state.function_name == function
        ):
            diag = p_engine.diagnose(state)
            return [dataclasses.asdict(p) for p in diag.prescriptions]
    return []


def _build_refactor_suggestion(
    delta: dict[str, float | int],
    after_dict: dict[str, Any] | None,
) -> str | None:
    """Build auto-verify suggestion for refactor loop (#210)."""
    if not delta or not after_dict:
        return None
    rate_change = delta.get("survival_rate_change", 0)
    after_rate = _profile_survival_rate(after_dict)
    if rate_change < 0 and after_rate > 0.2:
        return (
            f"Survival improved by {abs(rate_change):.0%} but is still "
            f"{after_rate:.0%}. Run mutation_prescribe for remaining "
            f"survivor categories."
        )
    if rate_change < 0 and after_rate <= 0.2:
        return (
            f"Survival reduced to {after_rate:.0%} — within acceptable "
            f"bounds. Specification is strong."
        )
    return None


def _impl_refactor_loop(
    engine: Any,
    project_root: str,
    file: str,
    function: str | None,
    test_skeleton_intent: str | None,
    reprofile: bool,
) -> dict[str, Any]:
    """Close the refactor loop: re-profile, compute delta, generate prescriptions."""
    before_state = _find_before_state(engine, file, function)
    before_dict = before_state.to_dict() if before_state else None

    if reprofile:
        after_dict = _reprofile_function(engine, project_root, file, function)
    else:
        after_dict = before_dict

    delta = _compute_refactor_delta(before_dict, after_dict)

    prescriptions: list[dict[str, Any]] = []
    if after_dict:
        prescriptions = _generate_after_prescriptions(engine, file, function)

    suggestion = _build_refactor_suggestion(delta, after_dict)

    return {
        "test_skeleton_intent": test_skeleton_intent,
        "before_profile": before_dict,
        "after_profile": after_dict,
        "delta": delta,
        "prescriptions": prescriptions,
        "suggestion": suggestion,
    }


# ---------------------------------------------------------------------------
# MCP registration — thin wrappers delegating to impl functions above
# ---------------------------------------------------------------------------


def register(mcp: Any, helpers: Any) -> dict[str, Any]:
    """Register mutation testing tools on the shared MCP instance."""

    @mcp.tool()
    def mutation_run_sampling(path: str, files: list[str] | None = None) -> str:
        """Run a fast, sampled mutation run to identify hotspots.

        WHEN TO USE: After making changes to specific files, run this to get quick
        feedback on whether your new tests are killing mutants. This is Tier 1
        sampling (low budget, fast).

        Example: mutation_run_sampling(path="/my/project", files=["src/utils.py"])

        Args:
            path: Project root path.
            files: Optional list of specific files to mutate. If empty, uses recent changes.
        """
        project_root = helpers["_validate_project_root"](path)
        engine = _get_engine(project_root)
        result = _impl_run_sampling(engine, files, project_root=project_root)
        if result is None:
            return json.dumps({"error": "Please provide specific files for sampling"})
        return helpers["_json_dumps"](result)

    @mcp.tool()
    def mutation_run_full(path: str, files: list[str] | None = None) -> str:
        """Run a deep, background mutation profiling run with test-impact gating.

        WHEN TO USE: To deeply verify the test quality of a component. This is Tier 2
        profiling (exhaustive, slower, uses test-mapping to optimize).

        Example: mutation_run_full(path="/my/project", files=["src/core.py"])

        Args:
            path: Project root path.
            files: Optional list of files to profile. If empty, profiles the project.
        """
        project_root = helpers["_validate_project_root"](path)
        engine = _get_engine(project_root)
        result = _impl_run_full(engine, project_root, files)
        return helpers["_json_dumps"](result)

    @mcp.tool()
    def mutation_get_state(path: str, file: str | None = None) -> str:
        """Retrieve the current persistent mutation state and metrics.

        WHEN TO USE: To check the status of previous mutation runs, identify
        uncovered functions (high survival), or see which functions require
        profiling.

        Example: mutation_get_state(path="/my/project")

        Args:
            path: Project root path.
            file: Optional specific file to filter results for.
        """
        project_root = helpers["_validate_project_root"](path)
        engine = _get_engine(project_root)
        result = _impl_get_state(engine, file)
        return helpers["_json_dumps"](result)

    @mcp.tool()
    def mutation_clear_state(path: str, files: list[str] | None = None) -> str:
        """Clear persistent mutation state for specific files or the entire project.

        WHEN TO USE: When code has drifted significantly or you want to force re-runs
        from scratch.

        Args:
            path: Project root path.
            files: Optional list of files to clear. If empty, clears all state.
        """
        project_root = helpers["_validate_project_root"](path)
        engine = _get_engine(project_root)
        result = _impl_clear_state(engine, files)
        return json.dumps(result)

    @mcp.tool()
    def mutation_prescribe(
        path: str,
        file: str | None = None,
        function: str | None = None,
        min_depth: str = "sampled",
    ) -> str:
        """Get deterministically mapped prescriptions and next-actions from mutation profiles.

        Outputs actionable next steps (like adding a test or decomposing a function)
        based on which mutation categories survived.

        Args:
            path: Project root path.
            file: Optional specific file to filter results for.
            function: Optional specific function name (e.g., 'foo') to filter for.
            min_depth: Minimum CoverageDepth ('sampled' or 'profiled') required.
        """
        project_root = helpers["_validate_project_root"](path)
        engine = _get_engine(project_root)
        result = _impl_prescribe(engine, file, function, min_depth)
        return helpers["_json_dumps"](result)

    @mcp.tool()
    def mutation_decompose(
        path: str,
        file: str | None = None,
        threshold: float = 0.50,
    ) -> str:
        """Find functions that are highly entangled and require structural decomposition.

        Args:
            path: Project root path.
            file: Optional specific file to filter results for.
            threshold: Minimum survival rate across multiple categories to trigger decomposition (default: 0.50).
        """
        project_root = helpers["_validate_project_root"](path)
        engine = _get_engine(project_root)
        result = _impl_decompose(engine, project_root, file, threshold)
        return helpers["_json_dumps"](result)

    @mcp.tool()
    def mutation_refactor_loop(
        path: str,
        file: str,
        function: str | None = None,
        test_skeleton_intent: str | None = None,
        reprofile: bool = True,
    ) -> str:
        """Close the loop by re-profiling a function and yielding the delta in survival rate.

        This tool can be used to coordinate targeted test enhancements. The agent
        receives a specific prescription, applies test changes (e.g. by generating tests),
        and then calls this tool to measure exact improvement.

        Args:
            path: Project root path.
            file: Specific file that was changed.
            function: Optional specific function that was changed.
            test_skeleton_intent: Optional string documenting what was improved.
            reprofile: Whether to re-run mutmut to get the new state.
        """
        project_root = helpers["_validate_project_root"](path)
        engine = _get_engine(project_root)
        result = _impl_refactor_loop(
            engine, project_root, file, function, test_skeleton_intent, reprofile
        )
        return helpers["_json_dumps"](result)

    return {
        "mutation_run_sampling": mutation_run_sampling,
        "mutation_run_full": mutation_run_full,
        "mutation_get_state": mutation_get_state,
        "mutation_clear_state": mutation_clear_state,
        "mutation_prescribe": mutation_prescribe,
        "mutation_decompose": mutation_decompose,
        "mutation_refactor_loop": mutation_refactor_loop,
    }
