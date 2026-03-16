"""Domain dataclasses for prescriptive specs.

StateVariable, StateTransition, Invariant, ForbiddenBehavior,
TestObligation, RefinementObligation, GenerationConstraint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .predicates import Predicate


@dataclass
class StateVariable:
    name: str
    type_hint: str
    initial_value: str  # JSON-serializable representation
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type_hint": self.type_hint,
            "initial_value": self.initial_value,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StateVariable:
        return cls(
            name=str(data.get("name", "")),
            type_hint=str(data.get("type_hint", "")),
            initial_value=str(data.get("initial_value", "")),
            description=str(data.get("description", "")),
        )


@dataclass
class StateTransition:
    """Init/Next in Lamport terms."""

    name: str
    precondition: Predicate
    postcondition: Predicate
    description: str
    source_claim: str  # "compass:toward:3" or "theory:core_theory:claim_5"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "precondition": self.precondition.to_dict(),
            "postcondition": self.postcondition.to_dict(),
            "description": self.description,
            "source_claim": self.source_claim,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StateTransition:
        return cls(
            name=str(data.get("name", "")),
            precondition=Predicate.from_dict(data.get("precondition", {})),
            postcondition=Predicate.from_dict(data.get("postcondition", {})),
            description=str(data.get("description", "")),
            source_claim=str(data.get("source_claim", "")),
        )


@dataclass
class Invariant:
    """A property that must hold in all reachable states."""

    name: str
    predicate: Predicate
    description: str
    source: str  # Provenance tag
    confidence: float
    kind: str  # "safety" | "liveness" | "alignment"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "predicate": self.predicate.to_dict(),
            "description": self.description,
            "source": self.source,
            "confidence": self.confidence,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Invariant:
        return cls(
            name=str(data.get("name", "")),
            predicate=Predicate.from_dict(data.get("predicate", {})),
            description=str(data.get("description", "")),
            source=str(data.get("source", "")),
            confidence=float(data.get("confidence", 0.0)),
            kind=str(data.get("kind", "safety")),
        )


@dataclass
class ForbiddenBehavior:
    predicate: Predicate
    description: str
    source: str  # "compass:forbidden:2" or "compass:away:1"
    severity: str  # "hard" (forbidden) | "soft" (away)

    def to_dict(self) -> dict[str, Any]:
        return {
            "predicate": self.predicate.to_dict(),
            "description": self.description,
            "source": self.source,
            "severity": self.severity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ForbiddenBehavior:
        return cls(
            predicate=Predicate.from_dict(data.get("predicate", {})),
            description=str(data.get("description", "")),
            source=str(data.get("source", "")),
            severity=str(data.get("severity", "soft")),
        )


@dataclass
class TestObligation:
    kind: str  # From TestPrescription.prescription_kind taxonomy
    description: str
    estimated_info_gain: float
    suggested_assertion: str
    targets_function: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "description": self.description,
            "estimated_info_gain": self.estimated_info_gain,
            "suggested_assertion": self.suggested_assertion,
            "targets_function": self.targets_function,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestObligation:
        return cls(
            kind=str(data.get("kind", "")),
            description=str(data.get("description", "")),
            estimated_info_gain=float(data.get("estimated_info_gain", 0.0)),
            suggested_assertion=str(data.get("suggested_assertion", "")),
            targets_function=str(data.get("targets_function", "")),
        )


TestObligation.__test__ = False  # type: ignore[attr-defined]


@dataclass
class RefinementObligation:
    category: str  # VALUE | SWAP | STATE | BOUNDARY | TYPE
    expected_kill: bool
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "expected_kill": self.expected_kill,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RefinementObligation:
        return cls(
            category=str(data.get("category", "")),
            expected_kill=bool(data.get("expected_kill", False)),
            rationale=str(data.get("rationale", "")),
        )


@dataclass
class GenerationConstraint:
    """Structured constraint for LLM code generation."""

    constraint_type: str  # "must_use" | "must_not_use" | "structure" | "naming" | "pattern"
    predicate: Predicate | None  # None when constraint_type is "naming"/"structure"
    description: str
    priority: int

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "constraint_type": self.constraint_type,
            "description": self.description,
            "priority": self.priority,
        }
        if self.predicate is not None:
            d["predicate"] = self.predicate.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GenerationConstraint:
        pred_data = data.get("predicate")
        return cls(
            constraint_type=str(data.get("constraint_type", "")),
            predicate=Predicate.from_dict(pred_data) if pred_data else None,
            description=str(data.get("description", "")),
            priority=int(data.get("priority", 5)),
        )
