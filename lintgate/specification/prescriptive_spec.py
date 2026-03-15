"""PrescriptiveSpec IR — Lamport-inspired behavioral contracts for prospective code generation.

Core dataclasses:
- Predicate IR (typed, normalizable, not stringly-typed)
- Domain types: StateVariable, StateTransition, Invariant, ForbiddenBehavior, etc.
- PrescriptiveSpec: the top-level specification record
- PrescriptiveSpecComposer: composes specs from theory + compass
- Persistence: save/load/index specs
- Target resolution: resolve_targets for theory freeze flow
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ── Predicate IR ──────────────────────────────────────────────────────


class PredicateOp(str, Enum):
    """Operators for the predicate IR."""

    EQ = "eq"
    NEQ = "neq"
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
    IN = "in"
    NOT_IN = "not_in"
    IS_TYPE = "is_type"
    HAS_ATTR = "has_attr"
    CALLS = "calls"
    NOT = "not"
    AND = "and"
    OR = "or"
    TRUE = "true"
    CUSTOM = "custom"
    # Extended ops — AST-checkable semantic predicates
    PURE = "pure"  # function has no side effects (no global/nonlocal writes, no I/O)
    RETURNS_NON_NULL = "returns_non_null"  # no bare `return` or `return None`
    RAISES = "raises"  # function body contains `raise ExceptionType`
    NO_RAISE = "no_raise"  # function body contains no `raise` statements
    PARAM_COUNT_LTE = "param_count_lte"  # parameter count ≤ value


@dataclass
class Predicate:
    """Typed predicate node — the atomic unit of formal semantics.

    For leaf predicates: op + subject + (object or value).
    For compound: op=AND|OR|NOT + operands (list[Predicate]).
    For CUSTOM: description is the only meaningful field (not auto-evaluated).
    """

    op: PredicateOp
    subject: str = ""
    target: str = ""  # comparison target (variable name)
    value: Any = None
    operands: list[Predicate] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"op": self.op.value}
        if self.subject:
            d["subject"] = self.subject
        if self.target:
            d["target"] = self.target
        if self.value is not None:
            d["value"] = self.value
        if self.operands:
            d["operands"] = [o.to_dict() for o in self.operands]
        if self.description:
            d["description"] = self.description
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Predicate:
        if not data:
            return cls(op=PredicateOp.TRUE, description="empty")
        return cls(
            op=PredicateOp(data.get("op", "true")),
            subject=str(data.get("subject", "")),
            target=str(data.get("target", data.get("object", ""))),
            value=data.get("value"),
            operands=[cls.from_dict(o) for o in data.get("operands", [])],
            description=str(data.get("description", "")),
        )

    def normalize(self) -> Predicate:
        """Canonical form for comparison: sorted operands, flattened AND/OR."""
        if self.op in (PredicateOp.AND, PredicateOp.OR):
            # Flatten nested same-op and sort by description then subject
            flat: list[Predicate] = []
            for child in self.operands:
                normed = child.normalize()
                if normed.op == self.op:
                    flat.extend(normed.operands)
                else:
                    flat.append(normed)
            flat.sort(key=lambda p: (p.description, p.subject, p.op.value))
            return Predicate(
                op=self.op,
                operands=flat,
                description=self.description,
            )
        if self.op == PredicateOp.NOT and self.operands:
            return Predicate(
                op=PredicateOp.NOT,
                operands=[self.operands[0].normalize()],
                description=self.description,
            )
        return self

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Predicate):
            return NotImplemented
        return self.normalize().to_dict() == other.normalize().to_dict()

    def __hash__(self) -> int:
        return hash(json.dumps(self.normalize().to_dict(), sort_keys=True))


# ── Convenience constructors ──────────────────────────────────────────


def pred_eq(subject: str, value: Any, desc: str = "") -> Predicate:
    return Predicate(op=PredicateOp.EQ, subject=subject, value=value, description=desc)


def pred_neq(subject: str, value: Any, desc: str = "") -> Predicate:
    return Predicate(op=PredicateOp.NEQ, subject=subject, value=value, description=desc)


def pred_lt(subject: str, value: Any, desc: str = "") -> Predicate:
    return Predicate(op=PredicateOp.LT, subject=subject, value=value, description=desc)


def pred_gt(subject: str, value: Any, desc: str = "") -> Predicate:
    return Predicate(op=PredicateOp.GT, subject=subject, value=value, description=desc)


def pred_gte(subject: str, value: Any, desc: str = "") -> Predicate:
    return Predicate(op=PredicateOp.GTE, subject=subject, value=value, description=desc)


def pred_type(subject: str, type_name: str, desc: str = "") -> Predicate:
    return Predicate(op=PredicateOp.IS_TYPE, subject=subject, value=type_name, description=desc)


def pred_and(*preds: Predicate, desc: str = "") -> Predicate:
    return Predicate(op=PredicateOp.AND, operands=list(preds), description=desc)


def pred_or(*preds: Predicate, desc: str = "") -> Predicate:
    return Predicate(op=PredicateOp.OR, operands=list(preds), description=desc)


def pred_not(pred: Predicate, desc: str = "") -> Predicate:
    return Predicate(op=PredicateOp.NOT, operands=[pred], description=desc)


def pred_true(desc: str = "") -> Predicate:
    return Predicate(op=PredicateOp.TRUE, description=desc)


def pred_custom(desc: str) -> Predicate:
    """Escape hatch for predicates that can't be structured. NOT auto-evaluated."""
    return Predicate(op=PredicateOp.CUSTOM, description=desc)


