"""PrescriptiveSpec composer — builds specs from theory + compass."""

from __future__ import annotations

import hashlib
import time
from typing import Any

# ── Composer ──────────────────────────────────────────────────────────
from .claim_projection import _CAUSAL_MARKERS, _CONTRASTIVE_MARKERS  # noqa: E402
from .predicates import (
    compile_claim,
)
from .spec import PrescriptiveSpec
from .types import (
    ForbiddenBehavior,
    GenerationConstraint,
    Invariant,
    RefinementObligation,
    StateTransition,
    StateVariable,
    TestObligation,
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
                return str(declared)
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
        from .sigma import estimate_prescriptive_sigma

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
