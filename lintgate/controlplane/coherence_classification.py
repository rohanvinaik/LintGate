"""Coherence classification rules for ControlPlane."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .coherence_scoring import (
    channel_finding_summary,
    effective_failure_count,
    find_shared_files,
    is_cross_domain_failure,
    ordered_failed_channels,
)
from .types import CoherenceResult

if TYPE_CHECKING:
    from .types import ChannelResult


def classify_isolated_failure(
    failed: list[ChannelResult],
    passed: list[ChannelResult],
    demoted_notes: list[str],
    loud: list[str],
    silent: list[str],
) -> CoherenceResult:
    """Classify single-channel failure as isolated (high or low confidence)."""
    failing_channel = failed[0].channel

    # High confidence: >=2 passing channels corroborate isolation
    if len(passed) >= 2:
        conf = min(1.0, 0.7 + 0.1 * len(passed))
        ch_summary = channel_finding_summary(failed[0])
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


def classify_systemic_failure(
    failed: list[ChannelResult],
    loud: list[str],
    silent: list[str],
    demoted_notes: list[str],
    severity_weighted: bool,
    channel_weights: dict[str, float] | None = None,
) -> CoherenceResult | None:
    """Classify as systemic if 3+ failures or cross-domain failure."""
    eff_count = (
        effective_failure_count(failed, channel_weights)
        if severity_weighted
        else float(len(failed))
    )
    has_cross_domain = (
        is_cross_domain_failure(failed, eff_count)
        if severity_weighted
        else is_cross_domain_failure(failed)
    )
    if not (eff_count >= 3.0 or has_cross_domain):
        return None

    systemic_notes: list[str] = list(demoted_notes)
    if eff_count >= 3.0:
        conf = 0.9
        if severity_weighted:
            systemic_notes.append(f"severity-weighted failure score={eff_count:.2f} (>=3.0)")
    else:
        # Cross-domain with only 2 failures — less certain it's truly systemic
        conf = 0.7
        systemic_notes.append(
            "cross-domain failure (infra + code) with only 2 channels — could be coincidental"
        )
        if severity_weighted:
            systemic_notes.append(f"severity-weighted failure score={eff_count:.2f}")

    channel_details = []
    for r in failed:
        channel_details.append(f"{r.channel}: {channel_finding_summary(r)}")
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


def classify_coupled_failure(
    failed: list[ChannelResult],
    loud: list[str],
    silent: list[str],
    demoted_notes: list[str],
) -> CoherenceResult:
    """Classify 2+ channel failures as coupled (with or without shared files)."""
    shared_files = find_shared_files(failed)
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
            ordered = ordered_failed_channels(failed)
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
    ordered = ordered_failed_channels(failed)
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
