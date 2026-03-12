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

    # Split budget across functions to prevent one slow function from consuming all
    if func_node and function:
        per_func_budget = budget_ms
    else:
        tree = parse_file(full)
        num_funcs = len(walk_functions(tree)) if tree else 1
        per_func_budget = max(budget_ms / max(num_funcs, 1), 200)

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
            budget_ms=per_func_budget,
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
    output: dict[str, Any] = {
        "file": file,
        "functions_sampled": len(results),
        "tests_discovered": len(ctx.test_files),
        "results": results,
        "next_actions": serialize_next_actions(next_actions),
    }
    _add_truthfulness_warnings(results, output)
    return str(helpers["_json_dumps"](output, output_mode="compact"))


def _add_truthfulness_warnings(results: list[dict], output: dict[str, Any]) -> None:
    """Surface top-level warnings when survival is likely an artifact."""
    discovery_artifacts = [
        r
        for r in results
        if r.get("survival_interpretation") in ("DISCOVERY_ARTIFACT", "MOCK_BOUNDARY_ARTIFACT")
    ]
    discovery_failures = [r for r in results if r.get("discovery_failed")]
    mock_artifacts = [r for r in results if r.get("topology_state") == "MOCK_BOUNDARY_DOMINANT"]

    warnings: list[str] = []
    if discovery_failures:
        warnings.append(
            f"{len(discovery_failures)} function(s) had test discovery failures. "
            "Survival rates reflect missing tests, not specification gaps."
        )
    if mock_artifacts:
        warnings.append(
            f"{len(mock_artifacts)} function(s) have mock-boundary-dominant topology. "
            "Survival may reflect mocked call paths, not genuine specification gaps."
        )
    if discovery_artifacts:
        output["artifact_count"] = len(discovery_artifacts)
    if warnings:
        output["truthfulness_warnings"] = warnings
    # Legacy field
    if discovery_failures:
        output["discovery_warning"] = warnings[0]


def impl_run_full(
    helpers: Any,
    path: str,
    file: str,
    function: str | None,
    budget_ms: float = 600_000,
    per_mutant_timeout_ms: float = 5000,
) -> str:
    import time as _time

    from lintgate.keys import canonical_function_key
    from lintgate.specification.mutation_engine import run_function_profiling
    from lintgate.specification.mutation_filter import filter_categories

    call_start = _time.monotonic()
    effective_budget = min(budget_ms, _HARD_TIMEOUT_MS)

    project_root = helpers["_validate_project_root"](path)
    full, func_node, err = resolve_function(project_root, file, function)
    if err and function:
        return str(helpers["_json_dumps"]({"error": err}))

    ctx = _build_mutation_context(project_root, full)

    # Split budget across functions when analyzing a whole file
    num_funcs = 1
    if not function:
        tree = parse_file(full)
        if tree:
            num_funcs = max(len(walk_functions(tree)), 1)
    per_func_budget = max(effective_budget / num_funcs, 1000)

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
            per_mutant_timeout_ms=per_mutant_timeout_ms,
            budget_ms=per_func_budget,
        ),
        filter_categories,
        canonical_function_key,
    )
    if isinstance(results, str):
        return results

    analysis = run_post_profiling_analysis(results, ctx.purity_map)
    elapsed_total = (_time.monotonic() - call_start) * 1000
    any_budget_exhausted = any(r.get("budget_exhausted") for r in results)

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
    output: dict[str, Any] = {
        "file": file,
        "functions_profiled": len(results),
        "tests_discovered": len(ctx.test_files),
        "results": results,
        "analysis": analysis,
        "total_budget_ms": effective_budget,
        "per_func_budget_ms": round(per_func_budget, 1),
        "elapsed_ms": round(elapsed_total, 1),
        "next_actions": serialize_next_actions(next_actions),
    }
    if any_budget_exhausted:
        output["budget_exhausted"] = True
        output["partial_results"] = True
    _add_truthfulness_warnings(results, output)
    return str(helpers["_json_dumps"](output, output_mode="compact"))


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
    """Extract prescriptions from cached mutation states.

    Uses survivor records for grounded prescriptions when available,
    falling back to category-level templates otherwise.
    """
    from lintgate.specification.witness_generation import generate_witness_prescription

    prescriptions: list[dict[str, Any]] = []
    for data in states:
        func_key = data.get("function_key", "")
        survivor_records = data.get("survivor_records", [])

        if survivor_records:
            # Grounded prescriptions from actual survivor data
            for survivor in survivor_records:
                prescriptions.append(generate_witness_prescription(survivor, func_key))
        else:
            # Fallback: category-level templates
            for cat_data in data.get("per_category", []):
                if cat_data.get("survived", 0) > 0:
                    prescriptions.append(
                        {
                            "function": func_key,
                            "category": cat_data["category"],
                            "survived": cat_data["survived"],
                            "survival_rate": cat_data.get("survival_rate", 0),
                            "action": prescription_for_category(cat_data["category"]),
                            "source_of_evidence": "category_template",
                        }
                    )
    return prescriptions


