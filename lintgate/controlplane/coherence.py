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


def compute_coherence(
    channel_results: list[ChannelResult],
    *,
    severity_weighted: bool = False,
    files_changed: list[str] | None = None,
) -> CoherenceResult:
    """Compute cross-channel coherence from channel results.

    Args:
        channel_results: Results from all channels (including skipped).
        severity_weighted: When True, channel failures are weighted by their
            highest-severity finding when evaluating the systemic threshold.
            Also demotes info-only failed channels from coherence classification.
            Informational-only failures count 0.25, warning-only 0.5,
            blocking 1.0. Default True (flipped in v2).
        files_changed: Files from the edit event. When provided, enables
            edit-scope classification (edit-related vs ambient channels).

    Returns:
        CoherenceResult with state, summary, and recommended action.
    """
    result = _compute_base_coherence(channel_results, severity_weighted=severity_weighted)

    # Apply edit-scope overlay when files_changed is available
    if files_changed:
        result = _apply_edit_scope(result, channel_results, files_changed)

    return result


def _compute_base_coherence(
    channel_results: list[ChannelResult],
    *,
    severity_weighted: bool = False,
) -> CoherenceResult:
    """Compute base coherence state without edit-scope overlay."""
    # Partition results by status
    enabled = [r for r in channel_results if r.status != "skip"]
    failed = [r for r in enabled if r.status == "fail"]
    passed = [r for r in enabled if r.status == "pass"]
    errored = [r for r in enabled if r.status in ("error", "timeout")]

    # Severity-aware demotion: info-only failed channels count as passing
    # for coherence classification. Only demote status=="fail", never error/timeout.
    demoted_notes: list[str] = []
    if severity_weighted and failed:
        actionable_failed = []
        for r in failed:
            if _has_actionable_findings(r):
                actionable_failed.append(r)
            else:
                # Demote: treat as effectively passing for coherence
                passed.append(r)
                info_count = len(r.findings)
                demoted_notes.append(
                    f"{r.channel} demoted: {info_count} informational finding"
                    f"{'s' if info_count != 1 else ''}, 0 actionable"
                )
        failed = actionable_failed

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
            classification_notes=demoted_notes,
        )

    # Rule 5: degraded — check before failure rules
    # Any channel error/timeout is a system health concern
    if errored:
        errored_names = [r.channel for r in errored]
        notes: list[str] = demoted_notes + [f"{len(errored_names)} channel(s) errored/timed out"]
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

    # Rule 1: stable — all channels pass (includes demoted info-only channels)
    if not failed:
        summary = "All channels clean."
        if demoted_notes:
            summary = "All actionable channels clean."
        return CoherenceResult(
            state="stable",
            summary=summary,
            recommended_action="Continue.",
            silent_channels=silent,
            loud_channels=[],
            confidence=1.0,
            classification_notes=demoted_notes,
        )

    # Rule 2: isolated — exactly one failure
    if len(failed) == 1:
        return _classify_isolated_failure(failed, passed, demoted_notes, loud, silent)

    # Rule 4: systemic — three+ failures or cross-domain failure
    systemic = _classify_systemic_failure(
        failed, loud, silent, demoted_notes, severity_weighted,
    )
    if systemic is not None:
        return systemic

    # Rule 3: coupled — two+ failures, check for file overlap
    if len(failed) >= 2:
        return _classify_coupled_failure(failed, loud, silent, demoted_notes)

    # Fallback (shouldn't reach here, but defensive)
    return CoherenceResult(
        state="coupled",
        summary=f"Channels reporting issues: {', '.join(loud)}.",
        recommended_action="Address blocking issues first.",
        silent_channels=silent,
        loud_channels=loud,
        confidence=0.5,
        classification_notes=demoted_notes
        + ["fallback classification — rule matching was inconclusive"],
    )


