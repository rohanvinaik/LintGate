"""Session Transfer utility for agent handoff operations."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import TYPE_CHECKING

from lintgate.controlplane.types import SessionTransferPacket

if TYPE_CHECKING:
    from pathlib import Path


def write_transfer_packet(path: Path, packet: SessionTransferPacket) -> None:
    """Persist a session transfer packet to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(asdict(packet), f, indent=2)


def read_transfer_packet(path: Path) -> SessionTransferPacket | None:
    """Read a session transfer packet from disk."""
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return SessionTransferPacket(**data)
    except Exception:
        return None