# Category → performance unlock mapping for decomposition bridge (#312)
_CATEGORY_PERFORMANCE_MAP: dict[str, dict[str, Any]] = {
    "BOUNDARY": {
        "unlock": "predicate_extraction",
        "description": "Branch/predicate logic can be extracted into guard functions",
        "performance_actions": ["extract guard predicates", "enable branch-free optimization"],
        "cacheable_subunit": False,
        "parallelizable_subunit": False,
        "jit_eligible": False,
    },
    "SWAP": {
        "unlock": "strategy_seam",
        "description": "Parameter-order or execution-order seams indicate interchangeable strategies",
        "performance_actions": [
            "extract strategy interface",
            "enable strategy selection at call-site",
        ],
        "cacheable_subunit": True,
        "parallelizable_subunit": True,
        "jit_eligible": False,
    },
    "VALUE": {
        "unlock": "memoization_candidate",
        "description": "Intermediate results can be cached — value mutations survive",
        "performance_actions": ["extract pure computation subunit", "apply memoization"],
        "cacheable_subunit": True,
        "parallelizable_subunit": False,
        "jit_eligible": False,
    },
    "STATE": {
        "unlock": "state_isolation",
        "description": "State mutations can be isolated into a separate stateful unit",
        "performance_actions": ["separate pure computation from state management"],
        "cacheable_subunit": False,
        "parallelizable_subunit": False,
        "jit_eligible": False,
    },
    "TYPE": {
        "unlock": "type_discrimination",
        "description": "Type-based dispatch can be extracted for specialization",
        "performance_actions": ["extract type-specialized fast paths", "enable monomorphization"],
        "cacheable_subunit": False,
        "parallelizable_subunit": False,
        "jit_eligible": True,
    },
}


