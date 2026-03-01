"""Scoring and weighting logic for ControlPlane coherence."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import ChannelResult


_SEVERITY_WEIGHT = {
    "blocking": 1.0,
    "warning": 0.55,
    "informational": 0.25,
    "none": 0.0,
}

# Severity count weights for volume-aware scoring.
_BLOCKING_COUNT_WEIGHT = 1.0
_WARNING_COUNT_WEIGHT = 0.35
_INFO_COUNT_WEIGHT = 0.10

# Cap per-channel contribution so one noisy channel doesn't dominate globally.
_MAX_CHANNEL_FAILURE_WEIGHT = 2.0


def channel_failure_weight(result: ChannelResult) -> float:
    """Compute severity-and-volume weighted contribution for one failed channel."""
    counts = finding_severity_counts(result)
    base_score = (
        counts["blocking"] * _BLOCKING_COUNT_WEIGHT
        + counts["warning"] * _WARNING_COUNT_WEIGHT
        + counts["informational"] * _INFO_COUNT_WEIGHT
    )
    if base_score > 0:
        # Dampen channels that are overwhelmingly informational — they
        # shouldn't inflate severity when nothing is actually blocking.
        total_findings = sum(counts.values())
        if total_findings > 0 and counts["informational"] / total_findings > 0.8:
            base_score *= 0.3
        return min(_MAX_CHANNEL_FAILURE_WEIGHT, base_score)
    return _SEVERITY_WEIGHT.get(result.severity, 0.5)


def finding_severity_counts(result: ChannelResult) -> dict[str, int]:
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


def effective_failure_count(
    failed_results: list[ChannelResult],
    channel_weights: dict[str, float] | None = None,
) -> float:
    """Compute severity-weighted failure count."""
    total = 0.0
    for result in failed_results:
        score = channel_failure_weight(result)
        if channel_weights is not None:
            importance = channel_weights.get(result.channel, 0.5)
            score *= importance
        total += score
    return total


def ordered_failed_channels(failed_results: list[ChannelResult]) -> list[str]:
    """Return failing channels sorted by severity weight (highest first)."""
    weighted = [
        (result.channel, channel_failure_weight(result)) for result in failed_results
    ]
    weighted.sort(key=lambda item: (-item[1], item[0]))
    return [name for name, _weight in weighted]


def find_shared_files(failed_results: list[ChannelResult]) -> set[str]:
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


def is_cross_domain_failure(
    failed_results: list[ChannelResult],
    effective_failure_count: float | None = None,
) -> bool:
    """Check for cross-domain failure pattern.

    Cross-domain: infrastructure channels (deps, git) + code channels
    (lint, tests, structure) both failing suggests a deeper structural issue.
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


def top_finding_kind(result: ChannelResult) -> str:
    """Return the most common finding kind for a channel result."""
    from collections import Counter

    kinds = [getattr(f, "kind", "") or "unknown" for f in result.findings]
    if not kinds:
        return ""
    most_common = Counter(kinds).most_common(1)
    return most_common[0][0] if most_common else ""


def channel_finding_summary(result: ChannelResult) -> str:
    """Build a compact summary string for a channel: 'N findings (top: kind)'."""
    count = len(result.findings)
    if count == 0:
        return "0 findings"
    top = top_finding_kind(result)
    return (
        f"{count} finding{'s' if count != 1 else ''} (top: {top})"
        if top
        else f"{count} finding{'s' if count != 1 else ''}"
    )
