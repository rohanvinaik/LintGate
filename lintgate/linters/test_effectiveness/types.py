"""Type definitions for test effectiveness analysis.

Assertion taxonomy — 16 kinds classified by mutation-killing power.
Derived from mutation testing data: find→rfind survivors, -1→+1 sentinel
survivors, and dict key mutation survivors reveal which assertion patterns
actually kill mutants vs. which merely execute code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class EffectivenessWeakness(str, Enum):
    """Semantic weakness taxonomy for test effectiveness.

    Waterfall priority (first match wins):
    1. UNTESTED: No mappings found.
    2. GENUINELY_WEAK: Low semantic ratio (<0.3), low effectiveness (<0.4).
    3. SENTINEL_HEAVY: High sentinel count (>30%) but isolated from value checks.
    4. BOOLEAN_CONTRACT_HEAVY: High reliance on boolean contracts without exact value checks.
    5. STRUCTURAL_ONLY: Only isinstance or hasattr checks.
    6. HEALTHY: High semantic ratio and variety.
    """

    UNTESTED = "untested"
    GENUINELY_WEAK = "genuinely_weak"
    SENTINEL_HEAVY = "sentinel_heavy"
    BOOLEAN_CONTRACT_HEAVY = "boolean_contract_heavy"
    STRUCTURAL_ONLY = "structural_only"
    HEALTHY = "healthy"


@dataclass
class ConfidenceDerivation:
    """Confidence metadata for effectiveness analysis."""

    score: float = 0.0
    derivation_methods: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConfidenceDerivation:
        return cls(
            score=data.get("score", 0.0),
            derivation_methods=data.get("derivation_methods", []),
        )


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
    HASATTR_CHECK = "hasattr_check"

    # Semantic (strong) — these kill value-altering mutants
    EQUALITY = "equality"
    INEQUALITY = "inequality"
    COMPARISON = "comparison"
    STRING_CONTAINS = "string_contains"
    COLLECTION_MEMBERSHIP = "collection_membership"
    DICT_KEY_CHECK = "dict_key_check"
    REGEX_MATCH = "regex_match"
    BOOLEAN_CONTRACT_CALL = "boolean_contract_call"
    BOOLEAN_CONTRACT_FIELD = "boolean_contract_field"
    SENTINEL_CHECK = "sentinel_check"

    # Boundary — catch off-by-one and error-path mutations
    RAISES = "raises"
    LENGTH_CHECK = "length_check"
    RANGE_CHECK = "range_check"

    # Property-based — catch broad mutation classes via invariants
    HYPOTHESIS_PROPERTY = "hypothesis_property"


class AnalysisState(str, Enum):
    """State taxonomy for test effectiveness analysis runs."""

    SUCCESS = "success"
    NO_PYTHON_FILES = "no_python_files"
    NO_TEST_FILES = "no_test_files"
    NO_SOURCE_FILES = "no_source_files"
    MANIFEST_BUILD_FAILED = "manifest_build_failed"
    UNMAPPED_TESTS = "unmapped_tests"
    NO_MAPPED_FUNCTIONS = "no_mapped_functions"
    NO_SOURCE_SYMBOLS = "no_source_symbols"
    # (#56) Distinct zero-function scenarios
    MAPPINGS_FOUND_BUT_NO_ANALYZABLE_PUBLIC_FUNCTIONS = (
        "mappings_found_but_no_analyzable_public_functions"
    )
    # (#70) Partial result due to runtime budget
    ANALYSIS_TRUNCATED = "analysis_truncated"


STRENGTH_MAP: dict[AssertionKind, float] = {
    # Structural (weak)
    AssertionKind.IS_NONE: 0.2,
    AssertionKind.IS_NOT_NONE: 0.3,
    AssertionKind.IS_TRUE: 0.2,
    AssertionKind.IS_FALSE: 0.25,
    AssertionKind.ISINSTANCE_CHECK: 0.3,
    AssertionKind.HASATTR_CHECK: 0.1,
    # Semantic (strong)
    AssertionKind.EQUALITY: 0.9,
    AssertionKind.INEQUALITY: 0.7,
    AssertionKind.COMPARISON: 0.85,
    AssertionKind.STRING_CONTAINS: 0.75,
    AssertionKind.COLLECTION_MEMBERSHIP: 0.8,
    AssertionKind.DICT_KEY_CHECK: 0.8,
    AssertionKind.REGEX_MATCH: 0.7,
    AssertionKind.BOOLEAN_CONTRACT_CALL: 0.7,
    AssertionKind.BOOLEAN_CONTRACT_FIELD: 0.65,
    AssertionKind.SENTINEL_CHECK: 0.6,
    # Boundary
    AssertionKind.RAISES: 0.7,
    AssertionKind.LENGTH_CHECK: 0.8,
    AssertionKind.RANGE_CHECK: 0.9,
    # Property-based
    AssertionKind.HYPOTHESIS_PROPERTY: 0.8,
}

# Threshold for classifying an assertion as "semantic" (strong)
SEMANTIC_STRENGTH_THRESHOLD = 0.7
TEFF_SCHEMA_VERSION = "2.0.0"


@dataclass
class AssertionInfo:
    """A single classified assertion within a test."""

    kind: AssertionKind
    line: int
    strength: float
    target_expression: str = ""  # Full unparsed expression (e.g., "result.count")
    target_root: str = ""  # Canonicalized root (e.g., "result")
    confidence: str = "structural"  # "structural" (AST-based) or "heuristic" (name-based)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "kind": self.kind.value,
            "line": self.line,
            "strength": self.strength,
        }
        if self.target_expression:
            d["target_expression"] = self.target_expression
        if self.target_root:
            d["target_root"] = self.target_root
        d["confidence"] = self.confidence
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AssertionInfo:
        return cls(
            kind=AssertionKind(data["kind"]),
            line=data["line"],
            strength=data["strength"],
            target_expression=data.get("target_expression", ""),
            target_root=data.get("target_root", ""),
            confidence=data.get("confidence", "structural"),
        )


@dataclass
class QualityProfile:
    """Multi-axis quality profile for assertions."""

    semantic_ratio: float = 0.0
    structural_ratio: float = 0.0
    boundary_ratio: float = 0.0
    actionable_ratio: float = 0.0
    assertion_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    quality_profile: QualityProfile = field(default_factory=QualityProfile)
    weakness_taxonomy: EffectivenessWeakness | None = None
    confidence: ConfidenceDerivation | None = None

    def compute_scores(
        self,
        derivation_methods: list[str] | None = None,
        has_isolated_sentinel: bool = False,
    ) -> None:
        """Recompute ratios, scores, taxonomy and confidence from the current assertions list."""
        if derivation_methods is None:
            derivation_methods = ["ast_walk"]

        if not self.assertions:
            self.semantic_ratio = 0.0
            self.structural_ratio = 0.0
            self.effectiveness_score = 0.0
            self.mutation_vulnerability = 1.0
            self.weakness_taxonomy = EffectivenessWeakness.UNTESTED

            conf_score = 0.1
            self.confidence = ConfidenceDerivation(
                score=conf_score, derivation_methods=derivation_methods
            )
            return

        total = len(self.assertions)
        semantic_count = 0
        boundary_count = 0
        structural_count = 0
        paired_guards = 0
        bool_contract_count = 0
        counts: dict[str, int] = {}

        # Define categories
        boundary_kinds = {
            AssertionKind.RAISES,
            AssertionKind.LENGTH_CHECK,
            AssertionKind.RANGE_CHECK,
        }

        is_structural_only = True

        for a in self.assertions:
            counts[a.kind.value] = counts.get(a.kind.value, 0) + 1
            if a.strength >= SEMANTIC_STRENGTH_THRESHOLD:
                semantic_count += 1
                is_structural_only = False

            if a.kind in (
                AssertionKind.BOOLEAN_CONTRACT_CALL,
                AssertionKind.BOOLEAN_CONTRACT_FIELD,
            ):
                bool_contract_count += 1

            if a.kind in boundary_kinds:
                boundary_count += 1
                is_structural_only = False
            elif a.strength < SEMANTIC_STRENGTH_THRESHOLD:
                structural_count += 1

            # Paired guard detection logic
            if (
                a.kind
                in (
                    AssertionKind.IS_NONE,
                    AssertionKind.IS_NOT_NONE,
                    AssertionKind.SENTINEL_CHECK,
                )
                and a.strength >= 0.5
            ):
                paired_guards += 1

        self.semantic_ratio = semantic_count / total
        self.structural_ratio = structural_count / total

        # Multi-axis profile
        denom = total - paired_guards
        actionable_ratio = (semantic_count + boundary_count) / denom if denom > 0 else 1.0

        self.quality_profile = QualityProfile(
            semantic_ratio=round(self.semantic_ratio, 3),
            structural_ratio=round(self.structural_ratio, 3),
            boundary_ratio=round(boundary_count / total, 3),
            actionable_ratio=round(min(1.0, actionable_ratio), 3),
            assertion_counts=counts,
        )

        # Weighted mean of assertion strengths
        self.effectiveness_score = sum(a.strength for a in self.assertions) / total
        self.mutation_vulnerability = 1.0 - self.effectiveness_score

        # Taxonomy Waterfall Priority
        if self.semantic_ratio < 0.3 and self.effectiveness_score < 0.4:
            self.weakness_taxonomy = EffectivenessWeakness.GENUINELY_WEAK
        elif has_isolated_sentinel:
            self.weakness_taxonomy = EffectivenessWeakness.SENTINEL_HEAVY
        elif (bool_contract_count / total) > 0.4:
            self.weakness_taxonomy = EffectivenessWeakness.BOOLEAN_CONTRACT_HEAVY
        elif is_structural_only:
            self.weakness_taxonomy = EffectivenessWeakness.STRUCTURAL_ONLY
        else:
            self.weakness_taxonomy = EffectivenessWeakness.HEALTHY

        # Confidence Derivation
        conf_score = 0.8  # Default for AST walk
        if "mutation_calibration" in derivation_methods:
            conf_score = 0.95
        if total < 2:
            conf_score -= 0.2

        self.confidence = ConfidenceDerivation(
            score=round(max(0.1, conf_score), 2), derivation_methods=derivation_methods
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "function_name": self.function_name,
            "test_count": self.test_count,
            "assertions": [a.to_dict() for a in self.assertions],
            "semantic_ratio": round(self.semantic_ratio, 3),
            "structural_ratio": round(self.structural_ratio, 3),
            "effectiveness_score": round(self.effectiveness_score, 3),
            "mutation_vulnerability": round(self.mutation_vulnerability, 3),
            "quality_profile": self.quality_profile.to_dict(),
            "weakness_taxonomy": self.weakness_taxonomy.value if self.weakness_taxonomy else None,
            "confidence": self.confidence.to_dict() if self.confidence else None,
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
            quality_profile=QualityProfile(**data.get("quality_profile", {})),
            weakness_taxonomy=EffectivenessWeakness(data["weakness_taxonomy"])
            if data.get("weakness_taxonomy")
            else None,
            confidence=ConfidenceDerivation.from_dict(data["confidence"])
            if data.get("confidence")
            else None,
        )
        return fe


@dataclass
class StrategyDiagnostics:
    """Diagnostics for a specific mapping strategy."""

    strategy: str
    attempted: int = 0
    mapped: int = 0
    dropped_ambiguous: int = 0
    dropped_no_candidate: int = 0
    dropped_shadowed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "attempted": self.attempted,
            "mapped": self.mapped,
            "dropped_ambiguous": self.dropped_ambiguous,
            "dropped_no_candidate": self.dropped_no_candidate,
            "dropped_shadowed": self.dropped_shadowed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StrategyDiagnostics:
        return cls(
            strategy=data.get("strategy", ""),
            attempted=data.get("attempted", 0),
            mapped=data.get("mapped", 0),
            dropped_ambiguous=data.get("dropped_ambiguous", 0),
            dropped_no_candidate=data.get("dropped_no_candidate", 0),
            dropped_shadowed=data.get("dropped_shadowed", 0),
        )


@dataclass
class MappingCounts:
    """Raw counters for mapping attempts and outcomes."""

    attempted: int = 0
    mapped: int = 0
    dropped_ambiguous: int = 0
    dropped_no_candidate: int = 0
    dropped_shadowed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SymbolStats:
    """High-level symbol coverage and examination metrics."""

    attempted: int = 0
    mapped: int = 0
    test_functions_examined: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DropAnalysis:
    """Detailed analysis of why mapping candidates were dropped."""

    dominant_reason: str | None = None
    dominant_pct: float | None = None
    top_examples: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MappingDiagnostics:
    """Diagnostics for test-to-source mapping."""

    counts: MappingCounts = field(default_factory=MappingCounts)
    symbol_stats: SymbolStats = field(default_factory=SymbolStats)
    drop_analysis: DropAnalysis = field(default_factory=DropAnalysis)
    strategy_breakdown: dict[str, StrategyDiagnostics] = field(default_factory=dict)
    scope_provenance: dict[str, Any] = field(default_factory=dict)

    _attempted_symbols: set[str] = field(default_factory=set, repr=False, init=False)
    _mapped_symbols: set[str] = field(default_factory=set, repr=False, init=False)
    _test_funcs: set[str] = field(default_factory=set, repr=False, init=False)
    _drop_examples: list[dict[str, str]] = field(default_factory=list, repr=False, init=False)

    @property
    def attempted(self) -> int:
        return self.counts.attempted

    @property
    def mapped(self) -> int:
        return self.counts.mapped

    @property
    def dropped_ambiguous(self) -> int:
        return self.counts.dropped_ambiguous

    @property
    def dropped_no_candidate(self) -> int:
        return self.counts.dropped_no_candidate

    @property
    def dropped_shadowed(self) -> int:
        return self.counts.dropped_shadowed

    @property
    def dominant_drop_reason(self) -> str | None:
        return self.drop_analysis.dominant_reason

    @property
    def dominant_drop_pct(self) -> float | None:
        return self.drop_analysis.dominant_pct

    @property
    def top_drop_examples(self) -> list[dict[str, str]]:
        return self.drop_analysis.top_examples

    @property
    def unique_symbols_attempted(self) -> int:
        return self.symbol_stats.attempted

    @property
    def unique_symbols_mapped(self) -> int:
        return self.symbol_stats.mapped

    @property
    def test_functions_examined(self) -> int:
        return self.symbol_stats.test_functions_examined

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts.to_dict(),
            "symbol_stats": self.symbol_stats.to_dict(),
            "drop_analysis": self.drop_analysis.to_dict(),
            "strategy_breakdown": {k: v.to_dict() for k, v in self.strategy_breakdown.items()},
            # Keep flat top-level fields for backward compatibility
            "attempted": self.attempted,
            "mapped": self.mapped,
            "dropped_ambiguous": self.dropped_ambiguous,
            "dropped_no_candidate": self.dropped_no_candidate,
            "dropped_shadowed": self.dropped_shadowed,
            "unique_symbols_attempted": self.unique_symbols_attempted,
            "unique_symbols_mapped": self.unique_symbols_mapped,
            "test_functions_examined": self.test_functions_examined,
            "dominant_drop_reason": self.dominant_drop_reason,
            "dominant_drop_pct": self.dominant_drop_pct,
            "scope_provenance": self.scope_provenance,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MappingDiagnostics:
        # Handle nested data if present, otherwise fall back to flat data
        counts_data = data.get("counts", {})
        counts = MappingCounts(
            attempted=counts_data.get("attempted", data.get("attempted", 0)),
            mapped=counts_data.get("mapped", data.get("mapped", 0)),
            dropped_ambiguous=counts_data.get(
                "dropped_ambiguous", data.get("dropped_ambiguous", 0)
            ),
            dropped_no_candidate=counts_data.get(
                "dropped_no_candidate", data.get("dropped_no_candidate", 0)
            ),
            dropped_shadowed=counts_data.get("dropped_shadowed", data.get("dropped_shadowed", 0)),
        )

        stats_data = data.get("symbol_stats", {})
        symbol_stats = SymbolStats(
            attempted=stats_data.get("attempted", data.get("unique_symbols_attempted", 0)),
            mapped=stats_data.get("mapped", data.get("unique_symbols_mapped", 0)),
            test_functions_examined=stats_data.get(
                "test_functions_examined", data.get("test_functions_examined", 0)
            ),
        )

        drop_data = data.get("drop_analysis", {})
        drop_analysis = DropAnalysis(
            dominant_reason=drop_data.get("dominant_reason", data.get("dominant_drop_reason")),
            dominant_pct=drop_data.get("dominant_pct", data.get("dominant_drop_pct")),
            top_examples=drop_data.get("top_examples", data.get("top_drop_examples", [])),
        )

        obj = cls(
            counts=counts,
            symbol_stats=symbol_stats,
            drop_analysis=drop_analysis,
            scope_provenance=data.get("scope_provenance", {}),
        )
        for k, v in data.get("strategy_breakdown", {}).items():
            obj.strategy_breakdown[k] = StrategyDiagnostics.from_dict(v)
        return obj


@dataclass
class TestEffectivenessManifest:
    """Project-wide test effectiveness inventory."""

    __test__ = False  # Not a pytest test class

    functions: dict[str, FunctionEffectiveness] = field(default_factory=dict)
    project_score: float = 0.0
    file_scores: dict[str, float] = field(default_factory=dict)
    functions_analyzed: int = 0
    mutation_vulnerable_count: int = 0
    diagnostics: MappingDiagnostics = field(default_factory=MappingDiagnostics)

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
            "diagnostics": self.diagnostics.to_dict(),
            "schema_version": TEFF_SCHEMA_VERSION,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestEffectivenessManifest:
        functions = {}
        for k, v in data.get("functions", {}).items():
            functions[k] = FunctionEffectiveness.from_dict(v)
        manifest = cls(
            functions=functions,
            file_scores=data.get("file_scores", {}),
            diagnostics=MappingDiagnostics.from_dict(data.get("diagnostics", {})),
        )
        manifest.update_metrics()
        return manifest
