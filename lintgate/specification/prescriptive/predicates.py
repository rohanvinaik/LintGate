"""Predicate IR — typed, normalizable predicates + claim compiler.

The atomic unit of formal semantics for prescriptive specs.
"""

from __future__ import annotations

import json
import re
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
