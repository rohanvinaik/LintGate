from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from lintgate.orchestration.cycle_detector import CycleDetectionResult
    from lintgate.types import LintIssue

# ── Reliability constants ───────────────────────────────────────────
RELIABILITY_HIGH = 1.0  # System-enforced or direct-injected (e.g. Claude Code hooks)
RELIABILITY_MEDIUM = 0.7  # Rule files with frontmatter / re-parsing (e.g. Cursor .mdc)
RELIABILITY_LOW = 0.3  # Human-gated or informational (e.g. Copilot, Generic)


@dataclass
class DeliveryItem:
    """Unified envelope for behavioral findings from any source."""

    source: str  # e.g. "cycle", "disposition", "lint"
    authority_level: Any  # AuthorityLevel enum
    content: dict[str, Any]
    formatting_hints: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "authority_level": str(self.authority_level.value)
            if hasattr(self.authority_level, "value")
            else str(self.authority_level),
            "content": self.content,
            "formatting_hints": self.formatting_hints,
            "timestamp": self.timestamp,
        }


def cycle_result_to_item(result: CycleDetectionResult) -> DeliveryItem:
    """Adapter for CycleDetectionResult."""
    from lintgate.orchestration.authority import AuthorityLevel

    auth = (
        AuthorityLevel.INTERVENTION
        if result.escalation_level == "enforced"
        else AuthorityLevel.NUDGE
    )
    return DeliveryItem(
        source="cycle",
        authority_level=auth,
        content={
            "reason": result.reason,
            "message": f"Potential edit cycle detected: {result.reason}",
            "diagnostics": result.diagnostics,
        },
    )


def disposition_nudge_to_item(nudge: str, rule_id: str) -> DeliveryItem:
    """Adapter for disposition enforcement nudges."""
    from lintgate.orchestration.authority import AuthorityLevel

    # Use 'PROTIP' vs 'IMPORTANT' logic if prefix provided, or default to NUDGE
    auth = AuthorityLevel.NUDGE
    if "URGENT" in nudge:
        auth = AuthorityLevel.WARNING
    elif "PROTIP" in nudge:
        auth = AuthorityLevel.ADVISORY

    return DeliveryItem(
        source="disposition",
        authority_level=auth,
        content={
            "rule_id": rule_id,
            "message": nudge,
        },
    )


def lint_finding_to_item(issue: LintIssue) -> DeliveryItem:
    """Adapter for LintIssue."""
    from lintgate.orchestration.authority import AuthorityLevel

    auth_map = {
        "blocking": AuthorityLevel.WARNING,
        "warning": AuthorityLevel.NUDGE,
        "informational": AuthorityLevel.ADVISORY,
    }
    auth = auth_map.get(issue.severity, AuthorityLevel.ADVISORY)
    return DeliveryItem(source="lint", authority_level=auth, content=issue.to_dict())


