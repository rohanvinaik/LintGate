"""Finding fingerprint and delta computation for ControlPlane reporter.

Extracted from reporter.py — provides stable fingerprinting of findings
(survives line-number changes) and delta computation between consecutive
ControlPlane runs.
"""

from __future__ import annotations

import hashlib
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
