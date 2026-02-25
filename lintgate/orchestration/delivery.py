"""Unified behavioral delivery protocol and channel abstractions."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# ── Reliability constants ───────────────────────────────────────────

RELIABILITY_HIGH = 1.0  # System-enforced or direct-injected (e.g. Claude Code hooks)
RELIABILITY_MEDIUM = 0.7  # Rule files with frontmatter / re-parsing (e.g. Cursor .mdc)
RELIABILITY_LOW = 0.3  # Human-gated or informational (e.g. Copilot, Generic)


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
        return f"Resolution hint available: {hint.get('repertoire')[:30]}..."

    def supports_proactive_push(self) -> bool:
        return True


# Default mapping for quick resolution
CHANNEL_MAP = {
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
    if available_channels is None:
        available_channels = CHANNEL_MAP

    for chan_type in preferred_channels:
        if chan_type in available_channels:
            channel = available_channels[chan_type]

            # Enrich finding with delivery metadata before formatting
            finding["delivery_channel"] = chan_type
            finding["reliability"] = channel.reliability

            return channel.format_finding(finding), chan_type

    return None, None
