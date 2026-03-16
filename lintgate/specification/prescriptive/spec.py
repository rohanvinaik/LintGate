"""PrescriptiveSpec — the top-level specification record.

This is the core IR dataclass. Domain types, predicates, persistence,
composition, and resolution live in sibling modules.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .predicates import (  # noqa: F401 — re-export
    Predicate,
    PredicateOp,
    compile_claim,
    pred_and,
    pred_custom,
    pred_eq,
    pred_gt,
    pred_gte,
    pred_lt,
    pred_neq,
    pred_no_raise,
    pred_not,
    pred_or,
    pred_param_count_lte,
    pred_pure,
    pred_raises,
    pred_returns_non_null,
    pred_true,
    pred_type,
)
from .types import (  # noqa: F401 — re-export
    ForbiddenBehavior,
    GenerationConstraint,
    Invariant,
    RefinementObligation,
    StateTransition,
    StateVariable,
    TestObligation,
)


# Lazy re-exports from sibling modules — avoids circular imports
# but allows `from .spec import X` for all X
def __getattr__(name: str) -> Any:
    """Re-export names from sibling modules for backward compatibility."""
    persistence_names = {
        "PrescriptiveWorkflowRecord",
        "_SPEC_DIR",
        "_target_hash",
        "save_spec",
        "load_spec",
        "load_all_specs",
        "load_spec_index",
        "save_workflow_record",
        "load_workflow_record",
        "spec_coverage",
        "_load_index",
    }
    resolver_names = {
        "ResolvedTarget",
        "resolve_targets",
        "_scan_pspec_stubs",
        "_find_function_at",
        "_match_claims_to_symbols",
        "_build_func_index",
    }
    claim_projection_names = {
        "project_claims",
        "_score_claim_relevance",
        "_claim_contradicted_by_spec",
    }
    composer_names = {
        "PrescriptiveSpecComposer",
    }
    if name in persistence_names:
        from . import persistence

        return getattr(persistence, name)
    if name in resolver_names:
        from . import resolver

        return getattr(resolver, name)
    if name in claim_projection_names:
        from . import claim_projection

        return getattr(claim_projection, name)
    if name in composer_names:
        from . import composer

        return getattr(composer, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
