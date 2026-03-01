"""Session Continuity — support state transfer across agents and turns.

Generates compact 'SessionTransferPacket' to maintain behavioral context
when a session is transferred or resumed.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionTransferPacket:
    """Compact state packet for session continuity."""

    session_id: str
    compliance_rate: float
    confirmed_hypotheses: list[dict[str, Any]]  # id, claim, confidence
    active_findings: list[str]  # list of kinds
    repertoire_summary: list[str]  # last 3 resolution hints
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        """Serialize to a compact JSON string."""
        return json.dumps(
            {
                "sid": self.session_id,
                "comp": round(self.compliance_rate, 2),
                "hyps": [
                    {
                        "id": h["id"],
                        "clm": h["claim"][:50],
                        "conf": round(h["confidence"], 2),
                    }
                    for h in self.confirmed_hypotheses
                ],
                "active": self.active_findings,
                "rep": self.repertoire_summary,
                "ts": int(self.timestamp),
            },
            separators=(",", ":"),
        )


def generate_transfer_packet(session: Any) -> SessionTransferPacket:
    """Generate a compact transfer packet from session memory."""
    bc = session.behavior_compass

    # 1. Confirmed hypotheses
    hyps = [
        {"id": h.id, "claim": h.claim, "confidence": h.confidence}
        for h in bc.hypotheses
        if h.status == "confirmed"
    ]

    # 2. Active findings (from pending_nudge_signals)
    active = bc.nudges.pending_nudge_signals

    # 3. Repertoire hints
    from lintgate.orchestration.repertoire import RepertoireManager

    rep_mgr = RepertoireManager(bc)
    rep_hints = []
    # Just take unique kinds from repertoire
    seen_kinds = set()
    for record in reversed(bc.get("resolution_repertoire", [])):
        kind = record.get("finding_kind")
        if kind and kind not in seen_kinds:
            hint = rep_mgr.get_resolution_hint(kind)
            if hint:
                rep_hints.append(hint)
                seen_kinds.add(kind)
        if len(rep_hints) >= 3:
            break

    return SessionTransferPacket(
        session_id=session.session_id,
        compliance_rate=bc.compliance_rate,
        confirmed_hypotheses=hyps,
        active_findings=active,
        repertoire_summary=rep_hints,
    )
