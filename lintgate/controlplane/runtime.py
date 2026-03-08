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
    CoherenceResult,
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
    session: SessionMemory | None = None,
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

    # Phase 0: Pre-pass — build shared artifacts once for all channels
    _run_prepass(event)

    # Phase 1: Filter to channels that should run
    active_channels: list[Channel] = []
    skipped_results: list[ChannelResult] = []

    for ch in channels:
        if not config.channel_enabled(ch.name):
            skipped_results.append(
                ChannelResult(
                    channel=ch.name,
                    status="skip",
                    severity="none",
                    metrics={"reason": "disabled_in_config"},
                )
            )
            continue

        try:
            if not ch.should_run(event, config):
                skipped_results.append(
                    ChannelResult(
                        channel=ch.name,
                        status="skip",
                        severity="none",
                        metrics={"reason": "event_not_relevant"},
                    )
                )
                continue
        except Exception as e:
            skipped_results.append(
                ChannelResult(
                    channel=ch.name,
                    status="error",
                    severity="none",
                    error_message=f"should_run failed: {type(e).__name__}: {e}",
                )
            )
            continue

        active_channels.append(ch)

    # Phase 2: Execute active channels in parallel
    channel_results: list[ChannelResult] = list(skipped_results)
    incomplete: list[str] = []

    if active_channels:
        results_from_exec = _execute_parallel(
            active_channels,
            event,
            config,
            global_deadline,
        )
        for ch_name, result in results_from_exec.items():
            channel_results.append(result)
            if result.status == "timeout":
                incomplete.append(ch_name)

    # Phase 2b: Collect git working tree context (#179)
    git_context = _collect_git_context(event, channel_results)

    # Phase 2c: Cross-channel coherence pass (#209)
    _run_cross_channel_coherence(channel_results)

    # Phase 3: Run coherence engine
    coherence = _compute_final_coherence(channel_results, config, event, session)

    elapsed_ms = (time.perf_counter() - start) * 1000
    return MeshResult(
        event=event,
        channel_results=channel_results,
        coherence=coherence,
        duration_ms=round(elapsed_ms, 1),
        incomplete_channels=incomplete,
        partial=len(incomplete) > 0,
        git_context=git_context,
    )


def _run_prepass(event: SupervisionEvent) -> None:
    """Phase 0: Build shared artifacts once for all channels.

    Currently builds the property manifest (expensive AST parsing) and
    stores it in ``event.context`` so performance and related channels
    can share it instead of rebuilding independently.

    When ``event.files_changed`` contains a small number of Python files
    (≤5), the prepass scopes discovery to those files only, avoiding a
    full project walk.  For larger change sets or when no files are
    specified, full canonical discovery runs.

    Gracefully degrades: if manifest build fails, channels fall back to
    building their own.
    """
    import contextlib

    if not event.project_root:
        return

    with contextlib.suppress(Exception):
        from lintgate.linters.performance_checks.manifest import build_manifest

        py_files = _scoped_discover(event)
        if py_files:
            manifest = build_manifest(event.project_root, py_files)
            event.context["property_manifest"] = manifest
            event.context["python_files"] = py_files

    with contextlib.suppress(Exception):
        from lintgate.linters.test_effectiveness.manifest import (
            build_test_effectiveness_manifest,
        )

        test_files = _discover_test_files(event.project_root)
        if test_files:
            # Reuse python_files from property manifest block, or
            # discover independently if that block failed.
            src_files = event.context.get("python_files")
            if not src_files:
                src_files = _scoped_discover(event)
            if src_files:
                teff_manifest = build_test_effectiveness_manifest(
                    event.project_root, src_files, test_files
                )
                event.context["test_effectiveness_manifest"] = teff_manifest


def _scoped_discover(event: SupervisionEvent) -> list[str]:
    """Discover Python files, scoped to files_changed when possible.

    Returns scoped file list when 1-5 .py files are in files_changed and
    all resolve to paths within the project root.  Falls back to full
    canonical discovery otherwise.
    """
    import os

    from lintgate.discovery import discover_project_files

    changed_py = [
        f for f in (event.files_changed or []) if f.endswith(".py")
    ]

    if changed_py and len(changed_py) <= 5:
        project_root = os.path.abspath(event.project_root)
        scoped: list[str] = []
        for f in changed_py:
            full = os.path.abspath(
                os.path.join(project_root, f) if not os.path.isabs(f) else f
            )
            # Sanitize: must be within project root and must exist
            if full.startswith(project_root + os.sep) and os.path.isfile(full):
                scoped.append(full)
        if scoped:
            return scoped

    return discover_project_files(event.project_root)


