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

from typing import TYPE_CHECKING, Any

from ..types import ChannelResult, CoherenceResult
from .classification import (
    classify_coupled_failure,
    classify_isolated_failure,
    classify_systemic_failure,
)
from .history import (
    detect_persistent_loud,
    detect_refactoring_tradeoffs,
    detect_resolutions,
    state_severity,
)
from .scope import (
    apply_edit_scope,
    classify_edit_scope,
    has_ambient_critical_findings,
)
from .scoring import (
    channel_failure_weight,
    channel_finding_summary,
    effective_failure_count,
    find_shared_files,
    finding_severity_counts,
    is_cross_domain_failure,
    ordered_failed_channels,
    top_finding_kind,
)

if TYPE_CHECKING:
    from ..session_memory import SessionMemory

# Backward-compat aliases for tests and internal callers that still import
# private helper names from this module.
_apply_edit_scope = apply_edit_scope
_classify_coupled_failure = classify_coupled_failure
_classify_edit_scope = classify_edit_scope
_classify_isolated_failure = classify_isolated_failure
_classify_systemic_failure = classify_systemic_failure
_detect_persistent_loud = detect_persistent_loud
_detect_refactoring_tradeoffs = detect_refactoring_tradeoffs
_detect_resolutions = detect_resolutions
_channel_failure_weight = channel_failure_weight
_channel_finding_summary = channel_finding_summary
_effective_failure_count = effective_failure_count
_find_shared_files = find_shared_files
_finding_severity_counts = finding_severity_counts
_has_ambient_critical_findings = has_ambient_critical_findings
_is_cross_domain_failure = is_cross_domain_failure
_ordered_failed_channels = ordered_failed_channels
_top_finding_kind = top_finding_kind


