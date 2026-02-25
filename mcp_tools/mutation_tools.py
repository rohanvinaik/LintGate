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
    from lintgate.mutation.engine import MutationEngine
    from lintgate.mutation.policy import RuntimeBudget
    from lintgate.mutation.state import MutationStateManager
    from lintgate.state import get_mutation_state_path

    state_path = get_mutation_state_path()
    state_manager = MutationStateManager(state_path)
    with contextlib.suppress(OSError, ValueError):
        state_manager.load()

    budget = RuntimeBudget()
    return MutationEngine(state_manager, budget)


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
        from lintgate.mutation.policy import MutationTelemetry

        project_root = helpers["_validate_project_root"](path)
        engine = _get_engine(project_root)

        if not files:
            # In a real impl, we'd discover changed files via git
            return json.dumps({"error": "Please provide specific files for sampling"})

        telemetry = MutationTelemetry("sampling_run")
        results = engine.run_inline_sampling(files, telemetry)

        output = {
            "run_id": telemetry.run_id,
            "functions_profiled": len(results),
            "results": [r.to_dict() for r in results],
            "telemetry": telemetry.to_bucket_dict(),
        }
        return helpers["_json_dumps"](output)

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
        from lintgate.mutation.policy import MutationTelemetry

        project_root = helpers["_validate_project_root"](path)
        engine = _get_engine(project_root)

        if not files:
            # Discover all files if none provided
            from lintgate.channels.performance_channel import _discover_python_files

            files = _discover_python_files(project_root)

        # In a real impl, we'd load the real test mapping
        test_mapping = {}

        telemetry = MutationTelemetry("full_profiling_run")
        results = engine.run_background_profiling(files, test_mapping, telemetry)

        output = {
            "run_id": telemetry.run_id,
            "functions_profiled": len(results),
            "results": [r.to_dict() for r in results],
            "telemetry": telemetry.to_bucket_dict(),
        }
        return helpers["_json_dumps"](output)

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

        all_states = engine.state_manager.state
        if file:
            all_states = {k: v for k, v in all_states.items() if k.startswith(file)}

        # Group by file for readability
        by_file = {}
        for _key, state in all_states.items():
            f_path = state.file_path
            by_file.setdefault(f_path, []).append(state.to_dict())

        return helpers["_json_dumps"]({"files": by_file})

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

        if not files:
            engine.state_manager.state = {}
            msg = "Cleared all mutation state"
        else:
            for f in files:
                # Remove all states for this file
                keys_to_remove = [k for k in engine.state_manager.state if k.startswith(f)]
                for k in keys_to_remove:
                    del engine.state_manager.state[k]
            msg = f"Cleared state for {len(files)} files"

        engine.state_manager.save()
        return json.dumps({"message": msg})

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
        import dataclasses

        from lintgate.mutation.prescriptions import PrescriptionEngine, get_test_skeleton_hints
        from lintgate.mutation.state import CoverageDepth

        project_root = helpers["_validate_project_root"](path)
        engine = _get_engine(project_root)

        p_engine = PrescriptionEngine()

        # Determine actual depth enum
        min_depth_enum = (
            CoverageDepth.SAMPLED if min_depth.lower() == "sampled" else CoverageDepth.PROFILED
        )

        all_states = engine.state_manager.state
        filtered_states = []

        for _key, state in all_states.items():
            if file and not state.file_path.endswith(file):
                continue
            if function and state.function_name != function:
                continue
            if min_depth_enum == CoverageDepth.PROFILED and state.depth != CoverageDepth.PROFILED:
                continue
            filtered_states.append(state)

        profiles = []
        diagnoses = []
        all_prescriptions = []
        all_next_actions = set()
        all_surviving_categories: set[str] = set()
        needs_decomposition = False

        overall_gate = "PASS"

        for state in filtered_states:
            diag = p_engine.diagnose(state)

            if diag.gate_status == "FAIL":
                overall_gate = "FAIL"
            elif diag.gate_status == "WARN" and overall_gate == "PASS":
                overall_gate = "WARN"

            # Track surviving categories
            all_surviving_categories.update(diag.surviving_categories)

            # Check if decomposition prescription was generated
            for p in diag.prescriptions:
                if p.category.value == "decompose_function":
                    needs_decomposition = True

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

            all_next_actions.update(diag.next_actions)

        # Generate test skeleton hints from surviving categories
        # When decomposition is needed, also derive axes from states for per-axis hints
        decomposition_axes = None
        if needs_decomposition:
            # Collect all survivor sites across states for axis derivation
            all_survivor_sites = []
            for state in filtered_states:
                if hasattr(state, "survivor_sites") and state.survivor_sites:
                    all_survivor_sites.extend(state.survivor_sites)

            if all_survivor_sites:
                # Create decomposition plan to get axes
                from lintgate.mutation.decomposition import DecompositionDetector

                detector = DecompositionDetector(engine.state_manager)
                # Create a dummy function ID for axis detection
                func_id = "prescribe_decomposition"
                plan = detector.create_decomposition_plan(func_id, all_survivor_sites)
                if plan and plan.axes:
                    decomposition_axes = [a.to_dict() for a in plan.axes]

        test_skeleton_hints = get_test_skeleton_hints(
            list(all_surviving_categories),
            include_decomposition=needs_decomposition,
            decomposition_axes=decomposition_axes,
        )

        # Deduplicate prescriptions to avoid overwhelming UX
        seen_prescriptions = set()
        unique_prescriptions = []
        for p in all_prescriptions:
            key = (p["category"], p["reason"], p["suggested_action"])
            if key not in seen_prescriptions:
                seen_prescriptions.add(key)
                unique_prescriptions.append(p)

        output = {
            "schema_version": 2,
            "profiles": profiles,
            "diagnoses": diagnoses,
            "prescriptions": unique_prescriptions,
            "test_skeleton_hints": test_skeleton_hints,
            "gate_status": overall_gate,
            "next_actions": list(all_next_actions),
        }
        return helpers["_json_dumps"](output)

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
        import dataclasses

        from lintgate.mutation.decomposition import DecompositionDetector

        project_root = helpers["_validate_project_root"](path)
        engine = _get_engine(project_root)

        detector = DecompositionDetector(engine.state_manager)
        if threshold is not None:
            detector.DECOMPOSITION_THRESHOLD = threshold

        candidates = detector.get_candidates(file_path=file)

        # We also return a summary of tractable functions for contrast
        already_tractable = []
        for state in engine.state_manager.state.values():
            if file and not state.file_path.endswith(file):
                continue
            if state.total > 0 and state.survival_rate < detector.DECOMPOSITION_THRESHOLD:
                already_tractable.append(f"{state.file_path}::{state.function_name}")

        # Build decomposition plans with axes for each candidate that has survivor_sites
        decomposition_plans = []
        plan_available = False

        for candidate in candidates:
            func_id = candidate.function_id
            state = engine.state_manager.state.get(func_id)

            if state and hasattr(state, "survivor_sites") and state.survivor_sites:
                plan = detector.create_decomposition_plan(func_id, state.survivor_sites)
                if plan and plan.axes:
                    decomposition_plans.append(plan.to_dict())
                    plan_available = True

        output = {
            "schema_version": 2,
            "decomposition_candidates": [dataclasses.asdict(c) for c in candidates],
            "decomposition_plans": decomposition_plans,
            "plan_available": plan_available,
            "already_tractable": already_tractable,
            "summary": f"Found {len(candidates)} candidates requiring decomposition.",
        }
        return helpers["_json_dumps"](output)

    @mcp.tool()
    def mutation_refactor_loop(
        path: str,
        file: str,
        function: str | None = None,
        test_skeleton_intent: str | None = None,
        reprofile: bool = True,
        mode: str = "sampled",
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
            mode: Mutation mode for reprofile - 'sampled' (fast inline) or 'profiled' (full coverage).
        """
        import dataclasses

        from lintgate.mutation.policy import MutationTelemetry

        project_root = helpers["_validate_project_root"](path)
        engine = _get_engine(project_root)

        # 1. Capture before-state
        before_state = None
        for _key, state in engine.state_manager.state.items():
            if state.file_path.endswith(file) and (not function or state.function_name == function):
                before_state = state
                break

        before_dict = before_state.to_dict() if before_state else None

        # 2. Re-profile
        reprofile_error = None
        after_dict = None
        telemetry = MutationTelemetry("refactor_loop")
        if reprofile:
            import os

            abs_file = os.path.join(project_root, file)
            if not os.path.exists(abs_file):
                abs_file = file  # fallback

            # Route to appropriate method based on mode
            # Valid modes: 'sampled' (fast inline), 'profiled' (full coverage)
            if mode not in ("sampled", "profiled"):
                mode = "sampled"  # Default to sampled for invalid mode

            try:
                if mode == "sampled":
                    results = engine.run_inline_sampling([abs_file], telemetry)
                else:  # mode == "profiled"
                    # For profiled mode, we need test mapping (empty for refactor loop)
                    results = engine.run_background_profiling([abs_file], {}, telemetry)

                for state in results:
                    if not function or state.function_name == function:
                        after_dict = state.to_dict()
                        break
            except Exception as e:
                reprofile_error = {
                    "type": type(e).__name__,
                    "message": str(e),
                }
        else:
            after_dict = before_dict  # Fallback

        # 3. Compute Delta
        delta: dict[str, float | int | dict[str, int]] = {}
        if before_dict and after_dict:
            before_rate = _profile_survival_rate(before_dict)
            after_rate = _profile_survival_rate(after_dict)

            # Compute per-category deltas
            before_by_cat = before_dict.get("survived_by_category", {})
            after_by_cat = after_dict.get("survived_by_category", {})
            all_cats = set(before_by_cat.keys()) | set(after_by_cat.keys())
            category_deltas = {}
            for cat in all_cats:
                before_count = before_by_cat.get(cat, 0)
                after_count = after_by_cat.get(cat, 0)
                category_deltas[cat] = after_count - before_count

            delta = {
                "survival_rate_change": after_rate - before_rate,
                "mutants_survived_change": after_dict["survived"] - before_dict["survived"],
                "category_deltas": category_deltas,
            }
        else:
            # No before profile - return explicit empty-delta schema
            delta = {
                "survival_rate_change": 0.0,
                "mutants_survived_change": 0,
                "category_deltas": {},
            }

        # 4. Generate prescriptions for after-state if present
        prescriptions = []
        if after_dict:
            from lintgate.mutation.prescriptions import PrescriptionEngine

            p_engine = PrescriptionEngine()

            for state in engine.state_manager.state.values():
                if state.file_path.endswith(file) and (
                    not function or state.function_name == function
                ):
                    diag = p_engine.diagnose(state)
                    prescriptions = [dataclasses.asdict(p) for p in diag.prescriptions]
                    break

        output = {
            "test_skeleton_intent": test_skeleton_intent,
            "before_profile": before_dict,
            "after_profile": after_dict,
            "delta": delta,
            "prescriptions": prescriptions,
            "telemetry": telemetry.to_bucket_dict() if telemetry else {},
        }

        if reprofile_error:
            output["reprofile_error"] = reprofile_error

        return helpers["_json_dumps"](output)

    return {
        "mutation_run_sampling": mutation_run_sampling,
        "mutation_run_full": mutation_run_full,
        "mutation_get_state": mutation_get_state,
        "mutation_clear_state": mutation_clear_state,
        "mutation_prescribe": mutation_prescribe,
        "mutation_decompose": mutation_decompose,
        "mutation_refactor_loop": mutation_refactor_loop,
    }
