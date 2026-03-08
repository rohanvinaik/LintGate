"""Mutation tools — AST mutation engine MCP surface.

Provides mutation_run_sampling, mutation_run_full, mutation_get_state,
mutation_prescribe, mutation_decompose, mutation_refactor_loop,
mutation_prescribe_tests, mutation_validate_tests, mutation_clear_state.
"""

from __future__ import annotations

import os
from typing import Any

from lintgate.next_action import NextAction, serialize_next_actions

from ._mutation_impl import (
    MutationContext,
    detect_purity,
    detect_purity_map,
    discover_test_files,
    generate_test_skeleton,
    get_cache_dir,
    iter_cached_states,
    load_cached_state,
    load_test_callables,
    prescription_for_category,
    resolve_function,
    run_on_functions_with_tests,
    run_post_profiling_analysis,
)

# ── Tool implementation functions ─────────────────────────────────


def _impl_run_sampling(
    helpers: Any, path: str, file: str, function: str | None, budget_ms: float
) -> str:
    from lintgate.keys import canonical_function_key
    from lintgate.specification.mutation_engine import run_function_sampling
    from lintgate.specification.mutation_filter import filter_categories

    project_root = helpers["_validate_project_root"](path)
    full, func_node, err = resolve_function(project_root, file, function)
    if err and function:
        return helpers["_json_dumps"]({"error": err})

    ctx = MutationContext(
        full_path=full,
        rel_path=os.path.relpath(full, project_root),
        cache_dir=get_cache_dir(project_root),
        purity_map=detect_purity_map(full),
        test_files=discover_test_files(project_root, full),
    )
    results = run_on_functions_with_tests(
        ctx,
        func_node,
        function,
        lambda node, key, cats, tests, orig: run_function_sampling(
            node,
            key,
            cats,
            tests,
            orig,
            budget_ms=budget_ms,
        ),
        filter_categories,
        canonical_function_key,
    )
    if isinstance(results, str):
        return results

    next_actions = [
        NextAction(
            tool="mutation_run_full",
            args={"path": path, "file": file},
            reason="Run exhaustive profiling for deeper analysis",
        )
    ]
    return helpers["_json_dumps"](
        {
            "file": file,
            "functions_sampled": len(results),
            "tests_discovered": len(ctx.test_files),
            "results": results,
            "next_actions": serialize_next_actions(next_actions),
        },
        output_mode="compact",
    )


def _impl_run_full(helpers: Any, path: str, file: str, function: str | None) -> str:
    from lintgate.keys import canonical_function_key
    from lintgate.specification.mutation_engine import run_function_profiling
    from lintgate.specification.mutation_filter import filter_categories

    project_root = helpers["_validate_project_root"](path)
    full, func_node, err = resolve_function(project_root, file, function)
    if err and function:
        return helpers["_json_dumps"]({"error": err})

    ctx = MutationContext(
        full_path=full,
        rel_path=os.path.relpath(full, project_root),
        cache_dir=get_cache_dir(project_root),
        purity_map=detect_purity_map(full),
        test_files=discover_test_files(project_root, full),
    )
    results = run_on_functions_with_tests(
        ctx,
        func_node,
        function,
        lambda node, key, cats, tests, orig: run_function_profiling(
            node,
            key,
            cats,
            tests,
            orig,
        ),
        filter_categories,
        canonical_function_key,
    )
    if isinstance(results, str):
        return results

    analysis = run_post_profiling_analysis(results, ctx.purity_map)

    next_actions = [
        NextAction(
            tool="mutation_prescribe",
            args={"path": path, "file": file},
            reason="Get prescriptions from mutation profiles",
        ),
        NextAction(
            tool="mutation_get_state",
            args={"path": path, "file": file},
            reason="View current mutation state",
        ),
    ]
    return helpers["_json_dumps"](
        {
            "file": file,
            "functions_profiled": len(results),
            "tests_discovered": len(ctx.test_files),
            "results": results,
            "analysis": analysis,
            "next_actions": serialize_next_actions(next_actions),
        },
        output_mode="compact",
    )


def _impl_get_state(helpers: Any, path: str, file: str | None, function: str | None) -> str:
    project_root = helpers["_validate_project_root"](path)
    cache_dir = get_cache_dir(project_root)
    if not cache_dir.exists():
        return helpers["_json_dumps"](
            {
                "note": "No mutation data yet",
                "next_actions": serialize_next_actions(
                    [
                        NextAction(
                            tool="mutation_run_sampling",
                            args={"path": path, "file": file or "<file>"},
                            reason="Run sampling first",
                        ),
                    ]
                ),
            }
        )
    states = iter_cached_states(cache_dir, file, function)
    return helpers["_json_dumps"](
        {"total_functions": len(states), "states": states}, output_mode="compact"
    )


