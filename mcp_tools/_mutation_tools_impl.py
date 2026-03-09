"""Mutation tool implementations — extracted from mutation_tools.py for file-size compliance.

All _impl_* functions live here. The public API remains mutation_tools.register().
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
    parse_file,
    prescription_for_category,
    resolve_function,
    run_on_functions_with_tests,
    run_post_profiling_analysis,
    save_cached_state,
    walk_functions,
)


def _build_mutation_context(project_root: str, full: str) -> MutationContext:
    """Build a MutationContext from resolved paths."""
    return MutationContext(
        full_path=full,
        rel_path=os.path.relpath(full, project_root),
        cache_dir=get_cache_dir(project_root),
        purity_map=detect_purity_map(full),
        test_files=discover_test_files(project_root, full),
    )


def impl_run_sampling(
    helpers: Any, path: str, file: str, function: str | None, budget_ms: float
) -> str:
    from lintgate.keys import canonical_function_key
    from lintgate.specification.mutation_engine import run_function_sampling
    from lintgate.specification.mutation_filter import filter_categories

    project_root = helpers["_validate_project_root"](path)
    full, func_node, err = resolve_function(project_root, file, function)
    if err and function:
        return str(helpers["_json_dumps"]({"error": err}))

    ctx = _build_mutation_context(project_root, full)
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
    return str(
        helpers["_json_dumps"](
            {
                "file": file,
                "functions_sampled": len(results),
                "tests_discovered": len(ctx.test_files),
                "results": results,
                "next_actions": serialize_next_actions(next_actions),
            },
            output_mode="compact",
        )
    )


def impl_run_full(helpers: Any, path: str, file: str, function: str | None) -> str:
    from lintgate.keys import canonical_function_key
    from lintgate.specification.mutation_engine import run_function_profiling
    from lintgate.specification.mutation_filter import filter_categories

    project_root = helpers["_validate_project_root"](path)
    full, func_node, err = resolve_function(project_root, file, function)
    if err and function:
        return str(helpers["_json_dumps"]({"error": err}))

    ctx = _build_mutation_context(project_root, full)
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
    return str(
        helpers["_json_dumps"](
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
    )


def impl_get_state(helpers: Any, path: str, file: str | None, function: str | None) -> str:
    project_root = helpers["_validate_project_root"](path)
    cache_dir = get_cache_dir(project_root)
    if not cache_dir.exists():
        return str(
            helpers["_json_dumps"](
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
        )
    states = iter_cached_states(cache_dir, file, function)
    return str(
        helpers["_json_dumps"](
            {"total_functions": len(states), "states": states}, output_mode="compact"
        )
    )


def impl_prescribe(helpers: Any, path: str, file: str | None, function: str | None) -> str:
    project_root = helpers["_validate_project_root"](path)
    states = iter_cached_states(get_cache_dir(project_root), file, function)
    if not states:
        return str(helpers["_json_dumps"]({"note": "No mutation data — run sampling first"}))

    prescriptions = _collect_prescriptions(states)
    next_actions: list[NextAction] = []
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
    return str(
        helpers["_json_dumps"](
            {
                "total_prescriptions": len(prescriptions),
                "prescriptions": prescriptions,
                "next_actions": serialize_next_actions(next_actions),
            },
            output_mode="compact",
        )
    )


def _collect_prescriptions(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract prescriptions from cached mutation states."""
    prescriptions: list[dict[str, Any]] = []
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
    return prescriptions


def impl_decompose(helpers: Any, path: str, file: str, function: str | None, mode: str) -> str:
    project_root = helpers["_validate_project_root"](path)
    states = iter_cached_states(get_cache_dir(project_root), file, function)

    candidates: list[dict[str, Any]] = []
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
    return str(
        helpers["_json_dumps"]({"mode": mode, "candidates": candidates}, output_mode="compact")
    )