class DeliveryBus:
    """Centralized delivery bus for aggregating and prioritizing findings.

    Implements rate-limiting (highest authority wins) and model-aware budgeting.
    """

    def __init__(self, config: Any, session: Any = None):
        self.config = config
        self.session = session
        self.items: list[DeliveryItem] = []
        self.processed_items: list[DeliveryItem] = []
        self.suppressed_counts: dict[str, int] = {}
        self.budget_mode: str = "full"

    def collect(self, item: DeliveryItem) -> None:
        """Enqueue an item for delivery."""
        self.items.append(item)

    def process(self) -> None:
        """Prioritize findings and apply rate limiting."""
        if not self.items:
            return

        # Sort by authority level (highest first)
        sorted_items = sorted(
            self.items, key=lambda x: self._auth_rank(x.authority_level), reverse=True
        )

        # Primary item is the one with highest authority
        self.processed_items = [sorted_items[0]]

        # Collapse the rest into counts
        self.suppressed_counts = {}
        for item in sorted_items[1:]:
            lvl = str(item.authority_level.value)
            self.suppressed_counts[lvl] = self.suppressed_counts.get(lvl, 0) + 1

    def _auth_rank(self, level: Any) -> int:
        """Get integer rank for authority sorting."""
        from lintgate.orchestration.authority import AuthorityLevel

        hierarchy = [
            AuthorityLevel.ADVISORY,
            AuthorityLevel.NUDGE,
            AuthorityLevel.WARNING,
            AuthorityLevel.INTERVENTION,
        ]
        try:
            return hierarchy.index(level)
        except ValueError:
            return 0

    def get_budget(self, compliance_rate: float | None = None) -> str:
        """Determine output budget (full, pulse, silent)."""
        from lintgate.orchestration.authority import AuthorityLevel

        # Force full budget if we have blocking or intervention findings
        has_high_auth = any(
            self._auth_rank(i.authority_level) >= self._auth_rank(AuthorityLevel.WARNING)
            for i in self.items
        )

        if compliance_rate is None:
            if self.session and hasattr(self.session, "behavior_compass"):
                # Handle compass as object or dict
                compass = self.session.behavior_compass
                compliance_rate = (
                    compass.get("compliance_rate", 1.0)
                    if isinstance(compass, dict)
                    else getattr(compass, "compliance_rate", 1.0)
                )
            else:
                compliance_rate = 1.0

        if has_high_auth or compliance_rate < 0.5:
            return "full"
        if compliance_rate > 0.8:
            return "pulse"
        return "full"

    def emit(self, preferred_channels: list[str]) -> dict[str, Any]:
        """Flush the bus and format content for delivery."""
        self.process()
        if not self.processed_items:
            return {}

        primary = self.processed_items[0]
        self.budget_mode = self.get_budget()

        if self.budget_mode == "silent" and self._auth_rank(
            primary.authority_level
        ) < self._auth_rank(self._get_intervention_level()):
            return {}

        message = primary.content.get("message", "Behavioral observation")

        # Pulse: truncate to one-liner
        if self.budget_mode == "pulse":
            clean_msg = message.split("\n")[0].strip()
            emoji = self._get_auth_emoji(primary.authority_level)
            message = f"{emoji} {clean_msg[:70]}... (run `controlplane_run` for details)"

        # Append suppression footer if any
        if self.suppressed_counts:
            suppressed_parts = []
            for lvl, count in sorted(self.suppressed_counts.items()):
                suppressed_parts.append(f"{count} {lvl}")
            footer = f"\n\n[...and {', '.join(suppressed_parts)} suppressed]"
            message += footer

        # Deliver via existing channel protocol
        finding_dict = primary.to_dict()
        finding_dict["message"] = message

        payload, chan = deliver_finding(finding_dict, preferred_channels)

        # Proactive MCP push persistence
        self._persist_status(finding_dict)

        return {"systemMessage": payload} if payload else {}

    def _get_auth_emoji(self, level: Any) -> str:
        from lintgate.orchestration.authority import AuthorityLevel

        emojis = {
            AuthorityLevel.INTERVENTION: "🚨",
            AuthorityLevel.WARNING: "🛑",
            AuthorityLevel.NUDGE: "⚠️",
            AuthorityLevel.ADVISORY: "🔍",
        }
        return emojis.get(level, "🔍")

    def _get_intervention_level(self) -> Any:
        from lintgate.orchestration.authority import AuthorityLevel

        return AuthorityLevel.INTERVENTION

    def _persist_status(self, finding: dict[str, Any]) -> None:
        """Write bus summary to disk for proactive MCP tool consumption."""
        try:
            target_dir = os.path.join(self.config.project_root, ".lintgate")
            os.makedirs(target_dir, exist_ok=True)
            status_path = os.path.join(target_dir, "behavior_status.json")

            # Extract knowledge metrics from session if available
            repertoire_hits = 0
            knowledge_meta: dict[str, object] = {}
            if self.session:
                knowledge_meta = getattr(self.session, "knowledge_meta", {})
                repertoire = getattr(self.session, "resolution_repertoire", [])
                # Trivial hit detection: if current finding kind has a resolution record
                if finding and repertoire:
                    kind = finding.get("kind")
                    if any(r.get("finding_kind") == kind for r in repertoire):
                        repertoire_hits = 1

            summary = {
                "last_active_source": finding.get("source"),
                "authority": finding.get("authority_level"),
                "message": finding.get("message"),
                "timestamp": time.time(),
                "budget": self.budget_mode,
                "suppressed_counts": self.suppressed_counts,
                "knowledge_staleness_hrs": knowledge_meta.get("staleness_hrs", 0.0),
                "survival_ratio": knowledge_meta.get("survival_ratio", 1.0),
                "repertoire_hits": repertoire_hits,
            }
            with open(status_path, "w") as f:
                json.dump(summary, f, indent=2)
        except Exception:
            pass