def pred_pure(desc: str = "function must be pure") -> Predicate:
    return Predicate(op=PredicateOp.PURE, description=desc)


def pred_returns_non_null(desc: str = "must not return None") -> Predicate:
    return Predicate(op=PredicateOp.RETURNS_NON_NULL, description=desc)


def pred_raises(exception_type: str, desc: str = "") -> Predicate:
    return Predicate(
        op=PredicateOp.RAISES,
        value=exception_type,
        description=desc or f"must raise {exception_type}",
    )


def pred_no_raise(desc: str = "must not raise exceptions") -> Predicate:
    return Predicate(op=PredicateOp.NO_RAISE, description=desc)


def pred_param_count_lte(max_params: int, desc: str = "") -> Predicate:
    return Predicate(
        op=PredicateOp.PARAM_COUNT_LTE,
        value=max_params,
        description=desc or f"at most {max_params} parameters",
    )


# ── Claim Compiler ───────────────────────────────────────────────────

# Patterns that map natural-language fragments to typed predicates.
# Each entry: (regex, factory_fn). First match wins.
# The factory receives the regex match object and returns a Predicate.

_TYPE_WORDS = frozenset(
    {
        "int",
        "str",
        "float",
        "bool",
        "list",
        "dict",
        "tuple",
        "set",
        "bytes",
        "string",
        "integer",
        "number",
        "array",
        "sequence",
        "mapping",
        "iterator",
        "generator",
        "coroutine",
        "object",
    }
)


def _is_type_word(word: str) -> bool:
    return word.lower() in _TYPE_WORDS


_CLAIM_PATTERNS: list[tuple[re.Pattern, Any]] = [
    # Return type patterns (only match actual type names, not "None", "True", etc.)
    (
        re.compile(r"\b(?:must|should|always)\s+return\s+(\w+)", re.I),
        lambda m: (
            pred_type("result", m.group(1), m.group(0)) if _is_type_word(m.group(1)) else None
        ),
    ),
    (
        re.compile(r"\breturn(?:s|ing)?\s+(?:a\s+)?(\w+)\b", re.I),
        lambda m: (
            pred_type("result", m.group(1), m.group(0)) if _is_type_word(m.group(1)) else None
        ),
    ),
    # Purity
    (re.compile(r"\b(?:must be|is|keep|prefer)\s+pure\b", re.I), lambda m: pred_pure(m.group(0))),
    (re.compile(r"\bno\s+side[\s-]?effects?\b", re.I), lambda m: pred_pure(m.group(0))),
    (re.compile(r"\bside[\s-]?effect[\s-]?free\b", re.I), lambda m: pred_pure(m.group(0))),
    # Non-null return
    (
        re.compile(r"\b(?:must|should)\s+not\s+return\s+None\b", re.I),
        lambda m: pred_returns_non_null(m.group(0)),
    ),
    (
        re.compile(r"\bnever\s+return(?:s)?\s+None\b", re.I),
        lambda m: pred_returns_non_null(m.group(0)),
    ),
    (
        re.compile(r"\bnon[\s-]?null(?:able)?\s+return\b", re.I),
        lambda m: pred_returns_non_null(m.group(0)),
    ),
    # Raises
    (
        re.compile(r"\b(?:must|should)\s+raise\s+(\w+(?:Error|Exception)?)\b", re.I),
        lambda m: pred_raises(m.group(1), m.group(0)),
    ),
    (re.compile(r"\braises?\s+(\w+Error)\b", re.I), lambda m: pred_raises(m.group(1), m.group(0))),
    # No raise / no exceptions
    (re.compile(r"\b(?:must|should)\s+not\s+raise\b", re.I), lambda m: pred_no_raise(m.group(0))),
    (re.compile(r"\bno\s+exceptions?\b", re.I), lambda m: pred_no_raise(m.group(0))),
    # Must call
    (
        re.compile(r"\b(?:must|should|always)\s+call\s+(\w+(?:\.\w+)*)\b", re.I),
        lambda m: Predicate(op=PredicateOp.CALLS, subject=m.group(1), description=m.group(0)),
    ),
    # Must not mutate (→ pure as best approximation)
    (re.compile(r"\b(?:must|should)\s+not\s+mutate\b", re.I), lambda m: pred_pure(m.group(0))),
    (re.compile(r"\bimmutable\s+input", re.I), lambda m: pred_pure(m.group(0))),
    # Parameter count
    (
        re.compile(r"\bat\s+most\s+(\d+)\s+param(?:eter)?s?\b", re.I),
        lambda m: pred_param_count_lte(int(m.group(1)), m.group(0)),
    ),
]


