"""Edit-scope classification for ControlPlane coherence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types import CoherenceResult

if TYPE_CHECKING:
    from .types import ChannelResult


# Security-critical rule IDs — ambient findings with these never downgrade to stable
_SECURITY_RULE_KEYWORDS = frozenset(
    {"secret", "sensitive", "credential", "token", "private_key", "api_key"}
)


def apply_edit_scope(
    result: CoherenceResult,
    channel_results: list[ChannelResult],
    files_changed: list[str],
) -> CoherenceResult:
    """Apply edit-scope overlay to a coherence result."""
    if result.state in ("stable", "degraded"):
        return result
    if not result.loud_channels:
        return result

    # Get the actual failing channel results
    failing_results = [
        cr
        for cr in channel_results
        if cr.channel in result.loud_channels and cr.status == "fail"
    ]
    if not failing_results:
        return result

    edit_related, ambient, unknown_scope = classify_edit_scope(
        failing_results,
        files_changed,
    )

    # Determine if we should downgrade
    # unknown_scope counts as edit-related for conservative downgrade decisions
    all_ambient = not edit_related and not unknown_scope and ambient

    if all_ambient:
        # Check for ambient blocking/security-critical findings
        has_ambient_critical = has_ambient_critical_findings(failing_results, ambient)
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
                + [
                    f"all {len(ambient)} failing channel(s) are ambient — downgraded to stable"
                ],
                edit_scoped=True,
                edit_related_channels=edit_related,
                ambient_channels=ambient,
                unknown_scope_channels=unknown_scope,
            )

    # Mixed: some edit-related, some ambient — keep original state but annotate
    if ambient:
        ambient_note = f"Note: {', '.join(ambient)} findings are pre-existing and unrelated to your edit."
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


def classify_edit_scope(
    failing_results: list[ChannelResult],
    files_changed: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Classify failing channels as edit-related, ambient, or unknown-scope."""
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


def has_ambient_critical_findings(
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
