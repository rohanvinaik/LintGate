from pathlib import Path

from lintgate.controlplane.session_transfer import (
    read_transfer_packet,
    write_transfer_packet,
)
from lintgate.controlplane.types import SessionTransferPacket


def test_session_transfer_packet_io(tmp_path):
    packet_path = tmp_path / "packet.json"

    packet = SessionTransferPacket(
        source_agent_id="claude",
        target_agent_id="aider",
        transfer_reason="complex_refactoring",
        active_findings=[{"id": 1, "msg": "test"}],
        context_summary="We hit a block on circular dependencies.",
    )

    write_transfer_packet(packet_path, packet)
    assert packet_path.exists()

    restored = read_transfer_packet(packet_path)
    assert restored is not None
    assert restored.source_agent_id == "claude"
    assert restored.target_agent_id == "aider"
    assert restored.transfer_reason == "complex_refactoring"
    assert len(restored.active_findings) == 1
    assert restored.context_summary == "We hit a block on circular dependencies."


def test_session_transfer_missing():
    packet = read_transfer_packet(Path("/non/existent/path.json"))
    assert packet is None
