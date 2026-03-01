"""Compass — 4-axis project understanding model.

Core data types and pure computation for the compass system.
Persistence and migration live in compass_io.py.

The 4 axes collapse 7 theory facets:
- problem (core_theory + alignment)
- solution (problem_solving + architecture + anti_patterns)
- implementation (abstractions + enforceable_rules)
- world (inferred from code/infra, no direct facet)

Depth scoring: 0=empty, 1=surface, 2=structural, 3=deep.
Spikiness only on required axes (problem, solution).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

# ── Constants ────────────────────────────────────────────────────────

COMPASS_PATH = ".claude/compass.yaml"
AXIS_NAMES = ("problem", "solution", "implementation", "world")
REQUIRED_AXES = ("problem", "solution")
OPTIONAL_AXES = ("implementation", "world")

# Map 7 theory facets → 4 compass axes
FACET_TO_AXIS: dict[str, str] = {
    "core_theory": "problem",
    "alignment": "problem",
    "problem_solving": "solution",
    "architecture": "solution",
    "anti_patterns": "solution",
    "abstractions": "implementation",
    "enforceable_rules": "implementation",
}

# Causal/contrastive markers that boost depth scoring
_CAUSAL_MARKERS = re.compile(
    r"\b(because|therefore|since|thus|consequently|as a result|"
    r"this means|which causes|in order to|so that)\b",
    re.IGNORECASE,
)
_CONTRASTIVE_MARKERS = re.compile(
    r"\b(however|but|instead|rather than|unlike|whereas|"
    r"not\b.*\bbut\b|in contrast|on the other hand)\b",
    re.IGNORECASE,
)


# ── Dataclasses ──────────────────────────────────────────────────────


@dataclass
class CompassClaim:
    """A single knowledge claim within a compass axis."""

    text: str
    source: str = ""  # file:line or "interview" or "code_inference"
    heading: str = ""
    confidence: float = 1.0
    provenance: str = "parsed"  # "parsed" | "inferred" | "interviewed"
    origin_facet: str = ""  # preserves 7-facet source for reversibility

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "source": self.source,
            "heading": self.heading,
            "confidence": self.confidence,
            "provenance": self.provenance,
            "origin_facet": self.origin_facet,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompassClaim:
        if not data:
            return cls(text="")
        return cls(
            text=str(data.get("text", "")),
            source=str(data.get("source", "")),
            heading=str(data.get("heading", "")),
            confidence=float(data.get("confidence", 1.0)),
            provenance=str(data.get("provenance", "parsed")),
            origin_facet=str(data.get("origin_facet", "")),
        )


@dataclass
class CompassAxis:
    """One of the 4 compass axes with scored depth."""

    name: str  # "problem" | "solution" | "implementation" | "world"
    claims: list[CompassClaim] = field(default_factory=list)
    summary: str = ""  # 1-sentence summary (best claim)
    depth: int = 0  # 0=empty, 1=surface, 2=structural, 3=deep

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "claims": [c.to_dict() for c in self.claims],
            "summary": self.summary,
            "depth": self.depth,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompassAxis:
        if not data:
            return cls(name="")
        return cls(
            name=str(data.get("name", "")),
            claims=[CompassClaim.from_dict(c) for c in data.get("claims", [])],
            summary=str(data.get("summary", "")),
            depth=int(data.get("depth", 0)),
        )


@dataclass
class CompassDirective:
    """A behavioral directive derived from compass claims."""

    kind: str  # "toward" | "away" | "forbidden"
    text: str
    source: str = ""  # which axis/claim produced this

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompassDirective:
        if not data:
            return cls(kind="toward", text="")
        return cls(
            kind=str(data.get("kind", "toward")),
            text=str(data.get("text", "")),
            source=str(data.get("source", "")),
        )


@dataclass
class GapReport:
    """Analysis of compass coverage gaps."""

    axis_depths: dict[str, int] = field(default_factory=dict)
    spikiness: float = 0.0  # stdev of normalized required axis depths
    sparse_axes: list[str] = field(default_factory=list)  # axes with depth <= 1
    interview_recommended: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis_depths": dict(self.axis_depths),
            "spikiness": round(self.spikiness, 4),
            "sparse_axes": list(self.sparse_axes),
            "interview_recommended": self.interview_recommended,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GapReport:
        if not data:
            return cls()
        return cls(
            axis_depths=dict(data.get("axis_depths", {})),
            spikiness=float(data.get("spikiness", 0.0)),
            sparse_axes=list(data.get("sparse_axes", [])),
            interview_recommended=bool(data.get("interview_recommended", False)),
        )


@dataclass
class CompassState:
    """Complete compass state — the central data model."""

    version: int = 1
    axes: dict[str, CompassAxis] = field(default_factory=dict)
    directives: list[CompassDirective] = field(default_factory=list)
    gap_report: GapReport = field(default_factory=GapReport)
    forged_at: float = field(default_factory=time.time)
    frozen: bool = False
    frozen_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "axes": {k: v.to_dict() for k, v in self.axes.items()},
            "directives": [d.to_dict() for d in self.directives],
            "gap_report": self.gap_report.to_dict(),
            "forged_at": self.forged_at,
            "frozen": self.frozen,
            "frozen_hash": self.frozen_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompassState:
        if not data:
            return cls()
        return cls(
            version=int(data.get("version", 1)),
            axes={k: CompassAxis.from_dict(v) for k, v in data.get("axes", {}).items()},
            directives=[
                CompassDirective.from_dict(d) for d in data.get("directives", [])
            ],
            gap_report=GapReport.from_dict(data.get("gap_report", {})),
            forged_at=float(data.get("forged_at", 0.0)),
            frozen=bool(data.get("frozen", False)),
            frozen_hash=str(data.get("frozen_hash", "")),
        )


# ── Depth Scoring ────────────────────────────────────────────────────


def _has_causal_marker(text: str) -> bool:
    """Check if text contains causal reasoning markers."""
    return bool(_CAUSAL_MARKERS.search(text))


def _has_contrastive_marker(text: str) -> bool:
    """Check if text contains contrastive reasoning markers."""
    return bool(_CONTRASTIVE_MARKERS.search(text))


def compute_axis_depth(claims: list[CompassClaim]) -> int:
    """Score axis depth from its claims.

    Depth levels:
    - 0: empty (no claims)
    - 1: surface (1-3 claims, definitional only)
    - 2: structural (4-8 claims, OR any claim with causal/contrastive markers)
    - 3: deep (9+ claims, OR >=3 claims with causal/contrastive markers)
    """
    if not claims:
        return 0

    count = len(claims)
    causal_count = sum(
        1
        for c in claims
        if _has_causal_marker(c.text) or _has_contrastive_marker(c.text)
    )

    if count >= 9 or causal_count >= 3:
        return 3
    if count >= 4 or causal_count >= 1:
        return 2
    return 1


# ── Gap Report ───────────────────────────────────────────────────────


def compute_gap_report(state: CompassState) -> GapReport:
    """Compute gap report from current compass state.

    Spikiness is computed only on required axes (problem, solution).
    Interview is recommended when spikiness > 0.3 or any required axis is empty.
    """
    axis_depths: dict[str, int] = {}
    for name in AXIS_NAMES:
        axis = state.axes.get(name)
        axis_depths[name] = axis.depth if axis else 0

    # Spikiness: stdev of normalized required axis depths / 3.0
    required_depths = [axis_depths.get(a, 0) / 3.0 for a in REQUIRED_AXES]
    if len(required_depths) >= 2:
        mean = sum(required_depths) / len(required_depths)
        variance = sum((d - mean) ** 2 for d in required_depths) / len(required_depths)
        spikiness = variance**0.5
    else:
        spikiness = 0.0

    sparse_axes = [a for a in AXIS_NAMES if axis_depths.get(a, 0) <= 1]
    any_required_empty = any(axis_depths.get(a, 0) == 0 for a in REQUIRED_AXES)
    interview_recommended = spikiness > 0.3 or any_required_empty

    return GapReport(
        axis_depths=axis_depths,
        spikiness=round(spikiness, 4),
        sparse_axes=sparse_axes,
        interview_recommended=interview_recommended,
    )


# ── Staleness ────────────────────────────────────────────────────────


def compute_staleness(state: CompassState, max_age_hours: float = 24.0) -> float:
    """Compute staleness as a 0.0-1.0 ratio.

    0.0 = just forged, 1.0 = at or beyond max_age_hours.
    """
    if state.forged_at <= 0:
        return 1.0
    age_hours = (time.time() - state.forged_at) / 3600.0
    return min(1.0, max(0.0, age_hours / max_age_hours))


# ── Hashing ──────────────────────────────────────────────────────────


def compute_compass_hash(state: CompassState) -> str:
    """Compute a content hash for the compass state."""
    raw = json.dumps(state.to_dict(), sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()[:16]
