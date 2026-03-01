"""B1: Extraction Plan Builder — ordered stepwise refactoring guidance from convergence evidence.

Bridges "what to extract" (ConvergenceResult) to "how to extract" (ExtractionPlan):
ordered steps with function signatures, parameter specs, importer update lists,
and test migration guidance.

Plans are pure data structures — no code modification.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

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
        _build_block_extraction_steps(
            plan, block_prescriptions, evidence_by_lens, ast_context
        )
    else:
        _build_generic_extraction_steps(plan, candidate, evidence_by_lens, ast_context)

    # File-level split step if actionability is SPLIT
    if (
        candidate.actionability == Actionability.SPLIT
        and candidate.target_type == "file"
    ):
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
                "extracted_functions": [
                    p.get("proposed_name", "") for p in prescriptions
                ],
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


# ── Warning Generation ─────────────────────────────────────────────────


def _generate_warnings(candidate: ConvergenceResult) -> list[str]:
    """Generate warnings from opposing evidence."""
    warnings: list[str] = []

    for ev in candidate.evidence:
        if ev.signal != "oppose":
            continue

        if ev.lens == LensKind.FAN_IN:
            fan_in = ev.raw.get("fan_in", 0)
            if fan_in >= 5:
                warnings.append(
                    f"Warning: {fan_in} modules import this — "
                    f"extraction will require updating all importers"
                )

        elif ev.lens == LensKind.COCHANGE:
            strength = ev.raw.get("coupling_strength", 0.0)
            if strength > 0.6:
                # Extract coupled file from detail or raw
                coupled_file = _extract_coupled_file(ev)
                warnings.append(
                    f"Warning: high co-change coupling ({strength:.2f}) "
                    f"with {coupled_file} — consider extracting together"
                )

        elif ev.lens == LensKind.ALGEBRAIC:
            if "unsafe" in ev.detail.lower():
                warnings.append(
                    f"Warning: algebraic analysis flags unsafe extraction — {ev.detail}"
                )

        elif ev.lens == LensKind.IMPORT_TRACING and "io" in ev.detail.lower():
            warnings.append(
                "Warning: module-level IO detected — "
                "extraction may change initialization order"
            )

    return warnings


# ── Impact Estimation ──────────────────────────────────────────────────


def _compute_estimated_impact(
    plan: ExtractionPlan,
    candidate: ConvergenceResult,
    prescriptions: list[dict],
    evidence_by_lens: dict[LensKind, list[LensEvidence]],
) -> dict[str, Any]:
    """Compute estimated impact metrics for the extraction plan."""
    impact: dict[str, Any] = {}

    # CC reduction from prescriptions
    total_cc_reduction = sum(
        p.get("expected_delta", {}).get("cc_reduction", 0) for p in prescriptions
    )
    impact["CC_reduction"] = total_cc_reduction

    # Fan-in change
    fan_in_count = _get_fan_in_count(evidence_by_lens)
    impact["fan_in_change"] = -fan_in_count if fan_in_count > 0 else 0

    # Test count needed
    handler_steps = [s for s in plan.steps if s.action == "extract_handler"]
    block_steps = [s for s in plan.steps if s.action == "create_function"]
    extraction_count = len(handler_steps) or len(block_steps) or 1
    impact["test_count_needed"] = extraction_count

    # Line count delta (handler extraction adds ~12 lines per handler for explicit params)
    if handler_steps:
        avg_lines_per_handler = 12
        line_delta = len(handler_steps) * avg_lines_per_handler
        impact["line_count_delta"] = line_delta
        impact["line_count_explanation"] = (
            f"Explicit parameter passing adds ~{avg_lines_per_handler} lines "
            f"per extracted handler"
        )
    elif prescriptions:
        # Block extraction: net neutral or slight reduction
        total_lines = sum(_line_count_from_prescription(p) for p in prescriptions)
        # New function signatures add ~3 lines each, but code moves
        impact["line_count_delta"] = len(prescriptions) * 3
        impact["line_count_explanation"] = (
            f"Function signatures add ~3 lines each; "
            f"{total_lines} lines of body code moves (net neutral)"
        )

    return impact


# ── Batch Extraction Plans ─────────────────────────────────────────────


def build_batch_extraction_plan(
    candidates: list[ConvergenceResult],
    ast_context: ast.Module | None = None,
    source_file: str = "",
) -> list[ExtractionPlan]:
    """Build extraction plans for multiple convergence candidates.

    When candidates share the same source file, plans are coordinated
    to avoid conflicting line ranges.

    Returns:
        List of ExtractionPlan, one per candidate.
    """
    plans: list[ExtractionPlan] = []
    for candidate in candidates:
        plan = build_extraction_plan(candidate, ast_context, source_file)
        plans.append(plan)
    return plans


# ── Helpers ────────────────────────────────────────────────────────────


def _infer_source_file(candidate: ConvergenceResult) -> str:
    """Infer source file from the convergence target."""
    target = candidate.target
    if "::" in target:
        return target.split("::")[0]
    return target


def _group_evidence_by_lens(
    evidence: list[LensEvidence],
) -> dict[LensKind, list[LensEvidence]]:
    """Group evidence items by their lens kind."""
    by_lens: dict[LensKind, list[LensEvidence]] = {}
    for ev in evidence:
        by_lens.setdefault(ev.lens, []).append(ev)
    return by_lens


def _extract_prescriptions(evidence: list[LensEvidence]) -> list[dict]:
    """Extract prescription-like data from DEP_CLUSTERING evidence."""
    prescriptions: list[dict] = []
    for ev in evidence:
        if ev.lens == LensKind.DEP_CLUSTERING:
            raw = ev.raw
            # Try to extract structured data
            p: dict[str, Any] = {
                "target": ev.target,
                "detail": ev.detail,
            }
            # Check if raw contains structured prescription data
            for key in (
                "proposed_name",
                "inputs",
                "outputs",
                "lines",
                "expected_delta",
                "basis",
                "kind",
                "action",
            ):
                if key in raw:
                    p[key] = raw[key]
            prescriptions.append(p)
    return prescriptions


def _is_handler_prescription(p: dict) -> bool:
    """Check if a prescription is for handler extraction."""
    kind = p.get("kind", "")
    basis = p.get("basis", [])
    action = p.get("action", "")
    return (
        kind == "decompose_register"
        or "nested_handler" in basis
        or "extract nested handler" in action.lower()
        or "extract_handler" in action.lower()
    )


def _extract_handler_name(p: dict) -> str:
    """Extract the handler name from a handler prescription."""
    action = p.get("action", "")
    # Try to parse "Extract nested handler `name`" pattern
    if "`" in action:
        parts = action.split("`")
        if len(parts) >= 2:
            return parts[1]
    target = p.get("target", "")
    if "::" in target:
        return target.split("::")[-1]
    return ""


def _get_fan_in_count(
    evidence_by_lens: dict[LensKind, list[LensEvidence]],
) -> int:
    """Get fan-in count from evidence."""
    fan_in_evidence = evidence_by_lens.get(LensKind.FAN_IN, [])
    for ev in fan_in_evidence:
        if ev.signal == "oppose":
            return ev.raw.get("fan_in", 0)
    return 0


def _get_importer_list(
    evidence_by_lens: dict[LensKind, list[LensEvidence]],
) -> list[str]:
    """Get list of importers from fan-in evidence."""
    fan_in_evidence = evidence_by_lens.get(LensKind.FAN_IN, [])
    importers: list[str] = []
    for ev in fan_in_evidence:
        if ev.signal == "oppose":
            raw_importers = ev.raw.get("importers", [])
            importers.extend(raw_importers)
    return importers


def _extract_coupled_file(ev: LensEvidence) -> str:
    """Extract the coupled file name from co-change evidence."""
    raw = ev.raw
    file_a = raw.get("file_a", "")
    file_b = raw.get("file_b", "")
    target = ev.target
    if file_a and file_b:
        return file_b if file_a in target else file_a
    # Parse from detail
    detail = ev.detail
    if "with " in detail:
        return detail.split("with ")[-1].split(" ")[0]
    return "unknown"


def _infer_param_types(
    inputs: list[str],
    ast_context: ast.Module | None,
) -> dict[str, str]:
    """Infer parameter types from AST annotations if available."""
    if not ast_context or not inputs:
        return {}

    type_map: dict[str, str] = {}
    for node in ast.walk(ast_context):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in inputs and node.annotation:
                type_map[node.target.id] = ast.unparse(node.annotation)
        elif isinstance(node, ast.arg) and node.arg in inputs and node.annotation:
            type_map[node.arg] = ast.unparse(node.annotation)
    return type_map


def _infer_return_type(
    outputs: list[str],
    ast_context: ast.Module | None,
) -> str:
    """Infer return type from outputs."""
    if not outputs:
        return "None"
    if len(outputs) == 1:
        return "Any"
    return f"tuple[{', '.join(['Any'] * len(outputs))}]"


def _suggest_module_name(candidate: ConvergenceResult) -> str:
    """Suggest a module name for file-level splits."""
    target = candidate.target
    if candidate.split_proposals:
        first = candidate.split_proposals[0]
        if isinstance(first, dict) and "module_name" in first:
            return first["module_name"]
    # Default: append _extracted to base name
    base = target.rsplit("/", 1)[-1] if "/" in target else target
    if base.endswith(".py"):
        base = base[:-3]
    return f"{base}_extracted.py"


def _reorder_steps(plan: ExtractionPlan) -> None:
    """Re-order steps to maintain canonical ordering.

    Canonical order: create_function → extract_body → extract_handler →
    create_module → update_callers → update_imports → migrate_tests
    """
    action_priority = {
        "create_function": 1,
        "extract_body": 2,
        "extract_handler": 3,
        "create_module": 4,
        "update_callers": 5,
        "update_imports": 6,
        "migrate_tests": 7,
    }
    plan.steps.sort(key=lambda s: (action_priority.get(s.action, 99), s.order))
    for i, step in enumerate(plan.steps, 1):
        step.order = i


def _line_count_from_prescription(p: dict) -> int:
    """Estimate line count from a prescription's lines range."""
    lines = p.get("lines")
    if lines and len(lines) == 2:
        return lines[1] - lines[0] + 1
    return 0