def _classify_isolated_failure(
    failed: list[ChannelResult],
    passed: list[ChannelResult],
    demoted_notes: list[str],
    loud: list[str],
    silent: list[str],
) -> CoherenceResult:
    """Classify single-channel failure as isolated (high or low confidence).

    High confidence: 1 failure with >=2 passes (Monty Hall state — silence
    in other channels concentrates attention on the single failing one).
    Low confidence: 1 failure with <2 passes (limited corroboration).
    """
    failing_channel = failed[0].channel

    # High confidence: >=2 passing channels corroborate isolation
    if len(passed) >= 2:
        conf = min(1.0, 0.7 + 0.1 * len(passed))
        ch_summary = _channel_finding_summary(failed[0])
        return CoherenceResult(
            state="isolated",
            summary=(
                f"Issue isolated to {failing_channel}. "
                f"{', '.join(silent)} confirm no problems in their domains."
            ),
            recommended_action=f"Focus on {failing_channel}: {ch_summary}.",
            silent_channels=silent,
            loud_channels=loud,
            confidence=round(conf, 2),
            classification_notes=demoted_notes,
        )

    # Low confidence: <2 passing channels
    low_confidence_notes: list[str] = list(demoted_notes)
    if passed:
        conf = 0.5 + 0.1 * len(passed)
        summary = (
            f"Issue isolated to {failing_channel}, but only "
            f"{', '.join(silent)} passed; confidence is limited."
        )
        low_confidence_notes.append(f"only {len(passed)} corroborating pass(es)")
    else:
        conf = 0.3
        summary = (
            f"Issue reported by {failing_channel}. "
            "No channels passed, so exclusion confidence is limited."
        )
        low_confidence_notes.append("no corroborating passes — isolation is assumed, not confirmed")
    return CoherenceResult(
        state="isolated",
        summary=summary,
        recommended_action=(
            f"Focus on {failing_channel} findings first, then rerun to gather corroborating pass signals."
        ),
        silent_channels=silent,
        loud_channels=loud,
        confidence=round(min(conf, 1.0), 2),
        classification_notes=low_confidence_notes,
    )


def _classify_systemic_failure(
    failed: list[ChannelResult],
    loud: list[str],
    silent: list[str],
    demoted_notes: list[str],
    severity_weighted: bool,
) -> CoherenceResult | None:
    """Classify as systemic if 3+ failures or cross-domain failure.

    Returns None if the systemic rule does not apply, allowing the caller
    to fall through to coupled classification.
    """
    effective_failure_count = (
        _effective_failure_count(failed) if severity_weighted else float(len(failed))
    )
    has_cross_domain_failure = (
        _is_cross_domain_failure(failed, effective_failure_count)
        if severity_weighted
        else _is_cross_domain_failure(failed)
    )
    if not (effective_failure_count >= 3.0 or has_cross_domain_failure):
        return None

    systemic_notes: list[str] = list(demoted_notes)
    if effective_failure_count >= 3.0:
        conf = 0.9
        if severity_weighted:
            systemic_notes.append(
                f"severity-weighted failure score={effective_failure_count:.2f} (>=3.0)"
            )
    else:
        # Cross-domain with only 2 failures — less certain it's truly systemic
        conf = 0.7
        systemic_notes.append(
            "cross-domain failure (infra + code) with only 2 channels — could be coincidental"
        )
        if severity_weighted:
            systemic_notes.append(f"severity-weighted failure score={effective_failure_count:.2f}")
    # Build per-channel summaries for actionable guidance
    channel_details = []
    for r in failed:
        channel_details.append(f"{r.channel}: {_channel_finding_summary(r)}")
    details_str = "; ".join(channel_details)
    return CoherenceResult(
        state="systemic",
        summary=(
            f"Multiple system failures: {', '.join(loud)}. "
            f"This suggests a structural problem, not isolated issues."
        ),
        recommended_action=f"Review overall approach. Failing channels: {details_str}.",
        silent_channels=silent,
        loud_channels=loud,
        confidence=round(conf, 2),
        classification_notes=systemic_notes,
    )