def compile_claim(text: str) -> Predicate:
    """Compile a natural-language claim into a typed Predicate.

    Tries pattern-based matching first. Falls back to pred_custom()
    for claims that don't match any known structural pattern.

    Deduplicates by (op, subject, value) so overlapping patterns
    for the same semantic intent don't produce spurious AND nodes.
    Returns a single Predicate — genuinely compound claims produce pred_and().
    """
    matched: list[Predicate] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for pattern, factory in _CLAIM_PATTERNS:
        m = pattern.search(text)
        if m:
            pred = factory(m)
            if pred is None:
                continue
            key = (pred.op.value, pred.subject, str(pred.value) if pred.value is not None else "")
            if key not in seen_keys:
                matched.append(pred)
                seen_keys.add(key)

    if not matched:
        return pred_custom(text)
    if len(matched) == 1:
        return matched[0]
    return pred_and(*matched, desc=text)


# ── Domain dataclasses ────────────────────────────────────────────────


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


# ── PrescriptiveSpec ──────────────────────────────────────────────────


@dataclass
class PrescriptiveSpec:
    """Top-level prescriptive specification record."""

    spec_id: str
    target_key: str  # module::function or "new:function_name"
    problem_class: str  # "pure" | "stateful" | "distributed"
    mode: str  # "prospective" | "retrospective"

    # Interface
    parameters: list[dict[str, str]] = field(default_factory=list)
    return_type: str = ""
    return_description: str = ""

    # State (empty for pure functions)
    state_variables: list[StateVariable] = field(default_factory=list)
    allowed_transitions: list[StateTransition] = field(default_factory=list)

    # Behavioral contract
    invariants: list[Invariant] = field(default_factory=list)
    forbidden_behaviors: list[ForbiddenBehavior] = field(default_factory=list)
    allowed_side_effects: list[str] = field(default_factory=list)
    algebraic_laws: list[dict[str, Any]] = field(default_factory=list)

    # Obligations
    test_obligations: list[TestObligation] = field(default_factory=list)
    refinement_obligations: list[RefinementObligation] = field(default_factory=list)

    # LLM generation constraints
    generation_constraints: list[GenerationConstraint] = field(default_factory=list)

    # Sigma
    prescriptive_sigma: int = 0

    # Provenance
    compass_hash: str = ""
    theory_claims_used: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "target_key": self.target_key,
            "problem_class": self.problem_class,
            "mode": self.mode,
            "parameters": self.parameters,
            "return_type": self.return_type,
            "return_description": self.return_description,
            "state_variables": [sv.to_dict() for sv in self.state_variables],
            "allowed_transitions": [t.to_dict() for t in self.allowed_transitions],
            "invariants": [inv.to_dict() for inv in self.invariants],
            "forbidden_behaviors": [fb.to_dict() for fb in self.forbidden_behaviors],
            "allowed_side_effects": self.allowed_side_effects,
            "algebraic_laws": self.algebraic_laws,
            "test_obligations": [to.to_dict() for to in self.test_obligations],
            "refinement_obligations": [ro.to_dict() for ro in self.refinement_obligations],
            "generation_constraints": [gc.to_dict() for gc in self.generation_constraints],
            "prescriptive_sigma": self.prescriptive_sigma,
            "compass_hash": self.compass_hash,
            "theory_claims_used": self.theory_claims_used,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PrescriptiveSpec:
        return cls(
            spec_id=str(data.get("spec_id", "")),
            target_key=str(data.get("target_key", "")),
            problem_class=str(data.get("problem_class", "pure")),
            mode=str(data.get("mode", "prospective")),
            parameters=data.get("parameters", []),
            return_type=str(data.get("return_type", "")),
            return_description=str(data.get("return_description", "")),
            state_variables=[StateVariable.from_dict(sv) for sv in data.get("state_variables", [])],
            allowed_transitions=[
                StateTransition.from_dict(t) for t in data.get("allowed_transitions", [])
            ],
            invariants=[Invariant.from_dict(inv) for inv in data.get("invariants", [])],
            forbidden_behaviors=[
                ForbiddenBehavior.from_dict(fb) for fb in data.get("forbidden_behaviors", [])
            ],
            allowed_side_effects=data.get("allowed_side_effects", []),
            algebraic_laws=data.get("algebraic_laws", []),
            test_obligations=[
                TestObligation.from_dict(to) for to in data.get("test_obligations", [])
            ],
            refinement_obligations=[
                RefinementObligation.from_dict(ro) for ro in data.get("refinement_obligations", [])
            ],
            generation_constraints=[
                GenerationConstraint.from_dict(gc) for gc in data.get("generation_constraints", [])
            ],
            prescriptive_sigma=int(data.get("prescriptive_sigma", 0)),
            compass_hash=str(data.get("compass_hash", "")),
            theory_claims_used=data.get("theory_claims_used", []),
            created_at=float(data.get("created_at", 0.0)),
        )


