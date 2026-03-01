"""PostToolUse hook context and telemetry counters for ControlPlane reporter.

Extracted from reporter.py — builds the compact additionalContext string
for Claude Code's PostToolUse hook protocol and lightweight telemetry
counters for threshold tuning.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .types import MeshResult


def _build_posttooluse_context(
    *,
    mesh_result: MeshResult,
    blocking_count: int,
    warning_count: int,
    informational_count: int,
    hidden_findings: int,
    channels_run: int,
    delta: dict[str, Any] | None = None,
    baseline_delta: dict[str, Any] | None = None,
    resurfaced_count: int = 0,
    cycle_alerts: list[str] | None = None,
) -> str:
    """Build compact additional context for Claude PostToolUse hooks.

    Fixed key order, compact format. Keys with zero/empty values are omitted.
    Max 300 chars — drops least-critical fields from bottom up if exceeded.
    """
    coherence = mesh_result.coherence

    # Build ordered key-value pairs (fixed order per plan)
    pairs: list[tuple[str, str]] = []

    # 1. coherence (always)
    pairs.append(("coherence", coherence.state))
    # 2. channels_run (always)
    pairs.append(("channels_run", str(channels_run)))
    # 3. blocking (only > 0)
    if blocking_count > 0:
        pairs.append(("blocking", str(blocking_count)))
    # 4. warnings (only > 0)
    if warning_count > 0:
        pairs.append(("warnings", str(warning_count)))
    # 5. edit_related (when edit_scoped)
    if getattr(coherence, "edit_scoped", False) and getattr(
        coherence, "edit_related_channels", None
    ):
        pairs.append(("edit_related", ",".join(coherence.edit_related_channels)))
    # 6. ambient_debt (when ambient channels exist)
    if getattr(coherence, "edit_scoped", False) and getattr(
        coherence, "ambient_channels", None
    ):
        pairs.append(("ambient_debt", ",".join(coherence.ambient_channels)))
    # 7. new_findings (from delta, > 0)
    if delta is not None:
        new_count = sum(f.get("count", 1) for f in delta.get("new", []))
        if new_count > 0:
            pairs.append(("new_findings", str(new_count)))
    # 8. resolved (from delta, > 0)
    if delta is not None:
        resolved = delta.get("resolved_count", 0)
        if resolved > 0:
            pairs.append(("resolved", str(resolved)))
    # 9. known_debt (from delta, > 0)
    if delta is not None:
        known = delta.get("still_active_count", 0)
        if known > 0:
            pairs.append(("known_debt", str(known)))
    # 10. session_regressions (from baseline delta, > 0)
    if baseline_delta is not None:
        session_new = sum(f.get("count", 1) for f in baseline_delta.get("new", []))
        if session_new > 0:
            pairs.append(("session_regressions", str(session_new)))
    # 11. loud (only failing channels)
    loud = ",".join(
        f"{cr.channel}:{cr.status}"
        for cr in mesh_result.channel_results
        if cr.status in ("fail", "error", "timeout")
    )
    if loud:
        pairs.append(("loud", loud))
    # 12. resurface (> 0)
    if resurfaced_count > 0:
        pairs.append(("resurface", str(resurfaced_count)))
    # 13. cycles (if any)
    if cycle_alerts:
        pairs.append(("cycles", ",".join(cycle_alerts)))

    # Serialize with max length enforcement (300 chars)
    max_context_len = 300
    result = "; ".join(f"{k}={v}" for k, v in pairs)
    while len(result) > max_context_len and len(pairs) > 2:
        pairs.pop()  # Drop least-critical (bottom) fields
        result = "; ".join(f"{k}={v}" for k, v in pairs)

    return result


def _build_telemetry_counters(
    *,
    mesh_result: MeshResult,
    delta: dict[str, Any] | None,
    baseline_delta: dict[str, Any] | None,
    display_findings: list,
    all_findings: list,
    resurfaced_count: int,
) -> dict[str, int]:
    """Build lightweight telemetry counters for threshold tuning.

    Increment-only counters, no complex aggregation. Stored in session
    memory and exposed via controlplane_status.
    """
    counters: dict[str, int] = {}

    coherence = mesh_result.coherence

    # edit_scope_downgrades: coherence was downgraded by edit-scope logic
    if (
        getattr(coherence, "edit_scoped", False)
        and coherence.classification_notes
        and (
            any("downgraded to stable" in n for n in coherence.classification_notes)
            or any(
                "downgraded to isolated" in n for n in coherence.classification_notes
            )
        )
    ):
        counters["edit_scope_downgrades"] = 1

    # edit_scope_preserved: edit-related findings preserved original state
    if getattr(coherence, "edit_scoped", False) and getattr(
        coherence, "edit_related_channels", None
    ):
        counters["edit_scope_preserved"] = 1

    # suppressed_known_debt: findings suppressed via delta filtering
    if delta is not None:
        suppressed = len(all_findings) - len(display_findings)
        if suppressed > 0:
            counters["suppressed_known_debt"] = suppressed

    # resurfaced_blockers: persistent blockers resurfaced by cadence rule
    if resurfaced_count > 0:
        counters["resurfaced_blockers"] = resurfaced_count

    # new_finding_precision: ratio of new findings to total (as percentage 0-100)
    if delta is not None and all_findings:
        new_count = len(display_findings)
        total = len(all_findings)
        counters["new_finding_precision"] = (
            round(100 * new_count / total) if total > 0 else 100
        )

    # session_regressions from baseline
    if baseline_delta is not None:
        session_new = sum(f.get("count", 1) for f in baseline_delta.get("new", []))
        if session_new > 0:
            counters["session_regressions"] = session_new

    return counters
