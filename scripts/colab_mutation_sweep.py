#!/usr/bin/env python3
"""Colab-portable batch mutation profiling script.

Runs mutation_run_sampling on all Python files in the project without
requiring the MCP server. Results are written to .lintgate/mutation/ in
the same JSON format the MCP tools expect, so they can be synced back
to the local project and immediately consumed by mutation_get_state,
mutation_prescribe, etc.

Usage (local):
    python scripts/colab_mutation_sweep.py /path/to/lintgate

Usage (Google Colab):
    1. Upload or clone the repo:
       !git clone <your-repo-url> /content/lintgate
    2. Install minimal deps:
       !pip install pyyaml packaging
    3. Run:
       !cd /content/lintgate && python scripts/colab_mutation_sweep.py /content/lintgate

    4. Download results:
       !zip -r /content/mutation_results.zip /content/lintgate/.lintgate/mutation/
       # Then use Colab file download

Parallelism:
    Set MUTATION_WORKERS=N env var (default: 4 on Colab, 2 locally).
    Colab free tier has 2 vCPUs but benefits from 4 workers since
    mutation evaluation is mixed CPU/IO.

Skip already-profiled files:
    Set MUTATION_SKIP_CACHED=1 to skip files that already have cached results.
"""
from __future__ import annotations

import ast
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# Ensure lintgate is importable
PROJECT_ROOT = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
sys.path.insert(0, PROJECT_ROOT)


def collect_python_files(root: str) -> list[str]:
    """Collect all non-test Python files under lintgate/ and mcp_tools/."""
    files = []
    for subdir in ("lintgate", "mcp_tools"):
        base = os.path.join(root, subdir)
        if not os.path.isdir(base):
            continue
        for dirpath, _, filenames in os.walk(base):
            for fn in sorted(filenames):
                if not fn.endswith(".py"):
                    continue
                if fn.startswith("test_") or fn.endswith("_test.py"):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root)
                files.append(rel)
    return files


def profile_single_file(args: tuple[str, str, float, bool]) -> dict:
    """Profile a single file — designed to run in a subprocess.

    Returns a summary dict with file, functions profiled, and timing.
    """
    project_root, rel_path, budget_ms, skip_cached = args

    # Re-import inside subprocess to avoid pickling issues
    sys.path.insert(0, project_root)

    from lintgate.specification.mutation_engine import run_function_sampling
    from mcp_tools._mutation_impl import (
        MutationContext,
        detect_purity_map,
        discover_test_files,
        get_cache_dir,
        load_test_callables,
        lookup_purity,
        parse_file,
        save_cached_state,
        walk_functions,
    )

    from lintgate.keys import canonical_function_key
    from lintgate.specification.mutation_filter import filter_categories

    full_path = os.path.join(project_root, rel_path)
    cache_dir = get_cache_dir(project_root)
    start = time.monotonic()

    result = {
        "file": rel_path,
        "functions_profiled": 0,
        "functions_skipped_cached": 0,
        "functions_skipped_trivial": 0,
        "errors": [],
        "elapsed_s": 0.0,
    }

    tree = parse_file(full_path)
    if tree is None:
        result["errors"].append(f"Parse error: {rel_path}")
        return result

    functions = walk_functions(tree)
    if not functions:
        return result

    # Build context
    test_files = discover_test_files(project_root, full_path)
    purity_map = detect_purity_map(full_path)

    ctx = MutationContext(
        full_path=full_path,
        rel_path=rel_path,
        cache_dir=cache_dir,
        purity_map=purity_map,
        test_files=test_files,
        project_root=project_root,
    )

    for qualname, node in functions:
        func_key = canonical_function_key(rel_path, qualname)

        # Skip if cached
        if skip_cached:
            safe_key = func_key.replace("::", "__").replace("/", "_")
            cache_file = cache_dir / f"{safe_key}.json"
            if cache_file.exists():
                result["functions_skipped_cached"] += 1
                continue

        # Skip trivial functions (getters, single-return, etc.)
        body = getattr(node, "body", [])
        if len(body) <= 1:
            # Single-statement body — likely trivial
            stmt = body[0] if body else None
            if isinstance(stmt, ast.Return) or isinstance(stmt, ast.Expr):
                result["functions_skipped_trivial"] += 1
                continue

        is_pure = lookup_purity(purity_map, qualname)
        cats = filter_categories(node, is_pure=is_pure)  # type: ignore[arg-type]

        bare_name = qualname.split(".")[-1]
        tests, _discovery_diag = load_test_callables(
            ctx.test_files,
            bare_name,
            project_root=ctx.project_root,
            func_key=func_key,
        )

        try:
            sr = run_function_sampling(
                node,  # type: ignore[arg-type]  # AsyncFunctionDef handled at runtime
                func_key,
                cats,
                tests,
                lambda *_a: None,
                budget_ms=budget_ms,
            )
            result_dict = sr.to_dict()
            result_dict["tests_loaded"] = len(tests)
            result_dict["is_pure"] = is_pure
            args_node = getattr(node, "args", None)
            result_dict["parameter_count"] = len(args_node.args) if args_node else 0
            save_cached_state(ctx.cache_dir, func_key, result_dict)
            result["functions_profiled"] += 1
        except Exception as e:
            result["errors"].append(f"{func_key}: {type(e).__name__}: {e}")

    result["elapsed_s"] = round(time.monotonic() - start, 2)
    return result


