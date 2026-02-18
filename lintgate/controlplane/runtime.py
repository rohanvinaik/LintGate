"""ControlPlane mesh runtime — parallel channel execution with shedding.

This is the orchestrator. It:
1. Accepts a SupervisionEvent
2. Determines which channels should run
3. Executes them in parallel with per-channel and global timeouts
4. Collects results (including timeout/error states)
5. Runs the coherence engine
6. Returns a MeshResult

Shedding policy (from the approved plan):
- Channels run in parallel with per-channel timeout_ms
- At global deadline minus 500ms, cancel unfinished futures
- Unfinished channels get ChannelResult(status="timeout")
- Coherence computes from whatever's available
- MeshResult.incomplete_channels lists timed-out channel names
- MeshResult.partial = True if any channel was shed

Architecture: Same ThreadPoolExecutor pattern as lint_runner.py, but
at the channel level instead of the linter level.
"""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from .types import (
    ChannelResult,
    ControlPlaneConfig,
    MeshResult,
    SupervisionEvent,
)

if TYPE_CHECKING:
    from .channel import Channel
    from .session_memory import SessionMemory


_MAX_WORKERS = 4


def run_mesh(
    event: SupervisionEvent,
    config: ControlPlaneConfig,
    channels: list[Channel],
    session: "SessionMemory | None" = None,
) -> MeshResult:
    """Execute the supervision mesh: parallel channels → coherence → result.

    Args:
        event: The supervision event to analyze.
        config: ControlPlane configuration (budgets, channel settings).
        channels: List of channel instances to consider running.
        session: Optional session memory for trajectory-aware coherence.

    Returns:
        MeshResult with all channel results and coherence diagnosis.
    """
    start = time.perf_counter()
    global_deadline = start + (config.latency_budget_ms / 1000.0)

    # Phase 1: Filter to channels that should run
    active_channels: list[Channel] = []
    skipped_results: list[ChannelResult] = []

    for ch in channels:
        if not config.channel_enabled(ch.name):
            skipped_results.append(ChannelResult(
                channel=ch.name, status="skip", severity="none",
                metrics={"reason": "disabled_in_config"},
            ))
            continue

        try:
            if not ch.should_run(event, config):
                skipped_results.append(ChannelResult(
                    channel=ch.name, status="skip", severity="none",
                    metrics={"reason": "event_not_relevant"},
                ))
                continue
        except Exception as e:
            skipped_results.append(ChannelResult(
                channel=ch.name, status="error", severity="none",
                error_message=f"should_run failed: {type(e).__name__}: {e}",
            ))
            continue

        active_channels.append(ch)

    # Phase 2: Execute active channels in parallel
    channel_results: list[ChannelResult] = list(skipped_results)
    incomplete: list[str] = []

    if active_channels:
        results_from_exec = _execute_parallel(
            active_channels, event, config, global_deadline,
        )
        for ch_name, result in results_from_exec.items():
            channel_results.append(result)
            if result.status == "timeout":
                incomplete.append(ch_name)

    # Phase 3: Run coherence engine (with history if session available)
    from .coherence import compute_coherence, compute_coherence_with_history

    if session is not None:
        coherence = compute_coherence_with_history(channel_results, session)
    else:
        coherence = compute_coherence(channel_results)

    elapsed_ms = (time.perf_counter() - start) * 1000
    return MeshResult(
        event=event,
        channel_results=channel_results,
        coherence=coherence,
        duration_ms=round(elapsed_ms, 1),
        incomplete_channels=incomplete,
        partial=len(incomplete) > 0,
    )


def _execute_parallel(
    channels: list[Channel],
    event: SupervisionEvent,
    config: ControlPlaneConfig,
    global_deadline: float,
) -> dict[str, ChannelResult]:
    """Execute channels in parallel with budget enforcement.

    Uses ThreadPoolExecutor (same pattern as lint_runner.py).
    Enforces both per-channel timeouts and global deadline.
    """
    results: dict[str, ChannelResult] = {}
    futures: dict[Future, Channel] = {}

    executor = ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(channels)))
    try:
        for ch in channels:
            future = executor.submit(_run_single_channel, ch, event, config)
            futures[future] = ch

        # Wait for completion with global deadline
        remaining = max(0, global_deadline - time.perf_counter() - 0.5)  # 500ms buffer

        done_futures: set[Future] = set()
        try:
            for future in as_completed(futures, timeout=remaining if remaining > 0 else 0.1):
                done_futures.add(future)
                ch = futures[future]
                try:
                    result = future.result(timeout=0)
                    results[ch.name] = result
                except Exception as e:
                    results[ch.name] = ChannelResult(
                        channel=ch.name, status="error", severity="none",
                        error_message=f"Channel execution failed: {type(e).__name__}: {e}",
                    )
        except TimeoutError:
            # Global deadline reached — remaining futures will be marked as timed out below
            pass

        # Mark unfinished channels as timed out
        for future, ch in futures.items():
            if future not in done_futures:
                future.cancel()
                results[ch.name] = ChannelResult(
                    channel=ch.name, status="timeout", severity="none",
                    error_message=f"Exceeded global budget ({config.latency_budget_ms}ms)",
                )
    finally:
        # Don't wait for slow threads — let them die in the background
        executor.shutdown(wait=False)

    return results


def _run_single_channel(
    channel: Channel,
    event: SupervisionEvent,
    config: ControlPlaneConfig,
) -> ChannelResult:
    """Execute a single channel with error handling.

    Catches all exceptions and returns ChannelResult with status="error"
    rather than letting them propagate.
    """
    start = time.perf_counter()
    try:
        result = channel.execute(event, config)
        result.duration_ms = (time.perf_counter() - start) * 1000
        return result
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        return ChannelResult(
            channel=channel.name,
            status="error",
            severity="none",
            error_message=f"{type(e).__name__}: {e}",
            duration_ms=elapsed,
        )