# ── Persistence ───────────────────────────────────────────────────────

_SPEC_DIR = ".lintgate/prescriptive_specs"


def _target_hash(target_key: str) -> str:
    return hashlib.sha256(target_key.encode()).hexdigest()[:16]


def save_spec(project_root: str, spec: PrescriptiveSpec) -> None:
    """Save a PrescriptiveSpec to disk and update the index."""
    spec_dir = os.path.join(project_root, _SPEC_DIR)
    os.makedirs(spec_dir, exist_ok=True)

    h = _target_hash(spec.target_key)
    spec_path = os.path.join(spec_dir, f"{h}.json")
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec.to_dict(), f, indent=2)

    # Update index
    index_path = os.path.join(spec_dir, "index.json")
    index = _load_index(index_path)
    index[spec.target_key] = spec.spec_id
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)


def load_spec(project_root: str, target_key: str) -> PrescriptiveSpec | None:
    """Load a PrescriptiveSpec by target_key."""
    spec_dir = os.path.join(project_root, _SPEC_DIR)
    h = _target_hash(target_key)
    spec_path = os.path.join(spec_dir, f"{h}.json")
    if not os.path.isfile(spec_path):
        return None
    with open(spec_path, encoding="utf-8") as f:
        data = json.load(f)
    return PrescriptiveSpec.from_dict(data)


