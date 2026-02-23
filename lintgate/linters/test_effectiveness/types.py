"""Type definitions for test effectiveness analysis.

Assertion taxonomy — 16 kinds classified by mutation-killing power.
Derived from mutation testing data: find→rfind survivors, -1→+1 sentinel
survivors, and dict key mutation survivors reveal which assertion patterns
actually kill mutants vs. which merely execute code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AssertionKind(str, Enum):
    """Assertion classification by mutation-killing power.

    Categories:
    - Structural (weak): verify existence/type but not value
    - Semantic (strong): verify exact values, catching value-altering mutations
    - Boundary: verify error conditions and size constraints
    - Property-based: verify invariants across random inputs
    """

    # Structural (weak) — these let value-altering mutants survive
    IS_NONE = "is_none"
    IS_NOT_NONE = "is_not_none"
    IS_TRUE = "is_true"
    IS_FALSE = "is_false"
    ISINSTANCE_CHECK = "isinstance_check"

    # Semantic (strong) — these kill value-altering mutants
    EQUALITY = "equality"
    INEQUALITY = "inequality"
    COMPARISON = "comparison"
    STRING_CONTAINS = "string_contains"
    COLLECTION_MEMBERSHIP = "collection_membership"
    DICT_KEY_CHECK = "dict_key_check"
    REGEX_MATCH = "regex_match"

    # Boundary — catch off-by-one and error-path mutations
    RAISES = "raises"
    LENGTH_CHECK = "length_check"
    RANGE_CHECK = "range_check"

    # Property-based — catch broad mutation classes via invariants
    HYPOTHESIS_PROPERTY = "hypothesis_property"


STRENGTH_MAP: dict[AssertionKind, float] = {
    # Structural (weak)
    AssertionKind.IS_NONE: 0.2,
    AssertionKind.IS_NOT_NONE: 0.3,
    AssertionKind.IS_TRUE: 0.2,
    AssertionKind.IS_FALSE: 0.25,
    AssertionKind.ISINSTANCE_CHECK: 0.3,
    # Semantic (strong)
    AssertionKind.EQUALITY: 0.9,
    AssertionKind.INEQUALITY: 0.7,
    AssertionKind.COMPARISON: 0.85,
    AssertionKind.STRING_CONTAINS: 0.75,
    AssertionKind.COLLECTION_MEMBERSHIP: 0.8,
    AssertionKind.DICT_KEY_CHECK: 0.8,
    AssertionKind.REGEX_MATCH: 0.7,
    # Boundary
    AssertionKind.RAISES: 0.7,
    AssertionKind.LENGTH_CHECK: 0.8,
    AssertionKind.RANGE_CHECK: 0.9,
    # Property-based
    AssertionKind.HYPOTHESIS_PROPERTY: 0.8,
}

# Threshold for classifying an assertion as "semantic" (strong)
SEMANTIC_STRENGTH_THRESHOLD = 0.7


@dataclass
class AssertionInfo:
    """A single classified assertion within a test."""

    kind: AssertionKind
    line: int
    strength: float
    target_expression: str = ""  # What's being asserted on (e.g., "result.count")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "kind": self.kind.value,
            "line": self.line,
            "strength": self.strength,
        }
        if self.target_expression:
            d["target_expression"] = self.target_expression
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AssertionInfo:
        return cls(
            kind=AssertionKind(data["kind"]),
            line=data["line"],
            strength=data["strength"],
            target_expression=data.get("target_expression", ""),
        )


@dataclass
class FunctionEffectiveness:
    """Effectiveness analysis for a single source function."""

    function_name: str
    test_count: int = 0
    assertions: list[AssertionInfo] = field(default_factory=list)
    semantic_ratio: float = 0.0
    structural_ratio: float = 0.0
    effectiveness_score: float = 0.0
    mutation_vulnerability: float = 1.0  # 1.0 = fully vulnerable (no tests)

    def compute_scores(self) -> None:
        """Recompute ratios and scores from the current assertions list."""
        if not self.assertions:
            self.semantic_ratio = 0.0
            self.structural_ratio = 0.0
            self.effectiveness_score = 0.0
            self.mutation_vulnerability = 1.0
            return

        total = len(self.assertions)
        semantic_count = sum(
            1 for a in self.assertions if a.strength >= SEMANTIC_STRENGTH_THRESHOLD
        )
        structural_count = total - semantic_count

        self.semantic_ratio = semantic_count / total
        self.structural_ratio = structural_count / total

        # Weighted mean of assertion strengths
        self.effectiveness_score = sum(a.strength for a in self.assertions) / total
        self.mutation_vulnerability = 1.0 - self.effectiveness_score

    def to_dict(self) -> dict[str, Any]:
        return {
            "function_name": self.function_name,
            "test_count": self.test_count,
            "assertions": [a.to_dict() for a in self.assertions],
            "semantic_ratio": round(self.semantic_ratio, 3),
            "structural_ratio": round(self.structural_ratio, 3),
            "effectiveness_score": round(self.effectiveness_score, 3),
            "mutation_vulnerability": round(self.mutation_vulnerability, 3),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FunctionEffectiveness:
        fe = cls(
            function_name=data["function_name"],
            test_count=data.get("test_count", 0),
            assertions=[AssertionInfo.from_dict(a) for a in data.get("assertions", [])],
            semantic_ratio=data.get("semantic_ratio", 0.0),
            structural_ratio=data.get("structural_ratio", 0.0),
            effectiveness_score=data.get("effectiveness_score", 0.0),
            mutation_vulnerability=data.get("mutation_vulnerability", 1.0),
        )
        return fe


@dataclass
class TestEffectivenessManifest:
    """Project-wide test effectiveness inventory."""

    functions: dict[str, FunctionEffectiveness] = field(default_factory=dict)
    project_score: float = 0.0
    file_scores: dict[str, float] = field(default_factory=dict)
    functions_analyzed: int = 0
    mutation_vulnerable_count: int = 0

    def update_metrics(self) -> None:
        """Recalculate aggregate scores from the current functions dictionary."""
        if not self.functions:
            self.project_score = 0.0
            self.functions_analyzed = 0
            self.mutation_vulnerable_count = 0
            return

        self.functions_analyzed = len(self.functions)
        scores = [f.effectiveness_score for f in self.functions.values()]
        self.project_score = sum(scores) / len(scores) if scores else 0.0
        self.mutation_vulnerable_count = sum(
            1 for f in self.functions.values() if f.mutation_vulnerability > 0.7
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "functions": {k: v.to_dict() for k, v in self.functions.items()},
            "project_score": round(self.project_score, 3),
            "file_scores": {k: round(v, 3) for k, v in self.file_scores.items()},
            "functions_analyzed": self.functions_analyzed,
            "mutation_vulnerable_count": self.mutation_vulnerable_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestEffectivenessManifest:
        functions = {}
        for k, v in data.get("functions", {}).items():
            functions[k] = FunctionEffectiveness.from_dict(v)
        manifest = cls(
            functions=functions,
            file_scores=data.get("file_scores", {}),
        )
        manifest.update_metrics()
        return manifest