def _discover_test_files(project_root: str) -> list[str]:
    """Discover test files for the test effectiveness manifest."""
    from lintgate.linters.test_effectiveness.test_analyzer import (
        _discover_test_files as discover,
    )

    return discover(project_root)


def _collect_git_context(
    event: SupervisionEvent,
    channel_results: list[ChannelResult],
) -> dict:
    """Collect git working tree context and annotate findings with scope."""
    if not event.project_root:
        return {}

    from lintgate.channels.git_channel import (
        classify_finding_scope,
        collect_working_tree_context,
    )

    git_context = collect_working_tree_context(event.project_root)
    if git_context.get("modified_files") or git_context.get("untracked_files"):
        mod = git_context.get("modified_files", [])
        untracked = git_context.get("untracked_files", [])
        for cr in channel_results:
            for finding in cr.findings:
                scope = classify_finding_scope(
                    finding.file, mod, untracked, event.project_root
                )
                finding.evidence = dict(finding.evidence) if finding.evidence else {}
                finding.evidence["scope"] = scope
    return git_context


def _run_cross_channel_coherence(channel_results: list[ChannelResult]) -> None:
    """Run cross-channel coherence pass and append synthetic channel result."""
    import contextlib

    with contextlib.suppress(Exception):
        from .cross_channel import cross_channel_coherence

        coh_findings = cross_channel_coherence(channel_results)
        if coh_findings:
            sev_order = {"blocking": 3, "warning": 2, "informational": 1, "none": 0}
            has_actionable = any(
                f.severity in ("blocking", "warning") for f in coh_findings
            )
            worst_sev: str = max(
                (f.severity for f in coh_findings),
                key=lambda s: sev_order.get(s, 0),
            )
            channel_results.append(
                ChannelResult(
                    channel="coherence",
                    status="fail" if has_actionable else "pass",
                    severity=worst_sev,  # type: ignore[arg-type]
                    findings=coh_findings,
                    metrics={"cross_channel_findings": len(coh_findings)},
                    duration_ms=0,
                )
            )

        with contextlib.suppress(Exception):
            from lintgate.convergence.integration import (
                convergence_to_metrics,
                extract_all_evidence,
            )

            convergence_results = extract_all_evidence(channel_results)
            if convergence_results:
                for cr in channel_results:
                    if cr.channel == "coherence":
                        cr.metrics["convergence"] = convergence_to_metrics(
                            convergence_results
                        )
                        break

        with contextlib.suppress(Exception):
            from lintgate.convergence.integration import (
                extract_file_evidence,
                file_convergence_to_metrics,
            )

            file_conv = extract_file_evidence(channel_results)
            if file_conv:
                for cr in channel_results:
                    if cr.channel == "structure":
                        cr.metrics["file_convergence"] = file_convergence_to_metrics(
                            file_conv
                        )
                        break


def _compute_final_coherence(
    channel_results: list[ChannelResult],
    config: ControlPlaneConfig,
    event: SupervisionEvent,
    session: SessionMemory | None,
) -> CoherenceResult:
    """Run coherence engine, optionally with session history."""
    from .coherence import compute_coherence, compute_coherence_with_history

    sw = config.severity_weighted_coherence
    cw = config.coherence_channel_weights
    fc = list(event.files_changed) if event.files_changed else None
    if session is not None:
        return compute_coherence_with_history(
            channel_results,
            session,
            severity_weighted=sw,
            channel_weights=cw,
            files_changed=fc,
        )
    return compute_coherence(
        channel_results, severity_weighted=sw, channel_weights=cw, files_changed=fc
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
            for future in as_completed(
                futures, timeout=remaining if remaining > 0 else 0.1
            ):
                done_futures.add(future)
                ch = futures[future]
                try:
                    result = future.result(timeout=0)
                    results[ch.name] = result
                except Exception as e:
                    results[ch.name] = ChannelResult(
                        channel=ch.name,
                        status="error",
                        severity="none",
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
                    channel=ch.name,
                    status="timeout",
                    severity="none",
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
