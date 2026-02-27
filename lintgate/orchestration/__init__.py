"""
Orchestration module for cross-architecture pattern pipeline.
"""

from lintgate.orchestration.attribution import SignalSourceDecomposition
from lintgate.orchestration.authority import AuthorityEscalationEngine, AuthorityLevel
from lintgate.orchestration.compliance import ComplianceManager, ComplianceStats
from lintgate.orchestration.continuity import SessionTransferPacket, generate_transfer_packet
from lintgate.orchestration.cycle_detector import (
    CycleDetectionResult,
    EditCycleState,
    detect_cycles,
    track_event,
)
from lintgate.orchestration.delivery import (
    BaseChannel,
    ClaudeCodeChannel,
    CursorChannel,
    DeliveryChannel,
    McpOnlyChannel,
    deliver_finding,
)
from lintgate.orchestration.disposition_enforcer import DispositionEnforcer
from lintgate.orchestration.remediation_router import route_finding
from lintgate.orchestration.repertoire import RepertoireManager, ResolutionRecord

__all__ = [
    "SignalSourceDecomposition",
    "AuthorityLevel",
    "AuthorityEscalationEngine",
    "ComplianceStats",
    "ComplianceManager",
    "SessionTransferPacket",
    "generate_transfer_packet",
    "CycleDetectionResult",
    "EditCycleState",
    "track_event",
    "detect_cycles",
    "DeliveryChannel",
    "BaseChannel",
    "ClaudeCodeChannel",
    "CursorChannel",
    "McpOnlyChannel",
    "deliver_finding",
    "DispositionEnforcer",
    "route_finding",
    "ResolutionRecord",
    "RepertoireManager",
]
