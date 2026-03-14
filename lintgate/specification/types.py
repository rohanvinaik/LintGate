"""Data types for the specification complexity system."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TestDesignSignals:
    """Test design signals extracted from AST — no test execution."""

    boundary_points: int = 0
    equivalence_partitions: int = 0
    decision_rule_count: int = 0
    predicate_effect_links: int = 0


TestDesignSignals.__test__ = False  # type: ignore[attr-defined]


@dataclass
class TestabilityProfile:
    """Design-for-testability score components."""

    testability_score: float = 1.0
    is_stateful: bool = False
    has_side_effects: bool = False
    injectable_deps: int = 0
    hidden_deps: int = 0


TestabilityProfile.__test__ = False  # type: ignore[attr-defined]


@dataclass
class TPAResult:
    """Test Point Analysis calibration result."""

    tpa_points: int = 0
    tpa_sigma: int = 0
    tpa_confidence: float = 1.0


@dataclass
class RiskProfile:
    """Risk model output for a function."""

    risk_score: float = 0.0
    priority_band: str = "P2"
    risk_factors: list[str] = field(default_factory=list)


@dataclass
class Traceability:
    """Traceability links for a function."""

    requirement_tags: list[str] = field(default_factory=list)
    covering_tests: list[str] = field(default_factory=list)
    covering_test_files: list[str] = field(default_factory=list)
    prescription_history: list[str] = field(default_factory=list)
    assertion_count: int = 0
    coupling_surface: int = 0


@dataclass
class SpecCore:
    """Core specification metrics for a function."""

    estimated_sigma: int = 0
    sigma_confidence: float = 1.0
    regime: str = "unknown"
    regime_rationale: str = ""
    specification_level: float = 0.0
    data_source: str = "static"
    behavioral_dimensions: int = 0
    phase: str = "bulk"
    is_pure: bool = False
    semantic_ratio: float = 0.0
    weakness_taxonomy: str = ""


@dataclass
class ASTMetrics:
    """AST-derived structural metrics."""

    ast_category_count: int = 0
    branch_count: int = 0
    parameter_count: int = 0


@dataclass
class TrajectoryState:
    """Trajectory state for dynamic phase detection (Thm 3.4).

    Tracks specification convergence history to enable ΔK-based
    phase transition detection rather than static thresholds.
    """

    delta_k: list[float] = field(default_factory=list)
    transition_index: int | None = None
    estimated_remaining: int = 0
    convergence_rate: float = 0.0


@dataclass
class PredictionResult:
    """Output of the specification predictor for a single function."""

    spec_level: float = 0.0
    regime: str = "unknown"
    regime_rationale: str = ""
    sigma: int = 0
    phase: str = "bulk"
    sigma_confidence: float = 1.0
    data_source: str = "static"
    testability: TestabilityProfile = field(default_factory=TestabilityProfile)
    design_signals: TestDesignSignals = field(default_factory=TestDesignSignals)
    tpa: TPAResult = field(default_factory=TPAResult)
    trajectory: TrajectoryState = field(default_factory=TrajectoryState)


@dataclass
class FunctionSpecification:
    """Per-function specification state.

    Composes sub-dataclasses to keep attribute count manageable.
    """

    function_key: str = ""
    source_file: str = ""
    core: SpecCore = field(default_factory=SpecCore)
    ast_metrics: ASTMetrics = field(default_factory=ASTMetrics)
    design_signals: TestDesignSignals = field(default_factory=TestDesignSignals)
    testability: TestabilityProfile = field(default_factory=TestabilityProfile)
    tpa: TPAResult = field(default_factory=TPAResult)
    risk: RiskProfile = field(default_factory=RiskProfile)
    traceability: Traceability = field(default_factory=Traceability)
    trajectory: TrajectoryState = field(default_factory=TrajectoryState)
    stop_criteria_met: bool = False
    optimization_hints: list[str] = field(default_factory=list)
    file_hash: str = ""
    computed_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "function_key": self.function_key,
            "source_file": self.source_file,
            "estimated_sigma": self.core.estimated_sigma,
            "sigma_confidence": round(self.core.sigma_confidence, 3),
            "regime": self.core.regime,
            "regime_rationale": self.core.regime_rationale,
            "specification_level": round(self.core.specification_level, 3),
            "data_source": self.core.data_source,
            "behavioral_dimensions": self.core.behavioral_dimensions,
            "phase": self.core.phase,
            "is_pure": self.core.is_pure,
            "is_stateful": self.testability.is_stateful,
            "semantic_ratio": round(self.core.semantic_ratio, 3),
            "ast_category_count": self.ast_metrics.ast_category_count,
            "branch_count": self.ast_metrics.branch_count,
            "parameter_count": self.ast_metrics.parameter_count,
            "weakness_taxonomy": self.core.weakness_taxonomy,
            "boundary_points": self.design_signals.boundary_points,
            "equivalence_partitions": self.design_signals.equivalence_partitions,
            "decision_rule_count": self.design_signals.decision_rule_count,
            "predicate_effect_links": self.design_signals.predicate_effect_links,
            "testability_score": round(self.testability.testability_score, 3),
            "tpa_points": self.tpa.tpa_points,
            "tpa_confidence": round(self.tpa.tpa_confidence, 3),
            "risk_score": round(self.risk.risk_score, 3),
            "priority_band": self.risk.priority_band,
            "requirement_tags": self.traceability.requirement_tags,
            "covering_tests": self.traceability.covering_tests,
            "covering_test_files": self.traceability.covering_test_files,
            "prescription_history": self.traceability.prescription_history,
            "assertion_count": self.traceability.assertion_count,
            "coupling_surface": self.traceability.coupling_surface,
            "trajectory": {
                "delta_k": self.trajectory.delta_k,
                "transition_index": self.trajectory.transition_index,
                "estimated_remaining": self.trajectory.estimated_remaining,
                "convergence_rate": self.trajectory.convergence_rate,
            },
            "stop_criteria_met": self.stop_criteria_met,
            "optimization_hints": self.optimization_hints,
            "file_hash": self.file_hash,
            "computed_at": self.computed_at,
        }


@dataclass
class SpecificationLedger:
    """Project-wide specification state."""

    functions: dict[str, FunctionSpecification] = field(default_factory=dict)
    specification_coverage: float = 0.0
    regime_distribution: dict[str, int] = field(default_factory=dict)
    risk_distribution: dict[str, int] = field(default_factory=dict)
    total_sigma: int = 0
    mean_testability: float = 0.0
    stop_criteria_met_count: int = 0
    schema_version: str = "3"

    def update_metrics(self) -> None:
        """Recalculate aggregate metrics from function data."""
        if not self.functions:
            self.specification_coverage = 0.0
            self.regime_distribution = {"A": 0, "B": 0, "unknown": 0}
            self.risk_distribution = {"P0": 0, "P1": 0, "P2": 0}
            self.total_sigma = 0
            self.mean_testability = 0.0
            self.stop_criteria_met_count = 0
            return

        levels = []
        regime_dist: dict[str, int] = {"A": 0, "B": 0, "unknown": 0}
        risk_dist: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0}
        total_sigma = 0
        testability_sum = 0.0
        stop_count = 0

        for fs in self.functions.values():
            levels.append(fs.core.specification_level)
            r = fs.core.regime
            regime_dist[r] = regime_dist.get(r, 0) + 1
            pb = fs.risk.priority_band
            risk_dist[pb] = risk_dist.get(pb, 0) + 1
            total_sigma += fs.core.estimated_sigma
            testability_sum += fs.testability.testability_score
            if fs.stop_criteria_met:
                stop_count += 1

        n = len(self.functions)
        self.specification_coverage = sum(levels) / n if n else 0.0
        self.regime_distribution = regime_dist
        self.risk_distribution = risk_dist
        self.total_sigma = total_sigma
        self.mean_testability = testability_sum / n if n else 0.0
        self.stop_criteria_met_count = stop_count

    def to_dict(self) -> dict:
        return {
            "functions": {k: v.to_dict() for k, v in self.functions.items()},
            "specification_coverage": round(self.specification_coverage, 3),
            "regime_distribution": self.regime_distribution,
            "risk_distribution": self.risk_distribution,
            "total_sigma": self.total_sigma,
            "mean_testability": round(self.mean_testability, 3),
            "stop_criteria_met_count": self.stop_criteria_met_count,
            "schema_version": self.schema_version,
        }


# ── Phase 3 types (composition) ─────────────────────────────────────


@dataclass
class IntegrationSurface:
    """Integration surface metrics for a cross-module call edge."""

    caller: str = ""
    callee: str = ""
    call_arg_count: int = 0
    callee_param_count: int = 0
    shared_mutable_state: bool = False
    type_boundary_crossing: bool = False
    surface_area: float = 0.0
    interface_complexity: float = 0.0


@dataclass
class CompositionEdge:
    """A composition edge between two functions across modules."""

    caller: str = ""
    callee: str = ""
    gamma: float = 0.0
    integration_surface: IntegrationSurface = field(default_factory=IntegrationSurface)
    interface_mutant_count: int = 0
    specification_independent: bool = False


@dataclass
class ModuleSpecification:
    """Module-level specification summary."""

    module_path: str = ""
    functions: list[str] = field(default_factory=list)
    local_sigma_sum: int = 0
    interface_gamma_sum: float = 0.0
    mean_integration_complexity: float = 0.0
    sheaf_compatible: bool = True