def _classify_coupled_failure(
    failed: list[ChannelResult],
    loud: list[str],
    silent: list[str],
    demoted_notes: list[str],
) -> CoherenceResult:
    """Classify 2+ channel failures as coupled (with or without shared files)."""
    shared_files = _find_shared_files(failed)
    if shared_files:
        file_list = ", ".join(sorted(shared_files)[:3])
        channel_counts = {r.channel: len(r.findings) for r in failed}
        counts_str = ", ".join(f"{ch}:{n}" for ch, n in channel_counts.items())
        if len(loud) == 2:
            summary = (
                f"{', '.join(loud)} both report issues in {file_list}. "
                f"These failures are likely related."
            )
            action = f"Address shared files first: {file_list}. Finding counts: {counts_str}."
        else:
            ordered = _ordered_failed_channels(failed)
            summary = (
                f"{', '.join(loud)} report overlapping issues in {file_list}. "
                f"These failures are likely related."
            )
            action = (
                f"Address shared files first: {file_list} ({counts_str}), "
                f"then resolve channel-specific findings in {', '.join(ordered)}."
            )
        return CoherenceResult(
            state="coupled",
            summary=summary,
            recommended_action=action,
            silent_channels=silent,
            loud_channels=loud,
            confidence=0.85,
            classification_notes=demoted_notes,
        )

    # Two failures, no file overlap — still coupled but independent
    ordered = _ordered_failed_channels(failed)
    channel_counts = {r.channel: len(r.findings) for r in failed}
    if len(ordered) == 2:
        action = (
            f"Address {ordered[0]} first ({channel_counts.get(ordered[0], 0)} findings), "
            f"then {ordered[1]} ({channel_counts.get(ordered[1], 0)} findings)."
        )
    else:
        parts = [f"{ch} ({channel_counts.get(ch, 0)})" for ch in ordered]
        action = f"Address channels in order: {', '.join(parts)}."
    return CoherenceResult(
        state="coupled",
        summary=f"{', '.join(loud)} report independent issues.",
        recommended_action=action,
        silent_channels=silent,
        loud_channels=loud,
        confidence=0.7,
        classification_notes=demoted_notes
        + [
            "no shared files between failing channels — "
            "classified as coupled but failures may be independent"
        ],
    )


