"""Evidence types for the convergence aggregator."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class LensKind(str, Enum):
    """Analysis lens that produced evidence."""

    PURITY = "purity"
    MUTATION = "mutation"
    COHESION = "cohesion"
    FAN_IN = "fan_in"
    COCHANGE = "cochange"
    DEP_CLUSTERING = "dep_clustering"
    ASSERTION_QUALITY = "assertion_quality"
    ALGEBRAIC = "algebraic"
    IMPORT_TRACING = "import_tracing"
    CALL_GRAPH = "call_graph"
    CROSS_CHANNEL = "cross_channel"
    SPECIFICATION = "specification"
    COMPOSITION_GAP = "composition_gap"
    CONTRACT_COVERAGE = "contract_coverage"


class Actionability(str, Enum):
    """Recommended action category based on convergence strength."""

    EXTRACT = "extract"
    SPLIT = "split"
    INVESTIGATE = "investigate"


_VALID_SIGNALS = frozenset({"support", "oppose"})


@dataclass(frozen=True)
class LensEvidence:
    """A single piece of evidence from one analysis lens."""

    lens: LensKind
    target: str
    confidence: float
    signal: str  # "support" or "oppose"
    detail: str
    raw: dict = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        if self.signal not in _VALID_SIGNALS:
            raise ValueError(f"signal must be 'support' or 'oppose', got {self.signal!r}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")


@dataclass
class ConvergenceResult:
    """Aggregated convergence result for a single target."""

    target: str
    support_prob: float
    oppose_prob: float
    net_confidence: float
    supporting_lenses: list[LensKind]
    opposing_lenses: list[LensKind]
    actionability: Actionability
    evidence: list[LensEvidence] = field(default_factory=list)
    target_type: str = "function"
    split_proposals: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "target": self.target,
            "support_prob": round(self.support_prob, 4),
            "oppose_prob": round(self.oppose_prob, 4),
            "net_confidence": round(self.net_confidence, 4),
            "supporting_lenses": [lk.value for lk in self.supporting_lenses],
            "opposing_lenses": [lk.value for lk in self.opposing_lenses],
            "actionability": self.actionability.value,
            "evidence_count": len(self.evidence),
            "target_type": self.target_type,
        }
        if self.split_proposals:
            d["split_proposals"] = self.split_proposals
        return d
