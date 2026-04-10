"""Fresh mutation validation for test regeneration.

Runs targeted mutation sampling against generated test files to produce
post-generation kill rates. This replaces stale-cache reads that report
pre-generation data — the core validation truthfulness fix.

Also provides assertion content checking: generated files with only
``pass`` stubs are not real tests even though pytest reports them as passed.
"""

from __future__ import annotations

import ast
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintgate.specification._regeneration_types import RebuildManifest


def count_test_assertions(gen_files: list[str]) -> int:
    """Count non-trivial assertions across generated test files.

    Counts ``assert`` statements that are NOT ``assert True``.
    Returns 0 for files that are all ``pass`` stubs.
    """
    total = 0
    for filepath in gen_files:
        try:
            with open(filepath, encoding="utf-8") as f:
                tree = ast.parse(f.read())
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert) and not _is_trivial_assert(node):
                total += 1
    return total


def _is_trivial_assert(node: ast.Assert) -> bool:
    """Check if an assert is trivially true (e.g., ``assert True``)."""
    test = node.test
    return isinstance(test, ast.Constant) and test.value is True


def run_fresh_kill_rates(
    plan: RebuildManifest,
    project_root: str,
    budget_per_func_ms: float = 500,
    max_per_category: int = 3,
    per_mutant_timeout_ms: float = 500,
    *,
    generated_dir: str | None = None,
    test_file_overrides: dict[str, str] | None = None,
) -> tuple[list[float], int, list[dict[str, Any]]]:
    """Run fresh mutation sampling for auto_generate_unit targets.

    Instead of reading stale mutation cache, imports the generated test
    files and runs real mutation sampling against each target function.

    When *generated_dir* is provided, resolves test files relative to that
    directory instead of ``project_root``.  When *test_file_overrides* is
    provided, uses the override path for matching function keys.

    Returns:
        (kill_rates, zero_kill_count, per_function_details)
    """
    from Wesker.engine import (
        MutationCategory,
        run_function_sampling,
    )

    from lintgate.specification.test_regeneration_strategy import Strategy
    from mcp_tools._mutation_impl import (
        _load_all_tests_from_files,
        detect_purity,
        resolve_function,
    )

    auto_funcs = [f for f in plan.functions if f.strategy == Strategy.AUTO_GENERATE_UNIT]
    if not auto_funcs:
        return [], 0, []

    rates: list[float] = []
    zero = 0
    details: list[dict[str, Any]] = []

    for func in auto_funcs:
        func_key = func.evidence.function_key
        source_file = func.evidence.source_file
        target_test = func.target_test_file

        # Resolve generated test file — use overrides or generated_dir if provided
        if test_file_overrides and func_key in test_file_overrides:
            gen_path = test_file_overrides[func_key]
        elif generated_dir:
            gen_path = os.path.join(generated_dir, os.path.basename(target_test))
        else:
            gen_path = os.path.join(project_root, target_test)
        if not os.path.isfile(gen_path):
            detail = {
                "function_key": func_key,
                "status": "no_generated_file",
                "kill_rate": 0.0,
            }
            details.append(detail)
            rates.append(0.0)
            zero += 1
            continue

        # Load test callables from generated file
        test_callables = _load_all_tests_from_files([gen_path])
        if not test_callables:
            detail = {
                "function_key": func_key,
                "status": "no_test_callables",
                "generated_file": target_test,
                "kill_rate": 0.0,
            }
            details.append(detail)
            rates.append(0.0)
            zero += 1
            continue

        # Resolve source function AST node
        func_name = func_key.split("::")[-1] if "::" in func_key else func_key
        full_path, func_node, err = resolve_function(project_root, source_file, func_name)
        if func_node is None:
            detail = {
                "function_key": func_key,
                "status": "source_unresolved",
                "error": err or "function not found",
                "kill_rate": 0.0,
            }
            details.append(detail)
            continue

        # Determine mutation categories (purity-aware)
        qualname = getattr(func_node, "_lintgate_qualname", func_name)
        is_pure = detect_purity(full_path, qualname)
        categories = {
            MutationCategory.VALUE,
            MutationCategory.SWAP,
            MutationCategory.BOUNDARY,
            MutationCategory.TYPE,
        }
        if not is_pure:
            categories.add(MutationCategory.STATE)

        # Run targeted sampling — cast to FunctionDef for the engine
        # (AsyncFunctionDef has the same AST shape for mutation purposes)
        import ast as _ast

        if not isinstance(func_node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            raise ValueError(
                f"Expected FunctionDef or AsyncFunctionDef, got {type(func_node).__name__}"
            )
        try:
            result = run_function_sampling(
                func_node,  # type: ignore[arg-type]
                func_key.rsplit("::", 1)[0] + "::" + qualname if "::" in func_key else qualname,
                categories,
                test_callables,
                lambda *_a: None,  # dummy original_func
                budget_ms=budget_per_func_ms,
                max_per_category=max_per_category,
                per_mutant_timeout_ms=per_mutant_timeout_ms,
            )
        except Exception as exc:
            detail = {
                "function_key": func_key,
                "status": "sampling_error",
                "error": str(exc)[:200],
                "kill_rate": 0.0,
            }
            details.append(detail)
            rates.append(0.0)
            zero += 1
            continue

        kr = 1.0 - result.survival_rate
        rates.append(kr)
        if kr <= 0.0:  # noqa: SIM102  # NOSONAR — exact zero check on discrete ratio
            zero += 1

        detail = {
            "function_key": func_key,
            "status": "sampled",
            "kill_rate": round(kr, 3),
            "total_mutants": result.total_mutants,
            "total_killed": result.total_killed,
            "tests_loaded": len(test_callables),
            "categories_tested": result.categories_tested,
            "is_pure": is_pure,
        }
        details.append(detail)

    return rates, zero, details