def load_all_specs(project_root: str) -> dict[str, PrescriptiveSpec]:
    """Load all PrescriptiveSpecs from disk."""
    spec_dir = os.path.join(project_root, _SPEC_DIR)
    if not os.path.isdir(spec_dir):
        return {}
    result: dict[str, PrescriptiveSpec] = {}
    for fname in os.listdir(spec_dir):
        if fname == "index.json" or not fname.endswith(".json"):
            continue
        fpath = os.path.join(spec_dir, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            spec = PrescriptiveSpec.from_dict(data)
            result[spec.target_key] = spec
        except (OSError, ValueError, KeyError):
            continue
    return result


def load_spec_index(project_root: str) -> dict[str, str]:
    """Load spec index (target_key → spec_id) — fast, no full spec load."""
    index_path = os.path.join(project_root, _SPEC_DIR, "index.json")
    return _load_index(index_path)


def spec_coverage(project_root: str, function_keys: list[str]) -> dict[str, Any]:
    """Compute prescriptive spec coverage over a set of function keys."""
    index = load_spec_index(project_root)
    covered = [k for k in function_keys if k in index]
    total = len(function_keys)
    return {
        "total_functions": total,
        "covered": len(covered),
        "coverage_ratio": len(covered) / total if total else 0.0,
        "uncovered": [k for k in function_keys if k not in index],
    }


def _load_index(index_path: str) -> dict[str, str]:
    if not os.path.isfile(index_path):
        return {}
    try:
        with open(index_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ── Target Resolution ─────────────────────────────────────────────────

# Pattern for identifying Python symbols in prose
_SYMBOL_PATTERN = re.compile(
    r"\b([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*)\b"  # snake_case dotted names
    r"|"
    r"\b([A-Z][a-zA-Z0-9]*(?:\.[A-Z][a-zA-Z0-9]*)*)\b",  # CamelCase dotted names
)

# Pattern for PSPEC annotations on function defs
_PSPEC_ANNOTATION = re.compile(r"#\s*PSPEC:\s*(.+)")


@dataclass
class ResolvedTarget:
    """A target matched by the resolver, with provenance."""

    target_key: str  # module::function
    source: str  # "explicit" | "stub" | "claim_match"
    matched_claim: str
    confidence: float  # 1.0 for explicit, 0.8 for stub, variable for claim_match

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_key": self.target_key,
            "source": self.source,
            "matched_claim": self.matched_claim,
            "confidence": self.confidence,
        }


def resolve_targets(
    compass: Any,  # CompassState
    theory_profile: dict[str, Any],
    project_root: str,
    explicit_targets: list[str] | None = None,
) -> list[ResolvedTarget]:
    """Determine which functions deserve prescriptive specs.

    Strategy 1 — Explicit targets (confidence=1.0)
    Strategy 2 — Interface stubs with # PSPEC: annotations (confidence=0.8)
    Strategy 3 — Claim-to-symbol matching (confidence=variable, ≥0.5 to emit)
    """
    results: list[ResolvedTarget] = []
    seen: set[str] = set()

    # Strategy 1: Explicit targets
    if explicit_targets:
        for target in explicit_targets:
            if target not in seen:
                results.append(
                    ResolvedTarget(
                        target_key=target,
                        source="explicit",
                        matched_claim="user-specified",
                        confidence=1.0,
                    )
                )
                seen.add(target)

    # Strategy 2: Scan for PSPEC stubs
    stubs = _scan_pspec_stubs(project_root)
    for target_key, annotation in stubs:
        if target_key not in seen:
            results.append(
                ResolvedTarget(
                    target_key=target_key,
                    source="stub",
                    matched_claim=annotation,
                    confidence=0.8,
                )
            )
            seen.add(target_key)

    # Strategy 3: Claim-to-symbol matching
    claim_matches = _match_claims_to_symbols(compass, theory_profile, project_root, seen)
    results.extend(claim_matches)

    return results


def _scan_pspec_stubs(project_root: str) -> list[tuple[str, str]]:
    """Scan project for # PSPEC: annotations on stub functions."""
    stubs: list[tuple[str, str]] = []
    for root, dirs, files in os.walk(project_root):
        # Skip hidden dirs and common non-source dirs
        rel = os.path.relpath(root, project_root)
        if rel != ".":
            parts = rel.split(os.sep)
            if any(part.startswith(".") for part in parts):
                dirs.clear()
                continue
            if any(part in ("node_modules", "__pycache__", ".git") for part in parts):
                dirs.clear()
                continue
        # Prune hidden/system subdirs from further traversal
        dirs[:] = [
            d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__")
        ]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    source = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            # Check for PSPEC annotations
            lines = source.split("\n")
            for i, line in enumerate(lines):
                m = _PSPEC_ANNOTATION.search(line)
                if not m:
                    continue
                annotation = m.group(1).strip()
                # Find the function def on the next few lines
                func_name = _find_function_at(source, i + 1)
                if func_name:
                    from lintgate.keys import canonical_function_key

                    relpath = os.path.relpath(fpath, project_root).replace(os.sep, "/")
                    target_key = canonical_function_key(relpath, func_name)
                    stubs.append((target_key, annotation))

    return stubs


def _find_function_at(source: str, annotation_line: int) -> str | None:
    """Find function name near annotation_line (0-indexed)."""
    import ast as _ast

    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return None

    for node in _ast.walk(tree):
        if (
            isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef))
            and abs(node.lineno - (annotation_line + 1)) <= 3
        ):
            return node.name
    return None


def _match_claims_to_symbols(
    compass: Any,
    theory_profile: dict[str, Any],
    project_root: str,
    seen: set[str],
) -> list[ResolvedTarget]:
    """Extract symbols from claims, match against project functions."""
    results: list[ResolvedTarget] = []

    # Collect all claim texts with confidence
    claim_items: list[tuple[str, float, str]] = []  # (text, confidence, source_tag)

    # From compass directives
    if hasattr(compass, "directives"):
        for i, d in enumerate(compass.directives):
            conf = 0.7 if d.kind == "toward" else 0.6
            claim_items.append((d.text, conf, f"compass:{d.kind}:{i}"))

    # From compass axis claims
    if hasattr(compass, "axes"):
        for axis_name, axis in compass.axes.items():
            for j, claim in enumerate(axis.claims):
                claim_items.append((claim.text, claim.confidence, f"compass:{axis_name}:{j}"))

    # From theory profile claims
    for facet_name, facet_data in theory_profile.items():
        if isinstance(facet_data, dict):
            claims = facet_data.get("claims", [])
            for k, claim in enumerate(claims):
                text = claim.get("text", "") if isinstance(claim, dict) else str(claim)
                conf = claim.get("confidence", 0.7) if isinstance(claim, dict) else 0.7
                claim_items.append((text, conf, f"theory:{facet_name}:{k}"))

    # Extract symbols from claims
    symbols_from_claims: list[tuple[str, float, str]] = []
    for text, conf, source in claim_items:
        if conf < 0.5:
            continue
        for match in _SYMBOL_PATTERN.finditer(text):
            sym = match.group(1) or match.group(2)
            if sym and len(sym) > 3 and sym.lower() not in _STOPWORDS:
                symbols_from_claims.append((sym, conf, source))

    # Build a simple function index from project
    func_index = _build_func_index(project_root)

    # Match symbols to functions
    for sym, conf, source in symbols_from_claims:
        for func_key in func_index:
            func_name = func_key.split("::")[-1] if "::" in func_key else func_key
            if sym == func_name or sym.endswith(f".{func_name}"):
                match_quality = 1.0 if sym == func_name else 0.8
                final_conf = conf * match_quality
                if final_conf >= 0.5 and func_key not in seen:
                    results.append(
                        ResolvedTarget(
                            target_key=func_key,
                            source="claim_match",
                            matched_claim=source,
                            confidence=round(final_conf, 2),
                        )
                    )
                    seen.add(func_key)

    return results