def _impl_prescribe(helpers: Any, path: str, file: str | None, function: str | None) -> str:
    project_root = helpers["_validate_project_root"](path)
    states = iter_cached_states(get_cache_dir(project_root), file, function)
    if not states:
        return helpers["_json_dumps"]({"note": "No mutation data — run sampling first"})

    prescriptions: list[dict] = []
    for data in states:
        func_key = data.get("function_key", "")
        for cat_data in data.get("per_category", []):
            if cat_data.get("survived", 0) > 0:
                prescriptions.append(
                    {
                        "function": func_key,
                        "category": cat_data["category"],
                        "survived": cat_data["survived"],
                        "survival_rate": cat_data.get("survival_rate", 0),
                        "action": prescription_for_category(cat_data["category"]),
                    }
                )
    next_actions = []
    if prescriptions:
        args: dict[str, str] = {"path": path}
        if file:
            args["file"] = file
        next_actions = [
            NextAction(
                tool="mutation_prescribe_tests",
                args=args,
                reason="Generate test skeletons for surviving categories",
            ),
        ]
    return helpers["_json_dumps"](
        {
            "total_prescriptions": len(prescriptions),
            "prescriptions": prescriptions,
            "next_actions": serialize_next_actions(next_actions),
        },
        output_mode="compact",
    )


def _impl_decompose(helpers: Any, path: str, file: str, function: str | None, mode: str) -> str:
    project_root = helpers["_validate_project_root"](path)
    states = iter_cached_states(get_cache_dir(project_root), file, function)

    candidates: list[dict] = []
    for data in states:
        surviving_cats = [
            c["category"] for c in data.get("per_category", []) if c.get("survived", 0) > 0
        ]
        if len(surviving_cats) >= 2:
            candidates.append(
                {
                    "function": data.get("function_key", ""),
                    "surviving_categories": surviving_cats,
                    "recommendation": "Consider decomposition — multiple surviving categories",
                }
            )
    return helpers["_json_dumps"]({"mode": mode, "candidates": candidates}, output_mode="compact")


def _impl_refactor_loop(helpers: Any, path: str, file: str, function: str | None) -> str:
    from lintgate.keys import canonical_function_key
    from lintgate.specification.mutation_engine import run_function_profiling
    from lintgate.specification.mutation_filter import filter_categories

    project_root = helpers["_validate_project_root"](path)
    full, func_node, err = resolve_function(project_root, file, function)
    if err:
        return helpers["_json_dumps"]({"error": err})

    results: list[dict] = []
    if func_node and function:
        rel_path = os.path.relpath(full, project_root)
        func_key = canonical_function_key(rel_path, function)
        prev = load_cached_state(get_cache_dir(project_root), func_key)
        prev_survival = prev.get("survival_rate", 1.0) if prev else None

        is_pure = detect_purity(full, function)
        cats = filter_categories(func_node, is_pure=is_pure)
        test_files = discover_test_files(project_root, full)
        tests = load_test_callables(test_files, function)
        pr = run_function_profiling(func_node, func_key, cats, tests, lambda *a: None)
        result_dict = pr.to_dict()
        result_dict["tests_loaded"] = len(tests)
        result_dict["is_pure"] = is_pure
        if prev_survival is not None:
            result_dict["previous_survival_rate"] = prev_survival
            result_dict["survival_delta"] = round(pr.survival_rate - prev_survival, 3)
        results.append(result_dict)
        get_cache_dir(project_root).mkdir(parents=True, exist_ok=True)

    next_actions = [
        NextAction(
            tool="spec_gate_check",
            args={"path": path},
            reason="Check if specification level now meets optimization hint thresholds",
        ),
    ]
    return helpers["_json_dumps"](
        {
            "file": file,
            "results": results,
            "next_actions": serialize_next_actions(next_actions),
        },
        output_mode="compact",
    )


def _impl_prescribe_tests(helpers: Any, path: str, file: str, function: str | None) -> str:
    project_root = helpers["_validate_project_root"](path)
    states = iter_cached_states(get_cache_dir(project_root), file, function)

    skeletons: list[dict] = []
    for data in states:
        func_key = data.get("function_key", "")
        for cat_data in data.get("per_category", []):
            if cat_data.get("survived", 0) > 0:
                skeletons.append(generate_test_skeleton(func_key, cat_data["category"]))
    next_actions = []
    if skeletons:
        args: dict[str, str] = {"path": path}
        if file:
            args["file"] = file
        next_actions = [
            NextAction(
                tool="mutation_validate_tests",
                args=args,
                reason="After writing tests, validate they kill targeted mutants",
                condition="after implementing the test skeletons",
            ),
        ]
    return helpers["_json_dumps"](
        {
            "skeletons": skeletons,
            "next_actions": serialize_next_actions(next_actions),
        },
        output_mode="compact",
    )


def _impl_clear_state(helpers: Any, path: str, file: str | None) -> str:
    import json

    project_root = helpers["_validate_project_root"](path)
    cache_dir = get_cache_dir(project_root)
    if not cache_dir.exists():
        return helpers["_json_dumps"]({"note": "No mutation state to clear"})

    cleared = 0
    for cache_file in list(cache_dir.glob("*.json")):
        if cache_file.name == "scheduler_state.json":
            continue
        if file:
            try:
                with open(cache_file, encoding="utf-8") as f:
                    data = json.load(f)
                if file not in data.get("function_key", ""):
                    continue
            except (OSError, json.JSONDecodeError):
                pass
        cache_file.unlink(missing_ok=True)
        cleared += 1
    return helpers["_json_dumps"]({"cleared": cleared})


