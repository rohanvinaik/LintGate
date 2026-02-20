"""Cross-channel coherence engine for ControlPlane.

This is the novel piece — where ControlPlane becomes more than the sum
of its channels. The coherence engine diagnoses system state from
multi-channel signals using principles from OTP theory:

- Silent channels provide **confident exclusion** (Monty Hall effect)
- Cross-channel disagreement IS the diagnostic signal
- Isolated failures are more actionable than systemic ones

V1 is rule-based. Future versions may incorporate:
- Kuramoto-style trajectory coherence across runs
- Mutual information between channel histories
- Minority channel SNR weighting

Coherence states:
1. stable: All enabled channels pass or skip
2. isolated: Exactly one channel fails (high-confidence when >=2 others pass)
3. coupled: Two+ channels fail, findings intersect on files
4. systemic: Three+ channels fail, or cross-domain failure
5. degraded: Any channel error/timeout beyond threshold
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types import ChannelResult, CoherenceResult

if TYPE_CHECKING:
    from .session_memory import SessionMemory


def compute_coherence(channel_results: list[ChannelResult]) -> CoherenceResult:
    """Compute cross-channel coherence from channel results.

    Args:
        channel_results: Results from all channels (including skipped).

    Returns:
        CoherenceResult with state, summary, and recommended action.
    """
    # Partition results by status
    enabled = [r for r in channel_results if r.status != "skip"]
    failed = [r for r in enabled if r.status == "fail"]
    passed = [r for r in enabled if r.status == "pass"]
    errored = [r for r in enabled if r.status in ("error", "timeout")]

    silent = [r.channel for r in passed]
    loud = [r.channel for r in failed]

    # No channels ran — nothing to diagnose
    if not enabled:
        return CoherenceResult(
            state="stable",
            summary="No channels active.",
            recommended_action="Continue.",
            silent_channels=[],
            loud_channels=[],
            confidence=1.0,
        )

    # Rule 5: degraded — check before failure rules
    # Any channel error/timeout is a system health concern
    if errored:
        errored_names = [r.channel for r in errored]
        notes = [f"{len(errored_names)} channel(s) errored/timed out"]
        if failed:
            notes.append(f"also {len(failed)} channel(s) failed — failures may be masked by errors")
        return CoherenceResult(
            state="degraded",
            summary=(
                f"Channel{'s' if len(errored_names) > 1 else ''} "
                f"{', '.join(errored_names)} "
                f"{'errored/timed out' if len(errored_names) > 1 else 'errored/timed out'}. "
                f"Results may be incomplete."
            ),
            recommended_action=(
                f"Check {errored_names[0]} channel health. "
                f"Available results from: {', '.join(r.channel for r in enabled if r.status not in ('error', 'timeout'))}."
            ),
            silent_channels=silent,
            loud_channels=loud,
            confidence=0.9,  # Degraded is unambiguous but results are incomplete
            classification_notes=notes,
        )

    # Rule 1: stable — all channels pass
    if not failed:
        return CoherenceResult(
            state="stable",
            summary="All channels clean.",
            recommended_action="Continue.",
            silent_channels=silent,
            loud_channels=[],
            confidence=1.0,
        )

    # Rule 2: isolated — exactly one failure, >=2 passes
    # This is the Monty Hall state: silence in other channels
    # concentrates attention on the single failing one
    if len(failed) == 1 and len(passed) >= 2:
        failing_channel = failed[0].channel
        # More passing channels = higher confidence in isolation
        conf = min(1.0, 0.7 + 0.1 * len(passed))
        return CoherenceResult(
            state="isolated",
            summary=(
                f"Issue isolated to {failing_channel}. "
                f"{', '.join(silent)} confirm no problems in their domains."
            ),
            recommended_action=f"Focus on {failing_channel} findings.",
            silent_channels=silent,
            loud_channels=loud,
            confidence=round(conf, 2),
        )

    # Single-channel failure with limited corroboration:
    # still isolated, but with lower confidence because we don't have
    # enough passing channels to strongly exclude other domains.
    if len(failed) == 1:
        failing_channel = failed[0].channel
        notes: list[str] = []
        if passed:
            conf = 0.5 + 0.1 * len(passed)
            summary = (
                f"Issue isolated to {failing_channel}, but only "
                f"{', '.join(silent)} passed; confidence is limited."
            )
            notes.append(f"only {len(passed)} corroborating pass(es)")
        else:
            conf = 0.3
            summary = (
                f"Issue reported by {failing_channel}. "
                "No channels passed, so exclusion confidence is limited."
            )
            notes.append("no corroborating passes — isolation is assumed, not confirmed")
        return CoherenceResult(
            state="isolated",
            summary=summary,
            recommended_action=(
                f"Focus on {failing_channel} findings first, then rerun to gather corroborating pass signals."
            ),
            silent_channels=silent,
            loud_channels=loud,
            confidence=round(min(conf, 1.0), 2),
            classification_notes=notes,
        )

    # Rule 4: systemic — three+ failures or cross-domain failure
    if len(failed) >= 3 or _is_cross_domain_failure(failed):
        notes = []
        if len(failed) >= 3:
            conf = 0.9
        else:
            # Cross-domain with only 2 failures — less certain it's truly systemic
            conf = 0.7
            notes.append(
                "cross-domain failure (infra + code) with only 2 channels — could be coincidental"
            )
        return CoherenceResult(
            state="systemic",
            summary=(
                f"Multiple system failures: {', '.join(loud)}. "
                f"This suggests a structural problem, not isolated issues."
            ),
            recommended_action="Step back and review the overall approach before fixing individual issues.",
            silent_channels=silent,
            loud_channels=loud,
            confidence=round(conf, 2),
            classification_notes=notes,
        )

    # Rule 3: coupled — two+ failures, check for file overlap
    if len(failed) >= 2:
        shared_files = _find_shared_files(failed)
        if shared_files:
            file_list = ", ".join(sorted(shared_files)[:3])
            return CoherenceResult(
                state="coupled",
                summary=(
                    f"{', '.join(loud)} both report issues in {file_list}. "
                    f"These failures are likely related."
                ),
                recommended_action=f"Address the shared files first: {file_list}.",
                silent_channels=silent,
                loud_channels=loud,
                confidence=0.85,
            )

        # Two failures, no file overlap — still coupled but independent
        return CoherenceResult(
            state="coupled",
            summary=f"{', '.join(loud)} report independent issues.",
            recommended_action=f"Address {loud[0]} first (higher severity), then {loud[1]}.",
            silent_channels=silent,
            loud_channels=loud,
            confidence=0.7,
            classification_notes=[
                "no shared files between failing channels — "
                "classified as coupled but failures may be independent"
            ],
        )

    # Fallback (shouldn't reach here, but defensive)
    return CoherenceResult(
        state="coupled",
        summary=f"Channels reporting issues: {', '.join(loud)}.",
        recommended_action="Address blocking issues first.",
        silent_channels=silent,
        loud_channels=loud,
        confidence=0.5,
        classification_notes=["fallback classification — rule matching was inconclusive"],
    )


def compute_coherence_with_history(
    channel_results: list[ChannelResult],
    session: SessionMemory | None = None,
) -> CoherenceResult:
    """Compute coherence with trajectory-aware annotations from session history.

    Calls compute_coherence() first, then enriches with history-based context:
    1. REGRESSION: coherence state worsened from previous run
    2. PERSISTENT: same channel loud 3+ consecutive runs → escalate wording
    3. RESOLUTION: previously-loud channel now silent → note resolution

    Only modifies summary and recommended_action text. Never changes the
    state enum — the base coherence engine is authoritative for that.

    Args:
        channel_results: Results from all channels.
        session: Optional session memory for history. If None, behaves
                 identically to compute_coherence().

    Returns:
        CoherenceResult with enriched summary/action if history available.
    """
    base = compute_coherence(channel_results)

    if session is None or not session.snapshots:
        return base

    annotations: list[str] = []

    # 1. REGRESSION detection: state worsened from last run
    if session.coherence_trajectory:
        prev_state = session.coherence_trajectory[-1]
        if _state_severity(base.state) > _state_severity(prev_state):
            annotations.append(f"REGRESSION: coherence degraded from {prev_state} → {base.state}")

    # 2. PERSISTENT detection: same channel loud 3+ consecutive runs
    persistent_channels = _detect_persistent_loud(session, base.loud_channels)
    for ch_name, streak in persistent_channels:
        annotations.append(f"PERSISTENT: {ch_name} has been failing for {streak} consecutive runs")

    # 3. RESOLUTION detection: previously-loud channel now silent
    resolved = _detect_resolutions(session, base.silent_channels)
    for ch_name in resolved:
        annotations.append(f"RESOLVED: {ch_name} is now passing")

    if not annotations:
        return base

    # Enrich the summary and recommended_action with annotations
    annotation_text = "; ".join(annotations)
    enriched_summary = f"{base.summary} [{annotation_text}]"
    enriched_action = base.recommended_action
    if persistent_channels:
        ch_names = [ch for ch, _ in persistent_channels]
        enriched_action += (
            f" Persistent issues in {', '.join(ch_names)} — consider a different approach."
        )

    return CoherenceResult(
        state=base.state,
        summary=enriched_summary,
        recommended_action=enriched_action,
        silent_channels=base.silent_channels,
        loud_channels=base.loud_channels,
        confidence=base.confidence,
        classification_notes=base.classification_notes,
    )


# ── History helpers ──────────────────────────────────────────────────


_STATE_SEVERITY = {
    "stable": 0,
    "isolated": 1,
    "coupled": 2,
    "systemic": 3,
    "degraded": 4,
}


def _state_severity(state: str) -> int:
    """Map coherence state to a severity integer for comparison."""
    return _STATE_SEVERITY.get(state, 0)


def _detect_persistent_loud(
    session: SessionMemory,
    current_loud: list[str],
) -> list[tuple[str, int]]:
    """Detect channels that have been loud for 3+ consecutive runs.

    Returns list of (channel_name, streak_count) for persistent channels.
    """
    if not current_loud or len(session.snapshots) < 2:
        return []

    persistent: list[tuple[str, int]] = []

    for ch_name in current_loud:
        # Count consecutive recent snapshots where this channel was loud
        streak = 0
        for snap in reversed(session.snapshots):
            if ch_name in snap.loud_channels:
                streak += 1
            else:
                break

        # +1 for the current run (not yet in snapshots)
        total_streak = streak + 1

        if total_streak >= 3:
            persistent.append((ch_name, total_streak))

    return persistent


def _detect_resolutions(
    session: SessionMemory,
    current_silent: list[str],
) -> list[str]:
    """Detect channels that were loud in the last run but are now silent.

    Only reports channels that were loud in the most recent snapshot,
    to avoid noise from channels that have been silent for a while.
    """
    if not session.snapshots:
        return []

    last_snapshot = session.snapshots[-1]
    last_loud = set(last_snapshot.loud_channels)
    current_silent_set = set(current_silent)

    return sorted(last_loud & current_silent_set)


def _find_shared_files(failed_results: list[ChannelResult]) -> set[str]:
    """Find files that appear in findings across multiple failed channels."""
    file_sets: list[set[str]] = []
    for result in failed_results:
        files = {f.file for f in result.findings if f.file}
        if files:
            file_sets.append(files)

    if len(file_sets) < 2:
        return set()

    # Intersection of all file sets
    shared = file_sets[0]
    for fs in file_sets[1:]:
        shared = shared & fs

    return shared


def _is_cross_domain_failure(failed_results: list[ChannelResult]) -> bool:
    """Check for cross-domain failure pattern.

    Cross-domain: infrastructure channels (deps, git) + code channels
    (lint, tests, structure) both failing suggests a deeper structural issue.
    """
    infra_channels = {"deps", "git"}
    code_channels = {"lint", "tests", "structure"}

    failed_names = {r.channel for r in failed_results}
    has_infra_failure = bool(failed_names & infra_channels)
    has_code_failure = bool(failed_names & code_channels)

    return has_infra_failure and has_code_failure