def _build_func_index(project_root: str) -> set[str]:
    """Build a set of function keys from project source files (shallow scan)."""
    import ast as _ast

    func_keys: set[str] = set()
    # Only scan top-level Python files and first-level packages
    for root, dirs, files in os.walk(project_root):
        rel = os.path.relpath(root, project_root)
        depth = len(rel.split(os.sep)) if rel != "." else 0
        if depth > 2:
            dirs.clear()
            continue
        if rel != "." and any(part.startswith(".") for part in rel.split(os.sep)):
            dirs.clear()
            continue
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for fname in files:
            if not fname.endswith(".py") or fname.startswith("test_"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    tree = _ast.parse(f.read())
            except (OSError, SyntaxError):
                continue
            relpath = os.path.relpath(fpath, project_root).replace(os.sep, "/")
            from lintgate.keys import canonical_function_key

            for node in _ast.walk(tree):
                if isinstance(
                    node, (_ast.FunctionDef, _ast.AsyncFunctionDef)
                ) and not node.name.startswith("_"):
                    func_keys.add(canonical_function_key(relpath, node.name))
    return func_keys


_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "from",
        "are",
        "not",
        "but",
        "has",
        "have",
        "was",
        "were",
        "been",
        "being",
        "will",
        "should",
        "would",
        "could",
        "when",
        "where",
        "which",
        "what",
        "than",
        "then",
        "each",
        "every",
        "other",
        "some",
        "most",
        "more",
        "also",
        "just",
        "only",
        "both",
        "such",
        "very",
        "true",
        "false",
        "none",
        "self",
        "return",
        "class",
        "import",
        "def",
        "pass",
        "raise",
        "try",
        "except",
        "finally",
        "while",
        "else",
        "elif",
        "yield",
        "async",
        "await",
    }
)


# ── Composer ──────────────────────────────────────────────────────────

# Reuse causal/contrastive markers from compass.py
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


