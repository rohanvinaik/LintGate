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
    type_context: dict[str, str] | None = None  # Parameter type annotations when available

    def to_dict(self) -> dict[str, Any]:
        d = {
            "kind": self.kind.value,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "bound_spec": asdict(self.bound_spec) if self.bound_spec else None,
        }
        if self.type_context:
            d["type_context"] = self.type_context
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AlgebraicProperty:
        kind = PropertyKind(data["kind"])
        bound_spec = BoundSpec(**data["bound_spec"]) if data.get("bound_spec") else None
        return cls(
            kind=kind,
            confidence=data["confidence"],
            evidence=data["evidence"],
            bound_spec=bound_spec,
            type_context=data.get("type_context"),
        )


class PurityTier(str, Enum):
    """Three-tier purity classification for optimization safety.

    PURE: Mathematically pure — no side effects, deterministic, safe to cache/parallelize.
    STABLE_READ: Impure but read-only/stable — reads external state but doesn't mutate.
        Safe for disk caches, preload strategies, memoized DB snapshots.
    STATEFUL: Mutates state or non-deterministic. Requires careful handling.
    """

    PURE = "pure"
    STABLE_READ = "stable_read"
    STATEFUL = "stateful"


@dataclass(frozen=True)
class FunctionProperties:
    """Complete algebraic classification for a single function."""

    purity: PurityResult
    properties: tuple[AlgebraicProperty, ...]
    optimization_hints: tuple[str, ...]  # e.g., "cacheable", "parallelizable", "foldable"
    source_file: str | None = None  # File path where this function was found
    extraction_safety: str = "safe"  # "safe" | "needs_module_state" | "unsafe"
    purity_tier: PurityTier = PurityTier.STATEFUL  # Defaults to most conservative

    def to_dict(self) -> dict[str, Any]:
        d = {
            "purity": self.purity.to_dict(),
            "properties": [p.to_dict() for p in self.properties],
            "optimization_hints": list(self.optimization_hints),
            "extraction_safety": self.extraction_safety,
            "purity_tier": self.purity_tier.value,
        }
        if self.source_file is not None:
            d["source_file"] = self.source_file
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FunctionProperties:
        purity = PurityResult.from_dict(data["purity"])
        properties = tuple(AlgebraicProperty.from_dict(p) for p in data.get("properties", []))
        tier_str = data.get("purity_tier", "stateful")
        try:
            tier = PurityTier(tier_str)
        except ValueError:
            tier = PurityTier.STATEFUL
        return cls(
            purity=purity,
            properties=properties,
            optimization_hints=tuple(data.get("optimization_hints", [])),
            source_file=data.get("source_file"),
            extraction_safety=data.get("extraction_safety", "safe"),
            purity_tier=tier,
        )


# ── Purity tier classification ────────────────────────────────────────

# Side-effect kinds that indicate read-only access (not mutation)
_READ_ONLY_SIDE_EFFECTS = frozenset({
    "impure_call",  # calls an impure function (may be read-only like db.query)
    "attribute_read",  # reads an attribute (not mutation)
})

# Side-effect kinds that indicate true mutation
_MUTATION_SIDE_EFFECTS = frozenset({
    "global_write",
    "io_call",
    "mutation",
    "nonlocal_write",
})


def classify_purity_tier(purity: PurityResult) -> PurityTier:
    """Classify a function into the three-tier purity model.

    PURE: is_pure=True (no side effects detected).
    STABLE_READ: Not pure, but all detected side effects are read-only
        (impure_call, attribute_read). No mutation evidence.
    STATEFUL: Has mutation side effects (global_write, io_call, mutation).
    """
    if purity.is_pure:
        return PurityTier.PURE

    if not purity.side_effects:
        # No side effects but not marked pure — conservative default
        return PurityTier.STATEFUL

    effect_kinds = {se.kind for se in purity.side_effects}

    # If ALL side effects are read-only, classify as STABLE_READ
    has_mutation = bool(effect_kinds & _MUTATION_SIDE_EFFECTS)
    if not has_mutation:
        return PurityTier.STABLE_READ

    return PurityTier.STATEFUL