def _build_performance_unlocks(
    surviving_cats: list[str],
    per_category_data: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map surviving mutation categories to concrete performance unlock recommendations."""
    unlocks: list[dict[str, Any]] = []
    survival_by_cat = {
        c["category"]: c.get("survival_rate", c.get("survived", 0) / max(c.get("total", 1), 1))
        for c in per_category_data
        if c.get("survived", 0) > 0
    }

    for cat in surviving_cats:
        mapping = _CATEGORY_PERFORMANCE_MAP.get(cat)
        if not mapping:
            continue
        survival = survival_by_cat.get(cat, 0.5)
        confidence = min(0.9, 0.5 + survival * 0.4)
        unlocks.append(
            {
                "category": cat,
                "unlock_type": mapping["unlock"],
                "description": mapping["description"],
                "performance_actions": mapping["performance_actions"],
                "predicted_subunits": {
                    "cacheable": mapping["cacheable_subunit"],
                    "parallelizable": mapping["parallelizable_subunit"],
                    "jit_eligible": mapping["jit_eligible"],
                },
                "confidence": round(confidence, 2),
                "survival_rate": round(survival, 3),
            }
        )
    return unlocks


def impl_decompose(helpers: Any, path: str, file: str, function: str | None, mode: str) -> str:
    from lintgate.specification.decomposition_evidence import evaluate_decomposition

    project_root = helpers["_validate_project_root"](path)
    states = iter_cached_states(get_cache_dir(project_root), file, function)

    # Load spec data for cross-lens enrichment
    spec_data_by_func = _load_spec_data_for_decompose(project_root, file)

    candidates: list[dict[str, Any]] = []
    for data in states:
        per_category = data.get("per_category", [])
        surviving_cats = [c["category"] for c in per_category if c.get("survived", 0) > 0]
        if len(surviving_cats) < 2:
            continue

        func_key = data.get("function_key", "")
        unlocks = _build_performance_unlocks(surviving_cats, per_category)
        has_cacheable = any(u["predicted_subunits"]["cacheable"] for u in unlocks)
        has_parallel = any(u["predicted_subunits"]["parallelizable"] for u in unlocks)
        has_jit = any(u["predicted_subunits"]["jit_eligible"] for u in unlocks)

        # Cross-lens decomposition verdict
        verdict = evaluate_decomposition(
            function_key=func_key,
            surviving_categories=surviving_cats,
            mutation_cache_entry=data,
            spec_data=spec_data_by_func.get(func_key),
            topology_state=data.get("topology_state", ""),
        )

        candidates.append(
            {
                "function": func_key,
                "surviving_categories": surviving_cats,
                "performance_unlocks": unlocks,
                "predicted_unlock_classes": {
                    "cacheable": has_cacheable,
                    "parallelizable": has_parallel,
                    "jit_eligible": has_jit,
                },
                "decomposition_verdict": verdict.to_dict(),
                "recommendation": verdict.rationale,
            }
        )

    output: dict[str, Any] = {"mode": mode, "candidates": candidates}

    # Summary counts
    if candidates:
        rec_counts = {"EXTRACT_BOUNDARY": 0, "KEEP_TESTING": 0, "INSUFFICIENT_EVIDENCE": 0}
        for c in candidates:
            rec = c["decomposition_verdict"]["recommendation"]
            rec_counts[rec] = rec_counts.get(rec, 0) + 1
        output["summary"] = rec_counts

        next_actions = []
        if rec_counts.get("EXTRACT_BOUNDARY", 0) > 0:
            next_actions.append(
                NextAction(
                    tool="convergence_analyze",
                    args={"path": path, "file": file},
                    reason="See detailed multi-lens evidence for extraction candidates",
                )
            )
        if rec_counts.get("KEEP_TESTING", 0) > 0:
            next_actions.append(
                NextAction(
                    tool="mutation_prescribe_tests",
                    args={"path": path, "file": file},
                    reason="Generate test skeletons — testing is preferred over extraction",
                )
            )
        next_actions.append(
            NextAction(
                tool="spec_file_analyze",
                args={"path": path, "file": file},
                reason="Check specification gaps to strengthen decomposition evidence",
            )
        )
        output["next_actions"] = serialize_next_actions(next_actions)

    return str(helpers["_json_dumps"](output, output_mode="compact"))


def _load_spec_data_for_decompose(
    project_root: str,
    file: str | None,
) -> dict[str, dict]:
    """Load spec analysis data for decomposition enrichment.

    Returns a dict mapping function_key → spec data dict.
    Falls back to empty dict if spec data is unavailable.
    """
    if not file:
        return {}
    try:
        import os

        from lintgate.specification.file_analyzer import analyze_file

        full_path = os.path.join(project_root, file) if not os.path.isabs(file) else file
        if not os.path.isfile(full_path):
            return {}
        result = analyze_file(full_path, project_root, enrich=False)
        return dict(result.functions)
    except Exception:
        return {}


def impl_spec_improve(
    helpers: Any,
    path: str,
    file: str,
    function: str | None = None,
    budget_ms: float = 30_000,
) -> str:
    """One-shot spec improvement: diagnose → profile → prescribe in a single call.

    Chains the spec→mutation→prescribe pipeline internally so the operator
    gets a consolidated report with everything needed to write the next test.

    Steps:
    1. Run spec_file_analyze (symbolic baseline) to identify under-specified functions
    2. Run mutation_run_sampling on the top targets
    3. Collect grounded prescriptions from survivors
    4. Return a consolidated action plan
    """
    import os
    import time

    project_root = helpers["_validate_project_root"](path)
    full_path = os.path.join(project_root, file) if not os.path.isabs(file) else file

    if not os.path.isfile(full_path):
        return str(
            helpers["_json_dumps"]({"error": f"File not found: {file}"}, output_mode="compact")
        )

    output: dict[str, Any] = {"file": file, "steps_completed": []}
    start = time.monotonic()

    # Step 1: Spec diagnosis (symbolic baseline — fast)
    try:
        from lintgate.specification.file_analyzer import analyze_file

        spec_result = analyze_file(full_path, project_root, enrich=True)
        spec_functions = spec_result.functions

        # Rank by specification gap (lowest spec_level first)
        ranked = sorted(
            spec_functions.items(),
            key=lambda kv: kv[1].get("specification_level", 0.0),
        )

        # Filter to function if specified
        if function:
            ranked = [(k, v) for k, v in ranked if function.lower() in k.lower()]

        # Take top targets (under-specified functions)
        targets = [(k, v) for k, v in ranked if v.get("specification_level", 0.0) < 0.8][:5]

        output["spec_diagnosis"] = {
            "total_functions": len(spec_functions),
            "under_specified": len(targets),
            "targets": [
                {
                    "function": k,
                    "sigma": v.get("sigma", 0),
                    "spec_level": round(v.get("specification_level", 0.0), 3),
                    "regime": v.get("regime", "unknown"),
                    "phase": v.get("phase", "unknown"),
                    "empirical_overlay": v.get("empirical_overlay", {}),
                }
                for k, v in targets
            ],
        }
        output["steps_completed"].append("spec_diagnosis")
    except Exception as e:
        output["spec_diagnosis"] = {"error": str(e)}
        targets = []

    if not targets:
        output["summary"] = "No under-specified functions found. Specification is in good shape."
        output["next_actions"] = serialize_next_actions(
            [
                NextAction(
                    tool="spec_gate_check",
                    args={"path": path, "file": file},
                    reason="Verify optimization gates pass",
                ),
            ]
        )
        return str(helpers["_json_dumps"](output, output_mode="compact"))

    # Step 2: Mutation sampling on top targets
    elapsed = (time.monotonic() - start) * 1000
    remaining_budget = budget_ms - elapsed

    if remaining_budget <= 0:
        output["mutation_sampling"] = {"skipped": "budget_exhausted"}
    else:
        try:
            impl_run_sampling(
                helpers,
                path,
                file,
                function,
                budget_ms=remaining_budget,
            )
            output["steps_completed"].append("mutation_sampling")
        except Exception as e:
            output["mutation_sampling"] = {"error": str(e)}

    # Step 3: Collect prescriptions from cached mutation state
    try:
        prescriptions = _collect_prescriptions(
            iter_cached_states(get_cache_dir(project_root), file, function)
        )
        output["prescriptions"] = prescriptions[:10]
        output["total_prescriptions"] = len(prescriptions)
        output["steps_completed"].append("prescriptions")
    except Exception as e:
        output["prescriptions_error"] = str(e)
        prescriptions = []

    # Step 4: Build action plan
    output["action_plan"] = _build_action_plan(targets, prescriptions)

    output["next_actions"] = serialize_next_actions(
        [
            NextAction(
                tool="mutation_prescribe_tests",
                args={"path": path, "file": file},
                reason="Generate test skeletons for the prescribed improvements",
            ),
            NextAction(
                tool="mutation_validate_tests",
                args={"path": path, "file": file},
                reason="After writing tests, validate they kill targeted mutants",
            ),
        ]
    )

    output["elapsed_ms"] = round((time.monotonic() - start) * 1000)
    return str(helpers["_json_dumps"](output, output_mode="compact"))


def _build_action_plan(
    targets: list[tuple[str, dict]],
    prescriptions: list[dict],
) -> list[dict[str, Any]]:
    """Build a prioritized action plan from spec targets and prescriptions."""
    plan: list[dict[str, Any]] = []

    # Group prescriptions by function
    rx_by_func: dict[str, list[dict]] = {}
    for rx in prescriptions:
        fk = rx.get("function", rx.get("function_key", ""))
        rx_by_func.setdefault(fk, []).append(rx)

    for func_key, spec in targets:
        func_rxs = rx_by_func.get(func_key, [])
        entry: dict[str, Any] = {
            "function": func_key,
            "spec_level": round(spec.get("specification_level", 0.0), 3),
            "phase": spec.get("phase", "unknown"),
        }

        if func_rxs:
            top_rx = func_rxs[0]
            entry["next_test"] = {
                "category": top_rx.get("category", top_rx.get("kind", "")),
                "why": top_rx.get("why_this_matters", top_rx.get("description", "")),
                "assertion_shape": top_rx.get("assertion_shape", ""),
                "confidence": top_rx.get("confidence", 0.0),
            }
            entry["total_prescriptions"] = len(func_rxs)
        else:
            entry["next_test"] = None
            entry["note"] = "No mutation data — run mutation_run_sampling first"

        plan.append(entry)

    return plan


# Hard circuit breaker — no mutation tool call can exceed this.
_HARD_TIMEOUT_MS = 600_000  # 10 minutes


def impl_refactor_loop(
    helpers: Any,
    path: str,
    file: str,
    function: str | None,
    budget_ms: float = 300_000,
) -> str:
    import time as _time

    call_start = _time.monotonic()
    project_root = helpers["_validate_project_root"](path)
    full, func_node, err = resolve_function(project_root, file, function)
    if err:
        return str(helpers["_json_dumps"]({"error": err}))

    cache_dir = get_cache_dir(project_root)
    targets = _resolve_refactor_targets(full, func_node, function)
    if isinstance(targets, str):
        return str(helpers["_json_dumps"]({"error": targets}))

    # Enforce hard circuit breaker
    effective_budget = min(budget_ms, _HARD_TIMEOUT_MS)

    rel_path = os.path.relpath(full, project_root)
    test_files = discover_test_files(project_root, full)
    results, timed_out_functions = _validate_targets(
        targets,
        full,
        rel_path,
        cache_dir,
        test_files,
        budget_ms=effective_budget,
        call_start=call_start,
    )

    elapsed_total = (_time.monotonic() - call_start) * 1000
    budget_exhausted = elapsed_total >= effective_budget or len(timed_out_functions) > 0

    next_actions = [
        NextAction(
            tool="spec_gate_check",
            args={"path": path},
            reason="Check if specification level now meets optimization hint thresholds",
        ),
    ]
    output: dict[str, Any] = {
        "file": file,
        "results": results,
        "total_budget_ms": effective_budget,
        "elapsed_ms": round(elapsed_total, 1),
        "next_actions": serialize_next_actions(next_actions),
    }
    if budget_exhausted:
        output["budget_exhausted"] = True
        output["partial_results"] = True
    if timed_out_functions:
        output["timed_out_functions"] = timed_out_functions
    return str(helpers["_json_dumps"](output, output_mode="compact"))


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


def _validate_targets(
    targets: list[tuple[str, Any]],
    full_path: str,
    rel_path: str,
    cache_dir: Any,
    test_files: list[str],
    budget_ms: float = 300_000,
    call_start: float | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate targets using sampling with budget splitting.

    Uses sampling (not exhaustive profiling) by default. Splits budget
    evenly across functions and returns partial results if budget exhausted.

    Returns (results, timed_out_functions).
    """
    import time as _time

    from lintgate.keys import canonical_function_key
    from lintgate.specification.mutation_engine import run_function_sampling
    from lintgate.specification.mutation_filter import filter_categories

    if call_start is None:
        call_start = _time.monotonic()

    num_funcs = max(len(targets), 1)
    per_func_budget = max(budget_ms / num_funcs, 200)

    results: list[dict[str, Any]] = []
    timed_out_functions: list[str] = []

    for qualname, node in targets:
        # Check overall budget before starting each function
        elapsed = (_time.monotonic() - call_start) * 1000
        if elapsed >= budget_ms:
            timed_out_functions.append(qualname)
            continue

        func_key = canonical_function_key(rel_path, qualname)
        prev = load_cached_state(cache_dir, func_key)
        prev_survival = prev.get("survival_rate", 1.0) if prev else None

        is_pure = detect_purity(full_path, qualname)
        cats = filter_categories(node, is_pure=is_pure)
        bare_name = qualname.split(".")[-1]
        tests, discovery_diag = load_test_callables(test_files, bare_name)

        # Remaining budget capped by per-function allocation
        remaining = budget_ms - (_time.monotonic() - call_start) * 1000
        func_budget = min(per_func_budget, max(remaining, 200))

        sr = run_function_sampling(
            node,
            func_key,
            cats,
            tests,
            lambda *a: None,
            budget_ms=func_budget,
            per_mutant_timeout_ms=min(500, func_budget),
        )
        result_dict: dict[str, Any] = sr.to_dict()
        result_dict["tests_loaded"] = len(tests)
        result_dict["is_pure"] = is_pure
        result_dict["per_func_budget_ms"] = round(func_budget, 1)
        if len(tests) == 0:
            result_dict["discovery_failed"] = len(test_files) > 0
            result_dict["discovery_diagnostics"] = discovery_diag.to_dict()
        if prev_survival is not None:
            result_dict["previous_survival_rate"] = prev_survival
            result_dict["survival_delta"] = round(sr.survival_rate - prev_survival, 3)
        results.append(result_dict)
        save_cached_state(cache_dir, func_key, result_dict)

    return results, timed_out_functions


def impl_prescribe_tests(helpers: Any, path: str, file: str, function: str | None) -> str:
    from lintgate.specification.witness_generation import generate_witness_prescription

    project_root = helpers["_validate_project_root"](path)
    states = iter_cached_states(get_cache_dir(project_root), file, function)

    skeletons: list[dict[str, Any]] = []
    for data in states:
        func_key = data.get("function_key", "")
        survivor_records = data.get("survivor_records", [])

        # Prefer witness-grounded skeletons from survivor records
        if survivor_records:
            for sr in survivor_records:
                rx = generate_witness_prescription(sr, func_key)
                skeleton = generate_test_skeleton(func_key, rx.get("category", "UNKNOWN"))
                # Enrich skeleton with witness data
                skeleton["witness"] = {
                    "mutant_id": rx.get("mutant_id", ""),
                    "why_this_matters": rx.get("why_this_matters", ""),
                    "suggested_input": rx.get("suggested_input", ""),
                    "assertion_shape": rx.get("assertion_shape", ""),
                    "confidence": rx.get("confidence", 0.0),
                    "source_of_evidence": rx.get("source_of_evidence", ""),
                }
                skeletons.append(skeleton)
        else:
            # Fallback to category-generic skeletons
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
