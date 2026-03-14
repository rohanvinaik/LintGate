"""Mutation/cache helpers for the platonic workflow."""

from __future__ import annotations

import json


def load_mutation_cache(
    project_root: str,
    rel_file: str,
) -> dict[str, dict] | None:
    """Load cached mutation state for a file."""
    try:
        from mcp_tools._mutation_impl import get_cache_dir, iter_cached_states

        cache_dir = get_cache_dir(project_root)
        cache: dict[str, dict] = {}
        for state in iter_cached_states(cache_dir, rel_file):
            key = state.get("function_key", "")
            if key:
                cache[key] = state
        return cache if cache else None
    except Exception:
        return None


def persist_mutation_cache_entries(
    project_root: str,
    mutation_cache: dict[str, dict] | None,
) -> None:
    """Persist the latest per-function mutation snapshot for downstream consumers."""
    if not mutation_cache:
        return
    try:
        from mcp_tools._mutation_impl import get_cache_dir, save_cached_state

        cache_dir = get_cache_dir(project_root)
        for func_key, entry in mutation_cache.items():
            if not func_key:
                continue
            payload = dict(entry)
            payload["function_key"] = func_key
            save_cached_state(cache_dir, func_key, payload)
    except Exception:
        return


def run_mutation_sampling(
    project_root: str,
    rel_file: str,
    budget_ms: float = 5000,
    generated_test_files: list[str] | None = None,
    max_per_category: int = 3,
    seed: int | None = None,
) -> list[dict]:
    """Run mutation sampling on a file, returning per-function results."""
    try:
        from lintgate.keys import canonical_function_key
        from lintgate.specification.mutation_engine import run_function_sampling
        from lintgate.specification.mutation_filter import filter_categories
        from mcp_tools._mutation_impl import resolve_function, run_on_functions_with_tests
        from mcp_tools._mutation_tools_impl import _build_mutation_context

        full, func_node, err = resolve_function(project_root, rel_file, None)
        if err and not full:
            return []

        ctx = _build_mutation_context(
            project_root,
            full,
            extra_test_files=generated_test_files,
        )
        per_func_budget = budget_ms
        _mpc = max_per_category
        _seed = seed

        results = run_on_functions_with_tests(
            ctx,
            func_node,
            None,
            lambda node, key, cats, tests, orig: run_function_sampling(
                node,
                key,
                cats,
                tests,
                orig,
                budget_ms=per_func_budget,
                max_per_category=_mpc,
                seed=_seed,
            ),
            filter_categories,
            canonical_function_key,
        )
        if isinstance(results, list):
            return results
        if isinstance(results, str):
            try:
                data = json.loads(results)
                if isinstance(data, list):
                    return data
            except Exception:
                return []
        return []
    except Exception:
        return []


def find_mutation_result(
    results: list[dict],
    func_key: str,
) -> dict | None:
    """Find the mutation result for a specific function."""
    for result in results:
        if result.get("function_key") == func_key:
            return result
    return None
