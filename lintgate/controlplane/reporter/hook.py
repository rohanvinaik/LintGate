"""PostToolUse hook context and telemetry counters for ControlPlane reporter.

Extracted from reporter.py — builds the compact additionalContext string
for Claude Code's PostToolUse hook protocol and lightweight telemetry
counters for threshold tuning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..types import MeshResult


@dataclass
class PostToolUseInputs:
    """Grouped inputs for _build_posttooluse_context.

    Replaces 10 keyword arguments with a single structured object.
    """

    mesh_result: MeshResult
    blocking_count: int
    warning_count: int
    informational_count: int
    hidden_findings: int
    channels_run: int
    delta: dict[str, Any] | None = None
    baseline_delta: dict[str, Any] | None = None
    resurfaced_count: int = 0
    cycle_alerts: list[str] = field(default_factory=list)


def _build_posttooluse_context(inputs: PostToolUseInputs) -> str:
    """Build compact additional context for Claude PostToolUse hooks.

    Fixed key order, compact format. Keys with zero/empty values are omitted.
    Max 300 chars — drops from the middle (delta pairs first), preserving
    both anchors (coherence/blocking at top) and signal-quality metadata
    (loud channels/cycles at bottom).
    """
    coherence = inputs.mesh_result.coherence

    # ── Tier 1: Always preserved (anchors) ────────────────────────
    anchor_pairs: list[tuple[str, str]] = []
    anchor_pairs.append(("coherence", coherence.state))
    anchor_pairs.append(("channels_run", str(inputs.channels_run)))
    if inputs.blocking_count > 0:
        anchor_pairs.append(("blocking", str(inputs.blocking_count)))
    if inputs.warning_count > 0:
        anchor_pairs.append(("warnings", str(inputs.warning_count)))

    # ── Tier 2: Signal-quality metadata (preserve over deltas) ────
    status_pairs: list[tuple[str, str]] = []
    _append_status_pairs(
        status_pairs, inputs.mesh_result, inputs.resurfaced_count, inputs.cycle_alerts
    )

    # ── Tier 3: Delta pairs (first to drop under pressure) ────────
    delta_pairs: list[tuple[str, str]] = []
    _append_delta_pairs(delta_pairs, coherence, inputs.delta, inputs.baseline_delta)

    # Assemble: anchors + deltas + status. Drop deltas first if over budget.
    all_pairs = anchor_pairs + delta_pairs + status_pairs
    result = "; ".join(f"{k}={v}" for k, v in all_pairs)

    if len(result) <= 300:
        return result

    # Over budget: drop delta pairs one at a time (middle tier)
    while len(result) > 300 and delta_pairs:
        delta_pairs.pop()
        all_pairs = anchor_pairs + delta_pairs + status_pairs
        result = "; ".join(f"{k}={v}" for k, v in all_pairs)

    # Still over: drop status pairs
    while len(result) > 300 and status_pairs:
        status_pairs.pop()
        all_pairs = anchor_pairs + status_pairs
        result = "; ".join(f"{k}={v}" for k, v in all_pairs)

    # Last resort: drop anchor pairs from bottom
    while len(result) > 300 and len(anchor_pairs) > 2:
        anchor_pairs.pop()
        result = "; ".join(f"{k}={v}" for k, v in anchor_pairs)

    return result


def _append_delta_pairs(
    pairs: list[tuple[str, str]],
    coherence: Any,
    delta: dict[str, Any] | None,
    baseline_delta: dict[str, Any] | None,
) -> None:
    """Append edit-scope and delta-derived pairs to the output list.

    Covers edit_related, ambient_debt, new_findings, resolved, known_debt,
    and session_regressions.
    """
    # 5. edit_related (when edit_scoped)
    if getattr(coherence, "edit_scoped", False) and getattr(
        coherence, "edit_related_channels", None
    ):
        pairs.append(("edit_related", ",".join(coherence.edit_related_channels)))
    # 6. ambient_debt (when ambient channels exist)
    if getattr(coherence, "edit_scoped", False) and getattr(coherence, "ambient_channels", None):
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


def _append_status_pairs(
    pairs: list[tuple[str, str]],
    mesh_result: MeshResult,
    resurfaced_count: int,
    cycle_alerts: list[str],
) -> None:
    """Append loud-channel, resurface, and cycle pairs to the output list."""
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


def _serialize_pairs(pairs: list[tuple[str, str]], *, max_len: int = 300) -> str:
    """Serialize key-value pairs with max length enforcement.

    Drops least-critical fields (from the bottom) until the result fits.
    Always preserves at least the first 2 pairs (coherence + channels_run).
    """
    result = "; ".join(f"{k}={v}" for k, v in pairs)
    while len(result) > max_len and len(pairs) > 2:
        pairs.pop()  # Drop least-critical (bottom) fields
        result = "; ".join(f"{k}={v}" for k, v in pairs)
    return result


def _extract_hook_fields(mesh_result: MeshResult) -> dict[str, str]:
    """Extract individual hook-state fields from a mesh result.

    Each field is a string representation of a tracked dimension.
    Used for both fingerprinting and field-level delta computation.
    """
    coherence_state = mesh_result.coherence.state
    blocking = sum(
        1 for cr in mesh_result.channel_results for f in cr.findings if f.severity == "blocking"
    )
    warning = sum(
        1 for cr in mesh_result.channel_results for f in cr.findings if f.severity == "warning"
    )
    loud = sorted(
        f"{cr.channel}:{cr.status}"
        for cr in mesh_result.channel_results
        if cr.status in ("fail", "error", "timeout")
    )
    channels_run = sum(1 for cr in mesh_result.channel_results if cr.status != "skip")
    return {
        "coherence": coherence_state,
        "blocking": str(blocking),
        "warning": str(warning),
        "loud": ",".join(loud),
        "channels_run": str(channels_run),
    }


def compute_hook_fingerprint(mesh_result: MeshResult) -> str:
    """Compute a compact fingerprint of hook-relevant state.

    Used for state-transition suppression: when the fingerprint hasn't changed
    between consecutive hook invocations and there are no blocking findings,
    the full report can be suppressed to reduce context noise.

    The fingerprint captures: coherence state, blocking count, warning count,
    and the set of loud (failing/errored/timed-out) channels.
    """
    import hashlib

    fields = _extract_hook_fields(mesh_result)
    parts = f"{fields['coherence']}|b={fields['blocking']}|w={fields['warning']}|{fields['loud']}"
    return hashlib.md5(parts.encode(), usedforsecurity=False).hexdigest()[:12]  # nosec B324 — not crypto, just fingerprinting


def compute_hook_fingerprint_detailed(mesh_result: MeshResult) -> dict[str, Any]:
    """Compute fingerprint with per-field breakdown for delta-first output.

    Returns {"fingerprint": str, "fields": dict[str, str]}.
    """
    import hashlib

    fields = _extract_hook_fields(mesh_result)
    parts = f"{fields['coherence']}|b={fields['blocking']}|w={fields['warning']}|{fields['loud']}"
    fp = hashlib.md5(parts.encode(), usedforsecurity=False).hexdigest()[:12]  # nosec B324
    return {"fingerprint": fp, "fields": fields}


def compute_field_deltas(
    current_fields: dict[str, str],
    previous_fields: dict[str, str],
) -> dict[str, dict[str, str]]:
    """Return only fields that changed, with old and new values."""
    deltas: dict[str, dict[str, str]] = {}
    for key in current_fields:
        curr = current_fields[key]
        prev = previous_fields.get(key, "")
        if curr != prev:
            deltas[key] = {"old": prev, "new": curr}
    return deltas


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
            or any("downgraded to isolated" in n for n in coherence.classification_notes)
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
        counters["new_finding_precision"] = round(100 * new_count / total) if total > 0 else 100

    # session_regressions from baseline
    if baseline_delta is not None:
        session_new = sum(f.get("count", 1) for f in baseline_delta.get("new", []))
        if session_new > 0:
            counters["session_regressions"] = session_new

    return counters
