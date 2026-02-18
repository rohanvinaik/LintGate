"""Phase 3: Execute selected linters.

Runs linters in parallel with per-linter timeouts and graceful
degradation for missing tools. File-scoped execution (only lint
changed files, not the whole project).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import TYPE_CHECKING

from .types import LinterContext, LinterResult, LintTier, ProjectConfig

if TYPE_CHECKING:
    from .linters.base import BaseLinter


def run_linters(
    tier: LintTier,
    config: ProjectConfig,
    registry: dict[str, BaseLinter],
    timeout_ms: int = 8000,
) -> list[LinterResult]:
    """Run all linters for the selected tier.

    Executes linters in parallel using ThreadPoolExecutor.
    Each linter gets its own timeout. The total budget is capped
    at timeout_ms.

    Args:
        tier: Selected lint tier (from tier_selector.py)
        config: Project config
        registry: Map of linter name -> BaseLinter instance
        timeout_ms: Total time budget in milliseconds

    Returns:
        List of LinterResult (one per linter, including skipped/errored)
    """
    if tier.skip:
        return []

    # Resolve linter names to instances
    selected = []
    unknown = []
    for name in tier.linters:
        if name in registry:
            selected.append(registry[name])
        else:
            unknown.append(name)

    # Report unknown linters
    results: list[LinterResult] = []
    for name in unknown:
        results.append(LinterResult(
            linter_name=name,
            status="skipped",
            error=f"Unknown linter: {name}",
        ))

    if not selected:
        return results

    # Build shared context
    ctx = LinterContext(
        files=tier.files,
        project_root=config.project_root,
        strictness=tier.strictness,
        config=config.linter_configs,
    )

    # Execute in parallel
    deadline = time.time() + timeout_ms / 1000.0

    with ThreadPoolExecutor(max_workers=min(4, len(selected))) as executor:
        futures = {}
        completed_futures = set()
        timed_out_futures = set()
        for linter in selected:
            fut = executor.submit(linter.execute, ctx)
            futures[fut] = linter

        try:
            for fut in as_completed(futures, timeout=max(0.5, deadline - time.time())):
                completed_futures.add(fut)
                try:
                    result = fut.result(timeout=0.1)
                    results.append(result)
                except Exception as e:
                    linter = futures[fut]
                    results.append(LinterResult(
                        linter_name=linter.name,
                        status="error",
                        error=f"Executor error: {type(e).__name__}: {e}",
                    ))
        except FuturesTimeoutError:
            # Expected when one or more linters exceed the remaining global budget.
            for fut, linter in futures.items():
                if fut in completed_futures or fut.done():
                    continue
                fut.cancel()
                timed_out_futures.add(fut)
                results.append(LinterResult(
                    linter_name=linter.name,
                    status="timeout",
                    error=f"Total budget exceeded ({timeout_ms}ms)",
                ))

    # Check for futures that didn't complete in time
    for fut, linter in futures.items():
        if fut in completed_futures or fut in timed_out_futures:
            continue

        # A future may complete right as as_completed() times out. Collect it.
        if fut.done():
            try:
                result = fut.result(timeout=0.0)
                results.append(result)
            except Exception as e:
                results.append(LinterResult(
                    linter_name=linter.name,
                    status="error",
                    error=f"Executor error: {type(e).__name__}: {e}",
                ))
            continue

        if not fut.done():
            fut.cancel()
            results.append(LinterResult(
                linter_name=linter.name,
                status="timeout",
                error=f"Total budget exceeded ({timeout_ms}ms)",
            ))

    return results
