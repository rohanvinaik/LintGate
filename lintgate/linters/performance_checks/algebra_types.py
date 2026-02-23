"""Types and data models for algebraic property detection and performance optimizations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class PropertyKind(str, Enum):
    """Types of algebraic properties a function may exhibit."""

    PURE = "pure"
    BOUNDED = "bounded"
    MONOTONIC = "monotonic"
    IDEMPOTENT = "idempotent"
    COMMUTATIVE = "commutative"
    ASSOCIATIVE = "associative"


@dataclass(frozen=True)
class SideEffect:
    """Evidence of impurity within a function."""

    kind: str  # "global_write", "io_call", "mutation", "impure_call", etc.
    node_type: str
    line: int
    detail: str  # human-readable explanation

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SideEffect:
        return cls(**data)


@dataclass(frozen=True)
class PurityResult:
    """The result of a purity analysis on a function."""

    function_name: str
    qualified_name: str  # e.g., "module.Class.method"
    line: int
    is_pure: bool
    confidence: float  # 0.0 - 1.0 (1.0 = verified by Hypothesis)
    side_effects: tuple[SideEffect, ...]
    parameter_count: int
    return_annotation: str | None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["side_effects"] = [asdict(s) for s in self.side_effects]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PurityResult:
        side_effects = tuple(SideEffect(**s) for s in data.get("side_effects", []))
        data_copy = dict(data)
        data_copy["side_effects"] = side_effects
        return cls(**data_copy)


@dataclass(frozen=True)
class BoundSpec:
    """A mathematically bounded range for a function's output."""

    lower: float | None
    upper: float | None
    source: str  # e.g., "clamp", "min_max", "annotation", "ratio"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BoundSpec:
        return cls(**data)


@dataclass(frozen=True)
class AlgebraicProperty:
    """An algebraic property detected for a pure function."""

    kind: PropertyKind
    confidence: float
    evidence: str  # What AST pattern triggered this classification
    bound_spec: BoundSpec | None = None  # Only populated if kind == BOUNDED

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "bound_spec": asdict(self.bound_spec) if self.bound_spec else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AlgebraicProperty:
        kind = PropertyKind(data["kind"])
        bound_spec = BoundSpec(**data["bound_spec"]) if data.get("bound_spec") else None
        return cls(
            kind=kind,
            confidence=data["confidence"],
            evidence=data["evidence"],
            bound_spec=bound_spec,
        )


@dataclass(frozen=True)
class FunctionProperties:
    """Complete algebraic classification for a single function."""

    purity: PurityResult
    properties: tuple[AlgebraicProperty, ...]
    optimization_hints: tuple[str, ...]  # e.g., "cacheable", "parallelizable", "foldable"
    source_file: str | None = None  # File path where this function was found

    def to_dict(self) -> dict[str, Any]:
        d = {
            "purity": self.purity.to_dict(),
            "properties": [p.to_dict() for p in self.properties],
            "optimization_hints": list(self.optimization_hints),
        }
        if self.source_file is not None:
            d["source_file"] = self.source_file
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FunctionProperties:
        purity = PurityResult.from_dict(data["purity"])
        properties = tuple(AlgebraicProperty.from_dict(p) for p in data.get("properties", []))
        return cls(
            purity=purity,
            properties=properties,
            optimization_hints=tuple(data.get("optimization_hints", [])),
            source_file=data.get("source_file"),
        )