# ── MCP Registration ──────────────────────────────────────────────


def register(mcp: Any, helpers: Any) -> dict[str, Any]:
    """Register mutation analysis tools on the shared MCP instance."""

    @mcp.tool()
    def mutation_run_sampling(
        path: str, file: str, function: str | None = None, budget_ms: float = 500
    ) -> str:
        """Fast sampled mutation run — inline AST mutation sampling.

        WHEN TO USE: After editing specific files. Generates ≤3 mutants per
        semantic category (VALUE, SWAP, STATE, BOUNDARY, TYPE), evaluates
        within time budget. Returns per-category kill/survive counts.

        Args:
            path: Project root path.
            file: Relative path to the Python file.
            function: Optional specific function name.
            budget_ms: Time budget in milliseconds (default 500).
        """
        return _impl_run_sampling(helpers, path, file, function, budget_ms)

    @mcp.tool()
    def mutation_run_full(path: str, file: str, function: str | None = None) -> str:
        """Deep exhaustive mutation profiling (Tier 2).

        WHEN TO USE: To verify test quality of a component. Generates all
        possible mutants, evaluates exhaustively. Slower but produces
        gateable results with full kill matrix.

        Args:
            path: Project root path.
            file: Relative path to the Python file.
            function: Optional specific function name.
        """
        return _impl_run_full(helpers, path, file, function)

    @mcp.tool()
    def mutation_get_state(path: str, file: str | None = None, function: str | None = None) -> str:
        """Current mutation state and metrics.

        WHEN TO USE: To review previous mutation runs. Shows cached
        sampling/profiling results, survival rates, and coverage depth.

        Args:
            path: Project root path.
            file: Optional file to filter by.
            function: Optional function name to filter by.
        """
        return _impl_get_state(helpers, path, file, function)

    @mcp.tool()
    def mutation_prescribe(path: str, file: str | None = None, function: str | None = None) -> str:
        """Deterministic prescriptions from mutation profiles.

        WHEN TO USE: After a mutation run. Analyzes survival profiles and
        recommends specific test improvements per surviving category.

        Args:
            path: Project root path.
            file: Optional file filter.
            function: Optional function filter.
        """
        return _impl_prescribe(helpers, path, file, function)

    @mcp.tool()
    def mutation_decompose(
        path: str, file: str = "", function: str | None = None, mode: str = "auto"
    ) -> str:
        """Find entangled functions from mutation data.

        WHEN TO USE: For refactoring decisions. Identifies functions where
        multiple mutation categories survive, suggesting the function has
        too many responsibilities.

        Args:
            path: Project root path.
            file: File to analyze.
            function: Optional function name.
            mode: Detection mode: auto, static, or dynamic.
        """
        return _impl_decompose(helpers, path, file, function, mode)

    @mcp.tool()
    def mutation_refactor_loop(path: str, file: str = "", function: str | None = None) -> str:
        """Re-profile after test improvement — close the feedback loop.

        WHEN TO USE: After writing prescribed tests. Re-runs profiling
        and computes survival rate delta.

        Args:
            path: Project root path.
            file: File to re-profile.
            function: Optional function name.
        """
        return _impl_refactor_loop(helpers, path, file, function)

    @mcp.tool()
    def mutation_prescribe_tests(path: str, file: str = "", function: str | None = None) -> str:
        """Generate targeted test skeletons from mutation profiles.

        WHEN TO USE: After mutation_prescribe identifies surviving categories.
        Generates pytest test function templates targeting specific categories.

        Args:
            path: Project root path.
            file: Source file.
            function: Optional function name.
        """
        return _impl_prescribe_tests(helpers, path, file, function)

    @mcp.tool()
    def mutation_validate_tests(path: str, file: str = "", function: str | None = None) -> str:
        """Re-profile and compute per-category survival deltas.

        WHEN TO USE: After writing prescribed tests. Validates that new
        tests actually killed the surviving mutants they targeted.

        Args:
            path: Project root path.
            file: Source file.
            function: Optional function name.
        """
        return _impl_refactor_loop(helpers, path, file, function)

    @mcp.tool()
    def mutation_clear_state(path: str, file: str | None = None) -> str:
        """Clear mutation state — use when code has drifted significantly.

        WHEN TO USE: When source code has changed substantially and cached
        mutation data is stale.

        Args:
            path: Project root path.
            file: Optional file to clear (clears all if not specified).
        """
        return _impl_clear_state(helpers, path, file)

    return {
        "mutation_run_sampling": mutation_run_sampling,
        "mutation_run_full": mutation_run_full,
        "mutation_get_state": mutation_get_state,
        "mutation_prescribe": mutation_prescribe,
        "mutation_decompose": mutation_decompose,
        "mutation_refactor_loop": mutation_refactor_loop,
        "mutation_prescribe_tests": mutation_prescribe_tests,
        "mutation_validate_tests": mutation_validate_tests,
        "mutation_clear_state": mutation_clear_state,
    }