def compute_coherence(
    channel_results: list[ChannelResult],
    *,
    severity_weighted: bool = False,
    channel_weights: dict[str, float] | None = None,
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
        channel_weights: Optional per-channel importance weights. When provided,
            each channel's failure score is scaled by its weight (default 0.5
            for unconfigured channels). None = all channels equal weight.
        files_changed: Files from the edit event. When provided, enables
            edit-scope classification (edit-related vs ambient channels).

    Returns:
        CoherenceResult with state, summary, and recommended action.
    """
    result = _compute_base_coherence(
        channel_results,
        severity_weighted=severity_weighted,
        channel_weights=channel_weights,
    )

    # Apply edit-scope overlay when files_changed is available
    if files_changed:
        result = apply_edit_scope(result, channel_results, files_changed)

    # Populate classification_reason from computed state
    if not result.classification_reason:
        result.classification_reason = _build_classification_reason(result)

    return result


def _compute_base_coherence(
    channel_results: list[ChannelResult],
    *,
    severity_weighted: bool = False,
    channel_weights: dict[str, float] | None = None,
) -> CoherenceResult:
    """Compute base coherence state without edit-scope overlay."""
    # Partition and handle demotion
    partition = _partition_results(channel_results, severity_weighted)
    enabled = partition["enabled"]
    failed = partition["failed"]
    passed = partition["passed"]
    errored = partition["errored"]
    demoted_notes = partition["demoted_notes"]

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
    if errored:
        return _handle_degraded_state(errored, failed, silent, loud, demoted_notes, enabled)

    # Rule 1: stable — all channels pass (includes demoted info-only channels)
    if not failed:
        return _handle_stable_state(demoted_notes, silent)

    # Rule 2: isolated — exactly one failure
    if len(failed) == 1:
        return classify_isolated_failure(failed, passed, demoted_notes, loud, silent)

    # Rule 4: systemic — three+ failures or cross-domain failure
    systemic = classify_systemic_failure(
        failed,
        loud,
        silent,
        demoted_notes,
        severity_weighted,
        channel_weights,
    )
    if systemic is not None:
        return systemic

    # Rule 3: coupled — two+ failures, check for file overlap
    return classify_coupled_failure(failed, loud, silent, demoted_notes)


def _partition_results(
    channel_results: list[ChannelResult], severity_weighted: bool
) -> dict[str, Any]:
    """Partition channel results by status and handle severity-aware demotion."""
    enabled = [r for r in channel_results if r.status != "skip"]
    failed = [r for r in enabled if r.status == "fail"]
    passed = [r for r in enabled if r.status == "pass"]
    errored = [r for r in enabled if r.status in ("error", "timeout")]

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

    return {
        "enabled": enabled,
        "failed": failed,
        "passed": passed,
        "errored": errored,
        "demoted_notes": demoted_notes,
    }


def _handle_stable_state(demoted_notes: list[str], silent: list[str]) -> CoherenceResult:
    """Handle Rule 1: stable state."""
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


def _handle_degraded_state(
    errored: list[ChannelResult],
    failed: list[ChannelResult],
    silent: list[str],
    loud: list[str],
    demoted_notes: list[str],
    enabled: list[ChannelResult],
) -> CoherenceResult:
    """Handle the degraded state when channels error or timeout."""
    errored_names = [r.channel for r in errored]
    notes: list[str] = demoted_notes + [f"{len(errored_names)} channel(s) errored/timed out"]
    if failed:
        notes.append(f"also {len(failed)} channel(s) failed — failures may be masked by errors")

    return CoherenceResult(
        state="degraded",
        summary=(
            f"Channel{'s' if len(errored_names) > 1 else ''} "
            f"{', '.join(errored_names)} "
            "errored/timed out. "
            f"Results may be incomplete."
        ),
        recommended_action=(
            f"Check {errored_names[0]} channel health. "
            f"Available results from: {', '.join(r.channel for r in enabled if r.status not in ('error', 'timeout'))}."
        ),
        silent_channels=silent,
        loud_channels=loud,
        confidence=0.9,
        classification_notes=notes,
    )


# ── Internal aliases for backward compatibility with tests ───────────

_classify_coupled_failure = classify_coupled_failure
_classify_isolated_failure = classify_isolated_failure
_classify_systemic_failure = classify_systemic_failure
_apply_edit_scope = apply_edit_scope


_state_severity = state_severity
_detect_persistent_loud = detect_persistent_loud
_detect_refactoring_tradeoffs = detect_refactoring_tradeoffs
_detect_resolutions = detect_resolutions


def compute_coherence_with_history(
    channel_results: list[ChannelResult],
    session: SessionMemory | None = None,
    *,
    severity_weighted: bool = False,
    channel_weights: dict[str, float] | None = None,
    files_changed: list[str] | None = None,
) -> CoherenceResult:
    """Compute coherence with trajectory-aware annotations from session history.

    Calls compute_coherence() first, then enriches with history-based context:
    1. REGRESSION: coherence state worsened from previous run
    2. PERSISTENT: same channel loud 3+ consecutive runs -> escalate wording
    3. RESOLUTION: previously-loud channel now silent -> note resolution
    4. NO_DATA: test channel passes but has no test files (bootstrap needed)

    Only modifies summary and recommended_action text. Never changes the
    state enum — the base coherence engine is authoritative for that.

    Args:
        channel_results: Results from all channels.
        session: Optional session memory for history. If None, behaves
                 identically to compute_coherence().
        severity_weighted: Forward to compute_coherence().
        channel_weights: Per-channel importance weights (None = disabled).
        files_changed: Forward to compute_coherence() for edit-scope classification.

    Returns:
        CoherenceResult with enriched summary/action if history available.
    """
    base = compute_coherence(
        channel_results,
        severity_weighted=severity_weighted,
        channel_weights=channel_weights,
        files_changed=files_changed,
    )

    # Bootstrap-aware "no data" annotation — applies even without session history
    bootstrap_notes = _detect_bootstrap_needed(channel_results)
    if bootstrap_notes and not (session and session.snapshots):
        # No session history but bootstrap annotation needed
        notes = list(base.classification_notes) + bootstrap_notes
        return CoherenceResult(
            state=base.state,
            summary=base.summary,
            recommended_action=base.recommended_action,
            silent_channels=base.silent_channels,
            loud_channels=base.loud_channels,
            confidence=base.confidence,
            classification_notes=notes,
            edit_scoped=base.edit_scoped,
            edit_related_channels=base.edit_related_channels,
            ambient_channels=base.ambient_channels,
            unknown_scope_channels=base.unknown_scope_channels,
        )

    if session is None or not session.snapshots:
        return base

    annotations: list[str] = []
    if bootstrap_notes:
        annotations.extend(bootstrap_notes)

    # 1. REGRESSION / IMPROVEMENT detection: state change from last run
    if session.coherence_trajectory:
        prev_state = session.coherence_trajectory[-1]
        if state_severity(base.state) > state_severity(prev_state):
            annotations.append(
                f"REGRESSION: coherence degraded from {prev_state} \u2192 {base.state}"
            )
        elif state_severity(base.state) < state_severity(prev_state):
            annotations.append(
                f"IMPROVEMENT: coherence improved from {prev_state} \u2192 {base.state}"
            )

    # 2. PERSISTENT detection: same channel loud 3+ consecutive runs
    persistent_channels = detect_persistent_loud(session, base.loud_channels)
    for ch_name, streak in persistent_channels:
        annotations.append(f"PERSISTENT: {ch_name} has been failing for {streak} consecutive runs")

    # 3. RESOLUTION detection: previously-loud channel now silent
    resolved = detect_resolutions(session, base.silent_channels)
    for ch_name in resolved:
        annotations.append(f"RESOLVED: {ch_name} is now passing")

    # 4. TRADEOFF detection: refactoring tradeoff patterns (e.g. CC down, args up)
    tradeoffs = detect_refactoring_tradeoffs(channel_results, session)
    for t in tradeoffs:
        annotations.append(
            f"TRADEOFF: {t['improved']} improved by {abs(float(str(t['improved_delta'])))}, "
            f"but {t['regressed']} increased by {t['regressed_delta']}"
        )

    if not annotations:
        return base

    # Enrich the summary and recommended_action with annotations
    annotation_text = "; ".join(annotations)
    enriched_summary = f"{base.summary} [{annotation_text}]"
    enriched_action = base.recommended_action
    if persistent_channels:
        ch_names = [ch for ch, _ in persistent_channels]
        enriched_action += (
            f" Persistent issues in {', '.join(ch_names)} \u2014 consider a different approach."
        )
    if any("IMPROVEMENT" in a for a in annotations):
        enriched_action += " Progress detected \u2014 continue current approach."

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


def _detect_bootstrap_needed(channel_results: list[ChannelResult]) -> list[str]:
    """Check if the test channel signals bootstrap_needed (zero test files).

    When the test channel passes but has no test files, "tests pass" is
    misleading — it means "no tests exist to fail." This annotation ensures
    the coherence engine does not treat silence as health.

    Returns:
        List of annotation strings (empty if no bootstrap signal detected).
    """
    notes: list[str] = []
    for cr in channel_results:
        if cr.channel != "tests":
            continue
        metrics = cr.metrics if isinstance(cr.metrics, dict) else {}
        if metrics.get("bootstrap_needed"):
            bootstrap_status = "available"
            # Check if bootstrap is already running
            try:
                from lintgate.orchestration.bootstrap_state import BootstrapState

                state = BootstrapState.load(metrics.get("project_root", ""))
                if state.status == "running":
                    bootstrap_status = f"running (phase: {state.phase})"
                elif state.status == "complete":
                    bootstrap_status = "complete"
            except Exception:
                pass

            notes.append(
                f"NO_DATA: tests channel passes with caveat \u2014 no test files exist. "
                f"Bootstrap pipeline {bootstrap_status}."
            )
            break
    return notes


def _build_classification_reason(result: CoherenceResult) -> str:
    """Build a human-readable reason for the coherence classification."""
    loud_count = len(result.loud_channels)
    silent_count = len(result.silent_channels)

    if result.state == "stable":
        return f"All {silent_count} channel(s) passed \u2014 no failures detected."
    if result.state == "degraded":
        return "One or more channels errored or timed out \u2014 results incomplete."
    if result.state == "isolated":
        return (
            f"Single failure in {', '.join(result.loud_channels)}; "
            f"{silent_count} channel(s) passed, corroborating isolation."
        )
    if result.state == "coupled":
        return (
            f"{loud_count} channels failed with overlapping file scope: "
            f"{', '.join(result.loud_channels)}."
        )
    if result.state == "systemic":
        return f"{loud_count} channels failed across domains: {', '.join(result.loud_channels)}."
    return f"State: {result.state}, {loud_count} loud, {silent_count} silent."
