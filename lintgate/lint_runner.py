"""Phase 3: Execute selected linters.

Runs linters in parallel with per-linter timeouts and graceful
degradation for missing tools. File-scoped execution (only lint
changed files, not the whole project).
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import TYPE_CHECKING

from .types import LinterContext, LinterResult, LintTier, ProjectConfig


def _is_external_package(filepath: str) -> bool:
    """Exclude site-packages and dist-packages from analysis.

    Resolves symlinks before checking so vendored links into
    a virtual-env are correctly filtered.
    """
    resolved = os.path.realpath(filepath)
    return any(seg in resolved for seg in ("/site-packages/", "/dist-packages/"))


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
        results.append(
            LinterResult(
                linter_name=name,
                status="skipped",
                error=f"Unknown linter: {name}",
            )
        )

    if not selected:
        return results

    # Filter out external packages (site-packages, dist-packages)
    filtered_files = [f for f in tier.files if not _is_external_package(f)]
    if not filtered_files:
        return results

    # Build shared context
    ctx = LinterContext(
        files=filtered_files,
        project_root=config.project_root,
        strictness=tier.strictness,
        config=config.linter_configs,
    )

    # Execute in parallel
    deadline = time.time() + timeout_ms / 1000.0
    parallel = _execute_parallel(selected, ctx, deadline, timeout_ms)
    results.extend(parallel)

    return results


def _collect_future_result(fut, linter: BaseLinter) -> LinterResult:
    """Safely collect a completed future's result."""
    try:
        return fut.result(timeout=0.1)
    except Exception as e:
        return LinterResult(
            linter_name=linter.name,
            status="error",
            error=f"Executor error: {type(e).__name__}: {e}",
        )


def _execute_parallel(
    selected: list[BaseLinter],
    ctx: LinterContext,
    deadline: float,
    timeout_ms: int,
) -> list[LinterResult]:
    """Execute linters in parallel with timeout management."""
    results: list[LinterResult] = []

    with ThreadPoolExecutor(max_workers=min(4, len(selected))) as executor:
        futures = {}
        completed_futures: set = set()
        timed_out_futures: set = set()
        for linter in selected:
            futures[executor.submit(linter.execute, ctx)] = linter

        try:
            for fut in as_completed(futures, timeout=max(0.5, deadline - time.time())):
                completed_futures.add(fut)
                results.append(_collect_future_result(fut, futures[fut]))
        except FuturesTimeoutError:
            for fut, linter in futures.items():
                if fut in completed_futures or fut.done():
                    continue
                fut.cancel()
                timed_out_futures.add(fut)
                results.append(
                    LinterResult(
                        linter_name=linter.name,
                        status="timeout",
                        error=f"Total budget exceeded ({timeout_ms}ms)",
                    )
                )

    # Collect stragglers that finished during shutdown
    for fut, linter in futures.items():
        if fut in completed_futures or fut in timed_out_futures:
            continue
        if fut.done():
            results.append(_collect_future_result(fut, linter))
        else:
            fut.cancel()
            results.append(
                LinterResult(
                    linter_name=linter.name,
                    status="timeout",
                    error=f"Total budget exceeded ({timeout_ms}ms)",
                )
            )

    return results