def impl_refactor_loop(helpers: Any, path: str, file: str, function: str | None) -> str:
    project_root = helpers["_validate_project_root"](path)
    full, func_node, err = resolve_function(project_root, file, function)
    if err:
        return str(helpers["_json_dumps"]({"error": err}))

    cache_dir = get_cache_dir(project_root)
    targets = _resolve_refactor_targets(full, func_node, function)
    if isinstance(targets, str):
        return str(helpers["_json_dumps"]({"error": targets}))

    rel_path = os.path.relpath(full, project_root)
    test_files = discover_test_files(project_root, full)
    results = _profile_targets(targets, full, rel_path, cache_dir, test_files)

    next_actions = [
        NextAction(
            tool="spec_gate_check",
            args={"path": path},
            reason="Check if specification level now meets optimization hint thresholds",
        ),
    ]
    return str(
        helpers["_json_dumps"](
            {
                "file": file,
                "results": results,
                "next_actions": serialize_next_actions(next_actions),
            },
            output_mode="compact",
        )
    )


def _resolve_refactor_targets(
    full: str,
    func_node: Any,
    function: str | None,
) -> list[tuple[str, Any]] | str:
    """Resolve function targets for refactor loop. Returns error string on failure."""
    if func_node and function:
        return [(function, func_node)]
    tree = parse_file(full)
    if tree is None:
        return f"Parse error: {full}"
    return walk_functions(tree)


def _profile_targets(
    targets: list[tuple[str, Any]],
    full_path: str,
    rel_path: str,
    cache_dir: Any,
    test_files: list[str],
) -> list[dict[str, Any]]:
    """Profile each target function and compute survival deltas."""
    from lintgate.keys import canonical_function_key
    from lintgate.specification.mutation_engine import run_function_profiling
    from lintgate.specification.mutation_filter import filter_categories

    results: list[dict[str, Any]] = []
    for qualname, node in targets:
        func_key = canonical_function_key(rel_path, qualname)
        prev = load_cached_state(cache_dir, func_key)
        prev_survival = prev.get("survival_rate", 1.0) if prev else None

        is_pure = detect_purity(full_path, qualname)
        cats = filter_categories(node, is_pure=is_pure)
        bare_name = qualname.split(".")[-1]
        tests = load_test_callables(test_files, bare_name)
        pr = run_function_profiling(node, func_key, cats, tests, lambda *a: None)
        result_dict: dict[str, Any] = pr.to_dict()
        result_dict["tests_loaded"] = len(tests)
        result_dict["is_pure"] = is_pure
        if prev_survival is not None:
            result_dict["previous_survival_rate"] = prev_survival
            result_dict["survival_delta"] = round(pr.survival_rate - prev_survival, 3)
        results.append(result_dict)
        save_cached_state(cache_dir, func_key, result_dict)
    return results


def impl_prescribe_tests(helpers: Any, path: str, file: str, function: str | None) -> str:
    project_root = helpers["_validate_project_root"](path)
    states = iter_cached_states(get_cache_dir(project_root), file, function)

    skeletons: list[dict[str, Any]] = []
    for data in states:
        func_key = data.get("function_key", "")
        for cat_data in data.get("per_category", []):
            if cat_data.get("survived", 0) > 0:
                skeletons.append(generate_test_skeleton(func_key, cat_data["category"]))
    next_actions: list[NextAction] = []
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
    return str(
        helpers["_json_dumps"](
            {
                "skeletons": skeletons,
                "next_actions": serialize_next_actions(next_actions),
            },
            output_mode="compact",
        )
    )


def impl_clear_state(helpers: Any, path: str, file: str | None) -> str:
    import json

    project_root = helpers["_validate_project_root"](path)
    cache_dir = get_cache_dir(project_root)
    if not cache_dir.exists():
        return str(helpers["_json_dumps"]({"note": "No mutation state to clear"}))

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
    return str(helpers["_json_dumps"]({"cleared": cleared}))