def main() -> None:
    project_root = os.path.abspath(PROJECT_ROOT)
    if not os.path.isdir(project_root):
        print(f"Error: {project_root} is not a directory")
        sys.exit(1)

    # Configuration from env
    workers = int(os.environ.get("MUTATION_WORKERS", "4"))
    budget_ms = float(os.environ.get("MUTATION_BUDGET_MS", "500"))
    skip_cached = os.environ.get("MUTATION_SKIP_CACHED", "1") == "1"

    files = collect_python_files(project_root)
    print(f"Found {len(files)} source files to profile")
    print(f"Workers: {workers}, Budget: {budget_ms}ms/function, Skip cached: {skip_cached}")
    print()

    # Ensure cache dir exists
    cache_dir = Path(project_root) / ".lintgate" / "mutation"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Build work items
    work = [(project_root, f, budget_ms, skip_cached) for f in files]

    total_functions = 0
    total_cached = 0
    total_trivial = 0
    total_errors = 0
    start = time.monotonic()

    # Run with process pool for true parallelism
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(profile_single_file, w): w[1] for w in work}
        for i, future in enumerate(as_completed(futures), 1):
            rel_path = futures[future]
            try:
                result = future.result()
                total_functions += result["functions_profiled"]
                total_cached += result["functions_skipped_cached"]
                total_trivial += result["functions_skipped_trivial"]
                total_errors += len(result["errors"])
                status = (
                    f"[{i}/{len(files)}] {rel_path}: "
                    f"{result['functions_profiled']} profiled, "
                    f"{result['functions_skipped_cached']} cached, "
                    f"{result['elapsed_s']}s"
                )
                if result["errors"]:
                    status += f" ({len(result['errors'])} errors)"
                print(status)
            except Exception as e:
                print(f"[{i}/{len(files)}] {rel_path}: FAILED - {e}")
                total_errors += 1

    elapsed = round(time.monotonic() - start, 1)
    print(f"\n{'='*60}")
    print(f"Sweep complete in {elapsed}s")
    print(f"  Functions profiled: {total_functions}")
    print(f"  Skipped (cached):   {total_cached}")
    print(f"  Skipped (trivial):  {total_trivial}")
    print(f"  Errors:             {total_errors}")
    print(f"  Results in:         {cache_dir}")
    print(f"\nTo sync back to local: scp -r <colab>:.lintgate/mutation/ .lintgate/mutation/")


if __name__ == "__main__":
    main()