class PrescriptiveSpecComposer:
    """Compose PrescriptiveSpecs from theory + compass."""

    def compose_prospective(
        self,
        target_key: str,
        compass: Any,  # CompassState
        theory_profile: dict[str, Any],
        interface_hint: dict[str, Any] | None = None,
    ) -> PrescriptiveSpec:
        """Build spec from theory + compass alone (no existing code)."""
        problem_class = self._classify_problem_class(None, interface_hint)

        invariants = self._extract_invariants_from_compass(compass)
        invariants.extend(self._theory_to_invariants(theory_profile, target_key))
        forbidden = self._extract_forbidden_from_compass(compass)

        # Build interface from hint
        params: list[dict[str, str]] = []
        return_type = ""
        return_desc = ""
        if interface_hint:
            params = interface_hint.get("parameters", [])
            return_type = interface_hint.get("return_type", "")
            return_desc = interface_hint.get("return_description", "")

        # State variables for stateful
        state_vars: list[StateVariable] = []
        transitions: list[StateTransition] = []
        if problem_class == "stateful" and interface_hint:
            for sv in interface_hint.get("state_variables", []):
                state_vars.append(StateVariable.from_dict(sv))
            for t in interface_hint.get("transitions", []):
                transitions.append(StateTransition.from_dict(t))

        spec = PrescriptiveSpec(
            spec_id=hashlib.sha256(f"{target_key}:{time.time()}".encode()).hexdigest()[:12],
            target_key=target_key,
            problem_class=problem_class,
            mode="prospective",
            parameters=params,
            return_type=return_type,
            return_description=return_desc,
            state_variables=state_vars,
            allowed_transitions=transitions,
            invariants=invariants,
            forbidden_behaviors=forbidden,
            compass_hash=getattr(compass, "frozen_hash", ""),
            theory_claims_used=self._collect_claim_sources(invariants, forbidden),
        )

        spec.generation_constraints = self._build_generation_constraints(spec)
        spec.prescriptive_sigma = self._compute_prescriptive_sigma(spec)
        return spec

    def compose_retrospective(
        self,
        func_spec: Any,  # FunctionSpecification
        compass: Any,  # CompassState
        theory_profile: dict[str, Any],
        algebra: Any | None = None,  # FunctionProperties
        mutation_state: dict[str, Any] | None = None,
    ) -> PrescriptiveSpec:
        """Enrich existing FunctionSpecification with prescriptive contract."""
        problem_class = self._classify_problem_class(func_spec, None)

        invariants = self._extract_invariants_from_compass(compass)
        invariants.extend(self._theory_to_invariants(theory_profile, func_spec.function_key))
        forbidden = self._extract_forbidden_from_compass(compass)

        # Algebraic laws
        alg_laws: list[dict[str, Any]] = []
        if algebra and hasattr(algebra, "algebraic_properties"):
            for prop in algebra.algebraic_properties:
                alg_laws.append(prop.to_dict() if hasattr(prop, "to_dict") else {"name": str(prop)})

        # Refinement obligations from mutation state
        refinement: list[RefinementObligation] = []
        if mutation_state:
            for cat_data in mutation_state.get("per_category", []):
                cat = cat_data.get("category", "")
                survived = cat_data.get("survived", 0)
                if survived > 0:
                    refinement.append(
                        RefinementObligation(
                            category=cat,
                            expected_kill=True,
                            rationale=f"{survived} mutants survived in {cat}",
                        )
                    )

        # Test obligations from existing spec gaps + design signals
        test_obs: list[TestObligation] = []
        sigma = getattr(func_spec.core, "estimated_sigma", 0)
        assertions = getattr(func_spec.traceability, "assertion_count", 0)
        if sigma > assertions:
            test_obs.append(
                TestObligation(
                    kind="exact_value",
                    description=f"Close specification gap: sigma={sigma}, assertions={assertions}",
                    estimated_info_gain=min(1.0, (sigma - assertions) / max(sigma, 1)),
                    suggested_assertion="assert func(...) == expected",
                    targets_function=func_spec.function_key,
                )
            )

        # Enrich from test design signals
        design = getattr(func_spec, "design_signals", None)
        if design:
            boundary_pts = getattr(design, "boundary_points", 0)
            equiv_parts = getattr(design, "equivalence_partitions", 0)
            if boundary_pts > 0 and assertions < boundary_pts:
                test_obs.append(
                    TestObligation(
                        kind="boundary",
                        description=f"{boundary_pts} boundary points detected, {assertions} assertions cover them",
                        estimated_info_gain=min(1.0, boundary_pts / max(sigma, 1)),
                        suggested_assertion="assert func(boundary_value) == expected",
                        targets_function=func_spec.function_key,
                    )
                )
            if equiv_parts > 1:
                test_obs.append(
                    TestObligation(
                        kind="equivalence",
                        description=f"{equiv_parts} equivalence partitions — test representative from each",
                        estimated_info_gain=min(1.0, equiv_parts / max(sigma, 1)),
                        suggested_assertion="assert func(partition_rep) == expected",
                        targets_function=func_spec.function_key,
                    )
                )

        # Enrich from traceability — prescription history as prior knowledge
        trace = getattr(func_spec, "traceability", None)
        prior_prescriptions = getattr(trace, "prescription_history", []) if trace else []
        covering_tests = getattr(trace, "covering_tests", []) if trace else []

        spec = PrescriptiveSpec(
            spec_id=hashlib.sha256(f"{func_spec.function_key}:{time.time()}".encode()).hexdigest()[
                :12
            ],
            target_key=func_spec.function_key,
            problem_class=problem_class,
            mode="retrospective",
            return_type="",
            invariants=invariants,
            forbidden_behaviors=forbidden,
            algebraic_laws=alg_laws,
            test_obligations=test_obs,
            refinement_obligations=refinement,
            compass_hash=getattr(compass, "frozen_hash", ""),
            theory_claims_used=self._collect_claim_sources(invariants, forbidden),
        )
        # Attach traceability metadata for downstream consumers
        if prior_prescriptions or covering_tests:
            spec.theory_claims_used.append(
                f"traceability:{len(covering_tests)}_tests,{len(prior_prescriptions)}_prior_prescriptions"
            )

        spec.generation_constraints = self._build_generation_constraints(spec)
        spec.prescriptive_sigma = self._compute_prescriptive_sigma(spec)
        return spec

    def _classify_problem_class(
        self,
        func_spec: Any | None,
        interface_hint: dict[str, Any] | None,
    ) -> str:
        """Pure/stateful/distributed from TestabilityProfile or declared hints."""
        if interface_hint:
            declared = interface_hint.get("problem_class")
            if declared in ("pure", "stateful", "distributed"):
                return declared
        if func_spec is not None:
            if getattr(func_spec.core, "is_pure", False):
                return "pure"
            if getattr(func_spec.testability, "is_stateful", False):
                return "stateful"
        return "pure"

    def _extract_invariants_from_compass(self, compass: Any) -> list[Invariant]:
        """Map toward directives → invariants via claim compiler."""
        invariants: list[Invariant] = []
        if not hasattr(compass, "directives"):
            return invariants

        for i, directive in enumerate(compass.directives):
            if directive.kind != "toward":
                continue
            invariants.append(
                Invariant(
                    name=f"toward_{i}",
                    predicate=compile_claim(directive.text),
                    description=directive.text,
                    source=f"compass:toward:{i}",
                    confidence=0.7,
                    kind="alignment",
                )
            )

        return invariants

    def _extract_forbidden_from_compass(self, compass: Any) -> list[ForbiddenBehavior]:
        """Map forbidden + away directives → ForbiddenBehavior via claim compiler."""
        forbidden: list[ForbiddenBehavior] = []
        if not hasattr(compass, "directives"):
            return forbidden

        for i, directive in enumerate(compass.directives):
            if directive.kind == "forbidden":
                forbidden.append(
                    ForbiddenBehavior(
                        predicate=compile_claim(directive.text),
                        description=directive.text,
                        source=f"compass:forbidden:{i}",
                        severity="hard",
                    )
                )
            elif directive.kind == "away":
                forbidden.append(
                    ForbiddenBehavior(
                        predicate=compile_claim(directive.text),
                        description=directive.text,
                        source=f"compass:away:{i}",
                        severity="soft",
                    )
                )

        return forbidden

    def _theory_to_invariants(
        self, theory_profile: dict[str, Any], _target_key: str
    ) -> list[Invariant]:
        """Extract invariants from theory claims. Confidence-gated (≥0.6)."""
        invariants: list[Invariant] = []

        for facet_name, facet_data in theory_profile.items():
            if not isinstance(facet_data, dict):
                continue
            claims = facet_data.get("claims", [])
            for k, claim in enumerate(claims):
                text = claim.get("text", "") if isinstance(claim, dict) else str(claim)
                conf = claim.get("confidence", 0.7) if isinstance(claim, dict) else 0.7

                if conf < 0.6:
                    continue

                # Boost confidence for claims with causal/contrastive markers
                has_causal = bool(_CAUSAL_MARKERS.search(text))
                has_contrastive = bool(_CONTRASTIVE_MARKERS.search(text))
                if has_causal or has_contrastive:
                    conf = min(1.0, conf + 0.1)

                kind = "safety"
                if facet_name in ("alignment", "core_theory"):
                    kind = "alignment"
                elif facet_name in ("anti_patterns",):
                    kind = "safety"

                invariants.append(
                    Invariant(
                        name=f"theory_{facet_name}_{k}",
                        predicate=compile_claim(text),
                        description=text,
                        source=f"theory:{facet_name}:{k}",
                        confidence=conf,
                        kind=kind,
                    )
                )

        return invariants

    def _build_generation_constraints(self, spec: PrescriptiveSpec) -> list[GenerationConstraint]:
        """Compose generation constraints from invariants + forbidden + algebraic laws."""
        constraints: list[GenerationConstraint] = []

        # From invariants
        for inv in spec.invariants:
            constraints.append(
                GenerationConstraint(
                    constraint_type="must_use" if inv.kind == "safety" else "pattern",
                    predicate=inv.predicate,
                    description=f"Invariant: {inv.description}",
                    priority=3 if inv.confidence >= 0.8 else 5,
                )
            )

        # From forbidden behaviors
        for fb in spec.forbidden_behaviors:
            constraints.append(
                GenerationConstraint(
                    constraint_type="must_not_use",
                    predicate=fb.predicate,
                    description=f"Forbidden: {fb.description}",
                    priority=1 if fb.severity == "hard" else 3,
                )
            )

        # From algebraic laws
        for law in spec.algebraic_laws:
            name = law.get("name", law.get("property_name", ""))
            constraints.append(
                GenerationConstraint(
                    constraint_type="pattern",
                    predicate=None,
                    description=f"Algebraic law: {name}",
                    priority=4,
                )
            )

        constraints.sort(key=lambda c: c.priority)
        return constraints

    def _compute_prescriptive_sigma(self, spec: PrescriptiveSpec) -> int:
        """σ_prescriptive from spec structure."""
        from .prescriptive_sigma import estimate_prescriptive_sigma

        return estimate_prescriptive_sigma(spec)

    def _collect_claim_sources(
        self,
        invariants: list[Invariant],
        forbidden: list[ForbiddenBehavior],
    ) -> list[str]:
        sources: list[str] = []
        for inv in invariants:
            if inv.source and inv.source not in sources:
                sources.append(inv.source)
        for fb in forbidden:
            if fb.source and fb.source not in sources:
                sources.append(fb.source)
        return sources