@runtime_checkable
class DeliveryChannel(Protocol):
    """Unified protocol for delivery behavioral findings to host agents.

    Channels represent different rendering and transport paths (e.g. rule files,
    system prompt injection, hook response text, etc.).
    """

    @property
    def channel_type(self) -> str:
        """Machine name for the delivery channel (e.g. 'hook_text', 'rule_file')."""
        ...

    @property
    def reliability(self) -> float:
        """Probability of the finding being seen/followed [0-1]."""
        ...

    @property
    def latency(self) -> float:
        """Relative latency of the delivery (0 = immediate, 1 = multi-turn)."""
        ...

    def format_finding(self, finding: dict[str, Any]) -> str:
        """Format a behavioral finding for this specific channel."""
        ...

    def format_repertoire_hint(self, hint: dict[str, Any]) -> str:
        """Format a resolution repertoire hint for this specific channel."""
        ...

    def supports_proactive_push(self) -> bool:
        """Whether the channel supports pushing content without an explicit event."""
        ...


class BaseChannel:
    """Base for concrete channels with common metadata formatting."""

    def _format_metadata(self, f: dict[str, Any]) -> str:
        auth = f.get("authority_level", "nudge")
        chan = f.get("delivery_channel", getattr(self, "channel_type", "unknown"))
        rel = f.get("reliability", getattr(self, "reliability", 0.0))
        return f"\n\n[lintgate metadata | authority: {auth} | channel: {chan} | reliability: {rel:.1f}]"

    def _get_authority_prefix(self, authority: str) -> str:
        prefixes = {
            "intervention": "🚨 CRITICAL INTERVENTION",
            "blocking": "🛑 Behavioral block",
            "nudge": "⚠️ Behavioral reminder",
            "advisory": "🔍 Behavioral observation",
        }
        return prefixes.get(authority.lower(), "🔍 Observation")


class ClaudeCodeChannel(BaseChannel):
    channel_type = "hook_text"
    reliability = RELIABILITY_HIGH
    latency = 0.0

    def format_finding(self, finding: dict[str, Any]) -> str:
        auth = finding.get("authority_level", "advisory")
        prefix = self._get_authority_prefix(auth)
        msg = f"{prefix}: {finding.get('message', 'Unspecified observation')}"
        if finding.get("hint"):
            msg += f"\n💡 Hint: {finding['hint']}"
        return msg + self._format_metadata(finding)

    def format_repertoire_hint(self, hint: dict[str, Any]) -> str:
        return f"✨ Proven resolution: {hint.get('repertoire', 'N/A')}"

    def supports_proactive_push(self) -> bool:
        return False


class CursorChannel(BaseChannel):
    channel_type = "rule_file"
    reliability = RELIABILITY_MEDIUM
    latency = 0.5

    def format_finding(self, finding: dict[str, Any]) -> str:
        auth = finding.get("authority_level", "advisory")
        prefix = self._get_authority_prefix(auth)
        msg = f"### {prefix}\n\n{finding.get('message')}"
        if finding.get("hint"):
            msg += f"\n\n> {finding['hint']}"
        return msg + self._format_metadata(finding)

    def format_repertoire_hint(self, hint: dict[str, Any]) -> str:
        return f"#### Recovery Route\n\n{hint.get('repertoire')}"

    def supports_proactive_push(self) -> bool:
        return True


class McpOnlyChannel(BaseChannel):
    channel_type = "mcp_status"
    reliability = RELIABILITY_LOW
    latency = 0.0

    def format_finding(self, finding: dict[str, Any]) -> str:
        return f"Pending behavior nudge: {finding.get('message')}"

    def format_repertoire_hint(self, hint: dict[str, Any]) -> str:
        return f"Resolution hint available: {str(hint.get('repertoire', ''))[:30]}..."

    def supports_proactive_push(self) -> bool:
        return True


# Default mapping for quick resolution
CHANNEL_MAP: dict[str, DeliveryChannel] = {
    "hook_text": ClaudeCodeChannel(),
    "rule_file": CursorChannel(),
    "mcp_status": McpOnlyChannel(),
}


def deliver_finding(
    finding: dict[str, Any],
    preferred_channels: list[str],
    available_channels: dict[str, DeliveryChannel] | None = None,
) -> tuple[str | None, str | None]:
    """Deliver a finding using the best available channel from the preferred list.

    Returns:
        tuple: (formatted_payload, channel_type) or (None, None) if no delivery possible.
    """
    channels = available_channels if available_channels is not None else CHANNEL_MAP

    for chan_type in preferred_channels:
        if chan_type in channels:
            channel = channels[chan_type]

            # Enrich finding with delivery metadata before formatting
            finding["delivery_channel"] = chan_type
            finding["reliability"] = channel.reliability

            return channel.format_finding(finding), chan_type

    return None, None
