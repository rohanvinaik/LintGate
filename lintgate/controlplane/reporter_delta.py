"""Finding fingerprint and delta computation for ControlPlane reporter.

Extracted from reporter.py — provides stable fingerprinting of findings
(survives line-number changes), delta computation between consecutive
ControlPlane runs, and display-finding filtering by delta quotas.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .types import MeshResult

# ── Finding Fingerprint & Delta ──────────────────────────────────────────

_SEVERITY_ORDER = {"informational": 0, "warning": 1, "blocking": 2}


def compute_finding_fingerprint(finding: Any, channel: str) -> str:
    """Stable fingerprint: channel|kind|normalized_file_path|msg_hash.

    Excludes line number — survives edits that shift lines.
    """
    file_path = (getattr(finding, "file", None) or "").replace("\\", "/")
    msg = getattr(finding, "message", "") or ""
    # Hash the full message to avoid prefix collisions when messages share a start.
    msg_hash = hashlib.sha256(msg.encode()).hexdigest()[:8]
    raw = f"{channel}|{getattr(finding, 'kind', '')}|{file_path}|{msg_hash}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def build_finding_index(mesh_result: MeshResult) -> dict[str, dict[str, Any]]:
    """Build fingerprint -> finding-summary dict from a MeshResult."""
    index: dict[str, dict[str, Any]] = {}
    for cr in mesh_result.channel_results:
        for f in cr.findings:
            fp = compute_finding_fingerprint(f, cr.channel)
            existing = index.get(fp)
            if existing is None:
                index[fp] = {
                    "channel": cr.channel,
                    "kind": getattr(f, "kind", ""),
                    "severity": getattr(f, "severity", ""),
                    "confidence": getattr(f, "confidence", None),
                    "file": f.short_location() if hasattr(f, "short_location") else "",
                    "message": (getattr(f, "message", "") or "")[:80],
                    "line": getattr(f, "line", None),
                    "count": 1,
                }
            else:
                existing["count"] = int(existing.get("count", 1)) + 1
                # Keep the highest-severity label for this fingerprint bucket.
                cur_rank = _SEVERITY_ORDER.get(str(existing.get("severity", "")), 0)
                new_sev = str(getattr(f, "severity", ""))
                new_rank = _SEVERITY_ORDER.get(new_sev, 0)
                if new_rank > cur_rank:
                    existing["severity"] = new_sev
    return index


def compute_finding_delta(
    current_index: dict[str, dict[str, Any]],
    previous_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Compute new/escalated/resolved/still_active from two finding indexes."""
    current_fps = set(current_index)
    previous_fps = set(previous_index)

    new_fps = current_fps - previous_fps
    resolved_fps = previous_fps - current_fps
    common_fps = current_fps & previous_fps

    new_findings = []
    escalated = []
    still_active_count = 0
    resolved_count = 0

    for fp in common_fps:
        current = current_index[fp]
        previous = previous_index[fp]
        cur_count = int(current.get("count", 1))
        prev_count = int(previous.get("count", 1))
        shared = min(cur_count, prev_count)

        cur_sev = _SEVERITY_ORDER.get(str(current.get("severity", "")), 0)
        prev_sev = _SEVERITY_ORDER.get(str(previous.get("severity", "")), 0)
        if cur_sev > prev_sev and shared > 0:
            escalated.append(
                {
                    **current,
                    "fingerprint": fp,
                    "previous_severity": previous.get("severity", ""),
                    "count": shared,
                }
            )
        else:
            still_active_count += shared

        if cur_count > prev_count:
            extra = cur_count - prev_count
            new_findings.append({**current, "fingerprint": fp, "count": extra})
        elif prev_count > cur_count:
            resolved_count += prev_count - cur_count

    for fp in sorted(new_fps):
        current = current_index[fp]
        new_findings.append({**current, "fingerprint": fp, "count": int(current.get("count", 1))})

    for fp in resolved_fps:
        previous = previous_index[fp]
        resolved_count += int(previous.get("count", 1))

    return {
        "new": new_findings,
        "escalated": escalated,
        "resolved_count": resolved_count,
        "still_active_count": still_active_count,
    }


# ── Display Filtering ───────────────────────────────────────────────────


@dataclass
class DisplayFilterResult:
    """Result of delta-based display filtering."""

    display_findings: list = field(default_factory=list)
    resurfaced_count: int = 0


def _build_delta_quota(delta: dict[str, Any]) -> dict[str, int]:
    """Build per-fingerprint display quota from new/escalated delta items."""
    quota: dict[str, int] = {}
    for key in ("new", "escalated"):
        for item in delta.get(key, []):
            fp = item.get("fingerprint", "")
            if fp:
                quota[fp] = quota.get(fp, 0) + max(1, int(item.get("count", 1)))
    return quota


def _apply_resurface_cadence(
    quota: dict[str, int],
    current_index: dict[str, dict[str, Any]] | None,
    previous_finding_index: dict[str, dict[str, Any]] | None,
    snapshot_count: int,
) -> int:
    """Add resurfaced persistent blocking findings to quota. Returns count added."""
    if not (snapshot_count > 0 and snapshot_count % 10 == 0 and previous_finding_index):
        return 0
    resurfaced = 0
    for fp, info in (current_index or {}).items():
        if (
            info.get("severity") == "blocking"
            and fp in previous_finding_index
            and quota.get(fp, 0) == 0
        ):
            quota[fp] = 1
            resurfaced += 1
    return resurfaced


def _select_by_quota(
    mesh_result: MeshResult,
    quota: dict[str, int],
) -> list:
    """Select findings up to per-fingerprint quota limits."""
    if not quota:
        return []
    display: list = []
    used: dict[str, int] = {}
    for cr in mesh_result.channel_results:
        for f in cr.findings:
            fp = compute_finding_fingerprint(f, cr.channel)
            allowed = quota.get(fp, 0)
            if allowed > 0 and used.get(fp, 0) < allowed:
                display.append(f)
                used[fp] = used.get(fp, 0) + 1
    return display


def filter_display_findings(
    *,
    all_findings: list,
    delta: dict[str, Any] | None,
    mesh_result: MeshResult,
    current_index: dict[str, dict[str, Any]] | None,
    previous_finding_index: dict[str, dict[str, Any]] | None,
    snapshot_count: int,
) -> DisplayFilterResult:
    """Filter findings by delta quotas for display.

    When delta is available, only new/escalated findings are shown, plus
    persistent blocking findings that resurface on a cadence (every 10 runs).

    When delta is not available, all findings are shown.
    """
    if delta is None:
        return DisplayFilterResult(display_findings=all_findings, resurfaced_count=0)

    quota = _build_delta_quota(delta)
    resurfaced_count = _apply_resurface_cadence(
        quota, current_index, previous_finding_index, snapshot_count
    )
    display_findings = _select_by_quota(mesh_result, quota)

    return DisplayFilterResult(
        display_findings=display_findings,
        resurfaced_count=resurfaced_count,
    )