def compute_coherence_with_history(
    channel_results: list[ChannelResult],
    session: SessionMemory | None = None,
    *,
    severity_weighted: bool = False,
    files_changed: list[str] | None = None,
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
        severity_weighted: Forward to compute_coherence().
        files_changed: Forward to compute_coherence() for edit-scope classification.

    Returns:
        CoherenceResult with enriched summary/action if history available.
    """
    base = compute_coherence(
        channel_results,
        severity_weighted=severity_weighted,
        files_changed=files_changed,
    )

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
        edit_scoped=base.edit_scoped,
        edit_related_channels=base.edit_related_channels,
        ambient_channels=base.ambient_channels,
        unknown_scope_channels=base.unknown_scope_channels,
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


def _has_actionable_findings(result: ChannelResult) -> bool:
    """Check if a channel result has any blocking or warning findings.

    Used for severity-aware demotion: channels with ONLY informational
    findings are demoted from coherence classification (they don't drive
    the coherence state). Only applies to status=="fail" channels.
    """
    for finding in result.findings:
        if getattr(finding, "severity", "") in ("blocking", "warning"):
            return True
    # Also check channel-level severity as fallback
    return result.severity in ("blocking", "warning")


# ── Edit-scope classification ───────────────────────────────────────


# Security-critical rule IDs — ambient findings with these never downgrade to stable
_SECURITY_RULE_KEYWORDS = frozenset(
    {"secret", "sensitive", "credential", "token", "private_key", "api_key"}
)


def _apply_edit_scope(
    result: CoherenceResult,
    channel_results: list[ChannelResult],
    files_changed: list[str],
) -> CoherenceResult:
    """Apply edit-scope overlay to a coherence result.

    Classifies each failing channel's findings as edit-related, ambient, or
    unknown-scope. If all failures are ambient (unrelated to the edit), the
    coherence state may be downgraded. This prevents noise from pre-existing
    codebase debt overwhelming the signal from the actual edit.

    Only applies to non-stable, non-degraded states with actual failures.
    """
    if result.state in ("stable", "degraded"):
        return result
    if not result.loud_channels:
        return result

    # Get the actual failing channel results
    failing_results = [
        cr for cr in channel_results if cr.channel in result.loud_channels and cr.status == "fail"
    ]
    if not failing_results:
        return result

    edit_related, ambient, unknown_scope = _classify_edit_scope(
        failing_results,
        files_changed,
    )

    # Determine if we should downgrade
    # unknown_scope counts as edit-related for conservative downgrade decisions
    all_ambient = not edit_related and not unknown_scope and ambient

    if all_ambient:
        # Check for ambient blocking/security-critical findings
        has_ambient_critical = _has_ambient_critical_findings(failing_results, ambient)
        if has_ambient_critical:
            # Don't fully downgrade — preserve as isolated with note
            return CoherenceResult(
                state="isolated",
                summary=(
                    f"Edit clean, but {len(ambient)} channel(s) have "
                    f"pre-existing critical findings: {', '.join(ambient)}."
                ),
                recommended_action=(
                    f"Your edit is fine. Address critical ambient debt in {', '.join(ambient)} when convenient."
                ),
                silent_channels=result.silent_channels,
                loud_channels=result.loud_channels,
                confidence=round(min(result.confidence, 0.8), 2),
                classification_notes=result.classification_notes
                + [
                    "all failures ambient but contain blocking/security findings — downgraded to isolated, not stable"
                ],
                edit_scoped=True,
                edit_related_channels=edit_related,
                ambient_channels=ambient,
                unknown_scope_channels=unknown_scope,
            )
        else:
            # Safe to downgrade to stable
            return CoherenceResult(
                state="stable",
                summary=(
                    f"Edit clean. {len(ambient)} channel(s) have pre-existing "
                    f"findings unrelated to your change: {', '.join(ambient)}."
                ),
                recommended_action="Continue. Address ambient findings when convenient.",
                silent_channels=result.silent_channels,
                # Stable state should not carry loud channels.
                loud_channels=[],
                confidence=round(min(result.confidence, 0.85), 2),
                classification_notes=result.classification_notes
                + [f"all {len(ambient)} failing channel(s) are ambient — downgraded to stable"],
                edit_scoped=True,
                edit_related_channels=edit_related,
                ambient_channels=ambient,
                unknown_scope_channels=unknown_scope,
            )

    # Mixed: some edit-related, some ambient — keep original state but annotate
    if ambient:
        ambient_note = (
            f"Note: {', '.join(ambient)} findings are pre-existing and unrelated to your edit."
        )
        return CoherenceResult(
            state=result.state,
            summary=result.summary,
            recommended_action=f"{result.recommended_action} {ambient_note}",
            silent_channels=result.silent_channels,
            loud_channels=result.loud_channels,
            confidence=result.confidence,
            classification_notes=result.classification_notes,
            edit_scoped=True,
            edit_related_channels=edit_related,
            ambient_channels=ambient,
            unknown_scope_channels=unknown_scope,
        )

    # All edit-related or unknown — return with scope annotations only
    return CoherenceResult(
        state=result.state,
        summary=result.summary,
        recommended_action=result.recommended_action,
        silent_channels=result.silent_channels,
        loud_channels=result.loud_channels,
        confidence=result.confidence,
        classification_notes=result.classification_notes,
        edit_scoped=True,
        edit_related_channels=edit_related,
        ambient_channels=ambient,
        unknown_scope_channels=unknown_scope,
    )


def _classify_edit_scope(
    failing_results: list[ChannelResult],
    files_changed: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Classify failing channels as edit-related, ambient, or unknown-scope.

    Path matching strategy (in order of precedence):
    1. Normalized absolute path match (primary)
    2. Basename match (fallback for relative-path findings)

    Findings with no file evidence go into unknown_scope.

    Returns:
        (edit_related, ambient, unknown_scope) channel name lists.
    """
    import os

    # Normalize changed file paths for matching
    changed_abs = set()
    changed_basenames = set()
    for fp in files_changed:
        norm = os.path.normpath(os.path.abspath(fp))
        changed_abs.add(norm)
        changed_basenames.add(os.path.basename(norm))

    edit_related: list[str] = []
    ambient: list[str] = []
    unknown_scope: list[str] = []

    for cr in failing_results:
        if not cr.findings:
            # No findings but status=="fail" — unknown scope
            unknown_scope.append(cr.channel)
            continue

        has_file_evidence = False
        touches_changed = False

        for finding in cr.findings:
            fpath = getattr(finding, "file", None) or ""
            if not fpath:
                continue
            has_file_evidence = True

            # Primary: absolute path match
            norm_finding = os.path.normpath(os.path.abspath(fpath))
            if norm_finding in changed_abs:
                touches_changed = True
                break

            # Fallback: basename match
            if os.path.basename(norm_finding) in changed_basenames:
                touches_changed = True
                break

        if not has_file_evidence:
            unknown_scope.append(cr.channel)
        elif touches_changed:
            edit_related.append(cr.channel)
        else:
            ambient.append(cr.channel)

    return edit_related, ambient, unknown_scope


def _has_ambient_critical_findings(
    failing_results: list[ChannelResult],
    ambient_channels: list[str],
) -> bool:
    """Check if any ambient channel has blocking or security-critical findings."""
    ambient_set = set(ambient_channels)
    for cr in failing_results:
        if cr.channel not in ambient_set:
            continue
        for finding in cr.findings:
            # Blocking severity is always critical
            if getattr(finding, "severity", "") == "blocking":
                return True
            # Security-critical rule IDs
            rule_id = (getattr(finding, "kind", "") or "").lower()
            if any(kw in rule_id for kw in _SECURITY_RULE_KEYWORDS):
                return True
    return False


def _top_finding_kind(result: ChannelResult) -> str:
    """Return the most common finding kind for a channel result.

    Uses Counter on finding.kind to identify the dominant issue type.
    Returns empty string if no findings.
    """
    from collections import Counter

    kinds = [getattr(f, "kind", "") or "unknown" for f in result.findings]
    if not kinds:
        return ""
    most_common = Counter(kinds).most_common(1)
    return most_common[0][0] if most_common else ""


def _channel_finding_summary(result: ChannelResult) -> str:
    """Build a compact summary string for a channel: 'N findings (top: kind)'."""
    count = len(result.findings)
    if count == 0:
        return "0 findings"
    top = _top_finding_kind(result)
    return (
        f"{count} finding{'s' if count != 1 else ''} (top: {top})"
        if top
        else f"{count} finding{'s' if count != 1 else ''}"
    )


_SEVERITY_WEIGHT = {"blocking": 1.0, "warning": 0.55, "informational": 0.25, "none": 0.0}

# Severity count weights for volume-aware scoring.
_BLOCKING_COUNT_WEIGHT = 1.0
_WARNING_COUNT_WEIGHT = 0.35
_INFO_COUNT_WEIGHT = 0.10

# Cap per-channel contribution so one noisy channel doesn't dominate globally.
_MAX_CHANNEL_FAILURE_WEIGHT = 2.0


def _channel_failure_weight(result: ChannelResult) -> float:
    """Compute severity-and-volume weighted contribution for one failed channel."""
    counts = _finding_severity_counts(result)
    base_score = (
        counts["blocking"] * _BLOCKING_COUNT_WEIGHT
        + counts["warning"] * _WARNING_COUNT_WEIGHT
        + counts["informational"] * _INFO_COUNT_WEIGHT
    )
    if base_score > 0:
        return min(_MAX_CHANNEL_FAILURE_WEIGHT, base_score)
    return _SEVERITY_WEIGHT.get(result.severity, 0.5)


def _finding_severity_counts(result: ChannelResult) -> dict[str, int]:
    """Count findings by severity with robust fallback to channel-level severity."""
    counts: dict[str, int] = {
        "blocking": 0,
        "warning": 0,
        "informational": 0,
    }
    for finding in result.findings:
        sev = finding.severity if finding.severity in counts else "informational"
        counts[sev] += 1

    if sum(counts.values()) == 0 and result.status == "fail":
        if result.severity in counts:
            counts[result.severity] = 1
        elif result.severity != "none":
            counts["warning"] = 1
    return counts


def _effective_failure_count(failed_results: list[ChannelResult]) -> float:
    """Compute severity-weighted failure count.

    Each failed channel contributes a capped weighted score from its finding mix:
    - blocking findings: 1.0 each
    - warning findings: 0.35 each
    - informational findings: 0.10 each
    - per-channel cap: 2.0

    This distinguishes one-channel "debt" from broad systemic failures:
    three channels with only one warning each produce 1.05 total (coupled),
    while channels with many blockers cross systemic threshold quickly.
    """
    return sum(_channel_failure_weight(result) for result in failed_results)


def _ordered_failed_channels(failed_results: list[ChannelResult]) -> list[str]:
    """Return failing channels sorted by severity weight (highest first)."""
    weighted = [(result.channel, _channel_failure_weight(result)) for result in failed_results]
    weighted.sort(key=lambda item: (-item[1], item[0]))
    return [name for name, _weight in weighted]


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


def _is_cross_domain_failure(
    failed_results: list[ChannelResult],
    effective_failure_count: float | None = None,
) -> bool:
    """Check for cross-domain failure pattern.

    Cross-domain: infrastructure channels (deps, git) + code channels
    (lint, tests, structure) both failing suggests a deeper structural issue.

    When effective_failure_count is provided, require a minimum weighted
    signal to avoid classifying low-severity cross-domain noise as systemic.
    """
    infra_channels = {"deps", "git"}
    code_channels = {"lint", "tests", "structure"}

    failed_names = {r.channel for r in failed_results}
    has_infra_failure = bool(failed_names & infra_channels)
    has_code_failure = bool(failed_names & code_channels)
    if not (has_infra_failure and has_code_failure):
        return False
    if effective_failure_count is None:
        return True
    return effective_failure_count >= 1.25
