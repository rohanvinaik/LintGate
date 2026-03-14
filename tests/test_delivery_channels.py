"""Tests for NSIL delivery protocol and host routing."""

from lintgate.orchestration.delivery import (
    RELIABILITY_HIGH,
    ClaudeCodeChannel,
    CursorChannel,
    DeliveryChannel,
    deliver_finding,
)
from lintgate.renderers.host_adapter import (
    CLAUDE_CAPABILITIES,
    CURSOR_CAPABILITIES,
    GENERIC_CAPABILITIES,
    resolve_delivery_channels,
)


class MockChannel:
    """Mock implementation of DeliveryChannel."""

    @property
    def channel_type(self) -> str:
        return "mock"

    @property
    def reliability(self) -> float:
        return RELIABILITY_HIGH

    @property
    def latency(self) -> float:
        return 0.0

    def format_finding(self, finding: dict) -> str:
        return f"FINDING: {finding.get('message')}"

    def format_repertoire_hint(self, hint: dict) -> str:
        return f"HINT: {hint.get('repertoire')}"

    def supports_proactive_push(self) -> bool:
        return True


def test_delivery_channel_protocol():
    """Verify that MockChannel satisfies the DeliveryChannel protocol."""
    channel = MockChannel()
    assert isinstance(channel, DeliveryChannel)
    assert channel.channel_type == "mock"
    assert channel.reliability == RELIABILITY_HIGH
    assert channel.latency == 0.0
    assert channel.format_finding({"message": "test"}) == "FINDING: test"
    assert channel.format_repertoire_hint({"repertoire": "test_hint"}) == "HINT: test_hint"
    assert channel.supports_proactive_push() is True


def test_resolve_delivery_channels_claude():
    """Verify delivery channel resolution for Claude."""
    channels = resolve_delivery_channels(CLAUDE_CAPABILITIES)
    assert "hook_text" in channels
    assert "mcp_status" in channels


def test_resolve_delivery_channels_cursor():
    """Verify delivery channel resolution for Cursor."""
    channels = resolve_delivery_channels(CURSOR_CAPABILITIES)
    assert "rule_file" in channels
    assert "mcp_status" not in channels
    assert "hook_text" not in channels


def test_resolve_delivery_channels_generic():
    """Verify delivery channel resolution for generic hosts."""
    channels = resolve_delivery_channels(GENERIC_CAPABILITIES)
    assert channels == ["mcp_status"]


def test_claude_code_channel():
    """Verify ClaudeCodeChannel formatting."""
    chan = ClaudeCodeChannel()
    finding = {"message": "Use focus_on_file", "hint": "Reduces context noise"}
    payload = chan.format_finding(finding)
    assert "🔍 Behavioral observation: Use focus_on_file" in payload
    assert "💡 Hint: Reduces context noise" in payload
    assert (
        "[lintgate metadata | authority: nudge | channel: hook_text | reliability: 1.0]" in payload
    )


def test_cursor_channel():
    """Verify CursorChannel formatting."""
    chan = CursorChannel()
    finding = {"message": "Too many files open"}
    payload = chan.format_finding(finding)
    assert "### 🔍 Behavioral observation" in payload
    assert "Too many files open" in payload
    assert (
        "[lintgate metadata | authority: nudge | channel: rule_file | reliability: 0.7]" in payload
    )


def test_deliver_finding_success():
    """Verify deliver_finding picks the best preferred channel."""
    finding = {"message": "test message"}
    preferred = ["hook_text", "mcp_status"]

    payload, chan_type = deliver_finding(finding, preferred)

    assert chan_type == "hook_text"
    assert payload is not None
    assert "🔍 Behavioral observation: test message" in payload
    assert finding["delivery_channel"] == "hook_text"
    assert finding["reliability"] == 1.0


def test_deliver_finding_fallback():
    """Verify deliver_finding falls back when first choice is missing."""
    finding = {"message": "test message"}
    # available_channels doesn't have 'missing_channel'
    preferred = ["missing_channel", "rule_file"]

    payload, chan_type = deliver_finding(finding, preferred)

    assert chan_type == "rule_file"
    assert payload is not None
    assert "### 🔍 Behavioral observation" in payload


def test_deliver_finding_none():
    """Verify deliver_finding returns None if no channels match."""
    finding = {"message": "test message"}
    preferred = ["invalid_1", "invalid_2"]

    payload, chan_type = deliver_finding(finding, preferred)

    assert payload is None
    assert chan_type is None
