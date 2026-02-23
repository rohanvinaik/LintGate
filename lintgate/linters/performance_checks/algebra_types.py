"""Types and data models for algebraic property detection and performance optimizations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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


@dataclass(frozen=True)
class BoundSpec:
    """A mathematically bounded range for a function's output."""

    lower: float | None
    upper: float | None
    source: str  # e.g., "clamp", "min_max", "annotation", "ratio"


@dataclass(frozen=True)
class AlgebraicProperty:
    """An algebraic property detected for a pure function."""

    kind: PropertyKind
    confidence: float
    evidence: str  # What AST pattern triggered this classification
    bound_spec: BoundSpec | None = None  # Only populated if kind == BOUNDED


@dataclass(frozen=True)
class FunctionProperties:
    """Complete algebraic classification for a single function."""

    purity: PurityResult
    properties: tuple[AlgebraicProperty, ...]
    optimization_hints: tuple[str, ...]  # e.g., "cacheable", "parallelizable", "foldable"
