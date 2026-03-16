"""B1: Extraction Plan Builder — ordered stepwise refactoring guidance from convergence evidence.

Bridges "what to extract" (ConvergenceResult) to "how to extract" (ExtractionPlan):
ordered steps with function signatures, parameter specs, importer update lists,
and test migration guidance.

Plans are pure data structures — no code modification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import ast

from .evidence import Actionability, ConvergenceResult, LensEvidence, LensKind

# ── Data Structures ────────────────────────────────────────────────────


@dataclass
class ExtractionStep:
    """A single ordered step in an extraction plan."""

    order: int
    action: str  # "create_function" | "extract_body" | "create_module" | "update_callers" | "update_imports" | "migrate_tests" | "extract_handler"
    target: str  # function/file being acted on
    detail: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "action": self.action,
            "target": self.target,
            "detail": self.detail,
            "rationale": self.rationale,
        }


@dataclass
class ExtractionPlan:
    """Complete extraction plan for a convergence target."""

    source_function: str
    source_file: str
    steps: list[ExtractionStep] = field(default_factory=list)
    estimated_impact: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    convergence: ConvergenceResult | None = None
    post_extraction_opportunities: list = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "source_function": self.source_function,
            "source_file": self.source_file,
            "steps": [s.to_dict() for s in self.steps],
            "estimated_impact": self.estimated_impact,
            "warnings": self.warnings,
        }
        if self.convergence is not None:
            d["convergence"] = self.convergence.to_dict()
        if self.post_extraction_opportunities:
            d["post_extraction_opportunities"] = [
                o.to_dict() if hasattr(o, "to_dict") else o
                for o in self.post_extraction_opportunities
            ]
        return d


# ── Plan Builder ───────────────────────────────────────────────────────


def build_extraction_plan(
    candidate: ConvergenceResult,
    ast_context: ast.Module | None = None,
    source_file: str = "",
) -> ExtractionPlan:
    """Build an ordered extraction plan from a ConvergenceResult.

    Generates steps based on evidence type and actionability.
    Steps are always ordered: create_function → extract_body → create_module →
    update_callers → update_imports → migrate_tests.

    Handler extraction plans (extract_handler) are generated when
    dep_clustering evidence contains handler/register patterns.

    Args:
        candidate: ConvergenceResult with evidence from convergence aggregator.
        ast_context: Optional parsed AST module for deeper analysis.
        source_file: Source file path for the target.

    Returns:
        ExtractionPlan with ordered steps, warnings, and estimated impact.
    """
    source_file = source_file or _infer_source_file(candidate)
    source_function = candidate.target

    plan = ExtractionPlan(
        source_function=source_function,
        source_file=source_file,
        convergence=candidate,
    )

    # Classify evidence for step generation
    evidence_by_lens = _group_evidence_by_lens(candidate.evidence)
    prescriptions = _extract_prescriptions(candidate.evidence)
    handler_prescriptions = [p for p in prescriptions if _is_handler_prescription(p)]
    block_prescriptions = [p for p in prescriptions if not _is_handler_prescription(p)]

    # Generate steps based on actionability
    if handler_prescriptions:
        _build_handler_extraction_steps(plan, handler_prescriptions, evidence_by_lens)
    elif block_prescriptions:
        _build_block_extraction_steps(plan, block_prescriptions, evidence_by_lens, ast_context)
    else:
        _build_generic_extraction_steps(plan, candidate, evidence_by_lens, ast_context)

    # File-level split step if actionability is SPLIT
    if candidate.actionability == Actionability.SPLIT and candidate.target_type == "file":
        _add_file_split_steps(plan, candidate, evidence_by_lens)

    # Generate warnings from opposing evidence
    plan.warnings = _generate_warnings(candidate)

    # Populate estimated impact
    plan.estimated_impact = _compute_estimated_impact(
        plan, candidate, prescriptions, evidence_by_lens
    )

    return plan


# ── Step Generators ────────────────────────────────────────────────────


def _build_handler_extraction_steps(
    plan: ExtractionPlan,
    handler_prescriptions: list[dict],
    evidence_by_lens: dict[LensKind, list[LensEvidence]],
) -> None:
    """Generate steps for handler extraction (bag-of-handlers pattern)."""
    order = 1

    for hp in handler_prescriptions:
        proposed_name = hp.get("proposed_name", f"_impl_{hp.get('target', 'handler')}")
        inputs = hp.get("inputs", [])
        outputs = hp.get("outputs", [])
        lines = hp.get("lines")
        handler_name = _extract_handler_name(hp)

        captured_writes = outputs
        if captured_writes:
            rationale = (
                f"Nested handler captures {len(inputs)} outer variables "
                f"and writes to {len(captured_writes)}. "
                f"Extraction requires returning modified values."
            )
        else:
            rationale = (
                f"Nested handler captures {len(inputs)} outer variables. "
                f"No writes to outer scope. Safe extraction to module-level "
                f"function with explicit parameters."
            )

        plan.steps.append(
            ExtractionStep(
                order=order,
                action="extract_handler",
                target=handler_name or hp.get("target", plan.source_function),
                detail={
                    "handler_name": handler_name,
                    "proposed_name": proposed_name,
                    "source_lines": list(lines) if lines else [],
                    "captured_variables": inputs,
                    "captured_writes": outputs,
                    "destination": "module_level",
                    "parameters": inputs,
                },
                rationale=rationale,
            )
        )
        order += 1

    # Final step: rewrite parent as thin delegation wrapper
    plan.steps.append(
        ExtractionStep(
            order=order,
            action="update_callers",
            target=plan.source_function,
            detail={
                "description": "Rewrite as thin delegation wrapper calling _impl_* functions",
                "handler_count": len(handler_prescriptions),
            },
            rationale="After extracting handlers, the registration function becomes a thin wrapper.",
        )
    )
    order += 1

    # Test migration
    plan.steps.append(
        ExtractionStep(
            order=order,
            action="migrate_tests",
            target=plan.source_function,
            detail={
                "description": "Update tests to call _impl_* functions directly",
                "test_count_needed": len(handler_prescriptions),
            },
            rationale="Extracted handlers should be tested directly for isolation.",
        )
    )


def _build_block_extraction_steps(
    plan: ExtractionPlan,
    prescriptions: list[dict],
    evidence_by_lens: dict[LensKind, list[LensEvidence]],
    ast_context: ast.Module | None,
) -> None:
    """Generate steps for contiguous block extraction."""
    order = 1

    for p in prescriptions:
        proposed_name = p.get("proposed_name", "_extracted_helper")
        inputs = p.get("inputs", [])
        outputs = p.get("outputs", [])
        lines = p.get("lines")

        # Step 1: create_function
        param_types = _infer_param_types(inputs, ast_context)
        return_type = _infer_return_type(outputs, ast_context)

        plan.steps.append(
            ExtractionStep(
                order=order,
                action="create_function",
                target=proposed_name,
                detail={
                    "parameters": inputs,
                    "parameter_types": param_types,
                    "return_type": return_type,
                    "outputs": outputs,
                    "proposed_name": proposed_name,
                },
                rationale=f"Define new function from dependency clustering: inputs={inputs}, outputs={outputs}.",
            )
        )
        order += 1

        # Step 2: extract_body
        plan.steps.append(
            ExtractionStep(
                order=order,
                action="extract_body",
                target=plan.source_function,
                detail={
                    "source_lines": list(lines) if lines else [],
                    "destination_function": proposed_name,
                },
                rationale=f"Move lines {lines} from '{plan.source_function}' into '{proposed_name}'.",
            )
        )
        order += 1

    # Step 3: update_callers
    plan.steps.append(
        ExtractionStep(
            order=order,
            action="update_callers",
            target=plan.source_function,
            detail={
                "description": "Update call sites to use extracted function(s)",
                "extracted_functions": [p.get("proposed_name", "") for p in prescriptions],
            },
            rationale="Replace inline code with calls to the extracted function(s).",
        )
    )
    order += 1

    # Step 4: update_imports
    fan_in_count = _get_fan_in_count(evidence_by_lens)
    if fan_in_count > 0:
        plan.steps.append(
            ExtractionStep(
                order=order,
                action="update_imports",
                target=plan.source_file,
                detail={
                    "importer_count": fan_in_count,
                    "importers": _get_importer_list(evidence_by_lens),
                },
                rationale=f"{fan_in_count} module(s) import this — update their imports.",
            )
        )
        order += 1

    # Step 5: migrate_tests
    plan.steps.append(
        ExtractionStep(
            order=order,
            action="migrate_tests",
            target=plan.source_function,
            detail={
                "description": "Identify and migrate tests covering extracted code",
                "test_count_needed": len(prescriptions),
            },
            rationale="Tests covering the extracted blocks should target the new functions.",
        )
    )


def _build_generic_extraction_steps(
    plan: ExtractionPlan,
    candidate: ConvergenceResult,
    evidence_by_lens: dict[LensKind, list[LensEvidence]],
    ast_context: ast.Module | None,
) -> None:
    """Generate generic extraction steps when no prescriptions are available."""
    order = 1

    # Step 1: create_function
    plan.steps.append(
        ExtractionStep(
            order=order,
            action="create_function",
            target=candidate.target,
            detail={
                "description": "Define extracted function based on convergence evidence",
                "supporting_lenses": [lk.value for lk in candidate.supporting_lenses],
            },
            rationale=f"Convergence ({candidate.net_confidence:.0%} net) across {len(candidate.supporting_lenses)} lenses supports extraction.",
        )
    )
    order += 1

    # Step 2: extract_body
    plan.steps.append(
        ExtractionStep(
            order=order,
            action="extract_body",
            target=candidate.target,
            detail={"description": "Identify and move target code block"},
            rationale="Extract the identified code segment into the new function.",
        )
    )
    order += 1

    # Step 3: update_callers
    plan.steps.append(
        ExtractionStep(
            order=order,
            action="update_callers",
            target=candidate.target,
            detail={"description": "Update call sites in source file"},
            rationale="Replace inline code with call to extracted function.",
        )
    )
    order += 1

    # Step 4: update_imports
    fan_in_count = _get_fan_in_count(evidence_by_lens)
    if fan_in_count > 0:
        plan.steps.append(
            ExtractionStep(
                order=order,
                action="update_imports",
                target=plan.source_file,
                detail={
                    "importer_count": fan_in_count,
                    "importers": _get_importer_list(evidence_by_lens),
                },
                rationale=f"{fan_in_count} module(s) import this — update their imports.",
            )
        )
        order += 1

    # Step 5: migrate_tests
    plan.steps.append(
        ExtractionStep(
            order=order,
            action="migrate_tests",
            target=candidate.target,
            detail={"test_count_needed": 1},
            rationale="Add or migrate tests for the extracted function.",
        )
    )


def _add_file_split_steps(
    plan: ExtractionPlan,
    candidate: ConvergenceResult,
    evidence_by_lens: dict[LensKind, list[LensEvidence]],
) -> None:
    """Add create_module step for file-level splits."""
    # Determine destination module name from split proposals or cohesion data
    destination = _suggest_module_name(candidate)

    next_order = max((s.order for s in plan.steps), default=0) + 1

    # Insert create_module before update steps
    plan.steps.append(
        ExtractionStep(
            order=next_order,
            action="create_module",
            target=destination,
            detail={
                "source_file": plan.source_file,
                "split_proposals": candidate.split_proposals,
                "description": f"Create new module '{destination}' for split components",
            },
            rationale="File-level convergence supports splitting into separate module(s).",
        )
    )

    # Re-order steps so create_module comes after extract_body and before update_callers
    _reorder_steps(plan)


from .extraction_plan_helpers import (  # noqa: F401, E402
    _compute_estimated_impact,
    _extract_handler_name,
    _extract_prescriptions,
    _generate_warnings,
    _get_fan_in_count,
    _get_importer_list,
    _group_evidence_by_lens,
    _infer_param_types,
    _infer_return_type,
    _infer_source_file,
    _is_handler_prescription,
    _reorder_steps,
    _suggest_module_name,
    build_batch_extraction_plan,
)
